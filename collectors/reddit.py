"""
Reddit collector (keyword-driven).
Searches Reddit by keyword via PullPush and/or Arctic Shift; no subreddit list.
Uses RedditAPIFacade for rate limiting and failover.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.constants import MAX_AGE_DAYS
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
from core.models import SourceType
from core.registry import BaseCollector, register

from collectors.reddit_backends import (
    BACKEND_ARCTIC_SHIFT,
    BACKEND_PULLPUSH,
    BACKEND_PULLPUSH_FAILOVER,
    REDDIT_DEFAULTS,
    RawComment,
    RawSubmission,
    RedditAPIFacade,
    SCOPE_REDDIT_KEYWORD,
    comment_source_id,
    default_rate_limit_state_path,
)

log = logging.getLogger(__name__)

# Setup guide fragments by backend (no single hardcoded blob)
SETUP_GUIDE_BY_BACKEND = {
    BACKEND_PULLPUSH: [
        "PullPush (api.pullpush.io): no keys required.",
        "Rate limits: 14 req/min, 900 req/hr. State stored in rate_limit_state_file.",
    ],
    BACKEND_ARCTIC_SHIFT: [
        "Arctic Shift (arctic-shift.photon-reddit.com): no keys required.",
        "Rate limits: ~1.5 req/s. State stored in rate_limit_state_file.",
    ],
    BACKEND_PULLPUSH_FAILOVER: [
        "PullPush as primary, Arctic Shift as fallback (on 429/5xx). No keys required.",
        "Set sources.reddit.backend to 'pullpush' or 'arctic_shift' to use a single API.",
    ],
}


@register
class RedditCollector(BaseCollector):
    """
    Keyword-driven Reddit collection. Searches all of Reddit by keyword;
    collects submissions and top-level comments. No subreddit list.
    """

    name = "reddit"

    def __init__(self) -> None:
        from storage.config_manager import ConfigManager
        cm = ConfigManager()
        self._config = cm.load()
        reddit_cfg = self._config.get("sources", {}).get("reddit", {})
        backend = reddit_cfg.get("backend", REDDIT_DEFAULTS["backend"])
        state_file = (reddit_cfg.get("rate_limit_state_file") or "").strip()
        if state_file:
            state_path = Path(state_file).expanduser()
        else:
            config_dir = Path(cm._path).parent if cm._path else None
            state_path = default_rate_limit_state_path(config_dir)
        failover_min = reddit_cfg.get("failover_duration_minutes", REDDIT_DEFAULTS["failover_duration_minutes"])
        retry = reddit_cfg.get("retry_attempts", REDDIT_DEFAULTS["retry_attempts"])
        self._facade = RedditAPIFacade(
            backend=backend,
            state_path=state_path,
            failover_duration_minutes=failover_min,
            retry_attempts=retry,
        )

    def discover(self, keyword: str) -> DiscoveredResources:
        """Return a thread-like resource for the keyword (same contract as other collectors)."""
        status = self._facade.check_ready()
        return DiscoveredResources(
            threads=[{"query": keyword, "backend": self._backend_name()}],
            extra={"ready": status.ready},
        )

    def build_plan(self, profile: KeywordProfile) -> SearchPlan:
        reddit_cfg = self._config.get("sources", {}).get("reddit", {})
        max_per = reddit_cfg.get("max_submissions_per_keyword", REDDIT_DEFAULTS["max_submissions_per_keyword"])
        target = SearchTarget(
            query=profile.canonical_name,
            scope=SCOPE_REDDIT_KEYWORD,
            params={},
        )
        return SearchPlan(targets=[target], max_results_per_target=max_per)

    def collect(self, plan: SearchPlan, cursors: dict[str, CursorState]) -> CollectResult:
        reddit_cfg = self._config.get("sources", {}).get("reddit", {})
        skip_authors = set(reddit_cfg.get("skip_authors") or ["[deleted]", "AutoModerator", "RemindMeBot"])
        min_score_comments = reddit_cfg.get("min_score_for_comments", REDDIT_DEFAULTS["min_score_for_comments"])
        max_comment_fetches = reddit_cfg.get("max_comment_fetches_per_run", REDDIT_DEFAULTS["max_comment_fetches_per_run"])
        submission_min_score = reddit_cfg.get("submission_min_score", REDDIT_DEFAULTS["submission_min_score"])

        all_signals: list[RawSignal] = []
        updated_cursors: dict[str, CursorState] = {}
        comment_fetches = 0
        now = datetime.now(timezone.utc)

        for target in plan.targets:
            if target.scope != SCOPE_REDDIT_KEYWORD:
                continue
            cursor = cursors.get(target.target_key)
            after_utc: int | None = None
            if cursor and cursor.last_collected_at:
                after_utc = int(cursor.last_collected_at.timestamp())
            elif cursor and cursor.last_cursor:
                try:
                    after_utc = int(cursor.last_cursor)
                except ValueError:
                    pass
            if after_utc is None:
                after_utc = int((now - timedelta(days=MAX_AGE_DAYS)).timestamp())

            keyword = target.query
            max_results = plan.max_results_per_target
            try:
                submissions = self._facade.search_submissions(keyword, after_utc, max_results)
            except Exception as e:
                log.warning("[reddit] collect failed for keyword %s: %s", keyword, e)
                continue

            max_created_utc: int | None = after_utc
            for sub in submissions:
                if sub.author in skip_authors:
                    continue
                if sub.score < submission_min_score:
                    continue
                if sub.created_utc:
                    if max_created_utc is None or sub.created_utc > max_created_utc:
                        max_created_utc = sub.created_utc
                sig = self._submission_to_signal(sub)
                if sig:
                    all_signals.append(sig)

                if comment_fetches >= max_comment_fetches:
                    continue
                if sub.num_comments <= 0 or sub.score < min_score_comments:
                    continue
                try:
                    comments = self._facade.search_comments(sub.id, limit=50)
                    comment_fetches += 1
                except Exception as e:
                    log.debug("[reddit] comments fetch failed for %s: %s", sub.id, e)
                    continue
                for c in comments:
                    if c.author in skip_authors:
                        continue
                    if not (c.parent_id or "").startswith("t3_"):
                        continue
                    csig = self._comment_to_signal(c, sub)
                    if csig:
                        all_signals.append(csig)

            new_cursor_ts = max_created_utc if max_created_utc is not None else (int(now.timestamp()) if submissions else (after_utc or int(now.timestamp())))
            new_dt = datetime.fromtimestamp(new_cursor_ts, tz=timezone.utc)
            updated_cursors[target.target_key] = CursorState(
                target_key=target.target_key,
                last_collected_at=new_dt,
                last_cursor=str(new_cursor_ts),
            )

        return CollectResult(signals=all_signals, updated_cursors=updated_cursors)

    def check_readiness(self) -> SourceStatus:
        return self._facade.check_ready()

    def get_setup_guide(self) -> list[str]:
        name = self._backend_name()
        return SETUP_GUIDE_BY_BACKEND.get(name, SETUP_GUIDE_BY_BACKEND[BACKEND_PULLPUSH_FAILOVER])

    def _backend_name(self) -> str:
        reddit_cfg = self._config.get("sources", {}).get("reddit", {})
        return reddit_cfg.get("backend", REDDIT_DEFAULTS["backend"])

    @staticmethod
    def _submission_to_signal(sub: RawSubmission) -> RawSignal | None:
        if not sub.id:
            return None
        created = datetime.fromtimestamp(sub.created_utc, tz=timezone.utc) if sub.created_utc else datetime.now(timezone.utc)
        url = sub.url or f"https://reddit.com/comments/{sub.id}"
        if not url.startswith("http"):
            url = f"https://reddit.com{url}"
        body = sub.selftext or sub.title
        return RawSignal(
            source=SourceType.REDDIT_POST.value,
            source_id=sub.id,
            url=url,
            title=sub.title or "",
            body=body or "",
            author=sub.author or "",
            created_at=created,
            collected_at=datetime.now(timezone.utc),
            score=sub.score,
            comments_count=sub.num_comments,
            extra={"subreddit": sub.subreddit, "link_url": sub.link_url},
        )

    @staticmethod
    def _comment_to_signal(c: RawComment, sub: RawSubmission) -> RawSignal | None:
        if not c.id:
            return None
        created = datetime.fromtimestamp(c.created_utc, tz=timezone.utc) if c.created_utc else datetime.now(timezone.utc)
        base_url = sub.url or f"https://reddit.com/comments/{sub.id}"
        if not base_url.startswith("http"):
            base_url = f"https://reddit.com{base_url}"
        url = f"{base_url.rstrip('/')}/{c.id}"
        return RawSignal(
            source=SourceType.REDDIT_COMMENT.value,
            source_id=comment_source_id(c.id),
            url=url,
            title="",
            body=c.body or "",
            author=c.author or "",
            created_at=created,
            collected_at=datetime.now(timezone.utc),
            score=c.score,
            comments_count=0,
            extra={"subreddit": c.subreddit, "submission_id": c.submission_id},
        )
