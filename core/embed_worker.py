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

        max_batches = self._config.get("processor", {}).get("max_batches_per_run", 5)

        rules = load_rules(self._config)
        if not rules:
            log.warning("[embed_worker] no extraction rules defined, skipping")
            return {"status": "skipped", "note": "No extraction_rules defined."}

        processor = EmbedProcessor(self._storage, rules, self._config)
        total = processor.process_all(max_batches=max_batches)

        if total == -1:
            log.warning("[embed_worker] another instance already running, skipping")
            return {
                "status": "skipped",
                "note": "Another processing run is already active.",
                "remaining": unprocessed,
            }

        remaining = self._storage.count_unprocessed()
        log.info("[embed_worker] done: classified=%d remaining=%d", total, remaining)
        return {"status": "done", "total": total, "remaining": remaining}
