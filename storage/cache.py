"""
Keyword profile cache.
Thin wrapper: profiles are stored in Postgres (keyword_profiles table).
This module provides a simple in-process LRU cache to avoid repeated DB lookups
within a single skill invocation.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)


class KeywordProfileCache:
    """
    Simple in-process cache for KeywordProfile data.
    Backed by PostgresStorage. Useful for multi-keyword orchestration
    where the same profile may be read multiple times.
    """

    def __init__(self, storage) -> None:
        self._storage = storage
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, canonical_name: str) -> dict[str, Any] | None:
        if canonical_name in self._cache:
            return self._cache[canonical_name]
        profile = self._storage.get_keyword_profile(canonical_name)
        if profile:
            self._cache[canonical_name] = profile
        return profile

    def set(self, canonical_name: str, profile_data: dict[str, Any]) -> None:
        self._cache[canonical_name] = profile_data

    def invalidate(self, canonical_name: str) -> None:
        self._cache.pop(canonical_name, None)

    def clear(self) -> None:
        self._cache.clear()
