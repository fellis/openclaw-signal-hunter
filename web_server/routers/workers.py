"""
Workers API: status (tasks + schedule) and logs (Docker container).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from storage.config_manager import ConfigManager
from web_server.db import execute, execute_update, fetchall, fetchone

log = logging.getLogger(__name__)

router = APIRouter()

# Max tail lines for logs (cap on backend)
LOG_TAIL_MAX = 20_000

# Single source of truth for worker filter: ids and display labels (exposed via API; skill tags logs with these ids)
WORKER_IDS = [
    "run_worker",
    "run_translate_worker",
    "run_collect_worker",
    "run_embed_worker",
    "embed",
    "runner",
    "other",
]
WORKER_LABELS: dict[str, str] = {
    "run_worker": "LLM",
    "run_translate_worker": "Translate",
    "run_collect_worker": "Collect",
    "run_embed_worker": "Embed classifier",
    "embed": "Vectorize",
    "runner": "Runner",
    "other": "Other",
}

# Log line format from skill (tagged): timestamp level worker name: message
LOG_LINE_RE_TAGGED = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d+)?)\s+(\w+)\s+(\S+)\s+([\w.]+):\s*(.*)$",
    re.DOTALL,
)
# Untagged (old or non-skill): timestamp level name: message -> treat as "other"
LOG_LINE_RE_UNTAGGED = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d+)?)\s+(\w+)\s+([\w.]+):\s*(.*)$",
    re.DOTALL,
)


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a single log line. Tagged lines (from skill) have worker in line; untagged get runner/other."""
    line = line.rstrip("\n\r")
    if not line:
        return None
    m = LOG_LINE_RE_TAGGED.match(line)
    if m:
        ts_str, level, worker, _logger, message = m.groups()
        return {"ts": ts_str, "level": level.lower(), "worker": worker, "message": message}
    m = LOG_LINE_RE_UNTAGGED.match(line)
    if m:
        ts_str, level, _logger, message = m.groups()
        return {"ts": ts_str, "level": level.lower(), "worker": "other", "message": message}
    # Shell or non-Python output
    return {"ts": "", "level": "info", "worker": "runner", "message": line}


