"""
LLM Task Queue Worker.
Processes tasks from llm_task_queue sequentially, one at a time.
Called by cron (every minute) via skill command 'run_worker'.

Task types and priorities (lower = higher priority):
  - resolve:          50  - enrich keyword with LLM, create collection plan
  - process_batch:    90  - embed-classify a batch of raw signals (no LLM)
  - summarize_batch:  95  - generate summaries for classified signals via LLM

Collection runs via a SEPARATE collect worker cron (cmd_run_collect_worker).
This worker is LLM-only: no API calls to GitHub/Reddit/HN/SO/HF.

Worker guarantees:
  - Only one LLM task runs at a time (has_running_llm_task check)
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

            # Auto-enqueue summarize_batch when relevant signals need summaries
            if (
                not self._storage.has_pending_task_of_type("summarize_batch")
                and self._storage.count_unsummarized() > 0
            ):
                self._storage.enqueue_llm_task(
                    task_type="summarize_batch",
                    priority=95,
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
                elif task_type == "process_batch":
                    result = self._handle_process_batch()
                elif task_type == "summarize_batch":
                    result = self._handle_summarize_batch()
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

    def _handle_process_batch(self) -> dict[str, Any]:
        """
        Run classification batches up to config.processor.max_batches_per_run.
        With embed mode each batch is fast (~10-60s), so multiple batches fit
        within the worker time budget. Default: 3 batches per task.
        Returns count of classified signals and remaining count.
        """
        from core.orchestrator import Orchestrator  # noqa: PLC0415

        max_batches = self._config.get("processor", {}).get("max_batches_per_run", 3)
        router = self._make_router()
        orch = Orchestrator(self._config, self._storage)
        result = orch.process(router, max_batches=max_batches)
        return result

    def _handle_summarize_batch(self) -> dict[str, Any]:
        """
        Generate summaries for relevant signals that were classified without one.
        Fetches unsummarized signals, calls LLM in small batches, saves results.
        After saving, add to embedding_queue so Qdrant indexing can proceed.
        Returns count of summarized signals and remaining count.
        """
        from core.llm_router import LLMCall, LLMRouter  # noqa: PLC0415
        import json  # noqa: PLC0415

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
                operation="process",
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
                summaries = json.loads(raw)
            except Exception:
                try:
                    recovered = raw.rstrip(", \n") + '"]'
                    summaries = json.loads(recovered)
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
        from core.llm_router import LLMRouter  # noqa: PLC0415
        return LLMRouter(self._config, usage_logger=self._storage.log_llm_usage)
