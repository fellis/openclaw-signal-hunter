"""
LLM Task Queue Worker.
Processes tasks from llm_task_queue sequentially, one at a time.
Called by cron via skill command 'run_worker'.

Task types and priorities (lower = higher priority):
  - resolve:          50  - enrich keyword with LLM, create collection plan
  - collect_keyword:  70  - collect signals for one keyword (max once per 24h)
  - process_batch:    90  - LLM classify a batch of unprocessed signals

Worker auto-enqueue logic (runs every tick before claiming next task):
  1. For each keyword with approved plan and last_collected_at > 24h (or NULL):
     enqueue collect_keyword if not already pending/running (up to 3 per tick)
  2. If unprocessed signals exist and no process_batch pending: enqueue process_batch

Worker guarantees:
  - Only one task runs at a time (has_running_llm_task check)
  - Retries failed tasks up to 3 times before marking as 'failed'
  - Resets tasks stuck in 'running' for > 10 minutes
  - Loops within one cron tick until time budget is exhausted
"""

from __future__ import annotations

import logging
import time
from typing import Any

from storage.postgres import PostgresStorage

log = logging.getLogger(__name__)

_MAX_STUCK_MINUTES = 10
_DEFAULT_BUDGET_SECONDS = 50  # leave 10s buffer before next cron tick


class LLMWorker:
    """
    Sequential LLM queue processor with time-budget loop.
    Instantiated and called once per cron tick via cmd_run_worker.
    Processes as many tasks as possible within the time budget.
    """

    def __init__(self, config: dict[str, Any], storage: PostgresStorage) -> None:
        self._config = config
        self._storage = storage

    def run_loop(self, budget_seconds: int = _DEFAULT_BUDGET_SECONDS) -> dict[str, Any]:
        """
        Process tasks in a loop until queue is empty or time budget is exhausted.
        Returns summary of all processed tasks.
        """
        deadline = time.monotonic() + budget_seconds
        processed = []
        errors = []

        # Recover stuck tasks once per loop invocation
        reset = self._storage.reset_stuck_llm_tasks(_MAX_STUCK_MINUTES)
        if reset:
            log.warning("[llm_worker] reset %d stuck task(s)", reset)

        while time.monotonic() < deadline:
            # Skip if another task is already running (shouldn't happen in loop, but safety check)
            if self._storage.has_running_llm_task():
                log.warning("[llm_worker] unexpected running task, stopping loop")
                break

            # Auto-enqueue collect_keyword for stale keywords (not collected in 24h)
            stale = self._storage.get_stale_keywords(min_age_hours=24, limit=3)
            for kw in stale:
                if not self._storage.has_pending_collect_for(kw):
                    self._storage.enqueue_llm_task(
                        task_type="collect_keyword",
                        priority=70,
                        payload={"keyword": kw},
                    )
                    log.info("[llm_worker] enqueued collect_keyword for stale keyword '%s'", kw)

            # Auto-enqueue process_batch when signals need classification
            if (
                not self._storage.has_pending_process_batch()
                and self._storage.count_unprocessed() > 0
            ):
                self._storage.enqueue_llm_task(
                    task_type="process_batch",
                    priority=90,
                    payload={},
                )

            task = self._storage.claim_next_llm_task()
            if not task:
                break  # queue empty

            task_id = task["id"]
            task_type = task["task_type"]
            payload = task["payload"]

            log.info("[llm_worker] starting task_id=%s type=%s payload=%s", task_id, task_type, payload)

            try:
                if task_type == "resolve":
                    result = self._handle_resolve(payload)
                elif task_type == "collect_keyword":
                    result = self._handle_collect_keyword(payload)
                elif task_type == "process_batch":
                    result = self._handle_process_batch()
                else:
                    raise ValueError(f"Unknown task_type: {task_type!r}")

                self._storage.complete_llm_task(task_id)
                log.info("[llm_worker] done task_id=%s type=%s", task_id, task_type)
                processed.append({"task_type": task_type, **result})

            except Exception as e:
                log.exception("[llm_worker] failed task_id=%s type=%s: %s", task_id, task_type, e)
                self._storage.fail_llm_task(task_id, str(e))
                errors.append({"task_type": task_type, "error": str(e)})

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

    def _handle_collect_keyword(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Collect signals for a single keyword using its approved collection plans.
        Updates last_collected_at on success.
        """
        from core.orchestrator import Orchestrator  # noqa: PLC0415

        keyword = payload.get("keyword", "")
        if not keyword:
            raise ValueError("collect_keyword task missing 'keyword' in payload")

        orch = Orchestrator(self._config, self._storage)
        result = orch.collect(keywords=[keyword])
        self._storage.update_keyword_collected_at(keyword)
        log.info("[llm_worker] collected signals for '%s'", keyword)
        return {"keyword": keyword, **result}

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
