"""
Benchmark: LLM borderline relevance in one batch of 10 vs stored labels.
- Fetches 10 random processed_signals where classification_source = 'llm'
- Sends one LLM request with all 10 (same prompt format, array response)
- Measures time and compares batch result to stored is_relevant (accuracy)

Run from signal-hunter root: python scripts/bench_borderline_batch.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# Same system prompt as llm_worker (v6), but batch instruction
_LLM_SYSTEM_V6_SINGLE = (
    "You are a market intelligence classifier for the AI/ML ecosystem.\n"
    "Your job: decide if a signal provides actionable market intelligence "
    "about AI/ML tools, models, frameworks, or infrastructure.\n"
    "You receive: source type, project name (if available), title, body.\n\n"
    "A signal is RELEVANT if it reveals any of these about the AI/ML market:\n"
    "- User pain points with AI/ML tools (crashes, performance, compatibility)\n"
    "- Feature requests for AI/ML products\n"
    "- Comparisons between AI/ML tools\n"
    "- New AI/ML products, releases, or announcements\n"
    "- Adoption of AI/ML in products or workflows\n"
    "- Bugs or issues that affect users of AI/ML tools\n"
    "- Discussions about AI/ML architecture, scaling, deployment\n"
    "- AI security or safety concerns with AI products\n"
    "- Market observations about AI/ML trends\n\n"
    "Key: if the project IS an AI/ML tool (LLM framework, model serving, "
    "AI agent platform, ML pipeline, vector DB, AI coding assistant, etc.), "
    "then bugs, feature requests and discussions in it ARE market signals - "
    "they show what users struggle with, what they need.\n\n"
    "NOT relevant:\n"
    "- Signals from non-AI projects with no AI/ML angle\n"
    "- Generic content (politics, gaming, cooking, sports, e-commerce)\n"
    "- Pure CI/infrastructure issues in non-AI projects\n\n"
    'Reply ONLY: {"relevant": true/false, "reason": "one sentence"}'
)

_BATCH_SYSTEM = (
    _LLM_SYSTEM_V6_SINGLE.rstrip()
    + "\n\nFor this request you receive MULTIPLE signals numbered [0], [1], ... "
    "Reply with a JSON array of exactly that many objects in the same order, "
    'e.g. [{"relevant": true, "reason": "..."}, {"relevant": false, "reason": "..."}]. '
    "No other text, only the JSON array."
)


def _extract_project(dedup_key: str) -> str:
    if dedup_key.startswith("github_issue:") or dedup_key.startswith("github_discussion:"):
        prefix = dedup_key.split(":", 1)[1]
        return prefix.rsplit("#", 1)[0]
    return ""


def _strip_hn_prefix(title: str) -> str:
    if title.startswith("Show HN: ") or title.startswith("Ask HN: "):
        return title.split(":", 1)[-1].strip()
    return title


def main() -> None:
    from storage.config_manager import ConfigManager
    from storage.postgres import PostgresStorage
    from core.llm_router import LLMCall, LLMRouter

    config = ConfigManager().load()
    storage = PostgresStorage()
    hybrid_cfg = config.get("hybrid_relevance", {})
    body_chars = int(hybrid_cfg.get("llm_body_chars", 600))
    temperature = float(hybrid_cfg.get("llm_temperature", 0.0))

    batch_size = 10
    rows = storage.fetch_random_llm_classified(limit=batch_size)
    if not rows:
        print(json.dumps({"error": "No LLM-classified signals in DB. Run worker until some borderline_relevance tasks complete."}))
        sys.exit(1)
    if len(rows) < batch_size:
        print(f"Only {len(rows)} LLM-classified signals in DB; using {len(rows)} for batch.", file=sys.stderr)

    # Build user message: [0] ... [N-1] blocks (same format as single)
    blocks = []
    for i, r in enumerate(rows):
        title = _strip_hn_prefix(r.get("title") or "")
        body = (r.get("body") or "")[:body_chars]
        source = r.get("source") or ""
        project = _extract_project(r.get("dedup_key") or "")
        parts = [f"[{i}]", f"Source: {source}"]
        if project:
            parts.append(f"Project: {project}")
        parts.append(f"Title: {title}")
        if body:
            parts.append(f"Body: {body}")
        blocks.append("\n".join(parts))
    user_msg = "\n\n".join(blocks)

    n = len(rows)
    max_tokens = max(512, n * 120)
    router = LLMRouter(config, usage_logger=None)
    call = LLMCall(
        operation="borderline_relevance_batch",
        messages=[
            {"role": "system", "content": _BATCH_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )

    print(f"Calling LLM with batch of {n} signals...", file=sys.stderr)
    t0 = time.perf_counter()
    try:
        raw_response = router.complete(call).strip()
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(json.dumps({"ok": False, "error": str(e), "elapsed_seconds": round(elapsed, 2)}))
        sys.exit(1)
    elapsed = time.perf_counter() - t0

    # Parse array
    if raw_response.startswith("```"):
        raw_response = raw_response.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        print(json.dumps({
            "ok": False,
            "elapsed_seconds": round(elapsed, 2),
            "error": "Response is not valid JSON",
            "response_preview": raw_response[:500],
        }))
        sys.exit(1)

    if not isinstance(parsed, list):
        print(json.dumps({
            "ok": False,
            "elapsed_seconds": round(elapsed, 2),
            "error": "Expected JSON array",
            "response_preview": raw_response[:500],
        }))
        sys.exit(1)

    # Compare with stored is_relevant
    stored = [bool(r["is_relevant"]) for r in rows]
    batch_relevant = []
    for i, item in enumerate(parsed):
        if isinstance(item, dict):
            batch_relevant.append(bool(item.get("relevant", False)))
        else:
            batch_relevant.append(False)
    # Pad if LLM returned fewer
    while len(batch_relevant) < len(stored):
        batch_relevant.append(False)
    batch_relevant = batch_relevant[: len(stored)]

    matches = sum(1 for s, b in zip(stored, batch_relevant) if s == b)
    total = len(stored)
    accuracy = matches / total if total else 0
    mismatches = [
        {"index": i, "dedup_key": rows[i]["dedup_key"], "stored": stored[i], "batch": batch_relevant[i]}
        for i in range(total)
        if stored[i] != batch_relevant[i]
    ]

    out = {
        "ok": True,
        "batch_size": total,
        "elapsed_seconds": round(elapsed, 2),
        "matches": matches,
        "accuracy": round(accuracy, 4),
        "mismatches": mismatches,
    }
    print(json.dumps(out, ensure_ascii=False))
    print(f"\nBatch of {total}: {elapsed:.2f}s, accuracy {accuracy:.2%} ({matches}/{total})", file=sys.stderr)
    if mismatches:
        print(f"Mismatches: {mismatches}", file=sys.stderr)


if __name__ == "__main__":
    main()
