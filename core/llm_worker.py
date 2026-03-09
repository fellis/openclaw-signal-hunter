"""
LLM Task Queue Worker.
Processes LLM tasks from llm_task_queue sequentially, one at a time.
Called by cron (every minute) via skill command 'run_worker'.

Task types and priorities (lower = higher priority):
  - resolve:              50  - enrich keyword with LLM, create collection plan
  - borderline_relevance: 70  - LLM relevance decision for borderline signals (hybrid mode)
  - summarize_batch:      90  - generate summaries for classified signals via LLM

Embedding classification (no LLM) runs via a SEPARATE embed worker cron
(cmd_run_embed_worker). Collection runs via cmd_run_collect_worker.

Worker guarantees:
  - Only one LLM task runs at a time (has_running_llm_task check)
  - Retries failed tasks up to 3 times before marking as 'failed'
  - Resets tasks stuck in 'running' for > 2 minutes (budget is 50s, so 2 min = safe margin)
  - Loops within one cron tick until time budget is exhausted
"""

from __future__ import annotations

import json
import logging

import json_repair
import time
from typing import Any

from core.embed_processor import EmbedProcessor
from core.models import ProcessedSignal
from storage.postgres import PostgresStorage

log = logging.getLogger(__name__)

_MAX_STUCK_MINUTES = 1  # recover faster after container/process restart
_DEFAULT_BUDGET_SECONDS = 50

_LLM_SYSTEM_V6 = (
    "You are a market intelligence classifier for the AI/ML ecosystem.\n"
    "Your job: decide if a signal provides actionable market intelligence "
    "about AI/ML tools, models, frameworks, or infrastructure.\n"
    "You receive: source type, project name (if available), title, body.\n\n"
    "A signal is RELEVANT if it reveals any of these about the AI/ML market:\n"
    "- User pain points with AI/ML tools (crashes, performance, compatibility)\n"
    "- Feature requests for AI/ML products\n"
    "- Comparisons between AI/ML tools\n"
    "- New AI/ML products, releases, or announcements\n"
    "- Adoption of AI/ML in products or workflows\n"
    "- Bugs or issues that affect users of AI/ML tools\n"
    "- Discussions about AI/ML architecture, scaling, deployment\n"
    "- AI security or safety concerns with AI products\n"
    "- Market observations about AI/ML trends\n\n"
    "Key: if the project IS an AI/ML tool (LLM framework, model serving, "
    "AI agent platform, ML pipeline, vector DB, AI coding assistant, etc.), "
    "then bugs, feature requests and discussions in it ARE market signals - "
    "they show what users struggle with, what they need.\n\n"
    "NOT relevant:\n"
    "- Signals from non-AI projects with no AI/ML angle\n"
    "- Generic content (politics, gaming, cooking, sports, e-commerce)\n"
    "- Pure CI/infrastructure issues in non-AI projects\n\n"
    'Reply ONLY: {"relevant": true/false, "reason": "one sentence"}'
)

_BATCH_INSTRUCTION = (
    "\n\nFor this request you receive MULTIPLE signals numbered [0], [1], ... "
    "Reply with a JSON array of exactly that many objects in the same order, "
    'e.g. [{"relevant": true, "reason": "..."}, {"relevant": false, "reason": "..."}]. '
    "No other text, only the JSON array."
)

_LLM_PROMPTS = {
    "v6": _LLM_SYSTEM_V6,
}


def _extract_project(dedup_key: str) -> str:
    """Extract project/repo name from dedup_key."""
    if dedup_key.startswith("github_issue:") or dedup_key.startswith("github_discussion:"):
        prefix = dedup_key.split(":", 1)[1]
        return prefix.rsplit("#", 1)[0]
    return ""


def _is_transient_network_error(exc: BaseException) -> bool:
    """True if the error is transient (DNS, connection reset) and task should be re-queued without consuming retry."""
    msg = str(exc).lower()
    if any(
        phrase in msg
        for phrase in (
            "name resolution",
            "connection reset",
            "server disconnected",
            "connection refused",
            "connection reset by peer",
        )
    ):
        return True
    if type(exc).__name__ == "gaierror":
        return True
    return False


