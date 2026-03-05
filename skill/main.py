"""
Signal Hunter CLI entry point.
Called by the TypeScript plugin: python -m skill <command> [args...]
All output is JSON written to stdout (or plain text for query).
Errors are written to stderr; process exits with code 1 on error.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Make parent directory importable (signal-hunter root)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)


def _load_config() -> dict[str, Any]:
    from storage.config_manager import ConfigManager  # noqa: PLC0415
    return ConfigManager().load()


def _make_storage():
    from storage.postgres import PostgresStorage  # noqa: PLC0415
    return PostgresStorage()


def _make_router(config: dict[str, Any]):
    from core.llm_router import LLMRouter  # noqa: PLC0415
    storage = _make_storage()
    return LLMRouter(config, usage_logger=storage.log_llm_usage)


def _make_config_manager():
    from storage.config_manager import ConfigManager  # noqa: PLC0415
    return ConfigManager()


def _out(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, default=str), flush=True)


def _err(msg: str, code: int = 1) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr, flush=True)
    sys.exit(code)


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------

def cmd_status() -> None:
    """Print system status: keywords, signal counts, LLM costs, processor config."""
    storage = _make_storage()
    config = _load_config()
    proc = config.get("processor", {})
    filters = config.get("filters", {})

    summary = storage.get_status_summary()
    summary["processor_config"] = {
        "signals_per_batch":   proc.get("max_signals_per_batch", 10),
        "max_tokens_per_batch": proc.get("max_tokens_per_batch", 10_000),
        "max_body_chars":      proc.get("max_body_chars", 1000),
        "batches_per_run":     proc.get("max_batches_per_run", 3),
        "db_fetch_size":       proc.get("batch_size", 200),
    }
    summary["filters_config"] = {
        "min_score":          filters.get("min_score", 0),
        "max_age_days":       filters.get("max_age_days", 90),
        "max_items_per_cycle": filters.get("max_items_per_cycle", 200),
    }
    _out(summary)


def cmd_resolve(keyword: str) -> None:
    """Discover and enrich a keyword, output proposal."""
    from core.registry import load_all_collectors  # noqa: PLC0415
    from core.resolver import KeywordResolver  # noqa: PLC0415
    from storage.pending import PendingStore  # noqa: PLC0415

    config = _load_config()
    load_all_collectors()
    storage = _make_storage()
    router = _make_router(config)
    resolver = KeywordResolver(router, storage)
    result = resolver.resolve(keyword)

    # Save pending plan so approve_plan needs only canonical_name
    pending = PendingStore()
    pending.save("plan", {
        "canonical_name": result.get("canonical_name", keyword),
        "plans": result.get("proposed_plan", {}),
    })

    _out(result)


def cmd_refresh_profile(keyword: str) -> None:
    """Force re-discovery and overwrite cached KeywordProfile."""
    from core.registry import load_all_collectors  # noqa: PLC0415
    from core.resolver import KeywordResolver  # noqa: PLC0415

    config = _load_config()
    load_all_collectors()
    storage = _make_storage()
    router = _make_router(config)
    resolver = KeywordResolver(router, storage)
    result = resolver.resolve(keyword, force_refresh=True)
    _out({"status": "ok", "message": f"Profile refreshed for '{keyword}'", "profile": result})


def cmd_approve_plan(json_str: str) -> None:
    """
    Save approved plan.
    json_str: '{"canonical_name": "..."}' - reads plan from pending state (set by resolve).
    Falls back to explicit plans if provided: '{"canonical_name": "...", "plans": {...}}'
    """
    from core.resolver import KeywordResolver  # noqa: PLC0415
    from storage.pending import PendingStore  # noqa: PLC0415

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    canonical_name = data.get("canonical_name", "")
    plans = data.get("plans")

    if not plans:
        # Read from pending state saved by cmd_resolve
        pending = PendingStore()
        pending_data = pending.load("plan")
        if pending_data:
            plans = pending_data.get("plans", {})
            if not canonical_name:
                canonical_name = pending_data.get("canonical_name", "")
            pending.clear("plan")

    if not canonical_name:
        _err("'canonical_name' is required")
    if not plans:
        _err("No plan found. Run sh_resolve first to generate a collection plan.")

    storage = _make_storage()
    config = _load_config()
    router = _make_router(config)
    resolver = KeywordResolver(router, storage)
    resolver.approve_plan(canonical_name, plans)
    _out({"status": "ok", "message": f"Plan approved for '{canonical_name}'"})


def cmd_update_plan(json_str: str) -> None:
    """
    Update plan: add or remove targets.
    json_str: '{"canonical_name": "...", "collector": "github", "add": [...], "remove": ["query1"]}'
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    storage = _make_storage()
    canonical_name = data.get("canonical_name", "")
    collector_name = data.get("collector", "github")
    add_targets = data.get("add", [])
    remove_queries = data.get("remove", [])

    try:
        storage.update_collection_plan(canonical_name, collector_name, add_targets, remove_queries)
        _out({
            "status": "ok",
            "message": f"Plan updated for '{canonical_name}/{collector_name}': "
                       f"+{len(add_targets)} targets, -{len(remove_queries)} removed",
        })
    except ValueError as e:
        _err(str(e))


