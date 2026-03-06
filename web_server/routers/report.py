"""
Report API router.
Provides hierarchical signal data: categories -> clusters -> signals.
Clustering is lazy (computed per category on demand, cached 24h).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request

from web_server.db import fetchall, fetchone
from web_server.services.clustering import (
    build_cluster_key,
    fetch_vectors,
    greedy_cluster,
    name_clusters,
)

log = logging.getLogger(__name__)

router = APIRouter()

_SOURCE_ORDER = [
    "github_issue", "github_discussion", "hn_post", "so_question",
    "reddit_post", "reddit_comment", "hf_discussion", "hf_paper",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_matched_rules(raw: Any) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def _primary_rule(matched_rules: list[dict]) -> str:
    if matched_rules:
        return matched_rules[0].get("rule_name") or "uncategorized"
    return "uncategorized"


def _build_where(
    date_from: str | None,
    date_to: str | None,
    sources: list[str],
    intensity_min: int | None,
    intensity_max: int | None,
    confidence_min: float | None,
    confidence_max: float | None,
    languages: list[str],
    keywords: list[str],
) -> tuple[str, list]:
    conditions = ["p.is_relevant = true"]
    params: list[Any] = []

    if date_from:
        conditions.append("r.created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("r.created_at <= %s")
        params.append(date_to)
    if sources:
        conditions.append("r.source = ANY(%s)")
        params.append(sources)
    if intensity_min is not None:
        conditions.append("p.intensity >= %s")
        params.append(intensity_min)
    if intensity_max is not None:
        conditions.append("p.intensity <= %s")
        params.append(intensity_max)
    if confidence_min is not None:
        conditions.append("p.confidence >= %s")
        params.append(confidence_min)
    if confidence_max is not None:
        conditions.append("p.confidence <= %s")
        params.append(confidence_max)
    if languages:
        conditions.append("p.language = ANY(%s)")
        params.append(languages)
    if keywords:
        kw_conds = ["%s = ANY(p.keywords_matched)" for _ in keywords]
        conditions.append("(" + " OR ".join(kw_conds) + ")")
        params.extend(keywords)

    return " AND ".join(conditions), params


def _fetch_signals(where: str, params: list) -> list[dict]:
    """Fetch all relevant signals matching filters. Returns lightweight rows."""
    rows = fetchall(
        f"""
        SELECT
            p.raw_signal_id::text,
            p.matched_rules,
            p.intensity,
            p.confidence::float,
            p.rank_score::float,
            r.source AS source_type,
            r.score,
            r.comments_count,
            r.created_at,
            r.title
        FROM processed_signals p
        JOIN raw_signals r ON r.id = p.raw_signal_id
        WHERE {where}
        ORDER BY p.rank_score DESC NULLS LAST
        LIMIT 20000
        """,
        params,
    )
    return rows


def _aggregate_signals(rows: list[dict], category_filter: list[str]) -> dict[str, dict]:
    """
    Group signals by primary_rule and compute aggregates.
    Returns {rule_name: aggregated_dict}.
    """
    groups: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        rules = _parse_matched_rules(row.get("matched_rules"))
        rule = _primary_rule(rules)
        if category_filter and rule not in category_filter:
            continue
        groups[rule].append(row)

    result = {}
    for rule, signals in groups.items():
        n = len(signals)
        sources: dict[str, int] = defaultdict(int)
        for s in signals:
            sources[s["source_type"]] += 1

        last_dt = max(
            (s["created_at"] for s in signals if s.get("created_at")),
            default=None,
        )

        result[rule] = {
            "name": rule,
            "count": n,
            "avg_rank_score": round(sum(s["rank_score"] for s in signals) / n, 3),
            "avg_intensity": round(sum(s["intensity"] for s in signals) / n, 2),
            "avg_confidence": round(sum(s["confidence"] for s in signals) / n, 3),
            "avg_score": round(sum(s["score"] for s in signals) / n, 1),
            "avg_comments": round(sum(s["comments_count"] for s in signals) / n, 1),
            "last_signal_at": last_dt.isoformat() if last_dt else None,
            "sources_breakdown": dict(sources),
            "signal_ids": [s["raw_signal_id"] for s in signals],
        }

    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/report")
async def get_report(
    request: Request,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    sources: list[str] = Query(default=[]),
    categories: list[str] = Query(default=[]),
    keywords: list[str] = Query(default=[]),
    intensity_min: int | None = Query(None, ge=1, le=5),
    intensity_max: int | None = Query(None, ge=1, le=5),
    confidence_min: float | None = Query(None, ge=0.0, le=1.0),
    confidence_max: float | None = Query(None, ge=0.0, le=1.0),
    languages: list[str] = Query(default=[]),
    sort_by: str = Query("avg_rank_score"),
    sort_dir: str = Query("desc"),
):
    """Return level-1 category aggregates (no clustering)."""
    cache = request.app.state.cache
    cache_key = dict(
        date_from=date_from, date_to=date_to, sources=sorted(sources),
        categories=sorted(categories), keywords=sorted(keywords),
        intensity_min=intensity_min, intensity_max=intensity_max,
        confidence_min=confidence_min, confidence_max=confidence_max,
        languages=sorted(languages), sort_by=sort_by, sort_dir=sort_dir,
    )
    cached = cache.get("report", cache_key)
    if cached is not None:
        return cached

    where, params = _build_where(
        date_from, date_to, sources, intensity_min, intensity_max,
        confidence_min, confidence_max, languages, keywords,
    )
    rows = _fetch_signals(where, params)
    groups = _aggregate_signals(rows, categories)

    sorted_cats = sorted(
        groups.values(),
        key=lambda c: c.get(sort_by, 0) or 0,
        reverse=(sort_dir == "desc"),
    )

    result = {"total_signals": sum(c["count"] for c in sorted_cats), "categories": sorted_cats}
    cache.set("report", cache_key, value=result, ttl=1800)
    return result


@router.get("/report/clusters")
async def get_clusters(
    request: Request,
    category: str = Query(...),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    sources: list[str] = Query(default=[]),
    keywords: list[str] = Query(default=[]),
    intensity_min: int | None = Query(None, ge=1, le=5),
    intensity_max: int | None = Query(None, ge=1, le=5),
    confidence_min: float | None = Query(None, ge=0.0, le=1.0),
    confidence_max: float | None = Query(None, ge=0.0, le=1.0),
    languages: list[str] = Query(default=[]),
):
    """Return level-2 clusters for a specific category (with LLM naming, cached 24h)."""
    cache = request.app.state.cache
    cache_key = dict(
        category=category, date_from=date_from, date_to=date_to,
        sources=sorted(sources), keywords=sorted(keywords),
        intensity_min=intensity_min, intensity_max=intensity_max,
        confidence_min=confidence_min, confidence_max=confidence_max,
        languages=sorted(languages),
    )
    cached = cache.get("clusters", cache_key)
    if cached is not None:
        return cached

    where, params = _build_where(
        date_from, date_to, sources, intensity_min, intensity_max,
        confidence_min, confidence_max, languages, keywords,
    )
    rows = _fetch_signals(where, params)
    groups = _aggregate_signals(rows, [category])

    if category not in groups:
        result = {"clusters": []}
        cache.set("clusters", cache_key, value=result, ttl=86400)
        return result

    cat_data = groups[category]
    signal_ids = cat_data["signal_ids"]
    titles_by_id = {row["raw_signal_id"]: row["title"] for row in rows if row["raw_signal_id"] in signal_ids}

    # Build row lookup for aggregation
    row_by_id = {row["raw_signal_id"]: row for row in rows if row["raw_signal_id"] in signal_ids}

    # Clustering key - if names are cached, skip LLM call
    cluster_key = build_cluster_key(signal_ids)
    cached_names = cache.get("cluster_names", cluster_key)

    # Fetch vectors and cluster
    vectors = fetch_vectors(signal_ids)
    clusters = greedy_cluster(signal_ids, vectors, threshold=0.72)

    # Get or compute names
    if cached_names is not None:
        cluster_names = cached_names
    else:
        cluster_names = name_clusters(clusters, titles_by_id)
        cache.set("cluster_names", cluster_key, value=cluster_names, ttl=86400)

    # Build cluster response
    result_clusters = []
    for cid, sids in sorted(clusters.items()):
        n = len(sids)
        src: dict[str, int] = defaultdict(int)
        rank_scores, intensities, confidences, scores, comments = [], [], [], [], []
        last_dt = None

        for sid in sids:
            row = row_by_id.get(sid)
            if not row:
                continue
            src[row["source_type"]] += 1
            rank_scores.append(row["rank_score"])
            intensities.append(row["intensity"])
            confidences.append(row["confidence"])
            scores.append(row["score"])
            comments.append(row["comments_count"])
            dt = row["created_at"]
            if dt and (last_dt is None or dt > last_dt):
                last_dt = dt

        result_clusters.append({
            "id": cid,
            "name": cluster_names.get(cid, f"Cluster {cid + 1}"),
            "count": n,
            "avg_rank_score": round(sum(rank_scores) / max(len(rank_scores), 1), 3),
            "avg_intensity": round(sum(intensities) / max(len(intensities), 1), 2),
            "avg_confidence": round(sum(confidences) / max(len(confidences), 1), 3),
            "avg_score": round(sum(scores) / max(len(scores), 1), 1),
            "avg_comments": round(sum(comments) / max(len(comments), 1), 1),
            "last_signal_at": last_dt.isoformat() if last_dt else None,
            "sources_breakdown": dict(src),
            "signal_ids": sids,
        })

    # Sort clusters by avg_rank_score desc
    result_clusters.sort(key=lambda c: c["avg_rank_score"], reverse=True)
    result = {"clusters": result_clusters}
    cache.set("clusters", cache_key, value=result, ttl=1800)
    return result


@router.get("/report/signals")
async def get_signals(
    ids: list[str] = Query(default=[]),
    sort_by: str = Query("rank_score"),
    sort_dir: str = Query("desc"),
):
    """Return level-3 full signal data by raw_signal_ids."""
    if not ids:
        return {"signals": []}

    # Postgres expects uuid[] literal
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
            r.views_count,
            r.created_at,
            r.collected_at,
            p.summary,
            p.rank_score::float,
            p.intensity,
            p.confidence::float,
            p.language,
            p.matched_rules
        FROM raw_signals r
        JOIN processed_signals p ON p.raw_signal_id = r.id
        WHERE r.id = ANY(%s::uuid[])
        ORDER BY p.rank_score DESC NULLS LAST
        """,
        (ids,),
    )

    signals = []
    for row in rows:
        rules = _parse_matched_rules(row.get("matched_rules"))
        rule_names = [r.get("rule_name") for r in rules if isinstance(r, dict)]
        signals.append({
            "raw_signal_id": row["raw_signal_id"],
            "title": row["title"],
            "url": row["url"],
            "source": row["source"],
            "author": row["author"],
            "score": row["score"],
            "comments_count": row["comments_count"],
            "views_count": row["views_count"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "collected_at": row["collected_at"].isoformat() if row["collected_at"] else None,
            "summary": row["summary"],
            "rank_score": row["rank_score"],
            "intensity": row["intensity"],
            "confidence": row["confidence"],
            "language": row["language"],
            "matched_rules": rule_names,
        })

    # Apply sorting
    valid_sort = {"rank_score", "intensity", "confidence", "score", "comments_count", "created_at"}
    if sort_by in valid_sort:
        rev = sort_dir == "desc"
        signals.sort(key=lambda s: s.get(sort_by) or 0, reverse=rev)

    return {"signals": signals}


@router.get("/keywords")
async def get_keywords():
    """Return list of tracked keywords for filter dropdown."""
    rows = fetchall("SELECT canonical_name FROM keyword_profiles ORDER BY canonical_name")
    return {"keywords": [r["canonical_name"] for r in rows]}


@router.get("/stats")
async def get_stats(request: Request):
    """Return overall system statistics."""
    cache = request.app.state.cache
    cached = cache.get("stats")
    if cached:
        return cached

    row = fetchone("""
        SELECT
            (SELECT COUNT(*) FROM raw_signals)::int AS raw_total,
            (SELECT COUNT(*) FROM processed_signals WHERE is_relevant = true)::int AS relevant_total,
            (SELECT COUNT(*) FROM processed_signals WHERE is_relevant = false)::int AS irrelevant_total,
            (SELECT COUNT(*) FROM processed_signals)::int AS processed_total,
            (SELECT COUNT(*) FROM raw_signals) - (SELECT COUNT(*) FROM processed_signals) AS unprocessed,
            (SELECT COUNT(*) FROM embedding_queue WHERE status = 'done')::int AS embedded_total,
            (SELECT COUNT(*) FROM embedding_queue WHERE status = 'pending')::int AS pending_embeddings,
            (SELECT COUNT(*) FROM keyword_profiles)::int AS keywords_total,
            (SELECT AVG(rank_score)::float FROM processed_signals WHERE is_relevant = true) AS avg_rank_score
    """)

    result = dict(row) if row else {}
    cache.set("stats", value=result, ttl=300)
    return result


@router.get("/charts/timeline")
async def get_timeline(
    days: int = Query(30, ge=7, le=365),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    sources: list[str] = Query(default=[]),
    keywords: list[str] = Query(default=[]),
):
    """Return daily signal counts by source for trend chart."""
    conditions = ["p.is_relevant = true"]
    params: list[Any] = []

    if date_from:
        conditions.append("r.created_at >= %s")
        params.append(date_from)
    elif not date_to:
        conditions.append(f"r.created_at >= now() - interval '{days} days'")

    if date_to:
        conditions.append("r.created_at <= %s")
        params.append(date_to)
    if sources:
        conditions.append("r.source = ANY(%s)")
        params.append(sources)
    if keywords:
        kw_conds = ["%s = ANY(p.keywords_matched)" for _ in keywords]
        conditions.append("(" + " OR ".join(kw_conds) + ")")
        params.extend(keywords)

    where = " AND ".join(conditions)
    rows = fetchall(
        f"""
        SELECT
            date_trunc('day', r.created_at)::date AS day,
            r.source AS source_type,
            COUNT(*)::int AS count,
            AVG(p.rank_score)::float AS avg_rank_score
        FROM processed_signals p
        JOIN raw_signals r ON r.id = p.raw_signal_id
        WHERE {where}
        GROUP BY day, r.source
        ORDER BY day
        """,
        params,
    )

    data = [
        {
            "day": row["day"].isoformat(),
            "source_type": row["source_type"],
            "count": row["count"],
            "avg_rank_score": round(row["avg_rank_score"] or 0, 3),
        }
        for row in rows
    ]
    return {"data": data}


@router.get("/charts/sources")
async def get_sources_breakdown(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    keywords: list[str] = Query(default=[]),
):
    """Return signal counts grouped by source."""
    conditions = ["p.is_relevant = true"]
    params: list[Any] = []

    if date_from:
        conditions.append("r.created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("r.created_at <= %s")
        params.append(date_to)
    if keywords:
        kw_conds = ["%s = ANY(p.keywords_matched)" for _ in keywords]
        conditions.append("(" + " OR ".join(kw_conds) + ")")
        params.extend(keywords)

    where = " AND ".join(conditions)
    rows = fetchall(
        f"""
        SELECT
            r.source AS source_type,
            COUNT(*)::int AS count,
            AVG(p.rank_score)::float AS avg_rank_score,
            AVG(p.intensity)::float AS avg_intensity
        FROM processed_signals p
        JOIN raw_signals r ON r.id = p.raw_signal_id
        WHERE {where}
        GROUP BY r.source
        ORDER BY count DESC
        """,
        params,
    )
    return {
        "data": [
            {
                "source_type": row["source_type"],
                "count": row["count"],
                "avg_rank_score": round(row["avg_rank_score"] or 0, 3),
                "avg_intensity": round(row["avg_intensity"] or 0, 2),
            }
            for row in rows
        ]
    }


@router.get("/charts/categories")
async def get_categories_breakdown(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    keywords: list[str] = Query(default=[]),
):
    """Return signal counts by matched_rule category."""
    conditions = ["p.is_relevant = true", "jsonb_array_length(p.matched_rules) > 0"]
    params: list[Any] = []

    if date_from:
        conditions.append("r.created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("r.created_at <= %s")
        params.append(date_to)
    if keywords:
        kw_conds = ["%s = ANY(p.keywords_matched)" for _ in keywords]
        conditions.append("(" + " OR ".join(kw_conds) + ")")
        params.extend(keywords)

    where = " AND ".join(conditions)
    rows = fetchall(
        f"""
        SELECT
            p.matched_rules->0->>'rule_name' AS category,
            COUNT(*)::int AS count,
            AVG(p.rank_score)::float AS avg_rank_score,
            AVG(p.intensity)::float AS avg_intensity,
            AVG(p.confidence)::float AS avg_confidence
        FROM processed_signals p
        JOIN raw_signals r ON r.id = p.raw_signal_id
        WHERE {where}
        GROUP BY category
        ORDER BY count DESC
        """,
        params,
    )
    return {
        "data": [
            {
                "category": row["category"],
                "count": row["count"],
                "avg_rank_score": round(row["avg_rank_score"] or 0, 3),
                "avg_intensity": round(row["avg_intensity"] or 0, 2),
                "avg_confidence": round(row["avg_confidence"] or 0, 3),
            }
            for row in rows
        ]
    }