class LLMWorker:
    """
    Sequential LLM queue processor with time-budget loop.
    Instantiated and called once per cron tick via cmd_run_worker.
    """

    def __init__(self, config: dict[str, Any], storage: PostgresStorage) -> None:
        self._config = config
        self._storage = storage
        self._router = None  # reuse one router (and its HTTP connections) to avoid per-tick DNS resolution

    def run_loop(self, budget_seconds: int = _DEFAULT_BUDGET_SECONDS) -> dict[str, Any]:
        """Process tasks in a loop until queue is empty or time budget is exhausted."""
        deadline = time.monotonic() + budget_seconds
        processed = []
        errors = []

        reset = self._storage.reset_stuck_llm_tasks(_MAX_STUCK_MINUTES)
        if reset:
            log.warning("[llm_worker] reset %d stuck task(s)", reset)

        while time.monotonic() < deadline:
            if self._storage.has_running_llm_task():
                # Reset tasks stuck in 'running' for > 1 min (e.g. previous tick died mid-call)
                reset = self._storage.reset_stuck_llm_tasks(timeout_minutes=1)
                if reset:
                    log.warning("[llm_worker] reset %d stuck task(s) (were running > 1 min)", reset)
                if self._storage.has_running_llm_task():
                    log.info(
                        "[llm_worker] task still running (another tick may be processing); skipping this tick"
                    )
                    break

            if (
                not self._storage.has_pending_task_of_type("summarize_batch")
                and self._storage.count_unsummarized() > 0
            ):
                self._storage.enqueue_llm_task(
                    task_type="summarize_batch",
                    priority=90,
                    payload={},
                )

            task = self._storage.claim_next_llm_task()
            if not task:
                break

            task_id = task["id"]
            task_type = task["task_type"]
            payload = task["payload"]

            if task_type == "borderline_relevance":
                hybrid_cfg = self._config.get("hybrid_relevance", {})
                batch_size = int(hybrid_cfg.get("llm_batch_size", 1))
                if batch_size > 1:
                    extra = self._storage.claim_pending_llm_tasks("borderline_relevance", batch_size - 1)
                    tasks_batch = [task] + extra
                else:
                    tasks_batch = [task]
            else:
                tasks_batch = [task]

            log.info(
                "[llm_worker] starting task_id=%s type=%s batch=%d payload=%s",
                task_id, task_type, len(tasks_batch), payload if len(tasks_batch) == 1 else {"batch": len(tasks_batch)},
            )

            try:
                if task_type == "resolve":
                    result = self._handle_resolve(payload)
                    self._storage.complete_llm_task(task_id)
                    log.info("[llm_worker] done task_id=%s type=%s", task_id, task_type)
                    processed.append({"task_type": task_type, **result})
                elif task_type == "borderline_relevance":
                    if len(tasks_batch) == 1:
                        result = self._handle_borderline_relevance(payload)
                        self._storage.complete_llm_task(task_id)
                        log.info("[llm_worker] done task_id=%s type=%s", task_id, task_type)
                        processed.append({"task_type": task_type, **result})
                    else:
                        results = self._handle_borderline_relevance_batch(tasks_batch)
                        for t, r in zip(tasks_batch, results):
                            if r.get("error"):
                                self._storage.fail_llm_task(t["id"], r["error"])
                                errors.append({"task_type": task_type, "error": r["error"]})
                            else:
                                self._storage.complete_llm_task(t["id"])
                                processed.append({"task_type": task_type, **r})
                        log.info("[llm_worker] done batch type=%s count=%d", task_type, len(tasks_batch))
                elif task_type == "summarize_batch":
                    result = self._handle_summarize_batch()
                    self._storage.complete_llm_task(task_id)
                    log.info("[llm_worker] done task_id=%s type=%s", task_id, task_type)
                    processed.append({"task_type": task_type, **result})
                else:
                    raise ValueError(f"Unknown task_type: {task_type!r}")

            except Exception as e:
                log.exception("[llm_worker] failed task_id=%s type=%s: %s", task_id, task_type, e)
                err_str = str(e)
                is_transient = _is_transient_network_error(e)
                for t in tasks_batch:
                    if is_transient:
                        self._storage.reset_llm_task_to_pending(t["id"])
                        log.warning("[llm_worker] transient error, task %s reset to pending (retry not consumed)", t["id"])
                    else:
                        self._storage.fail_llm_task(t["id"], err_str)
                    errors.append({"task_type": t["task_type"], "error": err_str})
                # Exit this tick so we don't re-claim the same batch and loop. Next tick (60s) will retry.
                if is_transient:
                    break

        remaining = self._storage.get_llm_queue_status()
        pending_count = sum(1 for t in remaining if t["status"] == "pending")

        if not processed and not errors:
            return {"status": "idle", "note": "LLM queue is empty."}

        return {
            "status": "done",
            "processed": len(processed),
            "errors": len(errors),
            "pending_remaining": pending_count,
            "tasks": processed,
        }

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    def _build_llm_relevant_processed_signal(self, raw: dict[str, Any]) -> ProcessedSignal:
        """Build minimal ProcessedSignal for LLM-relevant borderline; embed worker fills matched_rules later."""
        raw_extra = raw.get("extra") or {}
        if isinstance(raw_extra, str):
            try:
                raw_extra = json.loads(raw_extra)
            except (ValueError, TypeError):
                raw_extra = {}
        keywords_matched = raw_extra.get("keywords") or []
        rank_score = EmbedProcessor._compute_rank_score(
            intensity=1,
            confidence=1.0,
            score=raw.get("score", 0) or 0,
            created_at=raw.get("created_at"),
            comments_count=raw.get("comments_count", 0) or 0,
        )
        return ProcessedSignal(
            raw_signal_id=str(raw["id"]),
            dedup_key=raw["dedup_key"],
            is_relevant=True,
            matched_rules=[],
            summary=None,
            products_mentioned=[],
            intensity=1,
            confidence=1.0,
            keywords_matched=keywords_matched,
            language="en",
            rank_score=rank_score,
            borderline_override_pending=False,
            classification_source="llm",
        )

    def _handle_resolve(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve and auto-approve a keyword from the queue."""
        from core.registry import load_all_collectors
        from core.resolver import KeywordResolver

        keyword = payload.get("keyword", "")
        if not keyword:
            raise ValueError("resolve task missing 'keyword' in payload")

        load_all_collectors()
        router = self._make_router()
        resolver = KeywordResolver(router, self._storage)
        result = resolver.resolve(keyword)

        canonical = result.get("canonical_name", keyword)
        plans = result.get("proposed_plan", {})

        if plans:
            resolver.approve_plan(canonical, plans)
            log.info("[llm_worker] resolved and approved '%s' -> '%s'", keyword, canonical)
        else:
            log.warning("[llm_worker] no plan generated for '%s'", keyword)

        return {
            "keyword": keyword,
            "canonical_name": canonical,
            "auto_approved": bool(plans),
            "sources": list(plans.keys()) if plans else [],
        }

    def _handle_borderline_relevance(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Classify a borderline signal using LLM (v6 prompt).
        If relevant: upsert minimal ProcessedSignal (matched_rules=[]); embed worker fills rules later.
        If not relevant: clear borderline_override_pending flag.
        """
        from core.llm_router import LLMCall, LLMRouter
        from storage.text_cleaner import strip_hn_prefix

        dedup_key = payload.get("dedup_key", "")
        if not dedup_key:
            raise ValueError("borderline_relevance task missing 'dedup_key' in payload")

        raw = self._storage.fetch_raw_signal_by_dedup_key(dedup_key)
        if not raw:
            log.warning("[llm_worker] borderline signal not found: %s", dedup_key)
            return {"dedup_key": dedup_key, "skipped": True}

        hybrid_cfg = self._config.get("hybrid_relevance", {})
        prompt_version = hybrid_cfg.get("llm_prompt_version", "v6")
        body_chars = int(hybrid_cfg.get("llm_body_chars", 600))
        max_tokens = int(hybrid_cfg.get("llm_max_tokens", 150))
        temperature = float(hybrid_cfg.get("llm_temperature", 0.0))

        system_prompt = _LLM_PROMPTS.get(prompt_version, _LLM_SYSTEM_V6)

        title = strip_hn_prefix(raw.get("title") or "")
        body = (raw.get("body") or "")[:body_chars]
        source = raw.get("source") or ""
        project = _extract_project(dedup_key)

        parts = [f"Source: {source}"]
        if project:
            parts.append(f"Project: {project}")
        parts.append(f"Title: {title}")
        if body:
            parts.append(f"Body: {body}")
        user_msg = "\n".join(parts)

        router = self._make_router()
        call = LLMCall(
            operation="borderline_relevance",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        raw_response = router.complete(call).strip()

        try:
            parsed = json_repair.loads(raw_response)
            is_relevant = bool(parsed.get("relevant", False)) if isinstance(parsed, dict) else False
        except Exception:
            log.warning("[llm_worker] borderline: could not parse LLM response for %s: %r", dedup_key, raw_response[:200])
            is_relevant = False

        if is_relevant:
            ps = self._build_llm_relevant_processed_signal(raw)
            self._storage.upsert_processed_signal(ps)
            log.info("[llm_worker] borderline RELEVANT: %s", dedup_key)
            return {"dedup_key": dedup_key, "relevant": True, "matched_rules": []}
        else:
            self._storage.clear_borderline_pending(dedup_key)
            log.info("[llm_worker] borderline NOT relevant: %s", dedup_key)
            return {"dedup_key": dedup_key, "relevant": False}

    def _handle_borderline_relevance_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Classify up to N borderline signals in one LLM call. Returns one result dict per task
        (either {dedup_key, relevant, ...} or {error: str}). Caller completes/fails each task.
        """
        from core.llm_router import LLMCall, LLMRouter
        from storage.text_cleaner import strip_hn_prefix

        hybrid_cfg = self._config.get("hybrid_relevance", {})
        prompt_version = hybrid_cfg.get("llm_prompt_version", "v6")
        body_chars = int(hybrid_cfg.get("llm_body_chars", 600))
        max_tokens = int(hybrid_cfg.get("llm_max_tokens", 150))
        temperature = float(hybrid_cfg.get("llm_temperature", 0.0))

        system_prompt = _LLM_PROMPTS.get(prompt_version, _LLM_SYSTEM_V6) + _BATCH_INSTRUCTION

        rows: list[dict[str, Any]] = []
        for t in tasks:
            dedup_key = (t.get("payload") or {}).get("dedup_key", "")
            if not dedup_key:
                rows.append({"error": "missing dedup_key"})
                continue
            raw = self._storage.fetch_raw_signal_by_dedup_key(dedup_key)
            if not raw:
                log.warning("[llm_worker] borderline batch: signal not found %s", dedup_key)
                rows.append({"error": "signal not found"})
                continue
            rows.append({"task": t, "dedup_key": dedup_key, "raw": raw})

        if not rows or all("error" in r for r in rows):
            return [r if "error" in r else {"error": "no valid signals"} for r in rows]

        valid = [r for r in rows if "error" not in r]
        blocks = []
        for i, r in enumerate(valid):
            raw = r["raw"]
            dedup_key = r["dedup_key"]
            title = strip_hn_prefix(raw.get("title") or "")
            body = (raw.get("body") or "")[:body_chars]
            source = raw.get("source") or ""
            project = _extract_project(dedup_key)
            parts = [f"[{i}]", f"Source: {source}"]
            if project:
                parts.append(f"Project: {project}")
            parts.append(f"Title: {title}")
            if body:
                parts.append(f"Body: {body}")
            blocks.append("\n".join(parts))
        user_msg = "\n\n".join(blocks)

        n = len(valid)
        router = self._make_router()
        call = LLMCall(
            operation="borderline_relevance",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=max(512, n * 120),
            temperature=temperature,
        )
        t0 = time.monotonic()
        log.info("[llm_worker] LLM batch request START (shared backend) n=%d", n)
        raw_response = router.complete(call).strip()
        log.info("[llm_worker] LLM batch request DONE in %.1fs", time.monotonic() - t0)
        if raw_response.startswith("```"):
            raw_response = raw_response.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            parsed_list = json_repair.loads(raw_response)
            if not isinstance(parsed_list, list):
                parsed_list = []
        except Exception:
            log.warning("[llm_worker] borderline batch: could not parse LLM response: %r", raw_response[:200])
            return [{"error": "batch parse failed"} for _ in tasks]

        results_by_index: list[dict[str, Any]] = []
        for i, r in enumerate(valid):
            item = parsed_list[i] if i < len(parsed_list) else None
            if not item or not isinstance(item, dict):
                results_by_index.append({"error": "batch response missing item"})
                continue
            is_relevant = bool(item.get("relevant", False))
            dedup_key = r["dedup_key"]
            raw = r["raw"]
            if is_relevant:
                ps = self._build_llm_relevant_processed_signal(raw)
                self._storage.upsert_processed_signal(ps)
                log.info("[llm_worker] borderline RELEVANT: %s", dedup_key)
                results_by_index.append({"dedup_key": dedup_key, "relevant": True, "matched_rules": []})
            else:
                self._storage.clear_borderline_pending(dedup_key)
                log.info("[llm_worker] borderline NOT relevant: %s", dedup_key)
                results_by_index.append({"dedup_key": dedup_key, "relevant": False})

        out: list[dict[str, Any]] = []
        idx = 0
        for r in rows:
            if "error" in r:
                out.append(r)
            else:
                out.append(results_by_index[idx])
                idx += 1
        return out

    def _handle_summarize_batch(self) -> dict[str, Any]:
        """Generate summaries for relevant signals that were classified without one."""
        from core.llm_router import LLMCall, LLMRouter

        proc_cfg = self._config.get("processor", {})
        batch_size = int(proc_cfg.get("summary_batch_size", 5))
        fetch_limit = int(proc_cfg.get("summary_fetch_limit", 50))

        records = self._storage.fetch_unsummarized(limit=fetch_limit)
        if not records:
            return {"summarized": 0, "remaining": 0}

        router = self._make_router()

        _SYSTEM = (
            "You are a concise technical analyst. "
            "Return ONLY the JSON array, no markdown, no labels."
        )

        summarized = 0
        for i in range(0, len(records), batch_size):
            chunk = records[i : i + batch_size]
            texts = [r["text"][:600] for r in chunk]
            items = "\n\n".join(f"[{j}]\n{t}" for j, t in enumerate(texts))
            prompt = (
                f"Write a 1-2 sentence summary in English for each of the following "
                f"{len(texts)} texts. "
                f"Return a JSON array of strings in the same order, "
                f'e.g. ["summary0", "summary1"].\n\n{items}\n\nReturn ONLY the JSON array.'
            )
            max_tokens = max(512, len(texts) * 150)
            call = LLMCall(
                operation="summarize_batch",
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.0,
            )
            try:
                raw = router.complete(call).strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                summaries = json_repair.loads(raw)
            except Exception:
                log.warning("[llm_worker] summary parse failed for batch %d, skipping", i)
                continue

            if not isinstance(summaries, list):
                continue

            for rec, summary in zip(chunk, summaries):
                if summary and isinstance(summary, str):
                    self._storage.update_summary(rec["raw_signal_id"], rec["dedup_key"], summary)
                    summarized += 1

        remaining = self._storage.count_unsummarized()
        log.info("[llm_worker] summarized=%d remaining=%d", summarized, remaining)
        return {"summarized": summarized, "remaining": remaining}

    def _make_router(self):
        """Reuse one router per worker instance so HTTP connections (and DNS resolution) are reused."""
        if self._router is None:
            from core.llm_router import LLMRouter
            self._router = LLMRouter(self._config, usage_logger=self._storage.log_llm_usage)
        return self._router
