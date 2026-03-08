"""
Stress test: emulate LLM borderline_relevance classification 10 times in a row.
Uses the same code path as the worker (LLMRouter, same prompt, same operation).
Run from signal-hunter root or inside Docker to check for intermittent DNS/network failures.

  python scripts/llm_classification_stress_test.py
  docker compose run --rm signal-hunter-workers python /app/scripts/llm_classification_stress_test.py
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

# Same system prompt as llm_worker (v6) - full copy so we don't depend on worker module
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

# Minimal user message that resembles a real borderline_relevance payload (no DB needed)
_USER_MSG = """Source: github_issue
Project: openai/openai-python
Title: Timeout when streaming long responses
Body: When using stream=True, requests to the API sometimes time out for long completions. We need a way to increase the timeout or get partial results."""


def main() -> None:
    from storage.config_manager import ConfigManager
    from core.llm_router import LLMCall, LLMRouter

    config = ConfigManager().load()
    hybrid_cfg = config.get("hybrid_relevance", {})
    max_tokens = int(hybrid_cfg.get("llm_max_tokens", 150))
    temperature = float(hybrid_cfg.get("llm_temperature", 0.0))

    router = LLMRouter(config, usage_logger=None)
    call = LLMCall(
        operation="borderline_relevance",
        messages=[
            {"role": "system", "content": _LLM_SYSTEM_V6},
            {"role": "user", "content": _USER_MSG},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )

    n = 10
    results: list[dict] = []
    for i in range(n):
        t0 = time.perf_counter()
        try:
            raw = router.complete(call).strip()
            elapsed = time.perf_counter() - t0
            parsed = json.loads(raw)
            results.append({"run": i + 1, "ok": True, "elapsed_seconds": round(elapsed, 2), "relevant": bool(parsed.get("relevant"))})
            print(f"  run {i+1}/{n}: ok {elapsed:.2f}s", file=sys.stderr)
        except Exception as e:
            elapsed = time.perf_counter() - t0
            err_msg = str(e)[:500]
            results.append({"run": i + 1, "ok": False, "elapsed_seconds": round(elapsed, 2), "error": err_msg})
            print(f"  run {i+1}/{n}: FAILED {elapsed:.2f}s - {err_msg}", file=sys.stderr)

    ok_count = sum(1 for r in results if r.get("ok"))
    failed = [r for r in results if not r.get("ok")]
    summary = {
        "total_runs": n,
        "ok": ok_count,
        "failed": len(failed),
        "results": results,
    }
    if failed:
        summary["failed_errors"] = [r.get("error") for r in failed]

    print(json.dumps(summary, ensure_ascii=False))
    print(f"\nSummary: {ok_count}/{n} ok, {len(failed)} failed", file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
