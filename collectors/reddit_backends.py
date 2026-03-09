"""
Reddit API backends: PullPush, Arctic Shift, and facade with failover + rate limiting.
Keyword-driven search; state for rate limits stored in a file so it survives process restarts.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.models import SourceStatus

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (single place, no magic strings elsewhere)
# ---------------------------------------------------------------------------

SCOPE_REDDIT_KEYWORD = "reddit_keyword"
BACKEND_PULLPUSH = "pullpush"
BACKEND_ARCTIC_SHIFT = "arctic_shift"
BACKEND_PULLPUSH_FAILOVER = "pullpush_failover"

BASE_URLS = {
    BACKEND_PULLPUSH: "https://api.pullpush.io/reddit",
    BACKEND_ARCTIC_SHIFT: "https://arctic-shift.photon-reddit.com/api",
}

# rpm = requests per minute, rph = requests per hour (for PullPush); rps for Arctic
BACKEND_LIMITS = {
    BACKEND_PULLPUSH: {"rpm": 14, "rph": 900},
    BACKEND_ARCTIC_SHIFT: {"rps": 1.5},
}

REDDIT_DEFAULTS = {
    "backend": BACKEND_PULLPUSH_FAILOVER,
    "max_submissions_per_keyword": 100,
    "min_score_for_comments": 3,
    "max_comment_fetches_per_run": 300,
    "submission_min_score": 1,
    "comment_min_score": 1,
    "failover_duration_minutes": 10,
    "retry_attempts": 3,
    "rate_limit_state_file": "",
}

# ---------------------------------------------------------------------------
# Types (unified across backends)
# ---------------------------------------------------------------------------


@dataclass
class RawSubmission:
    id: str
    subreddit: str
    author: str
    title: str
    selftext: str
    score: int
    num_comments: int
    created_utc: int
    url: str
    link_url: str | None


@dataclass
class RawComment:
    id: str
    submission_id: str
    subreddit: str
    author: str
    body: str
    score: int
    created_utc: int
    parent_id: str


def comment_source_id(comment_id: str) -> str:
    """Single place for comment source_id format."""
    return f"comment:{comment_id}"


# ---------------------------------------------------------------------------
# Normalizers: unified dict -> dataclass (no duplication in backends)
# ---------------------------------------------------------------------------


def _dict_to_raw_submission(d: dict[str, Any]) -> RawSubmission | None:
    """Build RawSubmission from a unified dict (id, subreddit, author, ...)."""
    try:
        return RawSubmission(
            id=str(d.get("id", "")),
            subreddit=str(d.get("subreddit", "")),
            author=str(d.get("author", "")),
            title=str(d.get("title", "")),
            selftext=str(d.get("selftext", "")),
            score=int(d.get("score", 0)),
            num_comments=int(d.get("num_comments", 0)),
            created_utc=int(d.get("created_utc", 0)),
            url=str(d.get("url", "")),
            link_url=d.get("link_url"),
        )
    except (TypeError, ValueError):
        return None


def _dict_to_raw_comment(d: dict[str, Any]) -> RawComment | None:
    """Build RawComment from a unified dict."""
    try:
        return RawComment(
            id=str(d.get("id", "")),
            submission_id=str(d.get("submission_id", "")),
            subreddit=str(d.get("subreddit", "")),
            author=str(d.get("author", "")),
            body=str(d.get("body", "")),
            score=int(d.get("score", 0)),
            created_utc=int(d.get("created_utc", 0)),
            parent_id=str(d.get("parent_id", "")),
        )
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rate limit state (persistent file)
# ---------------------------------------------------------------------------

def default_rate_limit_state_path(config_dir: Path | None = None) -> Path:
    """Default path: config_dir/reddit_rate_limit.json or ~/.signal-hunter/reddit_rate_limit.json."""
    if config_dir is not None and config_dir.exists():
        return config_dir / "reddit_rate_limit.json"
    return Path.home() / ".signal-hunter" / "reddit_rate_limit.json"


def _load_rate_limit_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_rate_limit_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=0)


def _wait_for_rate_limit(backend: str, state_path: Path, limits: dict[str, Any]) -> None:
    """If at limit, sleep until window resets; then update state for new window."""
    state = _load_rate_limit_state(state_path)
    key = backend
    now = time.time()
    # Minute window
    rpm = limits.get("rpm")
    if rpm is not None:
        win = state.get(key, {}).get("minute", {})
        start = win.get("window_start_utc", 0)
        count = win.get("request_count", 0)
        if count >= rpm and start > 0:
            wait_sec = max(0, (start + 60) - now)
            if wait_sec > 0:
                log.debug("[reddit] rate limit: sleeping %.1fs (minute window)", wait_sec)
                time.sleep(wait_sec)
            state[key] = state.get(key, {})
            state[key]["minute"] = {"window_start_utc": int(now), "request_count": 0}
    # Hour window (PullPush)
    rph = limits.get("rph")
    if rph is not None:
        win = state.get(key, {}).get("hour", {})
        start = win.get("window_start_utc", 0)
        count = win.get("request_count", 0)
        if count >= rph and start > 0:
            wait_sec = max(0, (start + 3600) - now)
            if wait_sec > 0:
                log.debug("[reddit] rate limit: sleeping %.1fs (hour window)", wait_sec)
                time.sleep(wait_sec)
            state[key] = state.get(key, {})
            state[key]["hour"] = {"window_start_utc": int(now), "request_count": 0}
    # RPS (Arctic)
    rps = limits.get("rps")
    if rps is not None and rps > 0:
        interval = 1.0 / rps
        last = state.get(key, {}).get("last_request_utc", 0)
        if last > 0:
            elapsed = now - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
    _save_rate_limit_state(state_path, state)


def _record_request(backend: str, state_path: Path, limits: dict[str, Any]) -> None:
    state = _load_rate_limit_state(state_path)
    key = backend
    now = time.time()
    if key not in state:
        state[key] = {}
    # Minute
    if "rpm" in limits:
        win = state[key].get("minute", {})
        start = win.get("window_start_utc", 0)
        if start == 0 or (now - start) >= 60:
            start = int(now)
            count = 0
        else:
            count = win.get("request_count", 0)
        state[key]["minute"] = {"window_start_utc": start, "request_count": count + 1}
    # Hour
    if "rph" in limits:
        win = state[key].get("hour", {})
        start = win.get("window_start_utc", 0)
        if start == 0 or (now - start) >= 3600:
            start = int(now)
            count = 0
        else:
            count = win.get("request_count", 0)
        state[key]["hour"] = {"window_start_utc": start, "request_count": count + 1}
    state[key]["last_request_utc"] = now
    _save_rate_limit_state(state_path, state)


# ---------------------------------------------------------------------------
# Backend interface and implementations
# ---------------------------------------------------------------------------


class RedditSearchBackend(ABC):
    """Strategy: one way to search Reddit posts and comments."""

    @abstractmethod
    def search_submissions(
        self, keyword: str, after_utc: int | None, size: int
    ) -> list[RawSubmission]:
        pass

    @abstractmethod
    def search_comments(self, link_id: str, limit: int) -> list[RawComment]:
        pass

    @abstractmethod
    def check_ready(self) -> SourceStatus:
        pass


class PullPushBackend(RedditSearchBackend):
    """PullPush API (Pushshift successor)."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._base = BASE_URLS[BACKEND_PULLPUSH]
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "signal-hunter/0.1"})

    def search_submissions(
        self, keyword: str, after_utc: int | None, size: int
    ) -> list[RawSubmission]:
        params: dict[str, Any] = {"q": keyword, "size": min(size, 100), "sort": "asc", "sort_type": "created_utc"}
        if after_utc is not None:
            params["after"] = after_utc
        resp = self._client.get(f"{self._base}/search/submission", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or []
        out: list[RawSubmission] = []
        for item in items:
            d = self._submission_to_dict(item)
            s = _dict_to_raw_submission(d)
            if s and s.id:
                out.append(s)
        return out

    def _submission_to_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        permalink = item.get("permalink") or ""
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"
        return {
            "id": item.get("id"),
            "subreddit": item.get("subreddit"),
            "author": item.get("author"),
            "title": item.get("title"),
            "selftext": item.get("selftext"),
            "score": item.get("score"),
            "num_comments": item.get("num_comments"),
            "created_utc": item.get("created_utc"),
            "url": permalink or item.get("url"),
            "link_url": item.get("url") if item.get("is_self") is False else None,
        }

    def search_comments(self, link_id: str, limit: int) -> list[RawComment]:
        # link_id: Reddit submission id (e.g. 1kqb7oe), without t3_
        params: dict[str, Any] = {"link_id": f"t3_{link_id}", "size": min(limit, 100), "sort": "desc"}
        resp = self._client.get(f"{self._base}/search/comment", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or []
        out: list[RawComment] = []
        for item in items:
            d = self._comment_to_dict(item, link_id)
            c = _dict_to_raw_comment(d)
            if c and c.id and (c.parent_id or "").startswith("t3_"):
                out.append(c)
        return out

    def _comment_to_dict(self, item: dict[str, Any], submission_id: str) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "submission_id": submission_id,
            "subreddit": item.get("subreddit"),
            "author": item.get("author"),
            "body": item.get("body"),
            "score": item.get("score"),
            "created_utc": item.get("created_utc"),
            "parent_id": item.get("parent_id"),
        }

    def check_ready(self) -> SourceStatus:
        try:
            resp = self._client.get(f"{self._base}/search/submission", params={"q": "test", "size": 1})
            if resp.status_code == 200:
                return SourceStatus(source="reddit", ready=True, limit_info="14 req/min, 900 req/hr (PullPush)")
            return SourceStatus(source="reddit", ready=False, note=f"PullPush returned {resp.status_code}")
        except Exception as e:
            return SourceStatus(source="reddit", ready=False, note=str(e))


class ArcticShiftBackend(RedditSearchBackend):
    """Arctic Shift API. Endpoints may differ; normalize to same dict shape."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._base = BASE_URLS[BACKEND_ARCTIC_SHIFT]
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "signal-hunter/0.1"})

    def search_submissions(
        self, keyword: str, after_utc: int | None, size: int
    ) -> list[RawSubmission]:
        # Try common Arctic Shift style; adjust if API differs
        params: dict[str, Any] = {"q": keyword, "limit": min(size, 100), "sort": "asc"}
        if after_utc is not None:
            params["after"] = after_utc
        try:
            resp = self._client.get(f"{self._base}/posts/search", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                # Fallback: try different param names
                params2 = {"query": keyword, "limit": min(size, 100)}
                if after_utc is not None:
                    params2["after"] = after_utc
                resp = self._client.get(f"{self._base}/posts/search", params=params2)
                resp.raise_for_status()
            else:
                raise
        data = resp.json()
        items = data.get("data") or data.get("posts") or []
        out: list[RawSubmission] = []
        for item in items:
            d = self._submission_to_dict(item)
            s = _dict_to_raw_submission(d)
            if s and s.id:
                out.append(s)
        return out

    def _submission_to_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        permalink = item.get("permalink") or item.get("url") or ""
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"
        return {
            "id": item.get("id"),
            "subreddit": item.get("subreddit"),
            "author": item.get("author"),
            "title": item.get("title"),
            "selftext": item.get("selftext") or item.get("body"),
            "score": item.get("score"),
            "num_comments": item.get("num_comments", 0),
            "created_utc": item.get("created_utc"),
            "url": permalink,
            "link_url": item.get("url") if item.get("is_self") is False else None,
        }

    def search_comments(self, link_id: str, limit: int) -> list[RawComment]:
        try:
            resp = self._client.get(
                f"{self._base}/comments",
                params={"link_id": link_id, "limit": min(limit, 100)},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            return []
        data = resp.json()
        items = data.get("data") or data.get("comments") or []
        out: list[RawComment] = []
        for item in items:
            d = self._comment_to_dict(item, link_id)
            c = _dict_to_raw_comment(d)
            if c and c.id and (c.parent_id or "").startswith("t3_"):
                out.append(c)
        return out

    def _comment_to_dict(self, item: dict[str, Any], submission_id: str) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "submission_id": submission_id,
            "subreddit": item.get("subreddit"),
            "author": item.get("author"),
            "body": item.get("body"),
            "score": item.get("score"),
            "created_utc": item.get("created_utc"),
            "parent_id": item.get("parent_id", "t3_" + submission_id),
        }

    def check_ready(self) -> SourceStatus:
        try:
            resp = self._client.get(f"{self._base}/posts/search", params={"limit": 1})
            if resp.status_code == 200:
                return SourceStatus(source="reddit", ready=True, limit_info="~1.5 req/s (Arctic Shift)")
            return SourceStatus(source="reddit", ready=False, note=f"Arctic Shift returned {resp.status_code}")
        except Exception as e:
            return SourceStatus(source="reddit", ready=False, note=str(e))


# ---------------------------------------------------------------------------
# Facade: failover + rate limiting
# ---------------------------------------------------------------------------


class RedditAPIFacade:
    """Single entry for Reddit: rate limit (file-backed) + optional failover."""

    def __init__(
        self,
        backend: str,
        state_path: Path | None,
        failover_duration_minutes: int = 10,
        retry_attempts: int = 3,
    ) -> None:
        self._backend_name = backend
        self._state_path = state_path if state_path is not None else default_rate_limit_state_path(None)
        self._failover_duration_minutes = failover_duration_minutes
        self._retry_attempts = retry_attempts
        self._primary: RedditSearchBackend = PullPushBackend()
        self._fallback: RedditSearchBackend = ArcticShiftBackend()
        self._use_fallback_until: float = 0.0
        self._consecutive_failures = 0

    def _active_backend(self) -> tuple[str, RedditSearchBackend]:
        now = time.time()
        if self._backend_name == BACKEND_ARCTIC_SHIFT:
            return BACKEND_ARCTIC_SHIFT, self._fallback
        if self._backend_name == BACKEND_PULLPUSH_FAILOVER and now < self._use_fallback_until:
            return BACKEND_ARCTIC_SHIFT, self._fallback
        return BACKEND_PULLPUSH, self._primary

    def _limits_for(self, backend: str) -> dict[str, Any]:
        return BACKEND_LIMITS.get(backend, {})

    def search_submissions(
        self, keyword: str, after_utc: int | None, size: int
    ) -> list[RawSubmission]:
        name, backend = self._active_backend()
        limits = self._limits_for(name)
        _wait_for_rate_limit(name, self._state_path, limits)
        try:
            out = backend.search_submissions(keyword, after_utc, size)
            self._consecutive_failures = 0
            _record_request(name, self._state_path, limits)
            return out
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 429 or (code and 500 <= code < 600):
                self._consecutive_failures += 1
                if self._backend_name == BACKEND_PULLPUSH_FAILOVER and self._consecutive_failures >= self._retry_attempts:
                    self._use_fallback_until = time.time() + self._failover_duration_minutes * 60
                    log.warning("[reddit] failing over to Arctic Shift for %d min", self._failover_duration_minutes)
                    return self.search_submissions(keyword, after_utc, size)
            raise

    def search_comments(self, link_id: str, limit: int) -> list[RawComment]:
        name, backend = self._active_backend()
        limits = self._limits_for(name)
        _wait_for_rate_limit(name, self._state_path, limits)
        try:
            out = backend.search_comments(link_id, limit)
            self._consecutive_failures = 0
            _record_request(name, self._state_path, limits)
            return out
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 429 or (code and 500 <= code < 600):
                self._consecutive_failures += 1
                if self._backend_name == BACKEND_PULLPUSH_FAILOVER and self._consecutive_failures >= self._retry_attempts:
                    self._use_fallback_until = time.time() + self._failover_duration_minutes * 60
                    log.warning("[reddit] failing over to Arctic Shift for %d min", self._failover_duration_minutes)
                    return self.search_comments(link_id, limit)
            raise

    def check_ready(self) -> SourceStatus:
        name, backend = self._active_backend()
        return backend.check_ready()
