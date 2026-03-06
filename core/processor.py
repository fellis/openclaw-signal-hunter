"""
LLM Processor.
Classifies raw signals using the local LLM (token-aware batching validated in spike Phase 1).
Writes ProcessedSignal to Postgres + adds to embedding_queue (Outbox pattern).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

from core.llm_router import LLMCall, LLMRouter
from core.models import ExtractionRule, MatchedRule, ProcessedSignal
from storage.postgres import PostgresStorage

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a signal classifier for a market intelligence system.
You analyze content (GitHub issues, Reddit posts, forum threads) and classify them
according to provided rules. Always respond with valid JSON only, no extra text."""


class Processor:
    """
    Classifies unprocessed raw signals via LLM.
    Uses token-aware batching (validated: 20K tokens ~ 12sec on Devstral 24B).
    temperature=0.0 for deterministic classification.
    """

    def __init__(
        self,
        router: LLMRouter,
        storage: PostgresStorage,
        rules: list[ExtractionRule],
        config: dict[str, Any],
    ) -> None:
        self._router = router
        self._storage = storage
        self._rules = rules
        self._max_tokens_per_batch = config.get("processor", {}).get("max_tokens_per_batch", 10_000)
        self._max_signals_per_batch = config.get("processor", {}).get("max_signals_per_batch", 10)
        self._db_fetch_size = config.get("processor", {}).get("batch_size", 200)
        self._max_body_chars = config.get("processor", {}).get("max_body_chars", 1000)
        self._count_tokens = router.get_tokenizer()

    def process_all(self, max_batches: int | None = None) -> int:
        """
        Process unclassified signals with an exclusive lock to prevent parallel cron runs.
        max_batches: if set, stop after processing this many LLM batches.
                     Useful for cron-based drip processing (e.g. 1 batch per run).
                     None means process everything available.
        Returns count of processed signals. Returns -1 if already running (lock held).
        """
        import fcntl  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        lock_path = Path(__file__).parent.parent / ".processor.lock"
        lock_file = open(lock_path, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("[processor] another instance is already running, skipping this run")
            lock_file.close()
            return -1

        try:
            return self._run_batches(max_batches)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
            lock_path.unlink(missing_ok=True)

    def _run_batches(self, max_batches: int | None = None) -> int:
        """Internal: actual batch processing loop (called with lock held)."""
        total = 0
        batches_done = 0

        while True:
            if max_batches is not None and batches_done >= max_batches:
                log.info("[processor] reached max_batches=%d, stopping", max_batches)
                break

            signals = self._storage.fetch_unprocessed(limit=self._db_fetch_size)
            if not signals:
                break

            log.info("[processor] fetched %d unprocessed signals", len(signals))
            token_batches = self._build_token_aware_batches(signals)

            for i, token_batch in enumerate(token_batches):
                if max_batches is not None and batches_done >= max_batches:
                    break
                log.info(
                    "[processor] batch %d (%d signals)",
                    batches_done + 1, len(token_batch),
                )
                total += self._process_with_retry(token_batch)
                batches_done += 1

        log.info("[processor] done. batches=%d processed=%d", batches_done, total)
        return total

    def _process_with_retry(self, batch: list[dict[str, Any]], depth: int = 0) -> int:
        """
        Classify a batch. On failure, split in half and retry each chunk.
        Stops splitting when batch reaches a single signal (depth limit).
        Returns count of successfully classified signals.
        Results are matched to raw signals by position (idx), not by id.
        """
        if not batch:
            return 0
        try:
            results = self._classify_batch(batch)
            saved = 0
            for result in results:
                idx = result.get("idx")
                if idx is None or not isinstance(idx, int) or idx >= len(batch):
                    log.warning("[processor] result has invalid idx=%s (batch size=%d), skipping", idx, len(batch))
                    continue
                ps = self._build_processed_signal(result, batch[idx])
                if ps:
                    self._storage.upsert_processed_signal(ps)
                    saved += 1
            return saved
        except Exception as e:
            if len(batch) == 1 or depth >= 3:
                log.error("[processor] signal %s failed permanently: %s", batch[0].get("id"), e)
                return 0
            log.warning(
                "[processor] batch of %d failed (%s), splitting in half (depth=%d)",
                len(batch), type(e).__name__, depth,
            )
            mid = len(batch) // 2
            left = self._process_with_retry(batch[:mid], depth + 1)
            right = self._process_with_retry(batch[mid:], depth + 1)
            return left + right

    # ------------------------------------------------------------------
    # Private: batching
    # ------------------------------------------------------------------

    def _build_token_aware_batches(
        self, signals: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """
        Pack signals into batches respecting both limits:
        - max_tokens_per_batch: input token budget (prevents overly large prompts)
        - max_signals_per_batch: explicit signal count cap (controls output size and
          LLM response time - default 10 to stay under nginx proxy_read_timeout=60s)
        """
        batches: list[list[dict]] = []
        current: list[dict] = []
        current_tokens = 0

        for signal in signals:
            title = signal.get("title") or ""
            body = signal.get("body") or ""
            # Truncate body to prevent outlier long signals from blowing up the batch
            if len(body) > self._max_body_chars:
                body = body[:self._max_body_chars] + "..."
            full_text = f"{title}\n\n{body}".strip() if title else body
            tokens = self._count_tokens(full_text)

            over_token_budget = current and current_tokens + tokens > self._max_tokens_per_batch
            over_signal_cap = len(current) >= self._max_signals_per_batch

            if over_token_budget or over_signal_cap:
                batches.append(current)
                log.debug(
                    "[processor] batch closed: %d signals, %d tokens (cap=%d)",
                    len(current), current_tokens, self._max_signals_per_batch,
                )
                current = []
                current_tokens = 0

            current.append({**signal, "_body": full_text})
            current_tokens += tokens

        if current:
            batches.append(current)
            log.debug(
                "[processor] batch closed: %d signals, %d tokens (cap=%d)",
                len(current), current_tokens, self._max_signals_per_batch,
            )

        return batches

    # ------------------------------------------------------------------
    # Private: LLM call
    # ------------------------------------------------------------------

    def _classify_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Send batch to LLM, return parsed JSON list.
        Output is matched by position (index), not by id, to avoid UUID corruption issues.
        max_tokens scales with batch size: ~200 output tokens per signal.
        """
        prompt = self._build_prompt(batch)
        # ~200 output tokens per signal result, minimum 1024
        max_tokens = max(1024, len(batch) * 200)
        call = LLMCall(
            operation="process",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        log.debug("[processor] classify_batch: %d signals, max_tokens=%d", len(batch), max_tokens)
        raw = self._router.complete(call)

        # Strip markdown fences if LLM wraps response
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        return json.loads(raw)

    def _build_prompt(self, batch: list[dict[str, Any]]) -> str:
        rules_json = json.dumps(
            [
                {
                    "id": r.name,
                    "description": r.description,
                    "examples": r.examples,
                }
                for r in self._rules
            ],
            ensure_ascii=False,
            indent=2,
        )
        # Use sequential index instead of UUID - avoids LLM UUID corruption.
        # Matching in _build_processed_signal is done by position, not by id.
        signals_json = json.dumps(
            [{"idx": i, "text": s["_body"]} for i, s in enumerate(batch)],
            ensure_ascii=False,
            indent=2,
        )
        return f"""Classification rules:
{rules_json}

Signals to classify (array of objects with "idx" index and "text"):
{signals_json}

For each signal return a JSON array in the SAME ORDER, one object per signal:
{{
  "idx": <same idx from input>,
  "is_relevant": true/false,
  "irrelevant_reason": "<short reason if not relevant, else null>",
  "language": "<ISO 639-1 language code of the original text>",
  "intensity": <1-5, how strongly expressed>,
  "confidence": <0.0-1.0>,
  "matched_rules": ["<rule_id>", ...],
  "products_mentioned": ["<product name>", ...],
  "summary": "<1-2 sentence summary of the signal in English>"
}}

Return ONLY the JSON array, no markdown, no explanation.
"""

    # ------------------------------------------------------------------
    # Private: building ProcessedSignal
    # ------------------------------------------------------------------

    def _build_processed_signal(
        self,
        result: dict[str, Any],
        raw: dict[str, Any],
    ) -> ProcessedSignal | None:
        """Build a ProcessedSignal from LLM result and the raw signal row.
        raw is passed directly (matched by idx position, not by id).
        """

        is_relevant = bool(result.get("is_relevant", False))
        matched_rule_ids: list[str] = result.get("matched_rules", [])
        intensity = max(1, min(5, int(result.get("intensity") or 1)))
        confidence = float(result.get("confidence") or 0.5)

        matched_rules = [
            MatchedRule(
                rule_name=rule_id,
                confidence=confidence,
                evidence=result.get("summary") or "",
            )
            for rule_id in matched_rule_ids
        ]

        rank_score = self._compute_rank_score(
            intensity=intensity,
            confidence=confidence,
            score=raw.get("score", 0) or 0,
            comments_count=raw.get("comments_count", 0) or 0,
            created_at=raw.get("created_at"),
        )

        # keywords_matched: read from extra["keywords"] set during collection
        raw_extra = raw.get("extra") or {}
        if isinstance(raw_extra, str):
            try:
                raw_extra = json.loads(raw_extra)
            except (ValueError, TypeError):
                raw_extra = {}
        keywords_matched = raw_extra.get("keywords") or []

        return ProcessedSignal(
            raw_signal_id=str(raw["id"]),
            dedup_key=raw["dedup_key"],
            is_relevant=is_relevant,
            matched_rules=matched_rules,
            summary=result.get("summary"),
            products_mentioned=result.get("products_mentioned") or [],
            intensity=intensity,
            confidence=confidence,
            keywords_matched=keywords_matched,
            language=result.get("language") or "en",
            rank_score=rank_score,
        )

    @staticmethod
    def _compute_rank_score(
        intensity: int,
        confidence: float,
        score: int,
        created_at: datetime | None,
        comments_count: int = 0,
    ) -> float:
        """
        rank_score = (0.3 * log10(1 + score + 0.5*comments) + 0.7 * (intensity/5) * confidence)
                     * 0.5^(hours_ago / 168)

        comments_count gets half the weight of score: for sources where score is
        weak (e.g. GitHub reactions ~0-3), comments are the primary engagement signal.
        Weights: engagement=0.3, quality=0.7, half_life=7d.
        """
        now = datetime.now(timezone.utc)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        hours_ago = (now - created_at).total_seconds() / 3600.0 if created_at else 0

        engagement_raw = max(0, score) + 0.5 * max(0, comments_count)
        engagement = 0.3 * math.log10(1 + engagement_raw)
        quality = 0.7 * (intensity / 5.0) * confidence
        decay = 0.5 ** (hours_ago / 168.0)
        return round((engagement + quality) * decay, 4)