def _ts_to_float(ts_str: str) -> float | None:
    """Parse log timestamp '2026-03-08 02:33:23,799' to unix timestamp (float, UTC). Returns None if invalid."""
    if not ts_str:
        return None
    try:
        normalized = ts_str.strip().replace(",", ".")
        if "." in normalized:
            part, frac = normalized.split(".", 1)
            dt = datetime.strptime(part, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            ms = frac.ljust(3, "0")[:3]
            return dt.timestamp() + int(ms) / 1000.0
        dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _get_worker_container():
    """Resolve worker container via name or Compose label. Returns container or None."""
    try:
        import docker
        from docker.errors import NotFound
    except ImportError:
        return None
    socket_path = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    if not os.path.exists(socket_path):
        return None
    container_name = os.environ.get("WORKER_CONTAINER_NAME", "signal-hunter-workers")
    try:
        client = docker.DockerClient(base_url=f"unix://{socket_path}")
        container = None
        if container_name:
            try:
                container = client.containers.get(container_name)
            except NotFound:
                pass
        if container is None:
            for c in client.containers.list():
                if c.labels.get("com.docker.compose.service") == "signal-hunter-workers":
                    container = c
                    break
        return container
    except Exception:
        return None


def _get_docker_logs(
    container_name: str,
    tail: int = 500,
    since: float | int | None = None,
) -> tuple[list[dict[str, Any]], float | None]:
    """Fetch container logs via Docker SDK. Returns (parsed_lines, next_since_unix_float)."""
    container = _get_worker_container()
    if container is None:
        return [], None
    # Use requested name only for log context; we already resolved container
    if not container_name:
        container_name = "signal-hunter-workers"

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

    next_since = _ts_to_float(next_ts) if next_ts else None
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
        "run_translate_worker_interval_sec": worker_interval,
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

    # Translation worker: count signals pending translation (same criteria as TranslateWorker._count_pending)
    target_lang = os.environ.get("TRANSLATE_TARGET_LANG", "ru")
    trans_row = fetchone(
        """
        SELECT COUNT(*) AS pending
        FROM processed_signals p
        JOIN raw_signals r ON r.id = p.raw_signal_id
        JOIN embedding_queue eq ON eq.dedup_key = p.dedup_key
        WHERE p.is_relevant = true
          AND p.summary IS NOT NULL
          AND eq.status = 'done'
          AND (p.language IS NULL OR p.language != %s)
          AND NOT EXISTS (
              SELECT 1 FROM signal_translations st
              WHERE st.signal_id = r.id AND st.lang = %s
          )
        """,
        (target_lang, target_lang),
    )
    translation_worker = {"pending": int(trans_row.get("pending", 0) or 0) if trans_row else 0}

    workers = [{"id": wid, "label": WORKER_LABELS.get(wid, wid)} for wid in WORKER_IDS]
    result = {
        "schedule": schedule,
        "llm_queue": llm_queue,
        "embed_worker": embed_worker,
        "collect_worker": collect_worker,
        "embed_vectorize": embed_vectorize,
        "translation_worker": translation_worker,
        "workers": workers,
    }
    cache.set("workers_status", value=result, ttl=10)
    return result


@router.get("/logs")
async def get_workers_logs(
    request: Request,
    tail: int = Query(500, ge=1, le=LOG_TAIL_MAX),
    since: float | None = Query(None),
    worker: str = Query("all"),
    level: str = Query("all"),
):
    """Return container logs (parsed). since=unix ts for incremental fetch. Server-side clear: only lines after last clear."""
    container_name = os.environ.get("WORKER_CONTAINER_NAME", "signal-hunter-workers")
    lines, next_since = _get_docker_logs(container_name, tail=tail, since=since)

    cleared_at: float | None = getattr(request.app.state, "workers_log_cleared_at", None)
    if cleared_at is not None:
        def _after_clear(ln: dict[str, Any]) -> bool:
            ts_f = _ts_to_float(ln.get("ts") or "")
            return ts_f is None or ts_f > cleared_at
        lines = [ln for ln in lines if _after_clear(ln)]

    if since is not None:
        def _after_since(ln: dict[str, Any]) -> bool:
            ts_f = _ts_to_float(ln.get("ts") or "")
            return ts_f is None or ts_f > since
        lines = [ln for ln in lines if _after_since(ln)]

    # Drop consecutive duplicate runner lines (same JSON status printed every tick floods the UI)
    prev_msg: str | None = None
    deduped: list[dict[str, Any]] = []
    for ln in lines:
        if ln.get("worker") == "runner" and ln.get("message"):
            msg = (ln["message"] or "").strip()
            if msg == prev_msg:
                continue
            prev_msg = msg
        else:
            prev_msg = None
        deduped.append(ln)
    lines = deduped

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
async def clear_workers_logs_view(request: Request):
    """Server-side clear: from now on GET /logs returns only lines after this moment."""
    request.app.state.workers_log_cleared_at = time.time()
    return {"status": "ok"}


@router.post("/recollect")
async def post_recollect(body: dict[str, Any]):
    """
    Queue keywords for immediate (out-of-schedule) collection.
    Body: {"keywords": ["rag", "ollama", ...]}.
    Returns 202 with accepted keywords; 400 if invalid. Deduplicates against existing queue.
    """
    raw = body.get("keywords")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="keywords must be a list of strings")
    normalized = [str(k).strip().lower() for k in raw if k]
    if not normalized:
        raise HTTPException(status_code=400, detail="keywords list is empty")

    valid_rows = fetchall("SELECT canonical_name FROM keyword_profiles")
    valid_set = {r["canonical_name"] for r in valid_rows}
    invalid = [k for k in normalized if k not in valid_set]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown keywords (must exist in keyword_profiles): {invalid[:10]}{'...' if len(invalid) > 10 else ''}",
        )

    queue_rows = fetchall("SELECT keywords FROM recollect_queue")
    already_queued = set()
    for row in queue_rows:
        kw = row.get("keywords")
        if isinstance(kw, list):
            already_queued.update(kw)
        elif isinstance(kw, str):
            try:
                already_queued.update(json.loads(kw))
            except (TypeError, ValueError):
                pass
    to_insert = [k for k in normalized if k not in already_queued]
    to_insert = list(dict.fromkeys(to_insert))

    if not to_insert:
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "keywords": [],
                "message": "All selected keywords already in queue",
            },
        )

    execute(
        "INSERT INTO recollect_queue (keywords) VALUES (%s::jsonb)",
        (json.dumps(to_insert),),
    )
    return {
        "status": "accepted",
        "keywords": to_insert,
        "message": f"Recollect queued for {len(to_insert)} keyword(s)",
    }


@router.post("/retry-failed")
async def post_retry_failed():
    """
    Reset all failed LLM queue tasks to pending so the worker retries them.
    Returns the number of tasks reset.
    """
    count = execute_update(
        """
        UPDATE llm_task_queue
        SET status = 'pending', retry_count = 0, error = NULL, started_at = NULL
        WHERE status = 'failed'
        """
    )
    return {"status": "ok", "reset": count, "message": f"{count} failed task(s) reset to pending."}


@router.post("/restart")
async def restart_workers():
    """Restart the worker container via Docker API. Requires Docker socket mounted."""
    container = _get_worker_container()
    if container is None:
        raise HTTPException(
            status_code=503,
            detail="Worker container not found. Ensure Docker socket is mounted and workers are running.",
        )
    try:
        container.restart(timeout=30)
        return {"status": "ok", "message": "Workers container restarting."}
    except Exception as e:
        log.exception("Worker container restart failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
