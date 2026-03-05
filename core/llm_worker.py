"""
LLM Task Queue Worker.
Processes one task at a time from llm_task_queue.
Called by cron via skill command 'run_worker'.

Task types:
  - resolve:        resolve and auto-approve a keyword (priority 50)
  - process_batch:  run one LLM classification batch (priority 90)

Worker guarantees:
  - Only one task runs at a time (has_running_llm_task check)
  - Auto-enqueues process_batch when unprocessed signals exist
  - Retries failed tasks up to 3 times before marking as 'failed'
  - Resets tasks stuck in 'running' for > 10 minutes
"""

from __future__ import annotations

import logging
from typing import Any

from storage.postgres import PostgresStorage

log = logging.getLogger(__name__)

_MAX_STUCK_MINUTES = 10


class LLMWorker:
    """
    Single-task LLM queue processor.
    Instantiated and called once per cron tick via cmd_run_worker.
    """

    def __init__(self, config: dict[str, Any], storage: PostgresStorage) -> None:
        self._config = config
        self._storage = storage

    def run_once(self) -> dict[str, Any]:
        """
        Execute the next pending LLM task.
        Returns a status dict reported back to the bot via stdout.
        """
        # Recover tasks that got stuck (e.g. previous worker crashed)
        reset = self._storage.reset_stuck_llm_tasks(_MAX_STUCK_MINUTES)
        if reset:
            log.warning("[llm_worker] reset %d stuck task(s)", reset)

        # Skip if a task is already running (previous cron tick still working)
        if self._storage.has_running_llm_task():
            return {"status": "busy", "note": "Another LLM task is still running, skipping."}

        # Auto-enqueue one process_batch when signals need classification
        if (
            not self._storage.has_pending_process_batch()
            and self._storage.count_unprocessed() > 0
        ):
            self._storage.enqueue_llm_task(
                task_type="process_batch",
                priority=90,
                payload={},
            )

        # Claim the highest-priority pending task
        task = self._storage.claim_next_llm_task()
        if not task:
            return {"status": "idle", "note": "LLM queue is empty."}

        task_id = task["id"]
        task_type = task["task_type"]
        payload = task["payload"]

        log.info("[llm_worker] starting task_id=%s type=%s payload=%s", task_id, task_type, payload)

        try:
            if task_type == "resolve":
                result = self._handle_resolve(payload)
            elif task_type == "process_batch":
                result = self._handle_process_batch()
            else:
                raise ValueError(f"Unknown task_type: {task_type!r}")

            self._storage.complete_llm_task(task_id)
            log.info("[llm_worker] done task_id=%s type=%s", task_id, task_type)
            return {"status": "done", "task_type": task_type, **result}

        except Exception as e:
            log.exception("[llm_worker] failed task_id=%s type=%s: %s", task_id, task_type, e)
            self._storage.fail_llm_task(task_id, str(e))
            return {"status": "failed", "task_type": task_type, "error": str(e)}

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    def _handle_resolve(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Resolve and auto-approve a keyword from the queue.
        Uses KeywordResolver to enrich the keyword and immediately approves
        the generated collection plan (no user confirmation needed for bulk queues).
        """
        from core.registry import load_all_collectors  # noqa: PLC0415
        from core.resolver import KeywordResolver  # noqa: PLC0415

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

    def _handle_process_batch(self) -> dict[str, Any]:
        """
        Run one LLM classification batch (max_batches=1).
        Returns count of classified signals and remaining count.
        """
        from core.orchestrator import Orchestrator  # noqa: PLC0415

        router = self._make_router()
        orch = Orchestrator(self._config, self._storage)
        result = orch.process(router, max_batches=1)
        return result

    def _make_router(self):
        from core.llm_router import LLMRouter  # noqa: PLC0415
        return LLMRouter(self._config, usage_logger=self._storage.log_llm_usage)