def cmd_collect() -> None:
    """Collect signals from all approved plans."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    orch = Orchestrator(config, storage)
    result = orch.collect()
    _out({"status": "done", "phase": "collect", **result})


def cmd_embed() -> None:
    """Embed pending signals into Qdrant."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    device = config.get("embedder", {}).get("device", "cpu")
    storage = _make_storage()
    orch = Orchestrator(config, storage)
    result = orch.embed_pending(device=device)
    _out({"status": "done", "phase": "embed", **result})


def cmd_reprocess(json_str: str) -> None:
    """
    Reclassify signals for a keyword (optionally filtered by rules).
    json_str: '{"keyword": "RAG", "rules": ["rule1", "rule2"]}'
    rules is optional.
    """
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    keyword = data.get("keyword", "")
    rule_names = data.get("rules")  # None = all rules
    if not keyword:
        _err("'keyword' is required")

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)
    orch = Orchestrator(config, storage)
    result = orch.reprocess(keyword, rule_names, router)
    _out({"status": "done", "phase": "reprocess", **result})


def cmd_query(prompt: str) -> None:
    """Semantic search + Claude aggregation. Output is JSON with text field."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)
    orch = Orchestrator(config, storage)
    answer = orch.query(prompt, router)
    _out({"type": "answer", "text": answer})


def cmd_check_sources() -> None:
    """Check readiness of all configured collectors."""
    from core.registry import load_all_collectors, get_all  # noqa: PLC0415

    load_all_collectors()
    config = _load_config()
    sources_cfg = config.get("sources", {})

    results = []
    for cls in get_all():
        collector = cls()
        source_config = sources_cfg.get(cls.name, {})
        if not source_config.get("enabled", True):
            results.append({
                "source": cls.name,
                "ready": False,
                "status": "disabled",
                "limit_info": None,
                "note": "disabled in config",
            })
            continue
        status = collector.check_readiness()
        results.append({
            "source": status.source,
            "ready": status.ready,
            "status": "ready" if status.ready else "not_ready",
            "limit_info": status.limit_info,
            "missing": status.missing,
            "note": status.note,
        })
    _out({"sources": results})


def cmd_get_setup_guide(source: str) -> None:
    """Return step-by-step instructions for obtaining credentials for a source."""
    from core.registry import load_all_collectors, get  # noqa: PLC0415

    load_all_collectors()
    cls = get(source)
    if not cls:
        _err(f"Unknown source: '{source}'. Known: github, reddit, hackernews, stackoverflow, producthunt, huggingface")

    collector = cls()
    steps = collector.get_setup_guide()
    _out({"source": source, "steps": steps})


def cmd_set_credentials(json_str: str) -> None:
    """
    Save credentials to config.sources.<source>.credentials.
    json_str: '{"source": "github", "credentials": {"api_token": "ghp_xxx"}}'
    """
    from core.registry import load_all_collectors, get  # noqa: PLC0415

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    source = data.get("source", "")
    credentials = data.get("credentials", {})
    if not source:
        _err("'source' is required")

    cfg_mgr = _make_config_manager()
    cfg_mgr.set_nested(["sources", source, "credentials"], credentials)
    cfg_mgr.set_nested(["sources", source, "enabled"], True)

    # Verify readiness after saving
    load_all_collectors()
    cls = get(source)
    if cls:
        # Reload env with new credentials if needed
        os.environ.update({k: v for k, v in credentials.items() if isinstance(v, str)})
        status = cls().check_readiness()
        _out({
            "status": "ok",
            "source": source,
            "ready": status.ready,
            "limit_info": status.limit_info,
            "note": status.note,
        })
    else:
        _out({"status": "ok", "source": source, "note": "credentials saved (collector not loaded)"})


def cmd_list_providers() -> None:
    """Show table of LLM providers and routing."""
    config = _load_config()
    providers = config.get("llm_providers", {})
    routing = config.get("llm_routing", {})

    local_model = os.environ.get("LOCAL_LLM_MODEL", "not set")
    local_url = os.environ.get("LOCAL_LLM_BASE_URL", "not set")

    provider_list = [
        {
            "name": "local",
            "type": "openai_compat",
            "model": local_model,
            "base_url": local_url,
            "operations": [op for op, prov in routing.items() if prov == "local"],
        }
    ]
    for name, cfg in providers.items():
        provider_list.append({
            "name": name,
            "type": cfg.get("type", ""),
            "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url", ""),
            "operations": [op for op, prov in routing.items() if prov == name],
        })

    _out({"providers": provider_list, "routing": routing})


def cmd_set_routing(json_str: str) -> None:
    """
    Update LLM routing for an operation.
    json_str: '{"operation": "process", "provider": "claude"}'
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    operation = data.get("operation", "")
    provider = data.get("provider", "")
    valid_ops = ["process", "suggest_rules", "resolve_enrich", "resolve_strategy", "query"]
    valid_providers = ["local", "claude"]

    if operation not in valid_ops:
        _err(f"Unknown operation '{operation}'. Valid: {valid_ops}")
    if provider not in valid_providers:
        _err(f"Unknown provider '{provider}'. Valid: {valid_providers}")

    cfg_mgr = _make_config_manager()
    cfg_mgr.set_nested(["llm_routing", operation], provider)
    _out({"status": "ok", "operation": operation, "provider": provider,
          "message": f"llm_routing.{operation} → {provider}"})


