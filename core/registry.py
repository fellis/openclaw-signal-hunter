"""
Collector registry.
Uses the @register decorator pattern: each collector registers itself
by decorating its class. The orchestrator discovers collectors via get_all().
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import (
        CollectResult,
        CursorState,
        DiscoveredResources,
        KeywordProfile,
        SearchPlan,
        SourceStatus,
    )

_REGISTRY: dict[str, type["BaseCollector"]] = {}


def register(cls: type["BaseCollector"]) -> type["BaseCollector"]:
    """Class decorator: registers a collector by its .name attribute."""
    if not hasattr(cls, "name") or not cls.name:
        raise ValueError(f"Collector {cls.__name__} must define a non-empty class attribute 'name'")
    _REGISTRY[cls.name] = cls
    return cls


def get_all() -> list[type["BaseCollector"]]:
    """Return all registered collector classes."""
    return list(_REGISTRY.values())


def get(name: str) -> type["BaseCollector"] | None:
    """Return a collector class by name, or None if not found."""
    return _REGISTRY.get(name)


def load_all_collectors() -> None:
    """
    Auto-import every module in the collectors/ package so their
    @register decorators fire. Call once at startup.
    """
    import collectors  # noqa: PLC0415

    for _finder, module_name, _ispkg in pkgutil.iter_modules(collectors.__path__):
        importlib.import_module(f"collectors.{module_name}")


class BaseCollector(ABC):
    """
    Abstract base for all platform collectors.
    Each subclass handles exactly one data source (SRP).
    Subclasses are registered via @register decorator.
    """

    name: str = ""

    @abstractmethod
    def discover(self, keyword: str) -> "DiscoveredResources":
        """
        Query the platform API to find real resources related to the keyword.
        No guessing - only facts confirmed by API calls.
        Returns discovered repos / subreddits / tags / threads.
        """

    @abstractmethod
    def build_plan(self, profile: "KeywordProfile") -> "SearchPlan":
        """
        Build a SearchPlan for this collector from the enriched KeywordProfile.
        Prefers repo-scoped targets over global search where possible.
        """

    @abstractmethod
    def collect(
        self,
        plan: "SearchPlan",
        cursors: dict[str, "CursorState"],
    ) -> "CollectResult":
        """
        Execute the plan. Resumes from cursors if provided.
        Handles rate limiting, retries, and pagination internally.
        Returns collected signals + updated cursors.
        """

    def discover_new_sources(
        self,
        profile: "KeywordProfile",
        existing_plan: "SearchPlan",
    ) -> "list[SearchTarget]":
        """
        Return new SearchTargets not already present in existing_plan.
        Called once per collect cycle to expand the plan with newly appeared sources.

        Default implementation returns [] - suitable for query-based collectors
        (HN, Reddit, SO) that do not track fixed source lists.
        Override in collectors that monitor specific resources (GitHub repos, HF spaces).
        """
        return []

    @abstractmethod
    def check_readiness(self) -> "SourceStatus":
        """
        Verify credentials and make a test API call.
        Returns ready status + human-readable limit info.
        """

    @abstractmethod
    def get_setup_guide(self) -> list[str]:
        """
        Return step-by-step instructions for obtaining credentials.
        Each string is one step shown to the user in chat.
        """
