"""
Hugging Face collector.
Collects model/space discussions from Hugging Face Hub.
No auth required for public content; HF_TOKEN env gives higher rate limits.
API docs: https://huggingface.co/docs/hub/api
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

_HF_API = "https://huggingface.co/api"
_MAX_AGE_DAYS = 90
_RATE_LIMIT_PAUSE = 1.0
_MODEL_LIMIT = 10
_DISCUSSION_LIMIT = 100


@register
class HuggingFaceCollector(BaseCollector):
    """
    Collects Hugging Face model and space discussions.
    Discovery: top models + spaces by downloads/likes for the keyword.
    Collection: discussion threads from each discovered model/space.
    """

    name = "huggingface"

    def __init__(self) -> None:
        token = os.environ.get("HF_TOKEN", "")
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def discover(self, keyword: str) -> DiscoveredResources:
        """
        Find top models and spaces related to the keyword.
        Returns DiscoveredResources with repos (models + spaces).
        """
        log.info("[hf] discover: keyword='%s'", keyword)
        repos: list[dict[str, Any]] = []

        repos.extend(self._search_models(keyword, limit=_MODEL_LIMIT))
        time.sleep(_RATE_LIMIT_PAUSE)
        repos.extend(self._search_spaces(keyword, limit=5))

        return DiscoveredResources(repos=repos)

    def build_plan(self, profile: KeywordProfile) -> SearchPlan:
        """
        Build discussion-fetch targets for each discovered model/space.
        One target per model/space: fetch its open discussion threads.
        """
        hf_resources = profile.discovered.get("huggingface")
        repos: list[dict] = hf_resources.repos if hf_resources else []

        targets: list[SearchTarget] = []
        for repo in repos[:10]:
            repo_id = repo.get("id", "")
            if not repo_id:
                continue
            targets.append(SearchTarget(
                query=repo_id,
                scope="discussions",
                params={"repo_id": repo_id, "repo_type": repo.get("type", "model")},
            ))

        if not targets:
            # Fallback: global paper search
            targets.append(SearchTarget(
                query=profile.canonical_name,
                scope="papers",
                params={},
            ))

        return SearchPlan(targets=targets, max_results_per_target=_DISCUSSION_LIMIT)

    def discover_new_sources(
        self,
        profile: KeywordProfile,
        existing_plan: SearchPlan,
    ) -> list[SearchTarget]:
        """
        Search HF Hub for models and spaces not yet in the existing plan.
        Uses canonical_name + aliases from the enriched profile.
        Returns new discussion targets only.
        """
        existing_repo_ids = {
            t.params.get("repo_id", "")
            for t in existing_plan.targets
            if t.scope == "discussions"
        }

        queries = [profile.canonical_name] + profile.aliases[:2]
        found: dict[str, dict] = {}

        for q in queries:
            for repo in self._search_models(q, limit=_MODEL_LIMIT):
                rid = repo.get("id", "")
                if rid and rid not in existing_repo_ids and rid not in found:
                    found[rid] = repo
            time.sleep(_RATE_LIMIT_PAUSE)
            for repo in self._search_spaces(q, limit=5):
                rid = repo.get("id", "")
                if rid and rid not in existing_repo_ids and rid not in found:
                    found[rid] = repo
            time.sleep(_RATE_LIMIT_PAUSE)

        new_targets = [
            SearchTarget(
                query=rid,
                scope="discussions",
                params={"repo_id": rid, "repo_type": repo.get("type", "model")},
            )
            for rid, repo in found.items()
        ]

        if new_targets:
            log.info(
                "[hf] discover_new_sources: %d new space/model(s) for '%s'",
                len(new_targets), profile.canonical_name,
            )
        return new_targets

    def collect(
        self, plan: SearchPlan, cursors: dict[str, CursorState]
    ) -> CollectResult:
        all_signals: list[RawSignal] = []
        updated_cursors: dict[str, CursorState] = {}

        for target in plan.targets:
            cursor = cursors.get(target.target_key)
            since = cursor.last_collected_at if cursor else None

            try:
                if target.scope == "discussions":
                    signals, new_cursor = self._collect_discussions(
                        target.params["repo_id"],
                        target.params.get("repo_type", "model"),
                        since,
                        plan.max_results_per_target,
                    )
                elif target.scope == "papers":
                    signals, new_cursor = self._collect_papers(
                        target.query, since, plan.max_results_per_target
                    )
                else:
                    log.warning("[hf] unknown scope: %s", target.scope)
                    continue

                all_signals.extend(signals)
                updated_cursors[target.target_key] = new_cursor
                time.sleep(_RATE_LIMIT_PAUSE)

            except Exception as e:
                log.warning("[hf] collect failed for %s: %s", target.query, e)

        return CollectResult(signals=all_signals, updated_cursors=updated_cursors)

    def check_readiness(self) -> SourceStatus:
        try:
            with httpx.Client(headers=self._headers, timeout=10) as client:
                resp = client.get(f"{_HF_API}/models", params={"search": "test", "limit": 1})
                if resp.status_code == 200:
                    has_token = "Authorization" in self._headers
                    return SourceStatus(
                        source="huggingface",
                        ready=True,
                        limit_info="public API (rate limited)" if not has_token else "authenticated (higher limits)",
                        note=None if has_token else "Set HF_TOKEN for higher rate limits",
                    )
                return SourceStatus(
                    source="huggingface",
                    ready=False,
                    note=f"API returned {resp.status_code}",
                )
        except Exception as e:
            return SourceStatus(source="huggingface", ready=False, note=str(e))

    def get_setup_guide(self) -> list[str]:
        return [
            "Hugging Face API works without a token for public content.",
            "For higher rate limits, get a free token:",
            "  1. Go to https://huggingface.co/settings/tokens",
            "  2. Click 'New token'",
            "  3. Name: signal-hunter, Role: read",
            "  4. Copy the token",
            "  5. Set credentials: sh_set_credentials huggingface {\"api_token\": \"hf_xxx\"}",
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _search_models(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        """Search HF Hub for models related to the keyword."""
        try:
            with httpx.Client(headers=self._headers, timeout=15) as client:
                resp = client.get(
                    f"{_HF_API}/models",
                    params={
                        "search": keyword,
                        "sort": "downloads",
                        "direction": -1,
                        "limit": limit,
                    },
                )
                resp.raise_for_status()
                return [
                    {
                        "id": m.get("id", ""),
                        "type": "model",
                        "downloads": m.get("downloads", 0),
                        "likes": m.get("likes", 0),
                    }
                    for m in resp.json()
                    if m.get("id")
                ]
        except Exception as e:
            log.warning("[hf] search_models failed: %s", e)
            return []

    def _search_spaces(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        """Search HF Hub for spaces related to the keyword."""
        try:
            with httpx.Client(headers=self._headers, timeout=15) as client:
                resp = client.get(
                    f"{_HF_API}/spaces",
                    params={
                        "search": keyword,
                        "sort": "likes",
                        "direction": -1,
                        "limit": limit,
                    },
                )
                resp.raise_for_status()
                return [
                    {
                        "id": s.get("id", ""),
                        "type": "space",
                        "likes": s.get("likes", 0),
                    }
                    for s in resp.json()
                    if s.get("id")
                ]
        except Exception as e:
            log.warning("[hf] search_spaces failed: %s", e)
            return []

    def _collect_discussions(
        self,
        repo_id: str,
        repo_type: str,
        since: datetime | None,
        limit: int,
    ) -> tuple[list[RawSignal], CursorState]:
        """Fetch open discussion threads for a model or space."""
        url = f"{_HF_API}/{repo_type}s/{repo_id}/discussions"
        min_age = datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)
        signals: list[RawSignal] = []
        newest: datetime | None = None
        page = 1

        while len(signals) < limit:
            try:
                with httpx.Client(headers=self._headers, timeout=15) as client:
                    resp = client.get(url, params={"p": page, "type": "discussion"})
                    if resp.status_code == 404:
                        break
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:
                log.warning("[hf] discussions %s page %d failed: %s", repo_id, page, e)
                break

            items = data.get("discussions", [])
            if not items:
                break

            for item in items:
                created_str = item.get("createdAt", "")
                created = self._parse_dt(created_str)
                if not created:
                    continue
                if created < min_age:
                    return signals, self._make_cursor(repo_id, newest)
                if since and created <= since:
                    return signals, self._make_cursor(repo_id, newest)
                if newest is None:
                    newest = created

                signal = self._discussion_to_signal(item, repo_id, repo_type)
                if signal:
                    signals.append(signal)

                if len(signals) >= limit:
                    break

            if not data.get("numTotalItems", 0) > page * len(items):
                break
            page += 1
            time.sleep(_RATE_LIMIT_PAUSE)

        return signals, self._make_cursor(repo_id, newest)

    def _collect_papers(
        self, query: str, since: datetime | None, limit: int
    ) -> tuple[list[RawSignal], CursorState]:
        """Search HF Papers for keyword."""
        signals: list[RawSignal] = []
        newest: datetime | None = None

        try:
            with httpx.Client(headers=self._headers, timeout=15) as client:
                resp = client.get(
                    f"{_HF_API}/papers",
                    params={"q": query, "limit": min(limit, 20)},
                )
                if resp.status_code != 200:
                    return signals, self._make_cursor(query, None)

                items = resp.json() if isinstance(resp.json(), list) else resp.json().get("papers", [])
                for item in items:
                    published = self._parse_dt(item.get("publishedAt", ""))
                    if newest is None and published:
                        newest = published
                    signal = self._paper_to_signal(item)
                    if signal:
                        signals.append(signal)
        except Exception as e:
            log.warning("[hf] papers search failed for '%s': %s", query, e)

        return signals, self._make_cursor(query, newest)

    def _discussion_to_signal(
        self, item: dict[str, Any], repo_id: str, repo_type: str
    ) -> RawSignal | None:
        try:
            disc_id = str(item.get("num", ""))
            title = item.get("title", "")
            author = item.get("author", {}).get("name", "")
            created = self._parse_dt(item.get("createdAt", "")) or datetime.now(timezone.utc)
            url = f"https://huggingface.co/{repo_id}/discussions/{disc_id}"

            return RawSignal(
                source="hf_discussion",
                source_id=f"{repo_id}#{disc_id}",
                url=url,
                title=title,
                body=item.get("body", "") or title,
                author=author,
                created_at=created,
                collected_at=datetime.now(timezone.utc),
                score=item.get("numComments", 0),
                comments_count=item.get("numComments", 0),
                tags=[item.get("status", ""), repo_type],
                extra={"repo_id": repo_id, "repo_type": repo_type},
            )
        except Exception as e:
            log.debug("[hf] failed to parse discussion: %s", e)
            return None

    def _paper_to_signal(self, item: dict[str, Any]) -> RawSignal | None:
        try:
            paper_id = item.get("id", "")
            published = self._parse_dt(item.get("publishedAt", "")) or datetime.now(timezone.utc)
            return RawSignal(
                source="hf_discussion",
                source_id=f"paper:{paper_id}",
                url=f"https://huggingface.co/papers/{paper_id}",
                title=item.get("title", ""),
                body=item.get("summary", "") or item.get("title", ""),
                author="",
                created_at=published,
                collected_at=datetime.now(timezone.utc),
                score=item.get("upvotes", 0),
                comments_count=item.get("numComments", 0),
                tags=["paper"],
                extra={"arxiv_id": paper_id},
            )
        except Exception as e:
            log.debug("[hf] failed to parse paper: %s", e)
            return None

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _make_cursor(key: str, newest: datetime | None) -> CursorState:
        return CursorState(
            target_key=key,
            last_collected_at=newest or datetime.now(timezone.utc),
        )
