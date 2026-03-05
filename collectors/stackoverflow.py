"""
Stack Overflow collector.
Uses Stack Exchange REST API v2.3.
No auth: 300 req/day. With key (STACKOVERFLOW_KEY env): 10000 req/day.
"""

from __future__ import annotations

import logging
import os
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

_SO_API = "https://api.stackexchange.com/2.3"
_MAX_AGE_DAYS = 90
_PAGE_SIZE = 100
_RATE_LIMIT_PAUSE = 1.5


@register
class StackOverflowCollector(BaseCollector):
    """
    Collects SO questions via Stack Exchange REST API.
    Prefers tag-scoped queries over full-text search.
    """

    name = "stackoverflow"

    def __init__(self) -> None:
        self._api_key = os.environ.get("STACKOVERFLOW_KEY", "")

    def discover(self, keyword: str) -> DiscoveredResources:
        """Check if tag exists and count questions."""
        tags = []
        tag_slug = keyword.lower().replace(" ", "-")
        try:
            params = {"site": "stackoverflow", "inname": tag_slug, "pagesize": 5}
            if self._api_key:
                params["key"] = self._api_key

            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{_SO_API}/tags", params=params)
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    tags = [
                        {"name": t["name"], "count": t["count"]}
                        for t in items
                    ]
        except Exception as e:
            log.debug("[so] discover failed: %s", e)
        return DiscoveredResources(tags=tags)

    def build_plan(self, profile: KeywordProfile) -> SearchPlan:
        so_resources = profile.discovered.get("stackoverflow")
        tags = so_resources.tags if so_resources else []
        targets: list[SearchTarget] = []

        for tag in tags[:3]:
            tag_name = tag.get("name", "")
            if tag_name:
                targets.append(SearchTarget(query=tag_name, scope="tag", params={"tag": tag_name}))

        if not targets:
            targets.append(SearchTarget(
                query=profile.canonical_name,
                scope="search",
                params={},
            ))

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

            if target.scope == "tag":
                signals, new_cursor = self._collect_by_tag(
                    target.params.get("tag", target.query), since_ts, plan.max_results_per_target
                )
            else:
                signals, new_cursor = self._search_questions(
                    target.query, since_ts, plan.max_results_per_target
                )

            all_signals.extend(signals)
            updated_cursors[target.target_key] = new_cursor
            time.sleep(_RATE_LIMIT_PAUSE)

        return CollectResult(signals=all_signals, updated_cursors=updated_cursors)

    def check_readiness(self) -> SourceStatus:
        try:
            params: dict[str, Any] = {"site": "stackoverflow", "pagesize": 1}
            if self._api_key:
                params["key"] = self._api_key
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{_SO_API}/questions", params=params)
                if resp.status_code == 200:
                    quota = resp.json().get("quota_remaining", "?")
                    limit_info = f"{quota} req remaining today"
                    if not self._api_key:
                        limit_info += " (max 300/day without key)"
                    else:
                        limit_info += " (max 10000/day with key)"
                    return SourceStatus(
                        source="stackoverflow",
                        ready=True,
                        limit_info=limit_info,
                        note=None if self._api_key else "Set STACKOVERFLOW_KEY for 10000 req/day",
                    )
        except Exception as e:
            return SourceStatus(source="stackoverflow", ready=False, note=str(e))
        return SourceStatus(source="stackoverflow", ready=False)

    def get_setup_guide(self) -> list[str]:
        return [
            "Stack Overflow API works without a key (300 req/day limit).",
            "To get 10000 req/day:",
            "  1. Go to https://stackapps.com/apps/oauth/register",
            "  2. Register your application",
            "  3. Copy the 'Key' value (not client secret)",
            "  4. Set: STACKOVERFLOW_KEY=<your_key> in .env",
        ]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _base_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"site": "stackoverflow", "pagesize": _PAGE_SIZE, "order": "desc", "sort": "creation"}
        if self._api_key:
            params["key"] = self._api_key
        return params

    def _collect_by_tag(
        self, tag: str, since_ts: int, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        params = {**self._base_params(), "tagged": tag, "fromdate": since_ts}
        return self._paginate_questions(f"{_SO_API}/questions", params, limit, key=f"tag:{tag}")

    def _search_questions(
        self, query: str, since_ts: int, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        params = {**self._base_params(), "intitle": query, "fromdate": since_ts}
        return self._paginate_questions(f"{_SO_API}/search", params, limit, key=query)

    def _paginate_questions(
        self, url: str, params: dict[str, Any], limit: int, key: str
    ) -> tuple[list[RawSignal], CursorState]:
        signals: list[RawSignal] = []
        page = 1
        newest: datetime | None = None

        while len(signals) < limit:
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.get(url, params={**params, "page": page})
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:
                log.warning("[so] request failed: %s", e)
                break

            for item in data.get("items", []):
                created = datetime.fromtimestamp(item.get("creation_date", 0), tz=timezone.utc)
                if newest is None:
                    newest = created
                signal = self._item_to_signal(item)
                if signal:
                    signals.append(signal)

            if not data.get("has_more", False) or len(signals) >= limit:
                break
            page += 1
            time.sleep(_RATE_LIMIT_PAUSE)

        return signals, CursorState(
            target_key=key,
            last_collected_at=newest or datetime.now(timezone.utc),
        )

    @staticmethod
    def _item_to_signal(item: dict[str, Any]) -> RawSignal | None:
        try:
            q_id = str(item.get("question_id", ""))
            created = datetime.fromtimestamp(item.get("creation_date", 0), tz=timezone.utc)
            return RawSignal(
                source="so_question",
                source_id=q_id,
                url=item.get("link", f"https://stackoverflow.com/q/{q_id}"),
                title=item.get("title", ""),
                body=item.get("body", "") or item.get("title", ""),
                author=item.get("owner", {}).get("display_name", ""),
                created_at=created,
                collected_at=datetime.now(timezone.utc),
                score=item.get("score", 0),
                comments_count=item.get("answer_count", 0),
                tags=item.get("tags", []),
                extra={"is_answered": item.get("is_answered", False)},
            )
        except Exception:
            return None
