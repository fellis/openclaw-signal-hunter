"""
Qdrant vector storage wrapper.
Handles collection creation, upsert, semantic search, and neighbor lookup.
All Qdrant API calls are isolated here - the rest of the codebase uses this interface.
"""

from __future__ import annotations

import logging
import os
from datetime import timezone
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

log = logging.getLogger(__name__)

COLLECTION_NAME = "signals"
EMBEDDING_DIM = 1024


def _get_qdrant_url() -> str:
    return os.environ.get("QDRANT_URL", "http://localhost:6333")


class VectorStorage:
    """
    Thin wrapper over Qdrant.
    One collection ('signals'), cosine distance, 1024 dimensions.
    Uses qdrant-client 1.17+ API (query_points, not search).
    """

    def __init__(self, qdrant_url: str | None = None) -> None:
        self._url = qdrant_url or _get_qdrant_url()
        self._client: QdrantClient | None = None

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(url=self._url)
        return self._client

    def ensure_collection(self) -> None:
        """Create collection if it does not exist. Idempotent."""
        client = self._get_client()
        existing = {c.name for c in client.get_collections().collections}
        if COLLECTION_NAME not in existing:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
            log.info("Created Qdrant collection '%s'", COLLECTION_NAME)

    def upsert(self, points: list[dict[str, Any]]) -> int:
        """
        Upsert a batch of points into Qdrant.

        Each point dict must have:
          - id: int (raw_signal_id from Postgres)
          - vector: list[float] (1024-dim, pre-normalized)
          - payload: dict with {rule, intensity, rank_score, url, title, date}

        Returns count of upserted points.
        """
        if not points:
            return 0

        client = self._get_client()
        qdrant_points = [
            PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
            for p in points
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=qdrant_points)
        return len(qdrant_points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 50,
        threshold: float = 0.5,
        filter_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic search: return top_k points above similarity threshold.
        Results are sorted by similarity descending (Qdrant default).

        Returns list of {id, similarity, payload}.
        """
        client = self._get_client()
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        return [
            {"id": r.id, "similarity": r.score, "payload": r.payload}
            for r in response.points
            if r.score >= threshold
        ]

    def find_similar(
        self, point_id: int, top_k: int = 5
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """
        Find nearest neighbors of an existing point by its id.
        Returns (source_point, neighbors_list).
        Source point is excluded from neighbors.
        """
        client = self._get_client()
        existing = client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[point_id],
            with_vectors=True,
            with_payload=True,
        )
        if not existing:
            return None, []

        source = existing[0]
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=source.vector,
            limit=top_k + 1,
            with_payload=True,
        )
        neighbors = [
            {"id": r.id, "similarity": r.score, "payload": r.payload}
            for r in response.points
            if r.id != point_id
        ][:top_k]

        source_dict = {"id": source.id, "payload": source.payload}
        return source_dict, neighbors

    def delete_by_ids(self, ids: list[int]) -> None:
        """Remove vectors by raw_signal_id list (used during reprocess)."""
        if not ids:
            return
        client = self._get_client()
        from qdrant_client.models import PointIdsList  # noqa: PLC0415

        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=ids),
        )

    def count(self) -> int:
        """Return total number of vectors in the collection."""
        client = self._get_client()
        try:
            info = client.get_collection(COLLECTION_NAME)
            return info.points_count or 0
        except Exception:
            return 0
