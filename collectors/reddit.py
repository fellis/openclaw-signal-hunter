"""
Reddit collector stub.
Collects posts and comments from subreddits using Reddit's JSON API.
No auth required for public subreddits (rate limit: ~10 req/min).
For higher limits, use PRAW OAuth (60 req/min).
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

_REDDIT_BASE = "https://www.reddit.com"
_HEADERS = {"User-Agent": "signal-hunter/0.1 (research tool)"}
_RATE_LIMIT_PAUSE = 2.0
_MAX_AGE_DAYS = 90
_PAGE_SIZE = 100


@register
class RedditCollector(BaseCollector):
    """
    Collects Reddit posts via JSON API.
    Supports subreddit_hot, subreddit_new, subreddit_search scopes.
    """

    name = "reddit"

    def discover(self, keyword: str) -> DiscoveredResources:
        """Check if subreddit r/<keyword> exists and search for mentions."""
        subreddits = []
        mentioned_in = []

        # Try direct subreddit
        try:
            with httpx.Client(headers=_HEADERS, timeout=10) as client:
                resp = client.get(f"{_REDDIT_BASE}/r/{keyword}/about.json")
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    subreddits.append({
                        "name": keyword,
                        "subscribers": data.get("subscribers", 0),
                        "description": data.get("public_description", ""),
                    })
        except Exception as e:
            log.debug("[reddit] discover subreddit check failed: %s", e)

        # Search for mentions in popular subs
        try:
            with httpx.Client(headers=_HEADERS, timeout=10) as client:
                resp = client.get(
                    f"{_REDDIT_BASE}/search.json",
                    params={"q": keyword, "sort": "relevance", "limit": 5, "type": "link"},
                )
                if resp.status_code == 200:
                    items = resp.json().get("data", {}).get("children", [])
                    seen_subs: dict[str, int] = {}
                    for item in items:
                        sub = item.get("data", {}).get("subreddit", "")
                        seen_subs[sub] = seen_subs.get(sub, 0) + 1
                    mentioned_in = [{"name": k, "count": v} for k, v in seen_subs.items()]
        except Exception as e:
            log.debug("[reddit] discover search failed: %s", e)

        return DiscoveredResources(subreddits=subreddits, extra={"mentioned_in": mentioned_in})

    def build_plan(self, profile: KeywordProfile) -> SearchPlan:
        """Build subreddit targets from discovered resources."""
        reddit_resources = profile.discovered.get("reddit")
        subreddits = reddit_resources.subreddits if reddit_resources else []
        targets: list[SearchTarget] = []

        for sub in subreddits[:3]:
            sub_name = sub.get("name", "")
            if not sub_name:
                continue
            targets.append(SearchTarget(query="", scope="subreddit_new", params={"sub": sub_name}))
            targets.append(SearchTarget(
                query=profile.canonical_name,
                scope="subreddit_search",
                params={"sub": sub_name},
            ))

        if not targets:
            targets.append(SearchTarget(
                query=profile.canonical_name,
                scope="global_search",
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
            since = cursor.last_collected_at if cursor else None

            try:
                if target.scope in ("subreddit_hot", "subreddit_new"):
                    signals, new_cursor = self._collect_subreddit(
                        target.params.get("sub", ""), target.scope, since, plan.max_results_per_target
                    )
                elif target.scope == "subreddit_search":
                    signals, new_cursor = self._search_subreddit(
                        target.params.get("sub", ""), target.query, since, plan.max_results_per_target
                    )
                elif target.scope == "global_search":
                    signals, new_cursor = self._global_search(
                        target.query, since, plan.max_results_per_target
                    )
                else:
                    log.warning("[reddit] unknown scope: %s", target.scope)
                    continue

                all_signals.extend(signals)
                updated_cursors[target.target_key] = new_cursor
                time.sleep(_RATE_LIMIT_PAUSE)
            except Exception as e:
                log.warning("[reddit] collect failed for %s: %s", target.scope, e)

        return CollectResult(signals=all_signals, updated_cursors=updated_cursors)

    def check_readiness(self) -> SourceStatus:
        try:
            with httpx.Client(headers=_HEADERS, timeout=10) as client:
                resp = client.get(f"{_REDDIT_BASE}/r/LocalLLaMA/about.json")
                if resp.status_code == 200:
                    return SourceStatus(
                        source="reddit",
                        ready=True,
                        limit_info="~10 req/min (public JSON API, no auth needed)",
                    )
                return SourceStatus(
                    source="reddit",
                    ready=False,
                    note=f"API returned {resp.status_code}",
                )
        except Exception as e:
            return SourceStatus(source="reddit", ready=False, note=str(e))

    def get_setup_guide(self) -> list[str]:
        return [
            "Reddit public JSON API requires no authentication for read-only access.",
            "Rate limit: ~10 req/min with User-Agent header (enforced).",
            "For higher limits (60 req/min), register a Reddit app:",
            "  1. Go to https://www.reddit.com/prefs/apps",
            "  2. Scroll down, click 'create another app'",
            "  3. Name: signal-hunter, type: script, redirect: http://localhost",
            "  4. Note client_id (under app name) and client_secret",
            "  5. Set: REDDIT_CLIENT_ID=<id> REDDIT_CLIENT_SECRET=<secret> in .env",
            "(Optional PRAW integration not yet implemented - current limit sufficient for most use cases)",
        ]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _collect_subreddit(
        self, sub: str, scope: str, since: datetime | None, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        sort = "new" if scope == "subreddit_new" else "hot"
        url = f"{_REDDIT_BASE}/r/{sub}/{sort}.json"
        min_age = datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)
        signals: list[RawSignal] = []
        after = None
        newest: datetime | None = None

        while len(signals) < limit:
            params = {"limit": min(100, limit - len(signals))}
            if after:
                params["after"] = after

            try:
                with httpx.Client(headers=_HEADERS, timeout=15) as client:
                    resp = client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json().get("data", {})
            except Exception as e:
                log.warning("[reddit] subreddit %s/%s failed: %s", sub, sort, e)
                break

            items = data.get("children", [])
            if not items:
                break

            for item in items:
                post = item.get("data", {})
                created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
                if created < min_age:
                    return signals, self._make_cursor(sub, newest)
                if since and created <= since:
                    return signals, self._make_cursor(sub, newest)
                if newest is None:
                    newest = created

                signal = self._post_to_signal(post, sub)
                if signal:
                    signals.append(signal)

            after = data.get("after")
            if not after:
                break
            time.sleep(_RATE_LIMIT_PAUSE)

        return signals, self._make_cursor(sub, newest)

    def _search_subreddit(
        self, sub: str, query: str, since: datetime | None, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        url = f"{_REDDIT_BASE}/r/{sub}/search.json"
        params = {"q": query, "restrict_sr": "on", "sort": "new", "limit": min(100, limit)}
        signals: list[RawSignal] = []
        newest: datetime | None = None

        try:
            with httpx.Client(headers=_HEADERS, timeout=15) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("children", [])
                for item in items:
                    post = item.get("data", {})
                    created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
                    if newest is None:
                        newest = created
                    signal = self._post_to_signal(post, sub)
                    if signal:
                        signals.append(signal)
        except Exception as e:
            log.warning("[reddit] subreddit search failed %s: %s", sub, e)

        return signals, self._make_cursor(f"{sub}:{query}", newest)

    def _global_search(
        self, query: str, since: datetime | None, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        url = f"{_REDDIT_BASE}/search.json"
        params = {"q": query, "sort": "new", "type": "link", "limit": min(100, limit)}
        signals: list[RawSignal] = []
        newest: datetime | None = None

        try:
            with httpx.Client(headers=_HEADERS, timeout=15) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("children", [])
                for item in items:
                    post = item.get("data", {})
                    created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
                    if newest is None:
                        newest = created
                    signal = self._post_to_signal(post, None)
                    if signal:
                        signals.append(signal)
        except Exception as e:
            log.warning("[reddit] global search failed for '%s': %s", query, e)

        return signals, self._make_cursor(query, newest)

    @staticmethod
    def _post_to_signal(post: dict[str, Any], sub: str | None) -> RawSignal | None:
        try:
            post_id = post.get("id", "")
            subreddit = post.get("subreddit", sub or "")
            created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
            return RawSignal(
                source="reddit_post",
                source_id=post_id,
                url=f"https://reddit.com{post.get('permalink', '')}",
                title=post.get("title", ""),
                body=post.get("selftext") or post.get("url", ""),
                author=post.get("author", ""),
                created_at=created,
                collected_at=datetime.now(timezone.utc),
                score=post.get("score", 0),
                comments_count=post.get("num_comments", 0),
                tags=[post.get("link_flair_text")] if post.get("link_flair_text") else [],
                extra={"subreddit": subreddit},
            )
        except Exception:
            return None

    @staticmethod
    def _make_cursor(key: str, newest: datetime | None) -> CursorState:
        return CursorState(
            target_key=key,
            last_collected_at=newest or datetime.now(timezone.utc),
        )