_SH_EMBED_CRON_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SH_COLLECT_CRON_JOB_ID = "c9d4e5f6-a7b8-4c2d-9e0f-1a2b3c4d5e6f"
_SH_WORKER_CRON_JOB_ID = "e7f8a9b0-c1d2-3e4f-5a6b-7c8d9e0f1a2b"


# ------------------------------------------------------------------
# LLM Worker commands
# ------------------------------------------------------------------

def cmd_run_worker(json_str: str = "{}") -> None:
    """
    Process LLM tasks in a loop until the queue is empty or 50-second budget is exhausted.
    Called by cron on every tick (default: every minute).

    Behavior per loop iteration:
      1. Reset tasks stuck in 'running' for > 10 min (crash recovery)
      2. Skip if another task is still running
      3. Auto-enqueue process_batch if unprocessed signals exist and queue is empty
      4. Claim and execute the next pending task (by priority then age)
      5. On success: delete task. On error: retry up to 3 times then mark 'failed'.
      6. Repeat until queue empty or 50-second budget exceeded.
    """
    from core.llm_worker import LLMWorker  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    worker = LLMWorker(config, storage)
    result = worker.run_loop()
    _out(result)


def cmd_queue_resolve(json_str: str) -> None:
    """
    Add keywords to the LLM task queue for background resolve + auto-approve.
    json_str: '{"keywords": ["LangGraph", "CrewAI", ...]}'

    Each keyword is resolved and its collection plan is automatically approved
    by the worker (no manual approve_plan step needed for bulk queues).
    Keywords that already have a profile are skipped.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")
        return

    keywords = data.get("keywords", [])
    if not keywords:
        _err("'keywords' list is required")
        return

    storage = _make_storage()
    added = 0
    skipped = 0
    for kw in keywords:
        existing = storage.get_keyword_profile(kw)
        if existing:
            skipped += 1
            continue
        storage.enqueue_llm_task(
            task_type="resolve",
            priority=50,
            payload={"keyword": kw},
        )
        added += 1

    _out({
        "status": "ok",
        "queued": added,
        "skipped_existing": skipped,
        "total": len(keywords),
        "note": (
            f"{added} keywords added to queue, {skipped} skipped (already resolved). "
            "Worker processes one per cron tick. Check progress with sh_queue_status."
        ),
    })


def cmd_delete_keywords(json_str: str) -> None:
    """
    Delete keywords from the system (profiles + collection plans + report snapshots).
    json_str: '{"keywords": ["keyword1", "keyword2"], "confirmed": true}'

    Safety gate: if confirmed is not true, returns a preview without deleting anything.
    The bot MUST show the list to the user and ask for explicit confirmation before
    calling this with confirmed=true.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")
        return

    keywords = data.get("keywords", [])
    confirmed = data.get("confirmed", False)

    if not keywords:
        _err("'keywords' list is required")
        return

    storage = _make_storage()
    existing_all = set(storage.list_keyword_profiles())

    to_delete = [kw for kw in keywords if kw in existing_all]
    not_found = [kw for kw in keywords if kw not in existing_all]

    if not confirmed:
        _out({
            "status": "preview",
            "will_delete": to_delete,
            "not_found": not_found,
            "count": len(to_delete),
            "note": (
                f"Preview only - nothing deleted yet. "
                f"Call again with confirmed=true to permanently delete {len(to_delete)} keyword(s)."
            ),
        })
        return

    if not to_delete:
        _out({"status": "ok", "deleted": 0, "not_found": not_found, "note": "No matching keywords found."})
        return

    deleted = storage.delete_keywords(to_delete)
    _out({
        "status": "ok",
        "deleted": deleted,
        "keywords": to_delete,
        "not_found": not_found,
    })


