"""
One-time migration: enrich existing Qdrant points with new payload fields.

Adds to each point: source_type, confidence, language, keywords
by joining embedding_queue (done) + processed_signals + raw_signals in Postgres
then calling Qdrant set_payload for each batch.

Run from the signal-hunter directory with the venv active:
    python scripts/migrate_qdrant_payload.py

Or inside the openclaw-gateway container (has all deps):
    docker exec -it openclaw-gateway /home/node/.openclaw/extensions/signal-hunter/.venv/bin/python \
        /home/node/.openclaw/extensions/signal-hunter/scripts/migrate_qdrant_payload.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
BATCH_SIZE = 200
COLLECTION = "signals"


def check_env() -> None:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL is not set. Source .env first or export manually.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def uuid_to_qdrant_id(raw_signal_id: str) -> int:
    h = hashlib.sha256(str(raw_signal_id).encode()).digest()
    return int.from_bytes(h[:8], "big")


def fetch_all_done_embeddings() -> list[dict]:
    """Return all signals that have a done embedding, with full metadata."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    p.raw_signal_id::text,
                    p.confidence::float,
                    p.language,
                    p.keywords_matched,
                    r.source AS source_type
                FROM embedding_queue eq
                JOIN processed_signals p ON p.dedup_key = eq.dedup_key
                JOIN raw_signals r ON r.id = p.raw_signal_id
                WHERE eq.status = 'done'
            """)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_qdrant_payload(rows: list[dict]) -> int:
    """Batch-update Qdrant point payloads. Returns number of points updated."""
    import httpx

    updated = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]

        for row in batch:
            qdrant_id = uuid_to_qdrant_id(row["raw_signal_id"])
            keywords = row.get("keywords_matched") or []
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except Exception:
                    keywords = []

            payload = {
                "source_type": row.get("source_type") or "",
                "confidence": float(row.get("confidence") or 0),
                "language": row.get("language") or "en",
                "keywords": keywords if isinstance(keywords, list) else [],
            }

            try:
                resp = httpx.post(
                    f"{QDRANT_URL}/collections/{COLLECTION}/points/payload",
                    json={"payload": payload, "points": [qdrant_id]},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    updated += 1
                else:
                    log.warning("Qdrant set_payload failed for %s: %s", qdrant_id, resp.text[:100])
            except Exception as e:
                log.warning("HTTP error for %s: %s", qdrant_id, e)

        log.info("Processed batch %d-%d (%d updated so far)", i + 1, i + len(batch), updated)

    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    check_env()

    log.info("Fetching all done embeddings from Postgres…")
    rows = fetch_all_done_embeddings()
    log.info("Found %d done embeddings to migrate", len(rows))

    if not rows:
        log.info("Nothing to migrate.")
        return

    log.info("Updating Qdrant payload in batches of %d…", BATCH_SIZE)
    updated = update_qdrant_payload(rows)
    log.info("Done. Updated %d / %d points.", updated, len(rows))


if __name__ == "__main__":
    main()
