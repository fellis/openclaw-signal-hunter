"""
Orchestrator.
Coordinates the collect → process → embed pipeline.
Does not contain business logic - only wires components together.
Each operation streams JSON progress lines to stdout (for OpenClaw exec background).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any

from core.embedder import Embedder
from core.models import ExtractionRule, KeywordProfile, SearchPlan, SearchTarget
from core.processor import Processor
from core.registry import BaseCollector, get, get_all, load_all_collectors
from storage.postgres import PostgresStorage
from storage.vector import VectorStorage

log = logging.getLogger(__name__)


def _emit(data: dict[str, Any]) -> None:
    """Write a JSON progress line to stdout. OpenClaw reads these."""
    print(json.dumps(data, ensure_ascii=False), flush=True)




def load_rules(config: dict) -> list:
    """Load ExtractionRule list from config. Used by EmbedWorker and LLM Worker."""
    from core.models import ExtractionRule  # noqa: PLC0415
    raw_rules = config.get("extraction_rules", [])
    return [
        ExtractionRule(
            name=r["name"],
            description=r.get("description", ""),
            examples=r.get("examples", []),
            negative_examples=r.get("negative_examples", []),
            priority=r.get("priority", 1),
        )
        for r in raw_rules
    ]

class Orchestrator:
    """
    Top-level coordinator. All heavy operations delegate to dedicated classes.
    Emits structured progress via stdout (OpenClaw exec background contract).
    """

    def __init__(self, config: dict[str, Any], storage: PostgresStorage) -> None:
        self._config = config
        self._storage = storage

    def collect(self, keywords: list[str] | None = None) -> dict[str, Any]:
        """
        Run collection for approved plans.
        keywords: if provided, only collect plans whose canonical_name is in this list.
        Returns summary: {total: int, by_collector: {name: int}, keywords_filtered: list}.
        """
        load_all_collectors()
        plans = self._storage.get_all_active_plans()

        if not plans:
            _emit({"status": "done", "phase": "collect", "total": 0, "note": "No approved plans found."})
            return {"total": 0, "by_collector": {}}

        if keywords:
            kw_set = {k.lower() for k in keywords}
            plans = [p for p in plans if p["canonical_name"].lower() in kw_set]
            if not plans:
                note = f"No approved plans for: {keywords}"
                _emit({"status": "done", "phase": "collect", "total": 0, "note": note})
                return {"total": 0, "by_collector": {}}

        by_collector: dict[str, int] = {}

        sources_cfg = self._config.get("sources", {})

        for plan_row in plans:
            collector_name = plan_row["collector_name"]
            canonical_name = plan_row["canonical_name"]
            plan_data = plan_row["plan_data"]

            if not sources_cfg.get(collector_name, {}).get("enabled", True):
                log.info("[orchestrator] skipping disabled source: %s", collector_name)
                continue

            collector_cls = get(collector_name)
            if not collector_cls:
                log.warning("[orchestrator] unknown collector: %s", collector_name)
                continue

            collector: BaseCollector = collector_cls()
            cursors = self._storage.get_cursors(collector_name)

            try:
                plan = self._deserialize_plan(plan_data)

                # Expand plan with newly appeared sources (GitHub repos, HF spaces)
                profile_data = self._storage.get_keyword_profile(canonical_name)
                if profile_data:
                    profile = self._deserialize_profile(profile_data)
                    new_targets = collector.discover_new_sources(profile, plan)
                    if new_targets:
                        added = self._storage.add_plan_targets(
                            canonical_name, collector_name, new_targets
                        )
                        if added:
                            # Reload plan so the new targets are collected right away
                            refreshed = self._storage.get_collection_plans(canonical_name)
                            if collector_name in refreshed:
                                plan = self._deserialize_plan(refreshed[collector_name])
                            log.info(
                                "[orchestrator] expanded plan for %s/%s: +%d target(s)",
                                canonical_name, collector_name, added,
                            )

                _emit({
                    "status": "running", "phase": "collect",
                    "keyword": canonical_name, "source": collector_name,
                    "targets": len(plan.targets),
                })

                result = collector.collect(plan, cursors)

                inserted = 0
                for signal in result.signals:
                    # Tag signal with the collecting keyword so keywords_matched works
                    kw_list = signal.extra.setdefault("keywords", [])
                    if canonical_name not in kw_list:
                        kw_list.append(canonical_name)
                    if self._storage.upsert_raw_signal(signal):
                        inserted += 1

                self._storage.save_cursors(collector_name, result.updated_cursors)
                by_collector[collector_name] = by_collector.get(collector_name, 0) + inserted

                _emit({
                    "status": "running", "phase": "collect",
                    "keyword": canonical_name, "source": collector_name,
                    "collected": inserted, "total_signals": len(result.signals),
                })

            except Exception as e:
                log.error("[orchestrator] collect failed for %s/%s: %s", collector_name, canonical_name, e)
                _emit({"status": "error", "phase": "collect", "source": collector_name, "error": str(e)})

        total = sum(by_collector.values())
        result: dict[str, Any] = {"total": total, "by_collector": by_collector}
        if keywords:
            result["keywords_filtered"] = keywords
        _emit({"status": "done", "phase": "collect", **result})
        return result

    def process(self, router, max_batches: int | None = None) -> dict[str, Any]:
        """
        Classify unprocessed signals. Mode is selected by config.processor.mode:
          "embed" - embedding cosine similarity + LLM summary only (faster, no GPU for classify)
          "llm"   - full LLM classify (default, legacy behaviour)
        max_batches: None = process all; int = stop after N batches (cron mode).
        Returns {total: int, remaining: int}.
        """
        rules = self._load_rules()
        if not rules:
            _emit({"status": "done", "phase": "process", "total": 0,
                   "note": "No extraction_rules defined. Run suggest_rules first."})
            return {"total": 0, "remaining": 0}

        proc_mode = self._config.get("processor", {}).get("mode", "llm")
        if proc_mode == "embed":
            from core.embed_processor import EmbedProcessor  # noqa: PLC0415
            processor = EmbedProcessor(router, self._storage, rules, self._config)
        else:
            processor = Processor(router, self._storage, rules, self._config)

        _emit({"status": "running", "phase": "process", "processor": proc_mode,
               "mode": f"max_batches={max_batches}" if max_batches else "all"})
        total = processor.process_all(max_batches=max_batches)
        if total == -1:
            _emit({"status": "skipped", "phase": "process",
                   "note": "Another processing run is already active. Skipping to avoid duplicates."})
            return {"total": 0, "remaining": self._storage.count_unprocessed(), "skipped": True}
        remaining = self._storage.count_unprocessed()
        _emit({"status": "done", "phase": "process", "total": total, "remaining": remaining})
        return {"total": total, "remaining": remaining}

    def embed_pending(self, device: str = "cpu") -> dict[str, Any]:
        """
        Vectorize all pending signals in embedding_queue.
        Uses embedder_vectorizer config if present (dedicated container on separate port),
        falls back to embedder config so classification and vectorization don't compete.
        Returns {total: int}.
        """
        vector = VectorStorage()
        embedder_cfg = self._config.get("embedder", {})
        vectorizer_cfg = self._config.get("embedder_vectorizer", embedder_cfg)
        embedder = Embedder(
            storage=self._storage,
            vector=vector,
            batch_size=vectorizer_cfg.get("batch_size", embedder_cfg.get("batch_size", 64)),
            device=device,
            service_url=vectorizer_cfg.get("service_url", embedder_cfg.get("service_url")),
            max_items=vectorizer_cfg.get("max_items_per_run", embedder_cfg.get("max_items_per_run", 512)),
        )
        _emit({"status": "running", "phase": "embed"})
        total = embedder.embed_pending()
        _emit({"status": "done", "phase": "embed", "total": total})
        return {"total": total}

    def query(self, prompt: str, router) -> str:
        """
        Answer a user query using Qdrant semantic search + Claude aggregation.
        Returns formatted text response.
        """
        from core.embedder import Embedder  # noqa: PLC0415 already imported above
        from core.llm_router import LLMCall  # noqa: PLC0415

        vector = VectorStorage()
        embedder_cfg = self._config.get("embedder", {})
        embedder = Embedder(
            storage=self._storage,
            vector=vector,
            device=embedder_cfg.get("device", "cpu"),
            service_url=embedder_cfg.get("service_url"),
        )

        threshold = self._config.get("report", {}).get("similarity_threshold", 0.5)
        top_n = self._config.get("report", {}).get("top_n", 20)

        query_vector = embedder.embed_query(prompt)
        hits = vector.search(query_vector, top_k=50, threshold=threshold)

        hits.sort(
            key=lambda h: h["payload"].get("rank_score", 0) * h["similarity"],
            reverse=True,
        )
        hits = hits[:top_n]

        if not hits:
            return "_No relevant signals found. Try lowering the similarity threshold or collecting more data._"

        signals_text = "\n\n".join(
            f"[{i+1}] {h['payload'].get('title', '')}\n"
            f"URL: {h['payload'].get('url', '')}\n"
            f"Rule: {h['payload'].get('rule', '-')} | "
            f"Intensity: {h['payload'].get('intensity', '-')} | "
            f"Similarity: {h['similarity']:.2f} | "
            f"Score: {h['payload'].get('rank_score', 0):.3f}"
            for i, h in enumerate(hits)
        )

        report_cfg = self._config.get("report", {})
        language = report_cfg.get("language", "ru")
        include_evidence = report_cfg.get("include_evidence", True)

        system = f"""You are a market intelligence analyst.
