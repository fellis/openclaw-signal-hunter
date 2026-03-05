"""
Hacker News collector.
Uses Algolia Search API (no auth required, generous limits).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from core.models import (
    CollectResult,
    CursorState,
    DiscoveredResources,
    KeywordProfile,
    RawSignal,
    SearchPlan,
    SearchTarget,
    SourceStatus,
)
from core.registry import BaseCollector, register

log = logging.getLogger(__name__)

_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
_MAX_AGE_DAYS = 90
_PAGE_SIZE = 50
_RATE_LIMIT_PAUSE = 0.5


@register
class HackerNewsCollector(BaseCollector):
    """
    Collects HN posts and comments via Algolia search API.
    No authentication required.
    """

    name = "hackernews"

    def discover(self, keyword: str) -> DiscoveredResources:
        """Count HN threads mentioning keyword."""
        try:
            since = int((datetime.now(timezone.utc) - timedelta(days=90)).timestamp())
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    f"{_ALGOLIA_BASE}/search",
                    params={
                        "query": keyword,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{since}",
                        "hitsPerPage": 1,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    count = data.get("nbHits", 0)
                    return DiscoveredResources(
                        threads=[{"query": keyword, "threads_90d": count}]
                    )
        except Exception as e:
            log.debug("[hn] discover failed: %s", e)
        return DiscoveredResources()

    def build_plan(self, profile: KeywordProfile) -> SearchPlan:
        queries = [profile.canonical_name] + profile.aliases[:2]
        targets = [
            SearchTarget(query=q, scope="search", params={})
            for q in queries
        ]
        return SearchPlan(targets=targets, max_results_per_target=200)

    def collect(
        self, plan: SearchPlan, cursors: dict[str, CursorState]
    ) -> CollectResult:
        all_signals: list[RawSignal] = []
        updated_cursors: dict[str, CursorState] = {}

        for target in plan.targets:
            cursor = cursors.get(target.target_key)
            since_ts = int(cursor.last_collected_at.timestamp()) if cursor and cursor.last_collected_at else \
                int((datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)).timestamp())

            signals, new_cursor = self._search(target.query, since_ts, plan.max_results_per_target)
            all_signals.extend(signals)
            updated_cursors[target.target_key] = new_cursor
            time.sleep(_RATE_LIMIT_PAUSE)

        return CollectResult(signals=all_signals, updated_cursors=updated_cursors)

    def check_readiness(self) -> SourceStatus:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{_ALGOLIA_BASE}/search", params={"query": "test", "hitsPerPage": 1})
                if resp.status_code == 200:
                    return SourceStatus(
                        source="hackernews",
                        ready=True,
                        limit_info="No auth required, generous limits",
                    )
        except Exception as e:
            return SourceStatus(source="hackernews", ready=False, note=str(e))
        return SourceStatus(source="hackernews", ready=False)

    def get_setup_guide(self) -> list[str]:
        return [
            "Hacker News via Algolia API requires no authentication.",
            "API docs: https://hn.algolia.com/api",
            "No setup needed - it just works.",
        ]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _search(
        self, query: str, since_ts: int, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        signals: list[RawSignal] = []
        page = 0
        newest: datetime | None = None

        while len(signals) < limit:
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.get(
                        f"{_ALGOLIA_BASE}/search",
                        params={
                            "query": query,
                            "tags": "story",
                            "numericFilters": f"created_at_i>{since_ts}",
                            "hitsPerPage": _PAGE_SIZE,
                            "page": page,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:
                log.warning("[hn] search page %d failed: %s", page, e)
                break

            hits = data.get("hits", [])
            if not hits:
                break

            for hit in hits:
                created = datetime.fromtimestamp(
                    hit.get("created_at_i", 0), tz=timezone.utc
                )
                if newest is None:
                    newest = created
                signal = self._hit_to_signal(hit)
                if signal:
                    signals.append(signal)

            if page >= data.get("nbPages", 1) - 1 or len(signals) >= limit:
                break
            page += 1
            time.sleep(_RATE_LIMIT_PAUSE)

        return signals, CursorState(
            target_key=query,
            last_collected_at=newest or datetime.now(timezone.utc),
        )

    @staticmethod
    def _hit_to_signal(hit: dict[str, Any]) -> RawSignal | None:
        try:
            story_id = hit.get("objectID", "")
            created = datetime.fromtimestamp(hit.get("created_at_i", 0), tz=timezone.utc)
            return RawSignal(
                source="hn_post",
                source_id=story_id,
                url=hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                title=hit.get("title", ""),
                body=hit.get("story_text") or "",
                author=hit.get("author", ""),
                created_at=created,
                collected_at=datetime.now(timezone.utc),
                score=hit.get("points", 0),
                comments_count=hit.get("num_comments", 0),
                tags=["hn"],
                extra={"hn_id": story_id},
            )
        except Exception:
            return None
