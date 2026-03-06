"""
Semantic clustering service.
Groups signals by cosine similarity of their Qdrant vectors.
Names clusters with a single local LLM call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

COLLECTION = "signals"


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


def greedy_cluster(
    signal_ids: list[str],
    vectors: dict[str, list[float]],
    threshold: float = 0.75,
) -> dict[int, list[str]]:
    """
    Greedy cosine clustering of signals with known vectors.
    Signals without vectors are placed in their own singleton clusters.
    Returns {cluster_id: [raw_signal_id, ...]}.
    """
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
                sim = float(np.dot(arr[i], arr[j]))
                if sim >= threshold:
                    cluster.append(ids_with_vec[j])
                    assigned[j] = True
            clusters[cluster_id] = cluster
            cluster_id += 1

    # Signals without vectors go into one "other" cluster if any exist
    if ids_without_vec:
        clusters[cluster_id] = ids_without_vec

    return clusters


def name_clusters(
    clusters: dict[int, list[str]],
    titles_by_id: dict[str, str],
) -> dict[int, str]:
    """
    Generate descriptive names for all clusters using the local LLM.
    Falls back to 'Cluster N' on any error.
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

    prompt = (
        "You are a concise topic labeler. Name each cluster with a short label (2-5 words, English).\n"
        "The label must capture the main theme. Return ONLY valid JSON.\n\n"
        "Clusters:\n" + "\n".join(lines) + "\n\n"
        'Format: {"0": "label", "1": "label", ...}'
    )

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
            max_tokens=512,
        )
        text = response.choices[0].message.content.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            raw = json.loads(text[start:end])
            return {int(k): str(v) for k, v in raw.items()}
    except Exception as e:
        log.warning("[clustering] LLM naming failed: %s", e)

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


def build_cluster_key(signal_ids: list[str]) -> str:
    """Stable cache key for a set of signal IDs."""
    return hashlib.md5(json.dumps(sorted(signal_ids)).encode()).hexdigest()