Answer the user's question based ONLY on the provided signals.
- Write in {'Russian' if language == 'ru' else language}
- Every claim MUST be backed by a URL from the provided signals. Do not invent URLs.
- {'Include brief evidence (quote or reason) for each point.' if include_evidence else ''}
- Be concise and specific. No filler text."""

        user = f"""User question: {prompt}

Signals ({len(hits)} most relevant):
{signals_text}

Provide a structured answer. Include the URL for every claim."""

        call = LLMCall(
            operation="query",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        answer = router.complete(call)

        # Anti-hallucination gate: verify all URLs are from our signals
        valid_urls = {h["payload"].get("url", "") for h in hits}
        answer = self._validate_urls(answer, valid_urls)

        return answer

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_rules(self) -> list[ExtractionRule]:
        raw_rules = self._config.get("extraction_rules", [])
        return [
            ExtractionRule(
                name=r["name"],
                description=r.get("description", ""),
                examples=r.get("examples", []),
                negative_examples=r.get("negative_examples", []),
                priority=r.get("priority", 1),
            )
            for r in raw_rules
        ]

    @staticmethod
    @staticmethod
    def _deserialize_profile(profile_data: dict[str, Any]) -> KeywordProfile:
        """Reconstruct KeywordProfile from stored JSON (no discovered resources needed)."""
        return KeywordProfile(
            raw=profile_data.get("raw", ""),
            canonical_name=profile_data.get("canonical_name", ""),
            keyword_type=profile_data.get("keyword_type", "topic"),
            description=profile_data.get("description", ""),
            aliases=profile_data.get("aliases", []),
            related_terms=profile_data.get("related_terms", []),
            pain_patterns=profile_data.get("pain_patterns", []),
            search_queries=profile_data.get("search_queries", {}),
            relevant_subreddits=profile_data.get("relevant_subreddits", []),
        )

    @staticmethod
    def _deserialize_plan(plan_data: dict[str, Any]) -> SearchPlan:
        targets = [
            SearchTarget(
                query=t["query"],
                scope=t["scope"],
                params=t.get("params", {}),
            )
            for t in plan_data.get("targets", [])
        ]
        return SearchPlan(
            targets=targets,
            max_results_per_target=plan_data.get("max_results_per_target", 200),
        )

    def reprocess(
        self, keyword: str, rule_names: list[str] | None, router
    ) -> dict[str, Any]:
        """
        Delete ProcessedSignal + Qdrant vectors for keyword (optionally filtered by rules),
        re-queue as unprocessed, then run process.
        Idempotent: safe to run multiple times.
        """
        from storage.vector import VectorStorage  # noqa: PLC0415

        rows = self._storage.get_raw_signal_ids_for_keyword(keyword, rule_names)
        if not rows:
            _emit({"status": "done", "phase": "reprocess", "total": 0,
                   "note": f"No signals found for keyword='{keyword}'"})
            return {"total": 0}

        raw_ids = [str(r["raw_signal_id"]) for r in rows]
        _emit({
            "status": "running", "phase": "reprocess",
            "keyword": keyword, "deleting": len(raw_ids),
        })

        # Remove from Qdrant first
        vector = VectorStorage()
        from core.embedder import Embedder  # noqa: PLC0415
        int_ids = [
            Embedder._to_int_id(rid) for rid in raw_ids
        ]
        try:
            vector.delete_by_ids(int_ids)
        except Exception as e:
            log.warning("[orchestrator] reprocess: qdrant delete failed: %s", e)

        # Delete from Postgres (cascades to embedding_queue)
        deleted = self._storage.delete_processed_signals(raw_ids)
        _emit({"status": "running", "phase": "reprocess", "deleted": deleted})

        # Re-classify
        total = self.process(router)
        _emit({"status": "done", "phase": "reprocess", "deleted": deleted, "reprocessed": total.get("total", 0)})
        return {"deleted": deleted, "reprocessed": total.get("total", 0)}

    def generate_change_report(self, keyword: str, router) -> str:
        """
        Delta report: compare current state with last snapshot.
        Saves new snapshot to change_report_snapshots.
        Returns formatted report text.
        """
        from core.llm_router import LLMCall  # noqa: PLC0415
        from datetime import timezone  # noqa: PLC0415

        report_cfg = self._config.get("change_report", {})
        top_n_new = report_cfg.get("top_n_new", 10)
        instructions = report_cfg.get("instructions", "")
        approved_template = report_cfg.get("approved_template")

        now = datetime.now(timezone.utc)
        since = self._storage.get_last_report_at(keyword)
        if not since:
            since = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        new_signals = self._storage.fetch_new_signals_since(keyword, since, limit=top_n_new * 5)
        current_counts = self._storage.count_signals_by_rule(keyword, since=since)
        prev_year = since.year if since.month > 1 else since.year - 1
        prev_month = since.month - 1 if since.month > 1 else 12
        prev_counts = self._storage.count_signals_by_rule(
            keyword,
            since=datetime(prev_year, prev_month, 1, tzinfo=timezone.utc),
        )

        signals_text = "\n".join(
            f"- [{s.get('source', '')}] {s.get('title', '')} | "
            f"intensity:{s.get('intensity')} score:{s.get('rank_score', 0):.3f} | "
            f"{s.get('url', '')}"
            for s in new_signals[:top_n_new]
        )

        delta_text = "\n".join(
            f"- {rule}: {current_counts.get(rule, 0)} (prev: {prev_counts.get(rule, 0)}, "
            f"delta: {current_counts.get(rule, 0) - prev_counts.get(rule, 0):+d})"
            for rule in set(list(current_counts.keys()) + list(prev_counts.keys()))
        )

        few_shot = f"\n\nUse this approved format as a template:\n{approved_template}" if approved_template else ""
        prompt_instructions = instructions or "Top new signals, what grew vs last period, 1-2 sentence conclusion."

        call = LLMCall(
            operation="query",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a market intelligence analyst. Generate a change report.{few_shot}",
                },
                {
                    "role": "user",
                    "content": f"""Keyword: {keyword}
