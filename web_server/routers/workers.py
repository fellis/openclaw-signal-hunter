"""
Workers API: status (tasks + schedule) and logs (Docker container).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request

from storage.config_manager import ConfigManager
from web_server.db import fetchall, fetchone

log = logging.getLogger(__name__)

router = APIRouter()

# Max tail lines for logs (cap on backend)
LOG_TAIL_MAX = 20_000

# Logger name -> worker filter value
LOGGER_TO_WORKER: dict[str, str] = {
    "core.llm_worker": "run_worker",
    "core.translate_worker": "run_worker",
    "core.embed_worker": "run_embed_worker",
    "core.embed_processor": "run_embed_worker",
    "core.orchestrator": "embed",  # collect/embed both use orchestrator; default to embed for logs
    "core.embedder": "embed",
}
# Fallback for shell lines (e.g. "Worker runner started...")
RUNNER_WORKER = "runner"
OTHER_WORKER = "other"

# Python logging format: 2025-03-08 12:00:00,123 WARNING core.llm_worker: message
LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d+)?)\s+(\w+)\s+([\w.]+):\s*(.*)$",
    re.DOTALL,
)


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a single log line. Returns dict with ts, level, worker, message or None for non-matching."""
    line = line.rstrip("\n\r")
    if not line:
        return None
    m = LOG_LINE_RE.match(line)
    if m:
        ts_str, level, logger_name, message = m.groups()
        level = level.lower()
        worker = LOGGER_TO_WORKER.get(logger_name, OTHER_WORKER)
        return {"ts": ts_str, "level": level, "worker": worker, "message": message}
    # Shell or other output
    return {"ts": "", "level": "info", "worker": RUNNER_WORKER, "message": line}


