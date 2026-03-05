"""
Embedder.
Reads pending entries from embedding_queue, generates bge-m3 vectors for summary,
upserts into Qdrant, marks done in Postgres. Implements Outbox pattern.

Key validated facts from spike Phase 2:
- Embed summary (not body) - summary is the clean, structured representation
- normalize_embeddings=True required for cosine similarity
- batch_size=64 works stably
- Model is loaded once and reused (singleton within a process run)
"""

from __future__ import annotations

import logging
from datetime import timezone
from typing import Any

from storage.postgres import PostgresStorage
from storage.vector import VectorStorage

log = logging.getLogger(__name__)

_MODEL_NAME = "BAAI/bge-m3"


class Embedder:
    """
    Vectorizes processed signals and upserts them into Qdrant.
    Model is loaded lazily on first call and kept in memory.
    """

    def __init__(
        self,
        storage: PostgresStorage,
        vector: VectorStorage,
        batch_size: int = 64,
        device: str = "cpu",
    ) -> None:
        self._storage = storage
        self._vector = vector
        self._batch_size = batch_size
        self._device = device
        self._model = None

    def _get_model(self):
        """Lazy-load bge-m3. Loaded once per process, never reloaded."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            log.info("[embedder] loading model '%s' on device '%s'", _MODEL_NAME, self._device)
            self._model = SentenceTransformer(_MODEL_NAME, device=self._device)
        return self._model

    def embed_pending(self) -> int:
        """
        Process all pending items in embedding_queue.
        Returns number of vectors upserted.
        """
        self._vector.ensure_collection()
        rows = self._storage.fetch_pending_embeddings(limit=512)

        if not rows:
            log.info("[embedder] nothing pending")
            return 0

        log.info("[embedder] embedding %d pending signals", len(rows))
        model = self._get_model()
        total_upserted = 0

        for batch_start in range(0, len(rows), self._batch_size):
            batch = rows[batch_start : batch_start + self._batch_size]
            summaries = [r["summary"] for r in batch]

            try:
                vectors = model.encode(
                    summaries,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                points = [
                    {
                        "id": self._to_int_id(batch[i]["raw_signal_id"]),
                        "vector": vectors[i].tolist(),
                        "payload": self._build_payload(batch[i]),
                    }
                    for i in range(len(batch))
                ]
                self._vector.upsert(points)

                for row in batch:
                    self._storage.mark_embedding_done(str(row["queue_id"]))

                total_upserted += len(points)
                log.info(
                    "[embedder] upserted batch %d-%d (%d points)",
                    batch_start + 1,
                    batch_start + len(batch),
                    len(points),
                )
            except Exception as e:
                log.error("[embedder] batch %d-%d failed: %s", batch_start + 1, batch_start + len(batch), e)
                for row in batch:
                    self._storage.mark_embedding_failed(str(row["queue_id"]))

        log.info("[embedder] done. total upserted: %d", total_upserted)
        return total_upserted

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query with the same model used for indexing."""
        model = self._get_model()
        vector = model.encode(query, normalize_embeddings=True, show_progress_bar=False)
        return vector.tolist()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_int_id(raw_signal_id: Any) -> int:
        """
        Qdrant point IDs must be uint64.
        We hash the UUID to get a stable integer ID.
        """
        import hashlib  # noqa: PLC0415

        h = hashlib.sha256(str(raw_signal_id).encode()).digest()
        return int.from_bytes(h[:8], "big")

    @staticmethod
    def _build_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Build Qdrant point payload from a DB row."""
        created_at = row.get("created_at")
        if created_at and hasattr(created_at, "tzinfo") and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        date_str = created_at.isoformat() if created_at else None

        matched_rules = row.get("matched_rules") or []
        if isinstance(matched_rules, str):
            import json  # noqa: PLC0415

            matched_rules = json.loads(matched_rules)

        primary_rule = matched_rules[0].get("rule_name") if matched_rules else None

        return {
            "rule": primary_rule,
            "matched_rules": [r.get("rule_name") for r in matched_rules if isinstance(r, dict)],
            "intensity": row.get("intensity"),
            "rank_score": float(row.get("rank_score") or 0),
            "url": row.get("url"),
            "title": row.get("title"),
            "date": date_str,
        }
