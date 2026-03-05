"""
KeywordResolver.
Discovery-first approach: LLM never guesses resources, only enriches real API facts.
Flow: keyword -> all collectors .discover() -> facts -> LLM enrichment -> KeywordProfile -> cache.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.llm_router import LLMCall, LLMRouter
from core.models import (
    DiscoveredResources,
    KeywordProfile,
    KeywordType,
)
from core.registry import BaseCollector, get_all
from storage.postgres import PostgresStorage

log = logging.getLogger(__name__)

_ENRICH_SYSTEM = """You are an AI market intelligence assistant.
You analyze a keyword and discovery facts from platforms, then classify and enrich the keyword profile.
Always respond with valid JSON only."""


class KeywordResolver:
    """
    Resolves a raw keyword string into an enriched KeywordProfile.
    Uses discovery-first: only LLM-enriches facts already confirmed by APIs.
    Caches resolved profiles in Postgres (keyword_profiles table).
    """

    def __init__(self, router: LLMRouter, storage: PostgresStorage) -> None:
        self._router = router
        self._storage = storage

    def resolve(self, keyword: str, force_refresh: bool = False) -> dict[str, Any]:
        """
        Resolve keyword to a plan proposal.
        Returns a dict suitable for JSON output (shown to user for approval).
        Uses cache unless force_refresh=True.
        """
        canonical = keyword.lower().strip()

        if not force_refresh:
            cached = self._storage.get_keyword_profile(canonical)
            if cached:
                log.info("[resolver] cache hit for '%s'", canonical)
                return self._profile_to_proposal(cached)

        log.info("[resolver] resolving '%s' from scratch", canonical)

        # Step 1: Discovery from all enabled collectors
        all_collectors: list[BaseCollector] = [cls() for cls in get_all()]
        discovered: dict[str, DiscoveredResources] = {}

        for collector in all_collectors:
            try:
                log.info("[resolver] discover via %s", collector.name)
                resources = collector.discover(keyword)
                discovered[collector.name] = resources
            except Exception as e:
                log.warning("[resolver] %s.discover failed: %s", collector.name, e)

        # Step 2: LLM enrichment based on discovered facts
        profile = self._enrich(keyword, canonical, discovered)

        # Step 3: Cache in Postgres
        self._storage.save_keyword_profile(profile)

        # Step 4: Build plan proposals from each collector
        proposals = self._build_proposals(profile, all_collectors)

        return {
            "keyword": keyword,
            "canonical_name": profile.canonical_name,
            "keyword_type": profile.keyword_type.value,
            "description": profile.description,
            "aliases": profile.aliases,
            "relevant_subreddits": profile.relevant_subreddits,
            "discovery": self._discovery_summary(discovered),
            "proposed_plan": proposals,
        }

    def approve_plan(
        self,
        canonical_name: str,
        collector_plans: dict[str, Any],
    ) -> None:
        """
        Save approved collection plans for a keyword.
        collector_plans: {collector_name: [{"query": ..., "scope": ..., "params": ...}, ...]}
        """
        from core.models import SearchPlan, SearchTarget  # noqa: PLC0415

        for collector_name, targets_data in collector_plans.items():
            targets = [
                SearchTarget(
                    query=t.get("query", ""),
                    scope=t.get("scope", ""),
                    params=t.get("params", {}),
                )
                for t in targets_data
            ]
            plan = SearchPlan(targets=targets)
            self._storage.save_collection_plan(canonical_name, collector_name, plan)
            log.info(
                "[resolver] approved plan for %s/%s: %d targets",
                canonical_name, collector_name, len(targets),
            )

    # ------------------------------------------------------------------
    # Private: LLM enrichment
    # ------------------------------------------------------------------

    def _enrich(
        self,
        keyword: str,
        canonical: str,
        discovered: dict[str, DiscoveredResources],
    ) -> KeywordProfile:
        """Ask LLM to classify and enrich the keyword based on discovery facts."""
        discovery_summary = self._discovery_summary(discovered)
        prompt = self._build_enrich_prompt(keyword, discovery_summary)

        call = LLMCall(
            operation="resolve_enrich",
            messages=[
                {"role": "system", "content": _ENRICH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
            temperature=0.0,
        )

        try:
            raw = self._router.complete(call)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            enriched = json.loads(raw)
        except Exception as e:
            log.warning("[resolver] LLM enrichment failed: %s. Using defaults.", e)
            enriched = {}

        keyword_type = KeywordType(enriched.get("keyword_type", "product"))
        return KeywordProfile(
            raw=keyword,
            canonical_name=canonical,
            keyword_type=keyword_type,
            description=enriched.get("description", f"Signals about '{keyword}'"),
            discovered=discovered,
            aliases=enriched.get("aliases", []),
            related_terms=enriched.get("related_terms", []),
            pain_patterns=enriched.get("pain_patterns", []),
            search_queries=enriched.get("search_queries", {}),
            relevant_subreddits=enriched.get("relevant_subreddits", []),
        )

    @staticmethod
    def _build_enrich_prompt(keyword: str, discovery: dict[str, Any]) -> str:
        return f"""Keyword: "{keyword}"

Discovery facts from platform APIs:
{json.dumps(discovery, ensure_ascii=False, indent=2)}

Based on these facts and your knowledge, return JSON:
{{
  "keyword_type": "product | concept | problem | topic",
  "description": "1-2 sentence description of what this keyword is",
  "aliases": ["alternative names or spellings"],
  "related_terms": ["related technical terms"],
  "pain_patterns": ["common complaint patterns users have with this"],
  "search_queries": {{
    "github": ["additional GitHub search queries beyond discovered repos"]
  }},
  "relevant_subreddits": [
    "list of subreddit NAMES (without r/) where this topic is actively discussed",
    "include general tech subs if relevant (e.g. LocalLLaMA, MachineLearning, programming)",
    "aim for 5-10 subreddits"
  ]
}}

Return ONLY the JSON object, no markdown, no explanation.
"""

    # ------------------------------------------------------------------
    # Private: plan building + summaries
    # ------------------------------------------------------------------

    def _build_proposals(
        self,
        profile: KeywordProfile,
        collectors: list[BaseCollector],
    ) -> dict[str, Any]:
        """Build proposed SearchPlans from each collector using the enriched profile."""
        proposals: dict[str, Any] = {}
        for collector in collectors:
            try:
                plan = collector.build_plan(profile)
                proposals[collector.name] = [
                    {"query": t.query, "scope": t.scope, "params": t.params}
                    for t in plan.targets
                ]
            except Exception as e:
                log.warning("[resolver] %s.build_plan failed: %s", collector.name, e)
        return proposals

    @staticmethod
    def _discovery_summary(discovered: dict[str, DiscoveredResources]) -> dict[str, Any]:
        """Serialize discovered resources to a JSON-safe dict."""
        import dataclasses  # noqa: PLC0415

        return {
            name: dataclasses.asdict(resources)
            for name, resources in discovered.items()
        }

    @staticmethod
    def _profile_to_proposal(cached_data: dict[str, Any]) -> dict[str, Any]:
        """Convert cached profile_data back to proposal format."""
        return {
            "keyword": cached_data.get("raw", ""),
            "canonical_name": cached_data.get("canonical_name", ""),
            "keyword_type": cached_data.get("keyword_type", "product"),
            "description": cached_data.get("description", ""),
            "aliases": cached_data.get("aliases", []),
            "discovery": cached_data.get("discovered", {}),
            "proposed_plan": {},
            "_from_cache": True,
        }