def _get_docker_logs(
    container_name: str,
    tail: int = 500,
    since: int | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """Fetch container logs via Docker SDK. Returns (parsed_lines, next_since_unix)."""
    try:
        import docker
        from docker.errors import NotFound
    except ImportError:
        log.warning("docker SDK not installed")
        return [], None

    socket_path = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    if not os.path.exists(socket_path):
        log.warning("Docker socket not found: %s", socket_path)
        return [], None

    try:
        client = docker.DockerClient(base_url=f"unix://{socket_path}")
        container = None
        if container_name:
            try:
                container = client.containers.get(container_name)
            except NotFound:
                pass
        if container is None:
            # Find by Compose service label (works when project prefix changes container name)
            for c in client.containers.list():
                if c.labels.get("com.docker.compose.service") == "signal-hunter-workers":
                    container = c
                    break
        if container is None:
            log.warning("Worker container not found (name=%r, no label match)", container_name)
            return [], None
    except Exception as e:
        log.warning("Docker container %s: %s", container_name, e)
        return [], None

    tail = max(1, min(tail, LOG_TAIL_MAX))
    since_dt = None
    if since is not None and since > 0:
        since_dt = datetime.utcfromtimestamp(since)

    try:
        raw = container.logs(stream=False, tail=tail, since=since_dt)
    except Exception as e:
        log.warning("container.logs failed: %s", e)
        return [], None

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return [], None
    else:
        text = raw

    lines: list[dict[str, Any]] = []
    next_ts: str = ""
    for line in text.splitlines():
        parsed = _parse_log_line(line)
        if parsed:
            lines.append(parsed)
            if parsed.get("ts"):
                next_ts = parsed["ts"]

    next_since = None
    if next_ts:
        try:
            # Parse "2025-03-08 12:00:00,123" or "2025-03-08 12:00:00"
            normalized = next_ts.replace(",", ".")
            if "." in normalized:
                dt = datetime.strptime(normalized.split(".")[0], "%Y-%m-%d %H:%M:%S")
            else:
                dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
            next_since = int(dt.timestamp())
        except ValueError:
            pass

    return lines, next_since


@router.get("/status")
async def get_workers_status(request: Request):
    """Return worker tasks (queues) and schedule (intervals)."""
    cache = request.app.state.cache
    cached = cache.get("workers_status")
    if cached:
        return cached

    # Schedule from config (optional worker_runner section)
    config = ConfigManager().load()
    wr = config.get("worker_runner") or {}
    worker_interval = int(wr.get("worker_interval_sec", 60))
    collect_interval = int(wr.get("collect_interval_sec", 300))
    schedule = {
        "run_worker_interval_sec": worker_interval,
        "run_embed_worker_interval_sec": worker_interval,
        "run_collect_worker_interval_sec": collect_interval,
        "embed_interval_sec": worker_interval,
    }

    # LLM queue
    llm_rows = fetchall(
        """
        SELECT id, task_type, priority, status, retry_count, error, payload, created_at, started_at
        FROM llm_task_queue
        ORDER BY priority ASC, created_at ASC
        """
    )
    by_status: dict[str, list] = {"pending": [], "running": [], "failed": []}
    tasks_for_list: list[dict[str, Any]] = []
    for r in llm_rows:
        status = r.get("status") or "pending"
        if status not in by_status:
            by_status[status] = []
        entry = {"task_type": r.get("task_type"), "status": status, "payload": r.get("payload")}
        if r.get("retry_count"):
            entry["retry_count"] = r["retry_count"]
        if r.get("error"):
            entry["error"] = r["error"][:200] if isinstance(r["error"], str) else str(r["error"])[:200]
        if r.get("created_at"):
            entry["created_at"] = r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"])
        by_status[status].append(entry)
        tasks_for_list.append(entry)

    llm_queue = {
        "pending": len(by_status.get("pending", [])),
        "running": len(by_status.get("running", [])),
        "failed": len(by_status.get("failed", [])),
        "tasks": tasks_for_list[-20:],  # last 20
    }

    # Collect worker: next stale keyword
    stale_row = fetchone(
        """
        SELECT kp.canonical_name AS next_keyword, kp.last_collected_at
        FROM keyword_profiles kp
        WHERE (
            kp.last_collected_at IS NULL
            OR kp.last_collected_at < now() - interval '24 hours'
        )
        AND EXISTS (
            SELECT 1 FROM keyword_collection_plans kcp
            WHERE kcp.canonical_name = kp.canonical_name
        )
        ORDER BY kp.last_collected_at ASC NULLS FIRST
        LIMIT 1
        """
    )
    collect_worker = {
        "next_keyword": stale_row["next_keyword"] if stale_row else None,
        "last_collected_at": (
            stale_row["last_collected_at"].isoformat()
            if stale_row and stale_row.get("last_collected_at") and hasattr(stale_row["last_collected_at"], "isoformat")
            else (str(stale_row["last_collected_at"]) if stale_row and stale_row.get("last_collected_at") else None)
        ),
    }

    # Embed worker and vectorize: reuse stats-like counts
    stats_row = fetchone(
        """
        SELECT
            (SELECT COUNT(*) FROM raw_signals) - (SELECT COUNT(*) FROM processed_signals p JOIN raw_signals r ON r.id = p.raw_signal_id) AS unprocessed,
            (SELECT COUNT(*) FROM processed_signals p JOIN raw_signals r ON r.id = p.raw_signal_id WHERE p.borderline_override_pending = true)::int AS borderline_pending,
            (SELECT COUNT(*) FROM embedding_queue WHERE status = 'pending')::int AS pending_embeddings
        """
    )
    if stats_row:
        embed_worker = {
            "unprocessed": int(stats_row.get("unprocessed") or 0),
            "borderline_pending": int(stats_row.get("borderline_pending") or 0),
        }
        embed_vectorize = {"pending": int(stats_row.get("pending_embeddings") or 0)}
    else:
        embed_worker = {"unprocessed": 0, "borderline_pending": 0}
        embed_vectorize = {"pending": 0}

    result = {
        "schedule": schedule,
        "llm_queue": llm_queue,
        "embed_worker": embed_worker,
        "collect_worker": collect_worker,
        "embed_vectorize": embed_vectorize,
    }
    cache.set("workers_status", value=result, ttl=10)
    return result


@router.get("/logs")
async def get_workers_logs(
    tail: int = Query(500, ge=1, le=LOG_TAIL_MAX),
    since: int | None = Query(None),
    worker: str = Query("all"),
    level: str = Query("all"),
):
    """Return container logs (parsed). worker: all | run_worker | run_embed_worker | run_collect_worker | embed | runner | other. level: all | info | warning | error."""
    container_name = os.environ.get("WORKER_CONTAINER_NAME", "signal-hunter-workers")
    lines, next_since = _get_docker_logs(container_name, tail=tail, since=since)

    if worker != "all":
        lines = [ln for ln in lines if ln.get("worker") == worker]
    if level != "all":
        level_lower = level.lower()
        lines = [ln for ln in lines if ln.get("level") == level_lower]

    out: dict[str, Any] = {"lines": lines}
    if next_since is not None:
        out["next_since"] = next_since
    return out


@router.post("/logs/clear")
async def clear_workers_logs_view():
    """Client-side clear: no-op, returns ok. UI clears local state and refetches with tail only."""
    return {"status": "ok"}
