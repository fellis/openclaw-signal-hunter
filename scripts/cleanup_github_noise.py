"""
One-time cleanup: remove GitHub issues that don't pass the new quality filters.

Removed if ANY of the following is true:
  1. Has a noise label: invalid, spam, duplicate, wontfix, stale, ...
  2. Body has no meaningful content (empty or URL-only) AND no reactions AND comments <= 1

"Meaningful content" means text that remains after stripping all URLs.
An issue whose body is only a GitHub permalink (e.g., code reference) is treated as empty.

Run inside the web-report container:
  docker exec signal-hunter-web-report-1 python3 /app/scripts/cleanup_github_noise.py

Or on the host with DB access:
  python3 scripts/cleanup_github_noise.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_NOISE_LABELS = {
    "invalid", "spam", "duplicate", "wontfix", "won't fix",
    "not a bug", "not a question", "off-topic", "stale",
}

_QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
_COLLECTION = "signals"

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://signal:signal@localhost:5433/signal_hunter",
)


def uuid_to_qdrant_id(raw_signal_id: str) -> int:
    """Same hash as embedder.py - must stay in sync."""
    h = hashlib.sha256(str(raw_signal_id).encode()).digest()
    return int.from_bytes(h[:8], "big")


def _meaningful_body(body: str) -> bool:
    """Return True if body has meaningful text beyond URLs and whitespace."""
    stripped = re.sub(r'https?://\S+', '', body).strip()
    return bool(stripped)


def find_noise_ids(cur: psycopg2.extensions.cursor) -> list[str]:
    """
    Return raw_signal_id (uuid as str) for all github_issue rows
    that fail the quality filters.
    """
    cur.execute("""
        SELECT id::text, title, body, score, comments_count, tags
        FROM raw_signals
        WHERE source = 'github_issue'
    """)
    rows = cur.fetchall()
    log.info("Scanning %d github_issue rows ...", len(rows))

    noise_ids: list[str] = []
    reasons: dict[str, int] = {"noise_label": 0, "empty_low_signal": 0}

    for (rid, title, body, score, comments, tags_raw) in rows:
        # Parse tags
        tags: list[str] = []
        if tags_raw:
            if isinstance(tags_raw, list):
                tags = [str(t).lower() for t in tags_raw]
            elif isinstance(tags_raw, str):
                try:
                    parsed = json.loads(tags_raw)
                    tags = [str(t).lower() for t in parsed]
                except Exception:
                    tags = []

        # Filter 1: noise label
        if any(t in _NOISE_LABELS for t in tags):
            noise_ids.append(rid)
            reasons["noise_label"] += 1
            continue

        # Filter 2: no meaningful content + no engagement
        # Body is empty or URL-only AND score == 0 AND comments <= 1
        body_str = body or ""
        if not _meaningful_body(body_str) and not (score or 0) and (comments or 0) <= 1:
            noise_ids.append(rid)
            reasons["empty_low_signal"] += 1

    log.info(
        "Found %d noise signals: %d noise-label, %d empty/url-only low-signal",
        len(noise_ids), reasons["noise_label"], reasons["empty_low_signal"],
    )
    return noise_ids


def delete_from_qdrant(raw_signal_ids: list[str]) -> None:
    """Delete Qdrant points for given raw_signal_ids."""
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        log.warning("httpx not available - skipping Qdrant cleanup")
        return

    qdrant_ids = [uuid_to_qdrant_id(rid) for rid in raw_signal_ids]

    # Qdrant supports deleting by points filter or by IDs list
    # Use the REST API in batches of 1000
    batch_size = 1000
    deleted = 0
    for i in range(0, len(qdrant_ids), batch_size):
        batch = qdrant_ids[i : i + batch_size]
        payload = {"points": batch}
        try:
            resp = httpx.post(
                f"{_QDRANT_URL}/collections/{_COLLECTION}/points/delete",
                json=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                deleted += len(batch)
            else:
                log.warning("Qdrant delete batch %d returned %d: %s", i, resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Qdrant delete batch %d failed: %s", i, e)

    log.info("Qdrant: deleted %d / %d points", deleted, len(qdrant_ids))


def delete_from_postgres(cur: psycopg2.extensions.cursor, raw_signal_ids: list[str]) -> dict[str, int]:
    """Delete rows from all dependent tables. Returns counts."""
    counts: dict[str, int] = {}

    # Must delete in FK order: embedding_queue -> processed_signals -> raw_signals
    cur.execute("""
        DELETE FROM embedding_queue
        WHERE dedup_key IN (
            SELECT dedup_key FROM processed_signals
            WHERE raw_signal_id = ANY(%s::uuid[])
        )
    """, (raw_signal_ids,))
    counts["embedding_queue"] = cur.rowcount

    cur.execute("""
        DELETE FROM processed_signals
        WHERE raw_signal_id = ANY(%s::uuid[])
    """, (raw_signal_ids,))
    counts["processed_signals"] = cur.rowcount

    cur.execute("""
        DELETE FROM raw_signals
        WHERE id = ANY(%s::uuid[])
    """, (raw_signal_ids,))
    counts["raw_signals"] = cur.rowcount

    return counts


def main(dry_run: bool = False) -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    noise_ids = find_noise_ids(cur)

    if not noise_ids:
        log.info("Nothing to clean up.")
        conn.close()
        return

    # Check how many have vectors in Qdrant
    cur.execute("""
        SELECT COUNT(*) FROM embedding_queue eq
        JOIN processed_signals p ON p.dedup_key = eq.dedup_key
        WHERE p.raw_signal_id = ANY(%s::uuid[])
          AND eq.status = 'done'
    """, (noise_ids,))
    vectorized_count = cur.fetchone()[0]
    log.info("%d of these have Qdrant vectors (status=done)", vectorized_count)

    if dry_run:
        log.info("DRY RUN - no changes made.")
        conn.close()
        return

    confirm = input(f"\nDelete {len(noise_ids)} signals from PG + Qdrant? [yes/N]: ").strip().lower()
    if confirm != "yes":
        log.info("Aborted.")
        conn.close()
        return

    # Step 1: Qdrant cleanup (before PG so we still have the IDs)
    log.info("Deleting from Qdrant ...")
    delete_from_qdrant(noise_ids)

    # Step 2: PostgreSQL cleanup
    log.info("Deleting from PostgreSQL ...")
    counts = delete_from_postgres(cur, noise_ids)
    conn.commit()

    log.info("Done! Deleted: %s", counts)
    conn.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log.info("=== DRY RUN MODE ===")
    main(dry_run=dry_run)
