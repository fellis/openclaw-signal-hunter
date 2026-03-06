"""
GitHub collector.
Collects Issues and Discussions from GitHub repositories.
Validated in spike Phase 1: filters PRs, uses updated_at as cursor, deduplicates cleanly.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

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

_GITHUB_API = "https://api.github.com"
_DEFAULT_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
_MAX_AGE_DAYS = 90
_PAGE_SIZE = 100
_RATE_LIMIT_PAUSE = 1.0


@register
class GitHubCollector(BaseCollector):
    """
    Collects GitHub Issues (not PRs) from specific repositories.
    Uses updated_at as incremental cursor. Validates in spike Phase 1.
    """

    name = "github"

    def __init__(self) -> None:
        token = os.environ.get("GITHUB_TOKEN", "")
        self._headers = {**_DEFAULT_HEADERS}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def discover(self, keyword: str) -> DiscoveredResources:
        """
        Search GitHub for repositories related to the keyword.
        Returns top-20 repos by stars + issue count estimates.
        """
        log.info("[github] discover: keyword='%s'", keyword)
        repos = self._search_repos(keyword, limit=30)
        return DiscoveredResources(repos=repos)

    def build_plan(self, profile: KeywordProfile) -> SearchPlan:
        """
        Build repo-scoped targets from discovered repos.
        Prefers /repos/{owner}/{repo}/issues (no 1000-result cap).
        Falls back to global search if no repos found.
        """
        github_resources = profile.discovered.get("github")
        repos: list[dict] = github_resources.repos if github_resources else []

        targets: list[SearchTarget] = []
        for repo in repos[:20]:
            full_name = repo.get("full_name", "")
            if not full_name:
                continue
            targets.append(
                SearchTarget(
                    query=full_name,
                    scope="repo_issues",
                    params={"full_name": full_name},
                )
            )

        if not targets:
            targets.append(
                SearchTarget(
                    query=profile.canonical_name,
                    scope="global_search",
                    params={},
                )
            )

        return SearchPlan(targets=targets, max_results_per_target=200)

    def discover_new_sources(
        self,
        profile: KeywordProfile,
        existing_plan: SearchPlan,
    ) -> list[SearchTarget]:
        """
        Search GitHub for repos not yet in the existing plan.
        Uses canonical_name + aliases from the enriched profile.
        Returns new repo_issues targets only; global_search targets are ignored
        since they already capture all repos dynamically.
        """
        existing_full_names = {
            t.params.get("full_name", "")
            for t in existing_plan.targets
            if t.scope == "repo_issues"
        }

        queries = [profile.canonical_name] + profile.aliases[:3]
        found: dict[str, dict] = {}

        for q in queries:
            for repo in self._search_repos(q, limit=30):
                fn = repo.get("full_name", "")
                if fn and fn not in existing_full_names and fn not in found:
                    found[fn] = repo

        new_targets = [
            SearchTarget(
                query=fn,
                scope="repo_issues",
                params={"full_name": fn},
            )
            for fn in found
        ]

        if new_targets:
            log.info(
                "[github] discover_new_sources: %d new repo(s) for '%s'",
                len(new_targets), profile.canonical_name,
            )
        return new_targets

    def collect(
        self,
        plan: SearchPlan,
        cursors: dict[str, CursorState],
    ) -> CollectResult:
        """
        Collect issues for each target in the plan.
        Resumes from cursor (last updated_at) if available.
        """
        all_signals: list[RawSignal] = []
        updated_cursors: dict[str, CursorState] = {}

        for target in plan.targets:
            cursor = cursors.get(target.target_key)
            since = self._cursor_since(cursor)

            log.info(
                "[github] collect: scope=%s query=%s since=%s",
                target.scope,
                target.query,
                since.isoformat() if since else "none",
            )

            if target.scope == "repo_issues":
                signals, new_cursor = self._collect_repo_issues(
                    target.params["full_name"],
                    since=since,
                    limit=plan.max_results_per_target,
                )
            elif target.scope == "global_search":
                signals, new_cursor = self._collect_global_search(
                    target.query,
                    since=since,
                    limit=plan.max_results_per_target,
                )
            else:
                log.warning("[github] unknown scope: %s", target.scope)
                continue

            all_signals.extend(signals)
            updated_cursors[target.target_key] = new_cursor
            time.sleep(_RATE_LIMIT_PAUSE)

        return CollectResult(signals=all_signals, updated_cursors=updated_cursors)

    def check_readiness(self) -> SourceStatus:
        """Make a test API call to verify token and rate limits."""
        try:
            with httpx.Client(headers=self._headers, timeout=10) as client:
                resp = client.get(f"{_GITHUB_API}/rate_limit")
                resp.raise_for_status()
                data = resp.json()
                core = data.get("resources", {}).get("core", {})
                remaining = core.get("remaining", 0)
                limit = core.get("limit", 0)
                has_token = "Authorization" in self._headers
                return SourceStatus(
                    source="github",
                    ready=True,
                    limit_info=f"{remaining}/{limit} req remaining",
                    note=None if has_token else "No GITHUB_TOKEN - using public limits (60 req/hr)",
                )
        except Exception as e:
            return SourceStatus(
                source="github",
                ready=False,
                missing=["GITHUB_TOKEN"],
                note=str(e),
            )

    def get_setup_guide(self) -> list[str]:
        return [
            "1. Go to https://github.com/settings/tokens",
            "2. Click 'Generate new token (classic)'",
            "3. Select scopes: public_repo (read-only is enough)",
            "4. Copy the token and set: GITHUB_TOKEN=<token> in your .env",
            "5. Without a token, rate limit is 60 req/hr (usually enough for testing)",
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _search_repos(self, keyword: str, limit: int) -> list[dict]:
        """Search GitHub repos by keyword, return top results sorted by stars."""
        url = f"{_GITHUB_API}/search/repositories"
        params = {
            "q": keyword,
            "sort": "stars",
            "order": "desc",
            "per_page": min(limit, 30),
        }
        try:
            with httpx.Client(headers=self._headers, timeout=15) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                items = resp.json().get("items", [])
                return [
                    {
                        "full_name": r["full_name"],
                        "stars": r["stargazers_count"],
                        "open_issues": r["open_issues_count"],
                        "description": r.get("description", ""),
                    }
                    for r in items
                ]
        except Exception as e:
            log.warning("[github] search_repos failed: %s", e)
            return []

    def _collect_repo_issues(
        self,
        full_name: str,
        since: datetime | None,
        limit: int,
    ) -> tuple[list[RawSignal], CursorState]:
        """
        Fetch issues from /repos/{owner}/{repo}/issues.
        Filters out pull requests (GitHub includes them in issues endpoint).
        """
        url = f"{_GITHUB_API}/repos/{full_name}/issues"
        params: dict = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": _PAGE_SIZE,
        }
        if since:
            params["since"] = since.isoformat()

        signals: list[RawSignal] = []
        page = 1
        newest_updated_at: datetime | None = None
        min_age = datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)

        while len(signals) < limit:
            params["page"] = page
            try:
                with httpx.Client(headers=self._headers, timeout=20) as client:
                    resp = client.get(url, params=params)
                    self._handle_rate_limit(resp)
                    resp.raise_for_status()
                    items = resp.json()
            except Exception as e:
                log.warning("[github] repo_issues page %d failed: %s", page, e)
                break

            if not items:
                break

            for item in items:
                if "pull_request" in item:
                    continue
                updated_at = self._parse_dt(item.get("updated_at"))
                if updated_at and updated_at < min_age:
                    return signals, self._make_cursor(full_name, newest_updated_at)

                if newest_updated_at is None:
                    newest_updated_at = updated_at

                signal = self._issue_to_signal(item)
                if signal:
                    signals.append(signal)

                if len(signals) >= limit:
                    break

            if len(items) < _PAGE_SIZE:
                break
            page += 1
            time.sleep(_RATE_LIMIT_PAUSE)

        return signals, self._make_cursor(full_name, newest_updated_at)

    def _collect_global_search(
        self,
        query: str,
        since: datetime | None,
        limit: int,
    ) -> tuple[list[RawSignal], CursorState]:
        """
        Search issues globally via /search/issues (1000-result cap applies).
        Used only when no specific repos are found.
        """
        url = f"{_GITHUB_API}/search/issues"
        q = f"{query} is:issue"
        if since:
            q += f" updated:>{since.strftime('%Y-%m-%d')}"

        params = {"q": q, "sort": "updated", "order": "desc", "per_page": _PAGE_SIZE}
        signals: list[RawSignal] = []
        page = 1
        newest: datetime | None = None

        while len(signals) < limit:
            params["page"] = page
            try:
                with httpx.Client(headers=self._headers, timeout=20) as client:
                    resp = client.get(url, params=params)
                    self._handle_rate_limit(resp)
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:
                log.warning("[github] global_search page %d failed: %s", page, e)
                break

            items = data.get("items", [])
            if not items:
                break

            for item in items:
                updated_at = self._parse_dt(item.get("updated_at"))
                if newest is None:
                    newest = updated_at
                signal = self._issue_to_signal(item)
                if signal:
                    signals.append(signal)
                if len(signals) >= limit:
                    break

            if len(items) < _PAGE_SIZE or data.get("total_count", 0) <= page * _PAGE_SIZE:
                break
            page += 1
            time.sleep(_RATE_LIMIT_PAUSE)

        return signals, self._make_cursor(query, newest)

    def _issue_to_signal(self, item: dict) -> RawSignal | None:
        """Convert a GitHub issue JSON dict to a RawSignal."""
        try:
            issue_id = item["number"]
            repo_url = item.get("repository_url", "")
            repo_name = repo_url.replace(f"{_GITHUB_API}/repos/", "")
            return RawSignal(
                source="github_issue",
                source_id=f"{repo_name}#{issue_id}",
                url=item.get("html_url", ""),
                title=item.get("title", ""),
                body=item.get("body") or "",
                author=item.get("user", {}).get("login", ""),
                created_at=self._parse_dt(item.get("created_at")) or datetime.now(timezone.utc),
                collected_at=datetime.now(timezone.utc),
                score=item.get("reactions", {}).get("total_count", 0),
                comments_count=item.get("comments", 0),
                tags=[lb["name"] for lb in item.get("labels", [])],
                extra={"repo": repo_name, "state": item.get("state")},
            )
        except Exception as e:
            log.debug("[github] failed to parse issue: %s", e)
            return None

    def _handle_rate_limit(self, resp: httpx.Response) -> None:
        """Sleep if close to rate limit."""
        remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
        if remaining < 10:
            reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(0, reset_at - int(time.time())) + 2
            log.warning("[github] rate limit low (%d remaining), sleeping %ds", remaining, wait)
            time.sleep(wait)

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _cursor_since(cursor: CursorState | None) -> datetime | None:
        if cursor and cursor.last_collected_at:
            return cursor.last_collected_at
        return None

    @staticmethod
    def _make_cursor(key: str, newest: datetime | None) -> CursorState:
        return CursorState(
            target_key=key,
            last_collected_at=newest or datetime.now(timezone.utc),
            last_cursor=None,
        )
