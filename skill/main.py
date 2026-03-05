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
    """Print system status: keywords, signal counts, LLM costs."""
    storage = _make_storage()
    _out(storage.get_status_summary())


def cmd_resolve(keyword: str) -> None:
    """Discover and enrich a keyword, output proposal."""
    from core.registry import load_all_collectors  # noqa: PLC0415
    from core.resolver import KeywordResolver  # noqa: PLC0415

    config = _load_config()
    load_all_collectors()
    storage = _make_storage()
    router = _make_router(config)
    resolver = KeywordResolver(router, storage)
    result = resolver.resolve(keyword)
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
    Save approved plan. json_str: '{"canonical_name": "...", "plans": {"github": [{...}]}}'
    """
    from core.resolver import KeywordResolver  # noqa: PLC0415

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    storage = _make_storage()
    config = _load_config()
    router = _make_router(config)
    resolver = KeywordResolver(router, storage)
    resolver.approve_plan(data["canonical_name"], data["plans"])
    _out({"status": "ok", "message": f"Plan approved for '{data['canonical_name']}'"})


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


def cmd_process() -> None:
    """Run LLM classification on all unprocessed signals."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)
    orch = Orchestrator(config, storage)
    result = orch.process(router)
    _out({"status": "done", "phase": "process", **result})


def cmd_embed() -> None:
    """Embed pending signals into Qdrant."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    device = config.get("embedder", {}).get("device", "cpu")
    storage = _make_storage()
    orch = Orchestrator(config, storage)
    result = orch.embed_pending(device=device)
    _out({"status": "done", "phase": "embed", **result})


def cmd_full_cycle() -> None:
    """Run collect + process + embed in sequence."""
    from core.orchestrator import Orchestrator  # noqa: PLC0415

    config = _load_config()
    storage = _make_storage()
    router = _make_router(config)
    device = config.get("embedder", {}).get("device", "cpu")

    orch = Orchestrator(config, storage)
    collect_result = orch.collect()
    process_result = orch.process(router)
    embed_result = orch.embed_pending(device=device)

    _out({
        "status": "done",
        "phase": "full_cycle",
        "collected": collect_result.get("total", 0),
        "processed": process_result.get("total", 0),
        "embedded": embed_result.get("total", 0),
    })


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
        _out({"keyword": keyword, "analyzed_posts": len(sample), "suggested_rules": rules})
    except json.JSONDecodeError:
        _out({"keyword": keyword, "analyzed_posts": len(sample), "suggested_rules": raw})


def cmd_approve_rules(json_str: str) -> None:
    """Save approved extraction rules to config.json."""
    try:
        rules = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

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
    _out({"type": "preview", "keyword": keyword, "text": preview})


def cmd_approve_report_template(json_str: str) -> None:
    """
    Save approved report template.
    json_str: '{"keyword": "RAG", "template": "<text>"}' (template=null to reset)
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        _err(f"Invalid JSON: {e}")

    keyword = data.get("keyword", "")
    template = data.get("template")  # None = reset

    cfg_mgr = _make_config_manager()
    cfg_mgr.set_nested(["change_report", "approved_template"], template)

    # Save instructions too if provided
    if instructions := data.get("instructions"):
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
    "process":                  (cmd_process, False),
    "embed":                    (cmd_embed, False),
    "full_cycle":               (cmd_full_cycle, False),
    "reprocess":                (cmd_reprocess, True),
    "query":                    (cmd_query, True),
    "check_sources":            (cmd_check_sources, False),
    "get_setup_guide":          (cmd_get_setup_guide, True),
    "set_credentials":          (cmd_set_credentials, True),
    "list_providers":           (cmd_list_providers, False),
    "set_routing":              (cmd_set_routing, True),
    "suggest_rules":            (cmd_suggest_rules, True),
    "approve_rules":            (cmd_approve_rules, True),
    "generate_change_report":   (cmd_generate_change_report, True),
    "preview_change_report":    (cmd_preview_change_report, True),
    "approve_report_template":  (cmd_approve_report_template, True),
    "list_keywords":            (cmd_list_keywords, False),
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
    else:
        handler()


if __name__ == "__main__":
    main()
