"""
Translation Worker.

Runs as a standalone loop process (translate_worker container).
Translates title + summary of relevant, embedded signals to Russian.
Stores results in signal_translations table (signal_id, lang, field, text).

Flow:
  1. Find processed_signals that are relevant, have a summary,
     are embedded (embedding_queue status=done), and have no 'ru' translation yet.
  2. Batch up to BATCH_SIZE signals.
  3. Send title batch + summary batch to translator microservice.
  4. Upsert rows into signal_translations.
  5. Sleep POLL_INTERVAL seconds and repeat.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

DATABASE_URL   = os.environ.get("DATABASE_URL", "postgresql://signal:signal@postgres:5432/signal_hunter")
TRANSLATOR_URL = os.environ.get("TRANSLATOR_URL", "http://10.10.10.4:6340")
TARGET_LANG    = os.environ.get("TARGET_LANG", "ru")
BATCH_SIZE     = int(os.environ.get("BATCH_SIZE", "32"))
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL", "30"))


def _get_conn() -> Any:
    return psycopg2.connect(DATABASE_URL)


def _fetch_pending(conn: Any, batch: int) -> list[dict]:
    """
    Fetch signals that need translation:
    - relevant, have summary, are embedded
    - no translation row yet for TARGET_LANG
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                r.id        AS signal_id,
                r.title,
                p.summary
            FROM processed_signals p
            JOIN raw_signals r       ON r.id = p.raw_signal_id
            JOIN embedding_queue eq  ON eq.dedup_key = p.dedup_key
            WHERE p.is_relevant  = true
              AND p.summary       IS NOT NULL
              AND eq.status       = 'done'
              AND NOT EXISTS (
                  SELECT 1 FROM signal_translations st
                  WHERE st.signal_id = r.id AND st.lang = %s
              )
            ORDER BY p.rank_score DESC NULLS LAST
            LIMIT %s
            """,
            (TARGET_LANG, batch),
        )
        return [dict(r) for r in cur.fetchall()]


def _translate_batch(texts: list[str]) -> list[str]:
    """Call translator microservice; return translated strings in same order."""
    texts = [t or "" for t in texts]
    non_empty = [(i, t) for i, t in enumerate(texts) if t.strip()]
    if not non_empty:
        return texts[:]

    indices, payloads = zip(*non_empty)
    resp = httpx.post(
        f"{TRANSLATOR_URL}/translate",
        json={"texts": list(payloads), "target_lang": TARGET_LANG},
        timeout=120.0,
    )
    resp.raise_for_status()
    translated = resp.json()["translations"]

    result = list(texts)
    for idx, tr in zip(indices, translated):
        result[idx] = tr
    return result


def _upsert_translations(conn: Any, rows: list[tuple]) -> None:
    """
    rows: list of (signal_id, lang, field, text)
    Uses ON CONFLICT to upsert.
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO signal_translations (signal_id, lang, field, text)
            VALUES %s
            ON CONFLICT (signal_id, lang, field) DO UPDATE SET text = EXCLUDED.text, created_at = now()
            """,
            rows,
        )
    conn.commit()


def run_once(conn: Any) -> int:
    """Process one batch. Returns number of signals translated."""
    signals = _fetch_pending(conn, BATCH_SIZE)
    if not signals:
        return 0

    ids     = [str(s["signal_id"]) for s in signals]
    titles  = [s["title"] or "" for s in signals]
    summaries = [s["summary"] or "" for s in signals]

    log.info("[translate_worker] translating %d signals -> %s", len(signals), TARGET_LANG)

    try:
        translated_titles   = _translate_batch(titles)
        translated_summaries = _translate_batch(summaries)
    except Exception as exc:
        log.error("[translate_worker] translator call failed: %s", exc)
        return 0

    rows: list[tuple] = []
    for sid, tt, ts in zip(ids, translated_titles, translated_summaries):
        if tt.strip():
            rows.append((sid, TARGET_LANG, "title", tt))
        if ts.strip():
            rows.append((sid, TARGET_LANG, "summary", ts))

    if rows:
        _upsert_translations(conn, rows)
        log.info("[translate_worker] stored %d translation rows for %d signals", len(rows), len(signals))

    return len(signals)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("[translate_worker] starting. target_lang=%s batch=%d poll=%ds",
             TARGET_LANG, BATCH_SIZE, POLL_INTERVAL)

    conn: Any = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = _get_conn()

            done = run_once(conn)
            if done == 0:
                log.debug("[translate_worker] idle, sleeping %ds", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL if done == 0 else 1)

        except psycopg2.OperationalError as exc:
            log.warning("[translate_worker] db connection lost: %s - reconnecting", exc)
            conn = None
            time.sleep(5)
        except Exception as exc:
            log.error("[translate_worker] unexpected error: %s", exc, exc_info=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
