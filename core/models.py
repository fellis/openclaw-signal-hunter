"""
Domain models for Signal Hunter.
All data structures are plain dataclasses - no ORM, no framework coupling.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    GITHUB_ISSUE = "github_issue"
    GITHUB_DISCUSSION = "github_discussion"
    REDDIT_POST = "reddit_post"
    REDDIT_COMMENT = "reddit_comment"
    HN_POST = "hn_post"
    HN_COMMENT = "hn_comment"
    SO_QUESTION = "so_question"
    SO_ANSWER = "so_answer"
    PH_POST = "ph_post"
    HF_DISCUSSION = "hf_discussion"


class KeywordType(str, Enum):
    PRODUCT = "product"
    CONCEPT = "concept"
    PROBLEM = "problem"
    TOPIC = "topic"


@dataclass
class RawSignal:
    """A single piece of content collected from any source."""

    source: str
    source_id: str
    url: str
    title: str
    body: str
    author: str
    created_at: datetime
    collected_at: datetime

    score: int = 0
    comments_count: int = 0
    views_count: int = 0

    tags: list[str] = field(default_factory=list)
    parent_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Stable unique key for deduplication: '{source}:{source_id}'."""
        return f"{self.source}:{self.source_id}"


@dataclass
class MatchedRule:
    """A classification rule that matched a signal."""

    rule_name: str
    confidence: float
    evidence: str


@dataclass
class ProcessedSignal:
    """A signal after LLM classification."""

    raw_signal_id: str
    dedup_key: str
    is_relevant: bool
    matched_rules: list[MatchedRule]
    summary: str | None
    products_mentioned: list[str]
    intensity: int
    confidence: float
    keywords_matched: list[str]
    language: str
    rank_score: float
    linked_group_id: str | None = None
    processed_at: datetime | None = None


@dataclass
class DiscoveredResources:
    """Raw facts discovered by a collector's discover() call."""

    repos: list[dict[str, Any]] = field(default_factory=list)
    subreddits: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    threads: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class KeywordProfile:
    """Everything known about a keyword: discovery facts + LLM enrichment."""

    raw: str
    canonical_name: str
    keyword_type: KeywordType
    description: str

    discovered: dict[str, DiscoveredResources] = field(default_factory=dict)

    aliases: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    pain_patterns: list[str] = field(default_factory=list)
    search_queries: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class SearchTarget:
    """A single search target within a platform."""

    query: str
    scope: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def target_key(self) -> str:
        """Stable hash key for cursor storage."""
        payload = json.dumps(
            {"query": self.query, "scope": self.scope, "params": self.params},
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()


@dataclass
class SearchPlan:
    """Collection plan for a single collector."""

    targets: list[SearchTarget]
    max_results_per_target: int = 200


@dataclass
class CursorState:
    """Incremental collection state for a single search target."""

    target_key: str
    last_collected_at: datetime | None = None
    last_cursor: str | None = None


@dataclass
class CollectResult:
    """Output of a collector's collect() call."""

    signals: list[RawSignal]
    updated_cursors: dict[str, CursorState]


@dataclass
class SourceStatus:
    """Readiness status of a data source."""

    source: str
    ready: bool
    limit_info: str | None = None
    missing: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class ExtractionRule:
    """A user-defined classification rule."""

    name: str
    description: str
    examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)
    priority: int = 1
