"""
Semantic clustering service.

Uses the Strategy pattern so clustering implementations can be swapped
without touching callers. Active strategy is selected via the
CLUSTERING_STRATEGY env variable (default: "kmeans").

Supported strategies
--------------------
kmeans  - Spherical Mini-batch K-Means (sklearn). Recommended.
          Equivalent to cosine K-Means because vectors are L2-normalised.
          k = min(MAX_CLUSTERS, max(MIN_CLUSTERS, sqrt(n_vectorised))).

greedy  - Original greedy cosine clustering (O(n^2)). Kept for reference
          and small datasets (<200 signals).

Configuration via env
---------------------
CLUSTERING_STRATEGY   "kmeans" | "greedy"   default: "kmeans"
MAX_CLUSTERS          int                    default: 50
MIN_CLUSTERS          int                    default: 5
GREEDY_THRESHOLD      float                  default: 0.72
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from math import isqrt
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

COLLECTION = "signals"


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def uuid_to_qdrant_id(raw_signal_id: str) -> int:
    """Same hash as embedder.py - must stay in sync."""
    h = hashlib.sha256(str(raw_signal_id).encode()).digest()
    return int.from_bytes(h[:8], "big")


def fetch_vectors(signal_ids: list[str]) -> dict[str, list[float]]:
    """
    Fetch Qdrant vectors for given raw_signal_ids.
    Returns {raw_signal_id: vector}.
    """
    from qdrant_client import QdrantClient  # noqa: PLC0415

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=qdrant_url)

    id_map = {uuid_to_qdrant_id(sid): sid for sid in signal_ids}
    qdrant_ids = list(id_map.keys())

    if not qdrant_ids:
        return {}

    try:
        points = client.retrieve(
            collection_name=COLLECTION,
            ids=qdrant_ids,
            with_vectors=True,
        )
        return {id_map[p.id]: p.vector for p in points if p.vector is not None}
    except Exception as e:
        log.warning("[clustering] Qdrant retrieve failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Abstract strategy
# ---------------------------------------------------------------------------

class ClusteringStrategy(ABC):
    """
    Contract for all clustering implementations.
    Signals without vectors are excluded from clustering and returned
    separately so callers can handle them (e.g. put in an "Other" group).
    """

    @abstractmethod
    def cluster(
        self,
        signal_ids: list[str],
        vectors: dict[str, list[float]],
    ) -> dict[int, list[str]]:
        """
        Cluster signals that have vectors.
        Signals without vectors are placed together in the last cluster.

        Returns
        -------
        dict[cluster_id, list[raw_signal_id]]
        """


# ---------------------------------------------------------------------------
# Strategy: Greedy cosine (original, O(n^2), kept for reference)
# ---------------------------------------------------------------------------

class GreedyCosineClustering(ClusteringStrategy):
    """
    Original greedy algorithm: for each unassigned signal i, create a cluster
    and pull in every subsequent j with cosine_similarity(i, j) >= threshold.

    Drawbacks
    ---------
    - O(n^2) comparisons - slow for n > 500.
    - Compares against the anchor (first member), not the centroid.
    - Produces hundreds of tiny clusters on large inputs.

    Use for small categories (<200 signals) or A/B comparison.
    """

    def __init__(self, threshold: float = 0.72) -> None:
        self.threshold = threshold

    def cluster(
        self,
        signal_ids: list[str],
        vectors: dict[str, list[float]],
    ) -> dict[int, list[str]]:
        ids_with_vec = [sid for sid in signal_ids if sid in vectors]
        ids_without_vec = [sid for sid in signal_ids if sid not in vectors]

        clusters: dict[int, list[str]] = {}
        cluster_id = 0

        if ids_with_vec:
            arr = np.array([vectors[sid] for sid in ids_with_vec], dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms

            n = len(ids_with_vec)
            assigned = [False] * n

            for i in range(n):
                if assigned[i]:
                    continue
                cluster = [ids_with_vec[i]]
                assigned[i] = True
                for j in range(i + 1, n):
                    if assigned[j]:
                        continue
                    if float(np.dot(arr[i], arr[j])) >= self.threshold:
                        cluster.append(ids_with_vec[j])
                        assigned[j] = True
                clusters[cluster_id] = cluster
                cluster_id += 1

        if ids_without_vec:
            clusters[cluster_id] = ids_without_vec

        return clusters


# ---------------------------------------------------------------------------
# Strategy: Spherical Mini-batch K-Means (recommended)
# ---------------------------------------------------------------------------

class KMeansClustering(ClusteringStrategy):
    """
    Spherical K-Means via sklearn MiniBatchKMeans on L2-normalised vectors.
    Normalisation makes euclidean distance equivalent to cosine distance,
    so this effectively performs cosine K-Means.

    k is chosen automatically:
        k = min(max_clusters, max(min_clusters, isqrt(n_vectorised)))

    Signals without vectors are placed in a trailing cluster so they are
    still reachable, but their cluster is unnamed ("Other").

    Advantages over greedy
    ----------------------
    - O(k * n * iterations) - orders of magnitude faster.
    - Centroid is updated with every new member (no anchor drift).
    - Predictable, bounded cluster count.
    - Deterministic (fixed random_state).
    """

    def __init__(
        self,
        max_clusters: int = 50,
        min_clusters: int = 5,
    ) -> None:
        self.max_clusters = max_clusters
        self.min_clusters = min_clusters

    def _choose_k(self, n: int) -> int:
        return min(self.max_clusters, max(self.min_clusters, isqrt(n)))

    def cluster(
        self,
        signal_ids: list[str],
        vectors: dict[str, list[float]],
    ) -> dict[int, list[str]]:
        from sklearn.cluster import MiniBatchKMeans  # noqa: PLC0415

        ids_with_vec = [sid for sid in signal_ids if sid in vectors]
        ids_without_vec = [sid for sid in signal_ids if sid not in vectors]

        clusters: dict[int, list[str]] = {}

        if ids_with_vec:
            arr = np.array([vectors[sid] for sid in ids_with_vec], dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms  # unit vectors -> cosine ~ euclidean

            k = self._choose_k(len(ids_with_vec))
            # Ensure k does not exceed number of samples
            k = min(k, len(ids_with_vec))

            model = MiniBatchKMeans(
                n_clusters=k,
                random_state=42,
                batch_size=min(1024, len(ids_with_vec)),
                n_init=3,
            )
            labels = model.fit_predict(arr)

            for i, sid in enumerate(ids_with_vec):
                cid = int(labels[i])
                clusters.setdefault(cid, []).append(sid)

        # Signals without vectors go into a trailing "other" cluster
        if ids_without_vec:
            other_id = max(clusters.keys(), default=-1) + 1
            clusters[other_id] = ids_without_vec

        log.info(
            "[clustering] KMeans: %d signals -> %d clusters (vectorised: %d, no-vec: %d)",
            len(signal_ids),
            len(clusters),
            len(ids_with_vec),
            len(ids_without_vec),
        )
        return clusters


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_clustering_strategy(name: str | None = None) -> ClusteringStrategy:
    """
    Return a configured ClusteringStrategy.
    Strategy name is resolved from argument, then CLUSTERING_STRATEGY env var,
    then defaults to "kmeans".
    """
    resolved = (name or os.environ.get("CLUSTERING_STRATEGY", "kmeans")).lower()

    if resolved == "greedy":
        threshold = float(os.environ.get("GREEDY_THRESHOLD", "0.72"))
        log.info("[clustering] Strategy: GreedyCosine (threshold=%.2f)", threshold)
        return GreedyCosineClustering(threshold=threshold)

    max_clusters = int(os.environ.get("MAX_CLUSTERS", "50"))
    min_clusters = int(os.environ.get("MIN_CLUSTERS", "5"))
    log.info(
        "[clustering] Strategy: KMeans (max=%d, min=%d)", max_clusters, min_clusters
    )
    return KMeansClustering(max_clusters=max_clusters, min_clusters=min_clusters)


# ---------------------------------------------------------------------------
# LLM cluster naming
# ---------------------------------------------------------------------------

def name_clusters(
    clusters: dict[int, list[str]],
    titles_by_id: dict[str, str],
    parent_category: str | None = None,
) -> dict[int, str]:
    """
    Generate descriptive names for all clusters using the local LLM.
    parent_category is passed as context so the LLM avoids repeating it in labels.
    Falls back to first signal title on any error.
    Returns {cluster_id: name}.
    """
    if not clusters:
        return {}

    base_url = os.environ.get("LOCAL_LLM_BASE_URL")
    model = os.environ.get("LOCAL_LLM_MODEL", "llm")
    api_key = os.environ.get("LOCAL_LLM_API_KEY", "local")

    if not base_url:
        log.warning("[clustering] LOCAL_LLM_BASE_URL not set - using fallback names")
        return _fallback_names(clusters, titles_by_id)

    lines = []
    for cid, sids in sorted(clusters.items()):
        samples = [titles_by_id[sid] for sid in sids[:5] if sid in titles_by_id and titles_by_id[sid]]
        snippet = " | ".join(samples) if samples else "(no titles)"
        lines.append(f"{cid} ({len(sids)} signals): {snippet}")

    # Build context hint so the LLM generates specific sub-topic names
    # instead of repeating the parent category (e.g. "AI Agent X" inside
    # the "pain_point_ai_agent" category).
    category_hint = ""
    if parent_category:
        readable = parent_category.replace("_", " ").title()
        category_hint = (
            f"These clusters are all sub-topics within the parent category: \"{readable}\".\n"
            f"Do NOT repeat the parent category name in your labels. "
            f"Focus on what makes each cluster DISTINCT within that category.\n"
        )

    prompt = (
        "You are a concise topic labeler. Name each cluster with a short label (2-5 words, English).\n"
        + category_hint +
        "The label must capture the specific theme. Return ONLY valid JSON.\n\n"
        "Clusters:\n" + "\n".join(lines) + "\n\n"
        'Format: {"0": "label", "1": "label", ...}'
    )

    t0 = time.monotonic()
    log.info("[clustering] LLM cluster naming START (shared backend with worker)")
    try:
        import httpx  # noqa: PLC0415
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
            http_client=httpx.Client(verify=False),
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
        )
        text = response.choices[0].message.content.strip()
        finish = response.choices[0].finish_reason
        if finish == "length":
            log.warning("[clustering] LLM response truncated (max_tokens too low?) - trying partial parse")
            # Try to salvage partial JSON by closing the object
            text = text.rstrip().rstrip(",") + "}"

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                raw = json.loads(text[start:end])
                log.info("[clustering] LLM cluster naming DONE in %.1fs", time.monotonic() - t0)
                return {int(k): str(v) for k, v in raw.items()}
            except json.JSONDecodeError as parse_err:
                log.warning("[clustering] LLM JSON parse failed after %.1fs: %s", time.monotonic() - t0, parse_err)
    except Exception as e:
        log.warning("[clustering] LLM naming failed after %.1fs: %s", time.monotonic() - t0, e)

    log.info("[clustering] LLM cluster naming fallback after %.1fs", time.monotonic() - t0)
    return _fallback_names(clusters, titles_by_id)


def _fallback_names(
    clusters: dict[int, list[str]],
    titles_by_id: dict[str, str],
) -> dict[int, str]:
    """Use first signal title as cluster name (truncated)."""
    names = {}
    for cid, sids in clusters.items():
        first_title = next(
            (titles_by_id[sid] for sid in sids if sid in titles_by_id and titles_by_id[sid]),
            None,
        )
        if first_title:
            names[cid] = first_title[:60] + ("..." if len(first_title) > 60 else "")
        else:
            names[cid] = f"Cluster {cid + 1}"
    return names


# ---------------------------------------------------------------------------
# Cache key helper
# ---------------------------------------------------------------------------

def build_cluster_key(signal_ids: list[str]) -> str:
    """Stable cache key for a set of signal IDs."""
    return hashlib.md5(json.dumps(sorted(signal_ids)).encode()).hexdigest()
