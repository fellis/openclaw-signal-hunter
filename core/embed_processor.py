"""
Embedding-based Processor.
Classifies raw signals using cosine similarity against rule vectors (no LLM for classify).
Uses the embedder HTTP service (bge-m3) for fast local inference.

Two-stage classification pipeline:
  1. Domain pre-filter (when hybrid_relevance.enabled=true):
     - Compute domain score against AI/ML positive/negative anchors.
     - domain_score >= domain_high -> run rule matching (auto-accept zone)
     - domain_score <= domain_low  -> mark irrelevant immediately (auto-reject zone)
     - between thresholds          -> borderline: enqueue for LLM review
  2. Rule matching (for auto-accept and borderline-relevant after LLM):
     - Cosine similarity against per-rule vectors (description + examples).
     - Negative anchor penalty applied per rule.

Performance (measured on Devstral 24B GPU):
  - Embed classify + LLM summary: ~1.6s per relevant signal, ~0.1s per irrelevant
  - Domain pre-filter adds <5ms per signal (vectors pre-computed at init)

Accuracy (validated on 800 signals, domain_high=0.40, domain_low=0.28, v6 LLM prompt):
  - F1: 92.1% | Recall: 97.3% vs F1: 60.0% with rule-only system
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np

from core.models import ExtractionRule, MatchedRule, ProcessedSignal
from storage.postgres import PostgresStorage
from storage.text_cleaner import strip_hn_prefix

log = logging.getLogger(__name__)


class EmbedProcessor:
    """
    Classifies raw signals via embedding cosine similarity.
    Optionally applies a domain pre-filter (hybrid_relevance config section)
    before rule matching to improve precision and recall.

    Rule vectors are pre-computed once at init:
    - Each rule produces N vectors: one for description + one per example phrase.
    - Signal similarity against a rule = max similarity over all rule vectors.
    """

    _RELEVANCE_THRESHOLD = 0.40
    _RULE_THRESHOLD = 0.42

    _INTENSITY_BANDS = [
        (0.80, 5),
        (0.65, 4),
        (0.50, 3),
        (0.40, 2),
        (0.00, 1),
    ]

    def __init__(
        self,
        storage: PostgresStorage,
        rules: list[ExtractionRule],
        config: dict[str, Any],
    ) -> None:
        self._storage = storage
        self._rules = rules

        proc_cfg = config.get("processor", {})
        self._relevance_threshold = float(proc_cfg.get("relevance_threshold", self._RELEVANCE_THRESHOLD))
        self._rule_threshold = float(proc_cfg.get("rule_threshold", self._RULE_THRESHOLD))
        base = self._rule_threshold
        per_rule_cfg = proc_cfg.get("rule_thresholds", {})
        self._per_rule_thresholds: dict[str, float] = {
            rule.name: float(per_rule_cfg.get(rule.name, base))
            for rule in rules
        }
        # Penalty parameters - shared for both domain pre-filter and per-rule negatives.
        # adjusted_sim = pos_sim - neg_weight * max(0, neg_sim - neg_min_sim)
        self._neg_weight = float(proc_cfg.get("neg_weight", 0.4))
        self._neg_min_sim = float(proc_cfg.get("neg_min_sim", 0.3))
        self._embed_batch_size = int(proc_cfg.get("embed_batch_size", 32))
        self._db_fetch_size = int(proc_cfg.get("batch_size", 50))
        self._max_body_chars = int(proc_cfg.get("max_body_chars", 1000))

        embedder_cfg = config.get("embedder", {})
        self._service_url = (embedder_cfg.get("service_url") or "http://localhost:6335").rstrip("/")

        # Domain pre-filter config
        hybrid_cfg = config.get("hybrid_relevance", {})
        self._hybrid_enabled = bool(hybrid_cfg.get("enabled", False))
        self._domain_high = float(hybrid_cfg.get("domain_high", 0.40))
        self._domain_low = float(hybrid_cfg.get("domain_low", 0.28))
        self._llm_task_priority = int(hybrid_cfg.get("llm_task_priority", 70))

        # Pre-compute rule vectors once at init
        log.info("[embed_processor] pre-computing rule vectors (%d rules)", len(rules))
        self._rule_vectors, self._rule_neg_vectors = self._embed_rules(rules)
        log.info("[embed_processor] rule vectors ready")

        # Pre-compute domain anchor vectors if hybrid mode is on
        self._domain_pos_vecs: np.ndarray | None = None
        self._domain_neg_vecs: np.ndarray | None = None
        if self._hybrid_enabled:
            anchors = hybrid_cfg.get("domain_anchors", {})
            pos_anchors = anchors.get("positive", [])
            neg_anchors = anchors.get("negative", [])
            if pos_anchors:
                log.info("[embed_processor] pre-computing domain anchor vectors (%d pos, %d neg)",
                         len(pos_anchors), len(neg_anchors))
                all_anchors = pos_anchors + neg_anchors
                all_vecs = self._embed_texts(all_anchors)
                self._domain_pos_vecs = all_vecs[:len(pos_anchors)]
                self._domain_neg_vecs = all_vecs[len(pos_anchors):] if neg_anchors else None
                log.info("[embed_processor] domain anchor vectors ready")
            else:
                log.warning("[embed_processor] hybrid_relevance.enabled=true but no domain_anchors.positive defined")
                self._hybrid_enabled = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_all(self, max_batches: int | None = None) -> int:
        """
        Classify unprocessed signals using embedding similarity.
        Returns count of processed signals. Returns -1 if lock held.
        """
        import fcntl
        from pathlib import Path

        lock_path = Path(__file__).parent.parent / ".processor.lock"
        lock_file = open(lock_path, "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("[embed_processor] another instance is already running, skipping")
            lock_file.close()
            return -1

        try:
            return self._run_batches(max_batches)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
            lock_path.unlink(missing_ok=True)

    def classify_single(self, raw: dict[str, Any]) -> ProcessedSignal:
        """
        Embed + rule-match a single raw signal and return ProcessedSignal.
        Used by LLM Worker for borderline-relevant signals after LLM confirms relevance.
        Does NOT apply domain pre-filter - assumes signal is already deemed relevant.
        """
        title = strip_hn_prefix(raw.get("title") or "")
        body = (raw.get("body") or "")[:self._max_body_chars]
        text = f"{title}\n\n{body}".strip() if title else body
        vec = self._embed_texts([text])
        cls = self._classify_vectors(vec)[0]
        return self._build_processed_signal(cls, raw)

    # ------------------------------------------------------------------
    # Private: batch loop
    # ------------------------------------------------------------------

    def _run_batches(self, max_batches: int | None) -> int:
        total = 0
        batches_done = 0

        while True:
            if max_batches is not None and batches_done >= max_batches:
                log.info("[embed_processor] reached max_batches=%d, stopping", max_batches)
                break

            signals = self._storage.fetch_unprocessed(limit=self._db_fetch_size)
            if not signals:
                break

            log.info("[embed_processor] fetched %d unprocessed signals", len(signals))

            for s in signals:
                title = s.get("title") or ""
                body = s.get("body") or ""
                title = strip_hn_prefix(title)
                if len(body) > self._max_body_chars:
                    body = body[:self._max_body_chars] + "..."
                s["_text"] = f"{title}\n\n{body}".strip() if title else body

            try:
                signal_vectors = self._embed_texts([s["_text"] for s in signals])
            except Exception as e:
                log.error("[embed_processor] failed to embed signals batch: %s", e)
                break

            if self._hybrid_enabled and self._domain_pos_vecs is not None:
                saved = self._classify_with_domain_filter(signals, signal_vectors)
            else:
                classifications = self._classify_vectors(signal_vectors)
                saved = 0
                relevant_count = 0
                irrelevant_count = 0
                for signal, cls in zip(signals, classifications):
                    ps = self._build_processed_signal(cls, signal)
                    if ps:
                        self._storage.upsert_processed_signal(ps)
                        saved += 1
                        if cls["is_relevant"]:
                            relevant_count += 1
                        else:
                            irrelevant_count += 1
                log.info("[embed_processor] batch %d: %d relevant, %d irrelevant",
                         batches_done + 1, relevant_count, irrelevant_count)

            total += saved
            batches_done += 1

        log.info("[embed_processor] done. batches=%d processed=%d", batches_done, total)
        return total

    def _classify_with_domain_filter(
        self,
        signals: list[dict[str, Any]],
        signal_vectors: np.ndarray,
    ) -> int:
        """Apply domain pre-filter then route: auto-accept, auto-reject, or borderline."""
        pos_sims = signal_vectors @ self._domain_pos_vecs.T
        max_pos = pos_sims.max(axis=1)

        if self._domain_neg_vecs is not None and len(self._domain_neg_vecs) > 0:
            neg_sims = signal_vectors @ self._domain_neg_vecs.T
            max_neg = neg_sims.max(axis=1)
            penalty = np.maximum(0.0, max_neg - self._neg_min_sim)
            domain_scores = max_pos - self._neg_weight * penalty
        else:
            domain_scores = max_pos

        auto_accept = auto_reject = borderline = 0
        saved = 0

        for i, signal in enumerate(signals):
            score = float(domain_scores[i])

            if score >= self._domain_high:
                # Auto-accept: domain score alone is sufficient evidence of AI/ML relevance.
                # Rule matching is intentionally skipped here - this matches the validated
                # test logic (test_hybrid_500.py, F1 92.1%). Running rule matching in this
                # zone incorrectly rejects ~44% of AI/ML signals that lack a matching rule.
                ps = self._build_processed_signal(
                    {
                        "is_relevant": True,
                        "matched_rules": [],
                        "confidence": round(score, 4),
                        "intensity": self._sim_to_intensity(score),
                        "summary": None,
                        "language": "en",
                        "products_mentioned": [],
                        "irrelevant_reason": None,
                        "borderline_override_pending": False,
                    },
                    signal,
                )
                if ps:
                    self._storage.upsert_processed_signal(ps)
                    saved += 1
                auto_accept += 1

            elif score <= self._domain_low:
                # Auto-reject: mark irrelevant immediately
                ps = self._build_processed_signal(
                    {
                        "is_relevant": False,
                        "matched_rules": [],
                        "confidence": round(score, 4),
                        "intensity": 1,
                        "summary": None,
                        "language": "en",
                        "products_mentioned": [],
                        "irrelevant_reason": "domain score below threshold",
                        "borderline_override_pending": False,
                    },
                    signal,
                )
                if ps:
                    self._storage.upsert_processed_signal(ps)
                    saved += 1
                auto_reject += 1

            else:
                # Borderline: save as pending, enqueue for LLM review
                ps = self._build_processed_signal(
                    {
                        "is_relevant": False,
                        "matched_rules": [],
                        "confidence": round(score, 4),
                        "intensity": 1,
                        "summary": None,
                        "language": "en",
                        "products_mentioned": [],
                        "irrelevant_reason": "borderline domain score - pending LLM review",
                        "borderline_override_pending": True,
                    },
                    signal,
                )
                if ps:
                    self._storage.upsert_processed_signal(ps)
                    self._storage.enqueue_llm_task(
                        task_type="borderline_relevance",
                        priority=self._llm_task_priority,
                        payload={"dedup_key": signal["dedup_key"]},
                    )
                    saved += 1
                borderline += 1

        log.info(
            "[embed_processor] domain filter: %d auto-accept, %d auto-reject, %d borderline",
            auto_accept, auto_reject, borderline,
        )
        return saved

    # ------------------------------------------------------------------
    # Private: embedding
    # ------------------------------------------------------------------

    def _embed_rules(
        self,
        rules: list[ExtractionRule],
    ) -> tuple[list[np.ndarray], list[np.ndarray | None]]:
        """Embed each rule as positive and negative anchor vectors."""
        pos_lists: list[list[str]] = []
        neg_lists: list[list[str]] = []
        for rule in rules:
            pos = [f"{rule.name}: {rule.description}"]
            pos.extend(rule.examples or [])
            pos_lists.append(pos)
            neg_lists.append(rule.negative_examples or [])

        flat_pos = [t for anchors in pos_lists for t in anchors]
        flat_pos_vecs = self._embed_texts(flat_pos)

        pos_result: list[np.ndarray] = []
        offset = 0
        for anchors in pos_lists:
            n = len(anchors)
            pos_result.append(flat_pos_vecs[offset:offset + n])
            offset += n

        neg_result: list[np.ndarray | None] = []
        flat_neg = [t for anchors in neg_lists for t in anchors]
        if flat_neg:
            flat_neg_vecs = self._embed_texts(flat_neg)
            offset = 0
            for anchors in neg_lists:
                n = len(anchors)
                if n > 0:
                    neg_result.append(flat_neg_vecs[offset:offset + n])
                else:
                    neg_result.append(None)
                offset += n
        else:
            neg_result = [None] * len(rules)

        return pos_result, neg_result

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed texts via embedder service in batches. Returns (N, D) float32."""
        import httpx

        all_vectors = []
        for i in range(0, len(texts), self._embed_batch_size):
            batch = texts[i : i + self._embed_batch_size]
            resp = httpx.post(
                f"{self._service_url}/embed",
                json={"texts": batch, "normalize": True},
                timeout=180.0,
            )
            resp.raise_for_status()
            all_vectors.extend(resp.json()["vectors"])

        return np.array(all_vectors, dtype=np.float32)

    # ------------------------------------------------------------------
    # Private: classification
    # ------------------------------------------------------------------

    def _classify_vectors(self, signal_vectors: np.ndarray) -> list[dict[str, Any]]:
        """Compute cosine similarity between each signal and each rule."""
        results = []

        for sig_vec in signal_vectors:
            rule_sims: list[float] = []
            for pos_vecs, neg_vecs in zip(self._rule_vectors, self._rule_neg_vectors):
                pos_sim = float((pos_vecs @ sig_vec).max())
                if neg_vecs is not None and self._neg_weight > 0.0:
                    neg_sim = float((neg_vecs @ sig_vec).max())
                    penalty = max(0.0, neg_sim - self._neg_min_sim)
                    adjusted_sim = pos_sim - self._neg_weight * penalty
                else:
                    adjusted_sim = pos_sim
                rule_sims.append(adjusted_sim)

            max_sim = max(rule_sims) if rule_sims else 0.0

            matched_rule_names = [
                self._rules[j].name
                for j, sim in enumerate(rule_sims)
                if sim >= self._per_rule_thresholds.get(self._rules[j].name, self._rule_threshold)
            ]

            is_relevant = max_sim >= self._relevance_threshold and len(matched_rule_names) > 0

            results.append({
                "is_relevant": is_relevant,
                "matched_rules": matched_rule_names,
                "confidence": round(max_sim, 4),
                "intensity": self._sim_to_intensity(max_sim),
                "summary": None,
                "language": "en",
                "products_mentioned": [],
                "irrelevant_reason": None if is_relevant else "below similarity threshold",
                "borderline_override_pending": False,
            })

        return results

    @staticmethod
    def _sim_to_intensity(sim: float) -> int:
        """Map cosine similarity score to intensity 1-5."""
        for threshold, intensity in EmbedProcessor._INTENSITY_BANDS:
            if sim >= threshold:
                return intensity
        return 1

    # ------------------------------------------------------------------
    # Private: build ProcessedSignal
    # ------------------------------------------------------------------

    def _build_processed_signal(
        self,
        cls: dict[str, Any],
        raw: dict[str, Any],
    ) -> ProcessedSignal | None:
        """Build ProcessedSignal from classification result and raw signal row."""
        is_relevant = cls["is_relevant"]
        confidence = cls["confidence"]
        intensity = cls["intensity"]
        summary = cls.get("summary")
        borderline_override_pending = cls.get("borderline_override_pending", False)

        matched_rules = [
            MatchedRule(
                rule_name=rule_name,
                confidence=confidence,
                evidence=summary or "",
            )
            for rule_name in cls.get("matched_rules", [])
        ]

        rank_score = self._compute_rank_score(
            intensity=intensity,
            confidence=confidence,
            score=raw.get("score", 0) or 0,
            comments_count=raw.get("comments_count", 0) or 0,
            created_at=raw.get("created_at"),
        )

        raw_extra = raw.get("extra") or {}
        if isinstance(raw_extra, str):
            try:
                raw_extra = json.loads(raw_extra)
            except (ValueError, TypeError):
                raw_extra = {}
        keywords_matched = raw_extra.get("keywords") or []

        return ProcessedSignal(
            raw_signal_id=str(raw["id"]),
            dedup_key=raw["dedup_key"],
            is_relevant=is_relevant,
            matched_rules=matched_rules,
            summary=summary,
            products_mentioned=[],
            intensity=intensity,
            confidence=confidence,
            keywords_matched=keywords_matched,
            language=cls.get("language") or "en",
            rank_score=rank_score,
            borderline_override_pending=borderline_override_pending,
        )

    @staticmethod
    def _compute_rank_score(
        intensity: int,
        confidence: float,
        score: int,
        created_at: datetime | None,
        comments_count: int = 0,
    ) -> float:
        """rank_score = (engagement + quality) * time_decay."""
        now = datetime.now(timezone.utc)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        hours_ago = (now - created_at).total_seconds() / 3600.0 if created_at else 0

        engagement_raw = max(0, score) + 0.5 * max(0, comments_count)
        engagement = 0.3 * math.log10(1 + engagement_raw)
        quality = 0.7 * (intensity / 5.0) * confidence
        decay = 0.5 ** (hours_ago / 168.0)
        return round((engagement + quality) * decay, 4)
