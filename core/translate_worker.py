"""
Translation Worker.

Translates title + summary of relevant, embedded signals to the target language.
Stores results in signal_translations (signal_id, lang, field, text).

Called by cron via skill command 'run_translate_worker'.
One cron tick = one batch of BATCH_SIZE signals.

Skips signals already in the target language (no RU->RU or EN->EN translation).
Skips signals that already have a translation row for the target language.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

TRANSLATOR_URL    = os.environ.get("TRANSLATOR_URL", "https://llm.aegisalpha.io/translator")
TRANSLATOR_API_KEY = os.environ.get("LOCAL_LLM_API_KEY", "")
TARGET_LANG       = os.environ.get("TRANSLATE_TARGET_LANG", "ru")
BATCH_SIZE        = int(os.environ.get("TRANSLATE_BATCH_SIZE", "32"))


class TranslateWorker:
    """
    Single-batch translation worker.
    Called once per cron tick via cmd_run_translate_worker.
    """

    def __init__(self, storage: Any) -> None:
        self._url = storage._url

    def run(self) -> dict[str, Any]:
        """
        Translate one batch of signals.
        Returns summary dict: status, translated, remaining.
        """
        import psycopg2  # noqa: PLC0415
        conn = psycopg2.connect(self._url)
        try:
            return self._run(conn)
        finally:
            conn.close()

    def _run(self, conn: Any) -> dict[str, Any]:
        signals = self._fetch_pending(conn)

        if not signals:
            return {"status": "idle", "note": "No signals pending translation."}

        log.info("[translate_worker] translating %d signals -> %s", len(signals), TARGET_LANG)

        titles    = [s["title"] or "" for s in signals]
        summaries = [s["summary"] or "" for s in signals]

        try:
            translated_titles    = self._translate_batch(titles)
            translated_summaries = self._translate_batch(summaries)
        except Exception as exc:
            log.error("[translate_worker] translator call failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        rows: list[tuple] = []
        for s, tt, ts in zip(signals, translated_titles, translated_summaries):
            sid = str(s["signal_id"])
            if tt.strip():
                rows.append((sid, TARGET_LANG, "title", tt))
            if ts.strip():
                rows.append((sid, TARGET_LANG, "summary", ts))

        if rows:
            self._upsert(conn, rows)

        remaining = self._count_pending(conn)
        log.info("[translate_worker] stored %d rows, remaining ~%d", len(rows), remaining)
        return {
            "status": "done",
            "translated": len(signals),
            "rows_stored": len(rows),
            "remaining": remaining,
        }

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _fetch_pending(self, conn: Any) -> list[dict]:
        with conn.cursor() as cur:
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
                  AND (p.language IS NULL OR p.language != %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM signal_translations st
                      WHERE st.signal_id = r.id AND st.lang = %s
                  )
                ORDER BY p.rank_score DESC NULLS LAST
                LIMIT %s
                """,
                (TARGET_LANG, TARGET_LANG, BATCH_SIZE),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _count_pending(self, conn: Any) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM processed_signals p
                JOIN raw_signals r      ON r.id = p.raw_signal_id
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
                (TARGET_LANG, TARGET_LANG),
            )
            return cur.fetchone()[0]

    def _upsert(self, conn: Any, rows: list[tuple]) -> None:
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values  # noqa: PLC0415
            execute_values(
                cur,
                """
                INSERT INTO signal_translations (signal_id, lang, field, text)
                VALUES %s
                ON CONFLICT (signal_id, lang, field)
                DO UPDATE SET text = EXCLUDED.text, created_at = now()
                """,
                rows,
            )
        conn.commit()

    # ------------------------------------------------------------------
    # Translation API
    # ------------------------------------------------------------------

    def _translate_batch(self, texts: list[str]) -> list[str]:
        texts = [t or "" for t in texts]
        non_empty = [(i, t) for i, t in enumerate(texts) if t.strip()]
        if not non_empty:
            return texts[:]

        indices, payloads = zip(*non_empty)
        headers = {"Content-Type": "application/json"}
        if TRANSLATOR_API_KEY:
            headers["Authorization"] = f"Bearer {TRANSLATOR_API_KEY}"

        resp = httpx.post(
            f"{TRANSLATOR_URL}/translate",
            json={"texts": list(payloads), "target_lang": TARGET_LANG},
            headers=headers,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Translator returned non-JSON object: {type(data)}")
        translated = data.get("translations")
        if translated is None:
            raise ValueError(
                "Translator response missing 'translations' key. "
                f"Expected {{'translations': [...]}}. Got keys: {list(data.keys())!r}"
            )
        if len(translated) != len(payloads):
            raise ValueError(
                f"Translator returned {len(translated)} items, expected {len(payloads)}"
            )

        result = list(texts)
        for idx, tr in zip(indices, translated):
            result[idx] = tr
        return result