def cmd_queue_status() -> None:
    """Show current LLM task queue: pending, running, failed tasks."""
    storage = _make_storage()
    tasks = storage.get_llm_queue_status()

    by_status: dict = {"pending": [], "running": [], "failed": []}
    for t in tasks:
        entry: dict = {
            "task_type": t["task_type"],
            "payload": t["payload"],
        }
        if t.get("retry_count"):
            entry["retry_count"] = t["retry_count"]
        if t.get("error"):
            entry["error"] = t["error"]
        by_status.setdefault(t["status"], []).append(entry)

    _out({
        "status": "ok",
        "total": len(tasks),
        "pending": len(by_status.get("pending", [])),
        "running": len(by_status.get("running", [])),
        "failed": len(by_status.get("failed", [])),
        "tasks": by_status,
    })


def cmd_set_worker_interval(json_str: str) -> None:
    """
    Configure the LLM worker cron interval and save to config.
    Returns cron_job_id to update the cron schedule via cron.update.
    json_str: '{"interval_seconds": 60}'

    Note: OpenClaw cron minimum granularity is 1 minute (* * * * *).
    The interval_seconds is stored in config for reference.
    Recommended: * * * * * (every minute).
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")
        return

    interval_seconds = int(data.get("interval_seconds", 60))

    cm = _make_config_manager()
    config = cm.load()
    config.setdefault("worker", {})["interval_seconds"] = interval_seconds
    cm.save(config)

    _out({
        "status": "ok",
        "interval_seconds": interval_seconds,
        "cron_job_id": _SH_WORKER_CRON_JOB_ID,
        "note": (
            f"Worker interval set to {interval_seconds}s (stored in config). "
            f"To create or update the worker cron job, call cron.update with "
            f"jobId='{_SH_WORKER_CRON_JOB_ID}', "
            f"name='Signal Hunter - LLM Worker', "
            f"message='Run sh_worker to process next LLM task. "
            f"Report: what was processed, or say queue is idle/busy.' "
            f"and patch.schedule. "
            f"Recommended: {{\"kind\": \"cron\", \"expr\": \"* * * * *\"}} (every minute)."
        ),
    })


def cmd_set_embed_schedule(json_str: str) -> None:
    """
    Configure embedding schedule parameters.
    json_str: '{"max_items_per_run": 128}'

    max_items_per_run: max signals to embed per cron run (default 128).
      - Each item takes ~50-100ms on CPU with bge-m3 service.
      - 128 items per run = ~10-15s, safe for any timeout.
      - Set higher (e.g. 512) to drain the queue faster if many items accumulated.

    The cron interval is managed via OpenClaw's cron.update using the returned cron_job_id.
    Recommended interval: every 10 minutes (*/10 * * * *).
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")
        return

    max_items_per_run = data.get("max_items_per_run", 128)

    cm = _make_config_manager()
    config = cm.load()
    embedder = config.setdefault("embedder", {})
    embedder["max_items_per_run"] = max_items_per_run
    cm.save(config)

    _out({
        "status": "ok",
        "max_items_per_run": max_items_per_run,
        "cron_job_id": _SH_EMBED_CRON_JOB_ID,
        "note": (
            f"Config saved: {max_items_per_run} signals per embed run. "
            f"To create or update the embed cron job, call cron.update with "
            f"jobId='{_SH_EMBED_CRON_JOB_ID}', message='Run sh_embed to vectorize pending signals.' "
            f"and patch.schedule (e.g. {{\"kind\": \"cron\", \"expr\": \"*/10 * * * *\"}}). "
            f"Recommended: */10 * * * * (every 10 min) with max_items_per_run=128."
        ),
    })


