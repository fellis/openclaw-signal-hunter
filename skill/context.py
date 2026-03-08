"""
Skill plugin context: worker name and logging format.

Owns the worker tag so all log lines (including from httpx, core.*, etc.)
are explicitly tagged at source. Backend can parse worker from log line
without URL/JSON heuristics.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Set by set_worker() at CLI entry before dispatching to command
current_worker = "runner"

# Commands that are workers: their JSON output goes through logger (with worker tag), not raw stdout.
# Ids must match backend web_server.routers.workers.WORKER_IDS (single source of truth for filter/labels is API).
WORKER_COMMANDS = frozenset({
    "run_worker", "run_worker_daemon", "run_translate_worker", "run_collect_worker", "run_embed_worker", "embed",
})


class _WorkerFormatter(logging.Formatter):
    """Adds worker tag to every log record from this process."""

    def format(self, record: logging.LogRecord) -> str:
        import skill.context as _ctx
        record.worker = getattr(record, "worker", _ctx.current_worker)  # type: ignore[attr-defined]
        return super().format(record)


def setup_logging() -> None:
    """Configure root logger with worker-tagged format. Called once when skill context is loaded."""
    import os
    # Single handler only: ensure backend can parse worker from every line (no library default format)
    for h in list(logging.root.handlers):
        logging.root.removeHandler(h)
    level = getattr(logging, os.environ.get("LOG_LEVEL", "WARNING").upper(), logging.WARNING)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_WorkerFormatter("%(asctime)s %(levelname)s %(worker)s %(name)s: %(message)s"))
    logging.root.addHandler(handler)
    logging.root.setLevel(level)


setup_logging()


def set_worker(name: str) -> None:
    """Set current worker name for this process (command name)."""
    global current_worker
    current_worker = name


def out(data: Any) -> None:
    """Emit JSON: for worker commands via logger (tagged), otherwise to stdout."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    if current_worker in WORKER_COMMANDS:
        logging.getLogger(__name__).info(payload)
    else:
        print(payload, flush=True)
