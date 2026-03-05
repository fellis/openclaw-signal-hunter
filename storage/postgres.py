"""
PostgreSQL storage layer.
Wraps all DB operations behind a clean interface.
The rest of the codebase never touches SQL directly.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

from core.models import (
    CollectResult,
    CursorState,
    KeywordProfile,
    ProcessedSignal,
    RawSignal,
    SearchPlan,
)

log = logging.getLogger(__name__)


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    return url


class PostgresStorage:
    """
    All SQL queries live here. Uses psycopg2 with RealDictCursor.
    Connections are created per operation (not pooled) - safe for subprocess model.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self._url = database_url or _get_database_url()

    @contextmanager
    def _conn(self) -> Generator[PgConnection, None, None]:
        conn = psycopg2.connect(self._url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _cursor(self, conn: PgConnection):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ------------------------------------------------------------------
    # Raw signals
    # ------------------------------------------------------------------

    def upsert_raw_signal(self, signal: RawSignal) -> str | None:
        """
        Insert signal; skip on conflict (dedup_key already exists).
        Returns the UUID of the inserted row, or None if it was a duplicate.
        """
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO raw_signals
                        (dedup_key, source, source_id, url, title, body, author,
                         created_at, collected_at, score, comments_count, views_count,
                         tags, parent_url, extra)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedup_key) DO UPDATE SET
                        score          = EXCLUDED.score,
                        comments_count = EXCLUDED.comments_count,
                        collected_at   = EXCLUDED.collected_at,
                        extra          = jsonb_set(
                            COALESCE(raw_signals.extra, '{}'),
                            '{keywords}',
                            (
                                SELECT COALESCE(jsonb_agg(DISTINCT val), '[]')
                                FROM jsonb_array_elements_text(
                                    COALESCE(raw_signals.extra->'keywords', '[]') ||
                                    COALESCE(EXCLUDED.extra->'keywords', '[]')
                                ) AS val
                            )
                        )
                    RETURNING id, (xmax = 0) AS inserted
                    """,
                    (
                        signal.dedup_key,
                        signal.source,
                        signal.source_id,
                        signal.url,
                        signal.title,
                        signal.body,
                        signal.author,
                        signal.created_at,
                        signal.collected_at,
                        signal.score,
                        signal.comments_count,
                        signal.views_count,
                        signal.tags,
                        signal.parent_url,
                        json.dumps(signal.extra),
                    ),
                )
                row = cur.fetchone()
                return str(row["id"]) if row and row["inserted"] else None

    def fetch_unprocessed(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return raw signals that have no corresponding processed_signals row."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT r.id, r.dedup_key, r.title, r.body, r.score, r.created_at, r.extra
                    FROM raw_signals r
                    LEFT JOIN processed_signals p ON p.raw_signal_id = r.id
                    WHERE p.id IS NULL
                    ORDER BY r.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(row) for row in cur.fetchall()]

    def count_raw_signals(self) -> int:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute("SELECT COUNT(*) AS n FROM raw_signals")
                return cur.fetchone()["n"]

    # ------------------------------------------------------------------
    # Processed signals
    # ------------------------------------------------------------------

    def upsert_processed_signal(self, ps: ProcessedSignal) -> None:
        """
        Insert/update a processed signal.
        Also creates an embedding_queue entry (Outbox pattern).
        Both writes are in a single transaction.
        """
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                matched_rules_json = json.dumps(
                    [
                        {
                            "rule_name": r.rule_name,
                            "confidence": r.confidence,
                            "evidence": r.evidence,
                        }
                        for r in ps.matched_rules
                    ]
                )
                cur.execute(
                    """
                    INSERT INTO processed_signals
                        (raw_signal_id, dedup_key, is_relevant, matched_rules, summary,
                         products_mentioned, intensity, confidence, keywords_matched,
                         language, rank_score, linked_group_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedup_key) DO UPDATE SET
                        is_relevant      = EXCLUDED.is_relevant,
                        matched_rules    = EXCLUDED.matched_rules,
                        summary          = EXCLUDED.summary,
                        products_mentioned = EXCLUDED.products_mentioned,
                        intensity        = EXCLUDED.intensity,
                        confidence       = EXCLUDED.confidence,
                        keywords_matched = EXCLUDED.keywords_matched,
                        language         = EXCLUDED.language,
                        rank_score       = EXCLUDED.rank_score
                    """,
                    (
                        ps.raw_signal_id,
                        ps.dedup_key,
                        ps.is_relevant,
                        matched_rules_json,
                        ps.summary,
                        ps.products_mentioned,
                        ps.intensity,
                        ps.confidence,
                        ps.keywords_matched,
                        ps.language,
                        ps.rank_score,
                        ps.linked_group_id,
                    ),
                )
                if ps.is_relevant and ps.summary:
                    cur.execute(
                        """
                        INSERT INTO embedding_queue (dedup_key)
                        VALUES (%s)
                        ON CONFLICT (dedup_key) DO NOTHING
                        """,
                        (ps.dedup_key,),
                    )

    def fetch_pending_embeddings(self, limit: int = 256) -> list[dict[str, Any]]:
        """Return processed signals pending embedding, joined with raw_signals metadata."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT
                        p.raw_signal_id,
                        p.dedup_key,
                        p.summary,
                        p.matched_rules,
                        p.intensity,
                        p.rank_score,
                        r.url,
                        r.title,
                        r.created_at,
                        eq.id AS queue_id
                    FROM embedding_queue eq
                    JOIN processed_signals p ON p.dedup_key = eq.dedup_key
                    JOIN raw_signals r ON r.id = p.raw_signal_id
                    WHERE eq.status = 'pending'
                    ORDER BY eq.created_at
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(row) for row in cur.fetchall()]

    def mark_embedding_done(self, queue_id: str) -> None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "UPDATE embedding_queue SET status='done', last_attempt_at=%s WHERE id=%s",
                    (datetime.now(timezone.utc), queue_id),
                )

    def mark_embedding_failed(self, queue_id: str) -> None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    UPDATE embedding_queue
                    SET status = CASE WHEN attempts + 1 >= 3 THEN 'failed' ELSE status END,
                        attempts = attempts + 1,
                        last_attempt_at = %s
                    WHERE id = %s
                    """,
                    (datetime.now(timezone.utc), queue_id),
                )

    def count_processed(self) -> dict[str, int]:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE is_relevant) AS relevant,
                        COUNT(*) FILTER (WHERE NOT is_relevant) AS irrelevant
                    FROM processed_signals
                    """
                )
                row = dict(cur.fetchone())
                cur.execute("SELECT COUNT(*) AS n FROM embedding_queue WHERE status='pending'")
                row["embed_pending"] = cur.fetchone()["n"]
                return row

    # ------------------------------------------------------------------
    # Collection cursors
    # ------------------------------------------------------------------

    def get_cursors(self, collector_name: str) -> dict[str, CursorState]:
        """Return all cursors for a collector, keyed by target_key."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "SELECT target_key, last_collected_at, last_cursor FROM collection_cursors WHERE collector_name=%s",
                    (collector_name,),
                )
                return {
                    row["target_key"]: CursorState(
                        target_key=row["target_key"],
                        last_collected_at=row["last_collected_at"],
                        last_cursor=row["last_cursor"],
                    )
                    for row in cur.fetchall()
                }

    def save_cursors(
        self, collector_name: str, cursors: dict[str, CursorState]
    ) -> None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                for target_key, state in cursors.items():
                    cur.execute(
                        """
                        INSERT INTO collection_cursors
                            (collector_name, target_key, last_collected_at, last_cursor)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (collector_name, target_key) DO UPDATE SET
                            last_collected_at = EXCLUDED.last_collected_at,
                            last_cursor = EXCLUDED.last_cursor
                        """,
                        (
                            collector_name,
                            target_key,
                            state.last_collected_at,
                            state.last_cursor,
                        ),
                    )

    # ------------------------------------------------------------------
    # Keyword profiles
    # ------------------------------------------------------------------

    def save_keyword_profile(self, profile: KeywordProfile) -> None:
        import dataclasses  # noqa: PLC0415

        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO keyword_profiles
                        (canonical_name, raw, keyword_type, description, profile_data, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (canonical_name) DO UPDATE SET
                        raw = EXCLUDED.raw,
                        keyword_type = EXCLUDED.keyword_type,
                        description = EXCLUDED.description,
                        profile_data = EXCLUDED.profile_data,
                        updated_at = now()
                    """,
                    (
                        profile.canonical_name,
                        profile.raw,
                        profile.keyword_type.value,
                        profile.description,
                        json.dumps(dataclasses.asdict(profile)),
                    ),
                )

    def get_keyword_profile(self, canonical_name: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "SELECT profile_data FROM keyword_profiles WHERE canonical_name=%s",
                    (canonical_name,),
                )
                row = cur.fetchone()
                return row["profile_data"] if row else None

    def list_keyword_profiles(self) -> list[str]:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute("SELECT canonical_name FROM keyword_profiles ORDER BY canonical_name")
                return [row["canonical_name"] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Collection plans
    # ------------------------------------------------------------------

    def save_collection_plan(
        self, canonical_name: str, collector_name: str, plan: SearchPlan
    ) -> None:
        import dataclasses  # noqa: PLC0415

        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO keyword_collection_plans
                        (canonical_name, collector_name, plan_data, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (canonical_name, collector_name) DO UPDATE SET
                        plan_data = EXCLUDED.plan_data,
                        updated_at = now()
                    """,
                    (canonical_name, collector_name, json.dumps(dataclasses.asdict(plan))),
                )

    def get_collection_plans(self, canonical_name: str) -> dict[str, Any]:
        """Return {collector_name: plan_data} for a keyword."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "SELECT collector_name, plan_data FROM keyword_collection_plans WHERE canonical_name=%s",
                    (canonical_name,),
                )
                return {row["collector_name"]: row["plan_data"] for row in cur.fetchall()}

    def get_all_active_plans(self) -> list[dict[str, Any]]:
        """Return all approved plans for all keywords."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "SELECT canonical_name, collector_name, plan_data FROM keyword_collection_plans ORDER BY canonical_name"
                )
                return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # LLM usage logging
    # ------------------------------------------------------------------

    def log_llm_usage(
        self,
        provider: str,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO llm_usage_log
                        (provider, operation, model, input_tokens, output_tokens, cost_usd)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (provider, operation, model, input_tokens, output_tokens, cost_usd),
                )

    def get_monthly_llm_cost(self) -> dict[str, float]:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT provider, SUM(cost_usd) AS total
                    FROM llm_usage_log
                    WHERE logged_at >= date_trunc('month', now())
                    GROUP BY provider
                    """
                )
                result = {row["provider"]: float(row["total"]) for row in cur.fetchall()}
                result["total"] = sum(result.values())
                return result

    # ------------------------------------------------------------------
    # Top signals query (for CLI query output)
    # ------------------------------------------------------------------

    def query_top_signals(
        self,
        limit: int = 20,
        keyword: str | None = None,
        rule_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return top signals by rank_score, with raw_signal metadata joined."""
        conditions = ["p.is_relevant = true"]
        params: list[Any] = []

        if keyword:
            conditions.append("%s = ANY(p.keywords_matched)")
            params.append(keyword)

        if rule_name:
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(p.matched_rules) r WHERE r->>'rule_name' = %s)"
            )
            params.append(rule_name)

        where = " AND ".join(conditions)
        params.append(limit)

        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    f"""
                    SELECT
                        p.rank_score,
                        p.intensity,
                        p.confidence,
                        p.matched_rules,
                        p.summary,
                        p.language,
                        r.url,
                        r.title,
                        r.score AS engagement_score,
                        r.created_at
                    FROM processed_signals p
                    JOIN raw_signals r ON r.id = p.raw_signal_id
                    WHERE {where}
                    ORDER BY p.rank_score DESC
                    LIMIT %s
                    """,
                    params,
                )
                return [dict(row) for row in cur.fetchall()]

    def get_status_summary(self) -> dict[str, Any]:
        raw = self.count_raw_signals()
        processed = self.count_processed()
        llm_cost = self.get_monthly_llm_cost()
        keywords = self.list_keyword_profiles()
        oldest = self._get_oldest_signal_date()
        return {
            "keywords": keywords,
            "signals": {
                "total_raw": raw,
                "processed": processed.get("total", 0),
                "relevant": processed.get("relevant", 0),
                "irrelevant": processed.get("irrelevant", 0),
                "embed_pending": processed.get("embed_pending", 0),
                "unprocessed": raw - processed.get("total", 0),
            },
            "llm_cost_month_usd": llm_cost,
            "retention": {"oldest_signal": oldest},
        }

    def _get_oldest_signal_date(self) -> str | None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute("SELECT MIN(created_at) AS oldest FROM raw_signals")
                row = cur.fetchone()
                val = row["oldest"] if row else None
                return val.isoformat() if val else None

    # ------------------------------------------------------------------
    # Reprocess support
    # ------------------------------------------------------------------

    def get_raw_signal_ids_for_keyword(
        self, keyword: str, rule_names: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Return raw_signal ids + dedup_keys for signals matching keyword.
        If rule_names given, also filter by matched rule.
        """
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                if rule_names:
                    rule_filter = " AND (" + " OR ".join(
                        ["EXISTS (SELECT 1 FROM jsonb_array_elements(p.matched_rules) r WHERE r->>'rule_name' = %s)"] * len(rule_names)
                    ) + ")"
                    params: list[Any] = [keyword] + rule_names
                else:
                    rule_filter = ""
                    params = [keyword]

                cur.execute(
                    f"""
                    SELECT p.raw_signal_id, p.dedup_key
                    FROM processed_signals p
                    WHERE %s = ANY(p.keywords_matched)
                    {rule_filter}
                    """,
                    params,
                )
                return [dict(row) for row in cur.fetchall()]

    def delete_processed_signals(self, raw_signal_ids: list[str]) -> int:
        """Delete processed signals by raw_signal_id list. Returns count deleted."""
        if not raw_signal_ids:
            return 0
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "DELETE FROM processed_signals WHERE raw_signal_id = ANY(%s::uuid[])",
                    (raw_signal_ids,),
                )
                return cur.rowcount

    def get_raw_signals_by_ids(
        self, raw_signal_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch raw signals for re-queuing (reprocess)."""
        if not raw_signal_ids:
            return []
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT id, dedup_key, body, score, created_at
                    FROM raw_signals
                    WHERE id = ANY(%s::uuid[])
                    """,
                    (raw_signal_ids,),
                )
                return [dict(row) for row in cur.fetchall()]

    def fetch_raw_sample(
        self, keyword: str | None = None, limit: int = 300
    ) -> list[dict[str, Any]]:
        """
        Return a representative sample of raw signals (for suggest_rules).
        If keyword given, filter by signals that have a processed record for that keyword
        OR just return latest raw signals.
        """
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                if keyword:
                    cur.execute(
                        """
                        SELECT r.id, r.dedup_key, r.title, r.body, r.url, r.source, r.score
                        FROM raw_signals r
                        JOIN processed_signals p ON p.raw_signal_id = r.id
                        WHERE %s = ANY(p.keywords_matched)
                        ORDER BY r.score DESC, r.created_at DESC
                        LIMIT %s
                        """,
                        (keyword, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, dedup_key, title, body, url, source, score
                        FROM raw_signals
                        ORDER BY score DESC, created_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Change report snapshots
    # ------------------------------------------------------------------

    def save_change_report_snapshot(
        self,
        keyword: str,
        period_start: datetime,
        period_end: datetime,
        report_text: str,
        signal_count: int,
    ) -> None:
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO change_report_snapshots
                        (keyword, generated_at, period_start, period_end, report_text, signal_count)
                    VALUES (%s, now(), %s, %s, %s, %s)
                    """,
                    (keyword, period_start, period_end, report_text, signal_count),
                )

    def get_last_report_at(self, keyword: str) -> datetime | None:
        """Return generated_at of the most recent report for a keyword."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    "SELECT MAX(generated_at) AS last_at FROM change_report_snapshots WHERE keyword=%s",
                    (keyword,),
                )
                row = cur.fetchone()
                return row["last_at"] if row else None

    def fetch_new_signals_since(
        self, keyword: str, since: datetime, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Return processed signals for keyword created after 'since'."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT
                        p.rank_score, p.intensity, p.matched_rules, p.summary,
                        r.url, r.title, r.score AS engagement_score, r.created_at,
                        r.source
                    FROM processed_signals p
                    JOIN raw_signals r ON r.id = p.raw_signal_id
                    WHERE p.is_relevant = true
                      AND %s = ANY(p.keywords_matched)
                      AND r.created_at > %s
                    ORDER BY p.rank_score DESC
                    LIMIT %s
                    """,
                    (keyword, since, limit),
                )
                return [dict(row) for row in cur.fetchall()]

    def count_signals_by_rule(
        self, keyword: str, since: datetime | None = None
    ) -> dict[str, int]:
        """Count signals grouped by primary matched rule for a keyword."""
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                time_filter = "AND r.created_at > %s" if since else ""
                params: list[Any] = [keyword]
                if since:
                    params.append(since)

                cur.execute(
                    f"""
                    SELECT
                        (p.matched_rules->0->>'rule_name') AS rule_name,
                        COUNT(*) AS cnt
                    FROM processed_signals p
                    JOIN raw_signals r ON r.id = p.raw_signal_id
                    WHERE p.is_relevant = true
                      AND %s = ANY(p.keywords_matched)
                      AND jsonb_array_length(p.matched_rules) > 0
                      {time_filter}
                    GROUP BY rule_name
                    ORDER BY cnt DESC
                    """,
                    params,
                )
                return {row["rule_name"]: row["cnt"] for row in cur.fetchall() if row["rule_name"]}

    # ------------------------------------------------------------------
    # Plan management (update_plan support)
    # ------------------------------------------------------------------

    def update_collection_plan(
        self,
        canonical_name: str,
        collector_name: str,
        add_targets: list[dict[str, Any]],
        remove_queries: list[str],
    ) -> None:
        """Add or remove targets in an existing plan. Idempotent."""
        import dataclasses  # noqa: PLC0415

        existing = self.get_collection_plans(canonical_name).get(collector_name)
        if not existing:
            raise ValueError(f"No plan found for {canonical_name}/{collector_name}")

        targets = existing.get("targets", [])

        # Remove targets by query string match
        if remove_queries:
            targets = [t for t in targets if t.get("query") not in remove_queries]

        # Add new targets (dedup by query+scope)
        existing_keys = {(t.get("query"), t.get("scope")) for t in targets}
        for new_t in add_targets:
            key = (new_t.get("query"), new_t.get("scope"))
            if key not in existing_keys:
                targets.append(new_t)
                existing_keys.add(key)

        existing["targets"] = targets
        with self._conn() as conn:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    UPDATE keyword_collection_plans
                    SET plan_data = %s, updated_at = now()
                    WHERE canonical_name = %s AND collector_name = %s
                    """,
                    (json.dumps(existing), canonical_name, collector_name),
                )