def cmd_set_collect_schedule(json_str: str) -> None:
    """
    Return cron_job_id and instructions to set the collect cron schedule via cron.update.
    json_str: '{}' - no config params needed, collect has no batch settings.

    The cron interval is managed via OpenClaw's cron.update using the returned cron_job_id.
    Recommended: twice a day - '0 8,20 * * *' (Europe/Kiev).
    """
    _out({
        "status": "ok",
        "cron_job_id": _SH_COLLECT_CRON_JOB_ID,
        "note": (
            f"To create or update the collect cron job, call cron.update with "
            f"jobId='{_SH_COLLECT_CRON_JOB_ID}', "
            f"name='Signal Hunter - Auto Collect', "
            f"message='Run sh_collect to fetch new signals. Report only: how many new signals collected.' "
            f"and patch.schedule (e.g. {{\"kind\": \"cron\", \"expr\": \"0 8,20 * * *\", \"tz\": \"Europe/Kiev\"}}). "
            f"Recommended: twice a day - 0 8,20 * * * (08:00 and 20:00 Kiev time)."
        ),
    })


def cmd_suggest_rules(keyword: str) -> None:
    """Analyze real posts from DB and suggest extraction rules."""
    from core.llm_router import LLMCall  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)

    # Fetch real raw signals from DB (200-300 posts)
    sample = storage.fetch_raw_sample(keyword=keyword, limit=300)
    if not sample:
        # Fallback to generic prompt if no data yet
        sample_text = "(no data collected yet - generic suggestion)"
    else:
        # Use top 50 by score to keep prompt manageable
        top_sample = sorted(sample, key=lambda x: x.get("score", 0) or 0, reverse=True)[:50]
        sample_text = "\n\n".join(
            f"[{i+1}] {s.get('title', '')}\n{(s.get('body') or '')[:300]}"
            for i, s in enumerate(top_sample)
        )

    prompt = f"""Keyword: "{keyword}"

Below are real posts from developer platforms about this topic:
{sample_text}

Based on these REAL posts, suggest 5-8 extraction_rules for classifying signals.
Each rule should match a specific type of valuable signal (pain point, feature request, comparison, adoption, etc.).
Use actual phrases from the posts as examples.

Return JSON array:
[
  {{
    "name": "short_snake_case_id",
    "description": "What kind of content this rule matches",
    "priority": 1-5,
    "examples": ["exact phrase from posts or close paraphrase", "another example"]
  }}
]

Return ONLY the JSON array. Examples must be real phrases from the posts above."""

    call = LLMCall(
        operation="suggest_rules",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.2,
    )
    raw = router.complete(call)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        rules = json.loads(raw)
    except json.JSONDecodeError:
        rules = raw

    # Save pending so approve_rules needs no parameters
    if isinstance(rules, list):
        from storage.pending import PendingStore  # noqa: PLC0415
        PendingStore().save("rules", {"keyword": keyword, "rules": rules})

    _out({"keyword": keyword, "analyzed_posts": len(sample), "suggested_rules": rules})


