"""
Embed Worker - embedding-based signal classification.
Runs independently from LLMWorker. Does NOT use LLM.

Called by cron (every minute) via skill command 'run_embed_worker'.

Responsibilities:
  - Fetch raw signals that have no processed_signals row
  - Classify them via domain pre-filter + cosine similarity (EmbedProcessor)
  - Borderline signals are enqueued for LLM Worker review
  - Save results; summary is generated async by LLMWorker.summarize_batch

Worker guarantees:
  - File lock inside EmbedProcessor prevents concurrent runs
  - Fast: single cron tick processes up to batch_size * max_batches_per_run signals
  - No LLM calls in the hot path (LLM only for borderline, handled separately)
"""

from __future__ import annotations

import logging
from typing import Any

from storage.postgres import PostgresStorage

log = logging.getLogger(__name__)


class EmbedWorker:
    """
    Embedding-based classification worker.
    Instantiated and called once per cron tick via cmd_run_embed_worker.
    """

    def __init__(self, config: dict[str, Any], storage: PostgresStorage) -> None:
        self._config = config
        self._storage = storage

    def run(self) -> dict[str, Any]:
        """
        Run embedding classification for pending raw signals.
        Returns summary dict with classified count and remaining unprocessed count.
        """
        from core.embed_processor import EmbedProcessor
        from core.orchestrator import load_rules

        unprocessed = self._storage.count_unprocessed()
        if unprocessed == 0:
            log.info("[embed_worker] no unprocessed signals, idle")
            return {"status": "idle", "note": "No unprocessed signals."}

        rules = load_rules(self._config)
        if not rules:
            log.warning("[embed_worker] no extraction rules defined, skipping")
            return {"status": "skipped", "note": "No extraction_rules defined."}

        processor = EmbedProcessor(self._storage, rules, self._config)
        max_batches = self._config.get("processor", {}).get("max_batches_per_run", 5)
        total = processor.process_all(max_batches=max_batches)
        if total == -1:
            log.warning("[embed_worker] another instance already running, skipping")
            return {
                "status": "skipped",
                "note": "Another processing run is already active.",
                "remaining": unprocessed,
            }

        # Second pass: rule-match for LLM-relevant signals that have empty matched_rules
        proc_cfg = self._config.get("processor", {})
        rule_match_limit = int(proc_cfg.get("llm_relevant_rule_match_per_tick", 50))
        pending_dedup_keys = self._storage.fetch_llm_relevant_pending_rule_match(limit=rule_match_limit)
        rule_matched = 0
        for dedup_key in pending_dedup_keys:
            raw = self._storage.fetch_raw_signal_by_dedup_key(dedup_key)
            if not raw:
                continue
            ps = processor.classify_single(raw)
            self._storage.update_processed_signal_rule_match(
                dedup_key, ps.matched_rules, ps.confidence, ps.intensity
            )
            rule_matched += 1
        if rule_matched:
            log.info("[embed_worker] LLM-relevant rule match: %d signals", rule_matched)

        remaining = self._storage.count_unprocessed()
        log.info("[embed_worker] done: classified=%d remaining=%d", total, remaining)
        return {
            "status": "done",
            "total": total,
            "remaining": remaining,
            "llm_relevant_rule_matched": rule_matched,
        }