Period: {since.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}

New signals ({len(new_signals)} total, top {top_n_new} shown):
{signals_text or 'No new signals.'}

Signal counts by rule (current period):
{delta_text or 'No data.'}

Format instructions: {prompt_instructions}
""",
                },
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        report_text = router.complete(call)

        self._storage.save_change_report_snapshot(
            keyword=keyword,
            period_start=since,
            period_end=now,
            report_text=report_text,
            signal_count=len(new_signals),
        )
        return report_text

    def preview_change_report(self, keyword: str, instructions: str, router) -> str:
        """
        Generate an example report using provided instructions on real recent data.
        Does NOT save a snapshot - this is for preview/approval only.
        """
        from core.llm_router import LLMCall  # noqa: PLC0415
        from datetime import timezone, timedelta  # noqa: PLC0415

        now = datetime.now(timezone.utc)
        since = now - timedelta(days=7)
        top_n = self._config.get("change_report", {}).get("top_n_new", 10)

        new_signals = self._storage.fetch_new_signals_since(keyword, since, limit=top_n * 3)
        current_counts = self._storage.count_signals_by_rule(keyword, since=since)

        signals_text = "\n".join(
            f"- [{s.get('source', '')}] {s.get('title', '')} | "
            f"intensity:{s.get('intensity')} score:{s.get('rank_score', 0):.3f} | "
            f"{s.get('url', '')} | {s.get('created_at', '')}"
            for s in new_signals[:top_n]
        )

        call = LLMCall(
            operation="query",
            messages=[
                {"role": "system", "content": "You are a market intelligence analyst generating a sample report for user approval."},
                {
                    "role": "user",
                    "content": f"""Generate a PREVIEW report for keyword "{keyword}" using last 7 days of data.

Signals ({len(new_signals)} available, top {top_n} shown):
{signals_text or 'No signals available.'}

Signal counts by rule: {current_counts}

FORMAT INSTRUCTIONS FROM USER:
{instructions}

Generate the report exactly as the user wants to see it. This is a preview for their approval.
""",
                },
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        return router.complete(call)

    @staticmethod
    def _validate_urls(answer: str, valid_urls: set[str]) -> str:
        """
        Anti-hallucination gate: strip sentences containing URLs not in valid_urls.
        Returns cleaned answer with a note if URLs were removed.
        """
        import re  # noqa: PLC0415

        found_urls = re.findall(r'https?://\S+', answer)
        invalid = [u for u in found_urls if u.rstrip(".,)>") not in valid_urls]
        if not invalid:
            return answer

        log.warning("[orchestrator] removed %d hallucinated URLs from answer", len(invalid))
        cleaned = answer
        for url in invalid:
            cleaned = cleaned.replace(url, "[URL removed]")
        return cleaned + "\n\n_(Note: some URLs were removed as they could not be verified.)_"
