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
        self._db_fetch_size = config.get("processor", {}).get("batch_size", 200)
        self._max_body_chars = config.get("processor", {}).get("max_body_chars", 1000)
        self._count_tokens = router.get_tokenizer()

    def process_all(self) -> int:
        """Process all unclassified signals. Returns count of processed."""
        total = 0
        while True:
            batch = self._storage.fetch_unprocessed(limit=self._db_fetch_size)
            if not batch:
                break

            log.info("[processor] fetched %d unprocessed signals", len(batch))
            batches = self._build_token_aware_batches(batch)

            for i, token_batch in enumerate(batches):
                log.info(
                    "[processor] batch %d/%d (%d signals)",
                    i + 1, len(batches), len(token_batch),
                )
                total += self._process_with_retry(token_batch)

        log.info("[processor] done. total processed: %d", total)
        return total

    def _process_with_retry(self, batch: list[dict[str, Any]], depth: int = 0) -> int:
        """
        Classify a batch. On failure, split in half and retry each chunk.
        Stops splitting when batch reaches a single signal (depth limit).
        Returns count of successfully classified signals.
        """
        if not batch:
            return 0
        try:
            results = self._classify_batch(batch)
            id_to_raw = {str(s["id"]): s for s in batch}
            for result in results:
                ps = self._build_processed_signal(result, id_to_raw)
                if ps:
                    self._storage.upsert_processed_signal(ps)
            return len(batch)
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
        """Pack signals into batches respecting the token budget."""
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

            if current and current_tokens + tokens > self._max_tokens_per_batch:
                batches.append(current)
                log.debug("[processor] batch closed: %d signals, %d tokens", len(current), current_tokens)
                current = []
                current_tokens = 0

            current.append({**signal, "_body": full_text})
            current_tokens += tokens

        if current:
            batches.append(current)
            log.debug("[processor] batch closed: %d signals, %d tokens", len(current), current_tokens)

        return batches

    # ------------------------------------------------------------------
    # Private: LLM call
    # ------------------------------------------------------------------

    def _classify_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Send batch to LLM, return parsed JSON list."""
        prompt = self._build_prompt(batch)
        call = LLMCall(
            operation="process",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
            temperature=0.0,
        )
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
        signals_json = json.dumps(
            [{"id": str(s["id"]), "text": s["_body"]} for s in batch],
            ensure_ascii=False,
            indent=2,
        )
        return f"""Classification rules:
{rules_json}

Signals to classify (array of objects with "id" and "text"):
{signals_json}

For each signal return a JSON array with objects:
{{
  "id": "<same id from input>",
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
        id_to_raw: dict[str, dict[str, Any]],
    ) -> ProcessedSignal | None:
        raw_id = result.get("id")
        raw = id_to_raw.get(str(raw_id))
        if not raw:
            log.warning("[processor] LLM returned unknown id: %s", raw_id)
            return None

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
    ) -> float:
        """
        rank_score = (0.3 * log10(1+score) + 0.7 * (intensity/5) * confidence)
                     * 0.5^(hours_ago / 168)

        Validated in spike Phase 1. Weights: engagement=0.3, quality=0.7, half_life=7d.
        """
        now = datetime.now(timezone.utc)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        hours_ago = (now - created_at).total_seconds() / 3600.0 if created_at else 0

        engagement = 0.3 * math.log10(1 + max(0, score))
        quality = 0.7 * (intensity / 5.0) * confidence
        decay = 0.5 ** (hours_ago / 168.0)
        return round((engagement + quality) * decay, 4)
