"""
Search API router.
Text search via PostgreSQL ILIKE.
Semantic search via Qdrant with payload filters.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request

from web_server.db import fetchall
from web_server.services.clustering import uuid_to_qdrant_id

log = logging.getLogger(__name__)

router = APIRouter()


def _embed_query(text: str) -> list[float]:
    """Embed a search query via the embedder HTTP service."""
    embedder_url = os.environ.get("EMBEDDER_URL", "http://localhost:6335")
    resp = httpx.post(
        f"{embedder_url}/embed-query",
        json={"text": text, "normalize": True},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["vector"]


def _qdrant_search(
    vector: list[float],
    top_k: int,
    threshold: float,
    sources: list[str],
    intensities: list[int],
    confidence_min: float | None,
    confidence_max: float | None,
    keywords: list[str],
    date_from: str | None,
    date_to: str | None,
) -> list[dict]:
    """Semantic search in Qdrant with payload filter."""
    from qdrant_client import QdrantClient  # noqa: PLC0415
    from qdrant_client.models import (  # noqa: PLC0415
        Filter,
        FieldCondition,
        MatchAny,
        Range,
    )

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=qdrant_url)

    must: list[Any] = []

    if sources:
        must.append(FieldCondition(key="source_type", match=MatchAny(any=sources)))
    if keywords:
        must.append(FieldCondition(key="keywords", match=MatchAny(any=keywords)))
    if intensities:
        must.append(FieldCondition(key="intensity", match=MatchAny(any=intensities)))
    if confidence_min is not None or confidence_max is not None:
        must.append(FieldCondition(
            key="confidence",
            range=Range(gte=confidence_min, lte=confidence_max),
        ))

    qdrant_filter = Filter(must=must) if must else None

    try:
        hits = client.query_points(
            collection_name="signals",
            query=vector,
            limit=top_k,
            score_threshold=threshold,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [
            {
                "qdrant_id": hit.id,
                "similarity": round(hit.score, 4),
                "payload": hit.payload or {},
            }
            for hit in hits.points
        ]
    except Exception as e:
        log.warning("[search] Qdrant search failed: %s", e)
        return []


def _enrich_from_pg(qdrant_results: list[dict], lang: str = "en") -> list[dict]:
    """Join Qdrant results with PostgreSQL for full signal data."""
    if not qdrant_results:
        return []

    urls = [r["payload"].get("url") for r in qdrant_results if r["payload"].get("url")]
    if not urls:
        return []

    url_to_sim = {r["payload"]["url"]: r["similarity"] for r in qdrant_results}

    rows = fetchall(
        """
        SELECT
            r.id::text AS raw_signal_id,
            r.title,
            r.url,
            r.source,
            r.author,
            r.score,
            r.comments_count,
            r.created_at,
            r.collected_at,
            p.summary,
            p.rank_score::float,
            p.intensity,
            p.confidence::float,
            p.language,
            tt.text AS title_translated,
            ts.text AS summary_translated
        FROM raw_signals r
        JOIN processed_signals p ON p.raw_signal_id = r.id
        LEFT JOIN signal_translations tt
               ON tt.signal_id = r.id AND tt.lang = %s AND tt.field = 'title'
        LEFT JOIN signal_translations ts
               ON ts.signal_id = r.id AND ts.lang = %s AND ts.field = 'summary'
        WHERE r.url = ANY(%s)
        """,
        (lang, lang, urls),
    )

    use_translation = lang != "en"
    results = []
    for row in rows:
        url = row["url"]
        sim = url_to_sim.get(url, 0.0)
        title   = (row["title_translated"] or row["title"]) if use_translation else row["title"]
        summary = (row["summary_translated"] or row["summary"]) if use_translation else row["summary"]
        results.append({
            "raw_signal_id": row["raw_signal_id"],
            "title": title,
            "title_original": row["title"],
            "url": url,
            "source": row["source"],
            "author": row["author"],
            "score": row["score"],
            "comments_count": row["comments_count"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "collected_at": row["collected_at"].isoformat() if row["collected_at"] else None,
            "summary": summary,
            "summary_original": row["summary"],
            "translation_available": bool(row["title_translated"]),
            "rank_score": row["rank_score"],
            "intensity": row["intensity"],
            "confidence": row["confidence"],
            "language": row["language"],
            "similarity": sim,
            "combined_score": round(row["rank_score"] * sim, 4),
        })

    results.sort(key=lambda r: r["combined_score"], reverse=True)
    return results


@router.get("/search/semantic")
async def semantic_search(
    request: Request,
    q: str = Query(..., min_length=2),
    top_k: int = Query(50, ge=5, le=200),
    threshold: float = Query(0.45, ge=0.0, le=1.0),
    sources: list[str] = Query(default=[]),
    keywords: list[str] = Query(default=[]),
    intensities: list[int] = Query(default=[]),
    confidence_min: float | None = Query(None, ge=0.0, le=1.0),
    confidence_max: float | None = Query(None, ge=0.0, le=1.0),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    lang: str = Query("en"),
):
    """Semantic search via Qdrant with payload filters."""
    cache = request.app.state.cache
    cache_key = dict(
        q=q, top_k=top_k, threshold=threshold,
        sources=sorted(sources), keywords=sorted(keywords),
        intensities=sorted(intensities),
        confidence_min=confidence_min, confidence_max=confidence_max,
        date_from=date_from, date_to=date_to,
    )
    cached = cache.get("semantic_search", cache_key)
    if cached is not None:
        return cached

    try:
        vector = _embed_query(q)
    except Exception as e:
        log.warning("[search/semantic] embed_query failed: %s", e)
        return {"results": [], "error": "Embedder service unavailable"}

    hits = _qdrant_search(
        vector=vector,
        top_k=top_k,
        threshold=threshold,
        sources=sources,
        intensities=intensities,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        keywords=keywords,
        date_from=date_from,
        date_to=date_to,
    )

    results = _enrich_from_pg(hits, lang=lang)
    response = {"results": results, "total": len(results), "query": q}
    cache.set("semantic_search", cache_key, value=response, ttl=3600)
    return response


@router.get("/search/text")
async def text_search(
    request: Request,
    q: str = Query(..., min_length=2),
    limit: int = Query(50, ge=5, le=200),
    sources: list[str] = Query(default=[]),
    keywords: list[str] = Query(default=[]),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    sort_by: str = Query("rank_score"),
    sort_dir: str = Query("desc"),
    lang: str = Query("en"),
):
    """Full-text search via PostgreSQL ILIKE."""
    cache = request.app.state.cache
    cache_key = dict(
        q=q, limit=limit, sources=sorted(sources), keywords=sorted(keywords),
        date_from=date_from, date_to=date_to, sort_by=sort_by, sort_dir=sort_dir,
        lang=lang,
    )
    cached = cache.get("text_search", cache_key)
    if cached is not None:
        return cached

    conditions = ["p.is_relevant = true", "(r.title ILIKE %s OR r.body ILIKE %s)"]
    pattern = f"%{q}%"
    params: list[Any] = [pattern, pattern, lang, lang]

    if sources:
        conditions.append("r.source = ANY(%s)")
        params.append(sources)
    if keywords:
        kw_conds = ["%s = ANY(p.keywords_matched)" for _ in keywords]
        conditions.append("(" + " OR ".join(kw_conds) + ")")
        params.extend(keywords)
    if date_from:
        conditions.append("r.created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("r.created_at <= %s")
        params.append(date_to)

    valid_sort = {"rank_score", "intensity", "confidence", "score", "created_at"}
    order_col = sort_by if sort_by in valid_sort else "rank_score"
    order_dir = "DESC" if sort_dir == "desc" else "ASC"

    params.append(limit)

    where = " AND ".join(conditions)
    rows = fetchall(
        f"""
        SELECT
            r.id::text AS raw_signal_id,
            r.title,
            r.url,
            r.source,
            r.author,
            r.score,
            r.comments_count,
            r.created_at,
            r.collected_at,
            p.summary,
            p.rank_score::float,
            p.intensity,
            p.confidence::float,
            p.language,
            tt.text AS title_translated,
            ts.text AS summary_translated
        FROM processed_signals p
        JOIN raw_signals r ON r.id = p.raw_signal_id
        LEFT JOIN signal_translations tt
               ON tt.signal_id = r.id AND tt.lang = %s AND tt.field = 'title'
        LEFT JOIN signal_translations ts
               ON ts.signal_id = r.id AND ts.lang = %s AND ts.field = 'summary'
        WHERE {where}
        ORDER BY p.{order_col} {order_dir} NULLS LAST
        LIMIT %s
        """,
        params,
    )

    use_translation = lang != "en"
    results = [
        {
            "raw_signal_id": row["raw_signal_id"],
            "title": (row["title_translated"] or row["title"]) if use_translation else row["title"],
            "title_original": row["title"],
            "url": row["url"],
            "source": row["source"],
            "author": row["author"],
            "score": row["score"],
            "comments_count": row["comments_count"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "collected_at": row["collected_at"].isoformat() if row["collected_at"] else None,
            "summary": (row["summary_translated"] or row["summary"]) if use_translation else row["summary"],
            "summary_original": row["summary"],
            "translation_available": bool(row["title_translated"]),
            "rank_score": row["rank_score"],
            "intensity": row["intensity"],
            "confidence": row["confidence"],
            "language": row["language"],
            "query": q,
        }
        for row in rows
    ]

    response = {"results": results, "total": len(results), "query": q}
    cache.set("text_search", cache_key, value=response, ttl=900)
    return response
