"""
One-off script: call borderline_relevance LLM with the same prompt as the worker, measure time.
Run from signal-hunter root: python scripts/bench_borderline_llm.py
Uses .env for LOCAL_LLM_*, config.json for hybrid_relevance.llm_*.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# Same prompt as llm_worker
_LLM_SYSTEM_V6 = (
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


def _extract_project(dedup_key: str) -> str:
    if dedup_key.startswith("github_issue:") or dedup_key.startswith("github_discussion:"):
        prefix = dedup_key.split(":", 1)[1]
        return prefix.rsplit("#", 1)[0]
    return ""


def main() -> None:
    from storage.config_manager import ConfigManager
    from storage.postgres import PostgresStorage
    from storage.text_cleaner import strip_hn_prefix
    from core.llm_router import LLMCall, LLMRouter

    config = ConfigManager().load()
    storage = PostgresStorage()
    hybrid_cfg = config.get("hybrid_relevance", {})
    body_chars = int(hybrid_cfg.get("llm_body_chars", 600))
    max_tokens = int(hybrid_cfg.get("llm_max_tokens", 150))
    temperature = float(hybrid_cfg.get("llm_temperature", 0.0))

    # One pending borderline task (do not claim it)
    with storage._conn() as conn:
        with storage._cursor(conn) as cur:
            cur.execute(
                """
                SELECT payload FROM llm_task_queue
                WHERE task_type = 'borderline_relevance' AND status = 'pending'
                ORDER BY priority ASC, created_at ASC LIMIT 1
                """
            )
            row = cur.fetchone()
    if not row:
        # Fallback: minimal user message
        user_msg = "Source: github_issues\nProject: test/repo\nTitle: Add support for longer context\nBody: We need to handle 128k tokens."
        print("No pending borderline task; using minimal payload", file=sys.stderr)
    else:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
        dedup_key = payload.get("dedup_key", "")
        raw = storage.fetch_raw_signal_by_dedup_key(dedup_key)
        if not raw:
            user_msg = f"Source: unknown\nTitle: (signal not found)\nBody: dedup_key={dedup_key}"
        else:
            title = strip_hn_prefix(raw.get("title") or "")
            body = (raw.get("body") or "")[:body_chars]
            source = raw.get("source") or ""
            project = _extract_project(dedup_key)
            parts = [f"Source: {source}"]
            if project:
                parts.append(f"Project: {project}")
            parts.append(f"Title: {title}")
            if body:
                parts.append(f"Body: {body}")
            user_msg = "\n".join(parts)
        print(f"Using dedup_key: {dedup_key}", file=sys.stderr)

    messages = [
        {"role": "system", "content": _LLM_SYSTEM_V6},
        {"role": "user", "content": user_msg},
    ]
    call = LLMCall(
        operation="borderline_relevance",
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    router = LLMRouter(config, usage_logger=None)

    print("Calling LLM...", file=sys.stderr)
    t0 = time.perf_counter()
    try:
        response = router.complete(call)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(json.dumps({"ok": False, "error": str(e), "elapsed_seconds": round(elapsed, 2)}))
        sys.exit(1)
    elapsed = time.perf_counter() - t0
    print(json.dumps({
        "ok": True,
        "elapsed_seconds": round(elapsed, 2),
        "response": response.strip(),
    }, ensure_ascii=False))
    print(f"\nElapsed: {elapsed:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