def cmd_approve_rules(json_str: str = "") -> None:
    """
    Save approved extraction rules to config.json.
    No arguments needed - reads rules from pending state set by suggest_rules.
    Optional: pass explicit JSON array to override pending rules.
    """
    from storage.pending import PendingStore  # noqa: PLC0415

    rules = None

    # Try explicit JSON first (backwards compat)
    if json_str.strip():
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, list):
                rules = parsed
            elif isinstance(parsed, dict) and "confirmed" in parsed:
                pass  # fall through to pending
        except json.JSONDecodeError:
            pass

    # Fall back to pending state
    if rules is None:
        pending = PendingStore()
        pending_data = pending.load("rules")
        if pending_data:
            rules = pending_data.get("rules", [])
            pending.clear("rules")

    if not rules:
        _err("No rules found. Run sh_suggest_rules first, or pass rules as JSON array.")

    cfg_mgr = _make_config_manager()
    cfg_mgr.set_nested(["extraction_rules"], rules)
    _out({"status": "ok", "rules_saved": len(rules)})


def cmd_generate_change_report(keyword: str) -> None:
    """Generate delta report since last snapshot."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)
    orch = Orchestrator(config, storage)
    report = orch.generate_change_report(keyword, router)
    _out({"type": "report", "keyword": keyword, "text": report})


def cmd_preview_change_report(json_str: str) -> None:
    """
    Generate example report on real data for user approval.
    json_str: '{"keyword": "RAG", "instructions": "..."}'
    """
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    keyword = data.get("keyword", "")
    instructions = data.get("instructions", "")
    if not keyword:
        _err("'keyword' is required")

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)
    orch = Orchestrator(config, storage)
    preview = orch.preview_change_report(keyword, instructions, router)

    # Save pending so approve_report_template needs no template param
    from storage.pending import PendingStore  # noqa: PLC0415
    PendingStore().save("report_template", {"keyword": keyword, "template": preview, "instructions": instructions})

    _out({"type": "preview", "keyword": keyword, "text": preview})


def cmd_approve_report_template(json_str: str = "") -> None:
    """
    Save approved report template.
    No arguments needed - reads template from pending state set by preview_change_report.
    Optional: '{"keyword": "RAG", "instructions": "focus on pain points"}' to override.
    """
    from storage.pending import PendingStore  # noqa: PLC0415

    data: dict = {}
    if json_str.strip():
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            pass

    keyword = data.get("keyword", "")
    template = data.get("template")
    instructions = data.get("instructions")

    # Read from pending if template not explicitly provided
    if template is None:
        pending = PendingStore()
        pending_data = pending.load("report_template")
        if pending_data:
            template = pending_data.get("template")
            if not keyword:
                keyword = pending_data.get("keyword", "")
            if not instructions:
                instructions = pending_data.get("instructions", "")
            pending.clear("report_template")

    cfg_mgr = _make_config_manager()
    cfg_mgr.set_nested(["change_report", "approved_template"], template)
    if instructions:
        cfg_mgr.set_nested(["change_report", "instructions"], instructions)

    _out({
        "status": "ok",
        "keyword": keyword,
        "template_saved": template is not None,
        "message": "Template approved and saved" if template else "Template reset to instructions-only mode",
    })


def cmd_list_keywords() -> None:
    """List all tracked keywords."""
    storage = _make_storage()
    keywords = storage.list_keyword_profiles()
    _out({"keywords": keywords, "total": len(keywords)})


def cmd_embedder_service(json_str: str) -> None:
    """
    Manage the embedder Docker container.
    json_str: '{"action": "status|start|stop|restart|logs"}'
    """
    import subprocess  # noqa: PLC0415

    data: dict = {}
    if json_str.strip():
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            pass

    action = data.get("action", "status")
    compose_dir = str(Path(__file__).parent.parent)

    def _compose(*args: str, timeout: int = 30) -> tuple[str, str, int]:
        r = subprocess.run(
            ["docker", "compose", *args],
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode

    if action == "status":
        stdout, stderr, _ = _compose("ps", "embedder")
        health: dict = {}
        try:
            import httpx  # noqa: PLC0415
            config = _load_config()
            service_url = config.get("embedder", {}).get("service_url", "http://localhost:6335")
            resp = httpx.get(f"{service_url}/health", timeout=5.0)
            health = resp.json()
            running = True
        except Exception as e:
            health = {"error": str(e)}
            running = False
        _out({"action": "status", "running": running, "health": health, "docker_ps": stdout})

    elif action == "start":
        stdout, stderr, code = _compose("up", "-d", "embedder", timeout=60)
        _out({"action": "start", "success": code == 0, "output": stdout or stderr})

    elif action == "stop":
        stdout, stderr, code = _compose("stop", "embedder")
        _out({"action": "stop", "success": code == 0, "output": stdout or stderr})

    elif action == "restart":
        stdout, stderr, code = _compose("restart", "embedder")
        _out({"action": "restart", "success": code == 0, "output": stdout or stderr})

    elif action == "logs":
        lines = data.get("lines", 50)
        stdout, stderr, code = _compose("logs", "--tail", str(lines), "embedder")
        _out({"action": "logs", "logs": stdout or stderr})

    elif action == "build":
        stdout, stderr, code = _compose("build", "embedder", timeout=300)
        _out({"action": "build", "success": code == 0, "output": (stdout + "\n" + stderr).strip()})

    else:
        _out({"error": f"Unknown action '{action}'. Use: status | start | stop | restart | logs | build"})


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

# Command registry: name → (handler, needs_arg)
# needs_arg=True means the remaining args are joined and passed as a single string
COMMANDS: dict[str, tuple[Any, bool]] = {
    "status":                   (cmd_status, False),
    "resolve":                  (cmd_resolve, True),
    "refresh_profile":          (cmd_refresh_profile, True),
    "approve_plan":             (cmd_approve_plan, True),
    "update_plan":              (cmd_update_plan, True),
    "collect":                  (cmd_collect, False),
    "embed":                    (cmd_embed, False),
    "reprocess":                (cmd_reprocess, True),
    "query":                    (cmd_query, True),
    "check_sources":            (cmd_check_sources, False),
    "get_setup_guide":          (cmd_get_setup_guide, True),
    "set_credentials":          (cmd_set_credentials, True),
    "list_providers":           (cmd_list_providers, False),
    "set_routing":              (cmd_set_routing, True),
    "run_worker":               (cmd_run_worker, False),
    "queue_resolve":            (cmd_queue_resolve, True),
    "queue_status":             (cmd_queue_status, False),
    "set_worker_interval":      (cmd_set_worker_interval, True),
    "delete_keywords":          (cmd_delete_keywords, True),
    "set_embed_schedule":       (cmd_set_embed_schedule, True),
    "set_collect_schedule":     (cmd_set_collect_schedule, True),
    "suggest_rules":            (cmd_suggest_rules, True),
    "approve_rules":            (cmd_approve_rules, False),
    "generate_change_report":   (cmd_generate_change_report, True),
    "preview_change_report":    (cmd_preview_change_report, True),
    "approve_report_template":  (cmd_approve_report_template, False),
    "list_keywords":            (cmd_list_keywords, False),
    "embedder_service":         (cmd_embedder_service, True),
}


def main() -> None:
    args = sys.argv[1:]
    if not args:
        _err(f"Usage: python -m skill <command> [args...]\nCommands: {', '.join(COMMANDS)}")

    command = args[0]
    if command not in COMMANDS:
        _err(f"Unknown command: '{command}'. Available: {', '.join(COMMANDS)}")

    handler, needs_arg = COMMANDS[command]
    provided = args[1:]

    if needs_arg:
        if not provided:
            _err(f"Command '{command}' requires an argument.")
        handler(" ".join(provided))
    elif provided:
        # Optional argument - pass if provided, skip if not
        try:
            handler(" ".join(provided))
        except TypeError:
            handler()
    else:
        handler()


if __name__ == "__main__":
    main()
