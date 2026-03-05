---
name: signal-hunter
description: Social listening for AI/ML market. Monitors GitHub, Reddit, HN, SO. Collects and classifies signals (pain points, feature requests, comparisons). Answers questions about accumulated data.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["ANTHROPIC_API_KEY", "DATABASE_URL", "QDRANT_URL"]}, "primaryEnv": "ANTHROPIC_API_KEY"}}
---

# Signal Hunter

Run skill functions via: `python -m skill <command> [arguments]`

## Source management
- `check_sources` → status and rate limits of all sources
- `get_setup_guide <source>` → step-by-step instructions for obtaining credentials
- `set_credentials <source> <json>` → save credentials to config

## Keyword management
- `resolve <keyword>` → discovery + plan proposal (show to user)
- `approve_plan <json>` → save approved plan `{"canonical_name": "...", "plans": {...}}`
- `update_plan <json>` → update plan `{"canonical_name": "...", "add": [...], "remove": [...], "collector": "github"}`
- `refresh_profile <keyword>` → re-run discovery and update KeywordProfile
- `list_keywords` → list all tracked keywords

## Collection and processing (cron)
- `collect` → collect from all approved plans (incremental, uses cursors)
- `process` → LLM classification of unprocessed signals (runs automatically via cron every 2 min)
- `embed` → index pending signals into Qdrant (runs automatically via cron every 10 min)
- `full_cycle` → collect + process + embed in sequence (use only when explicitly requested)
- `reprocess <json>` → reclassify signals `{"keyword": "...", "rules": ["rule1"]}` (rules optional)

## Classification rules
- `suggest_rules <keyword>` → analyze real posts and suggest extraction_rules
- `approve_rules <json>` → save approved rules to config.json

## Queries
- `query <prompt>` → answer question from signals database

## Change reports
- `generate_change_report <keyword>` → delta report since last report
- `preview_change_report <json>` → example report `{"keyword": "...", "instructions": "..."}`
- `approve_report_template <json>` → save template `{"keyword": "...", "template": "..."}`

## LLM providers
- `list_providers` → table of providers and routing
- `set_routing <json>` → update routing `{"operation": "process", "provider": "claude"}`

## Status
- `status` → full system statistics

## Calling rules
- Run long operations (collect, process, reprocess) via OpenClaw exec with background=true
- Notify user with result after long operation completes
- Use atomic write for config.json (built into skill)
- Never show full credentials to user - only ready/not_ready status
- CRITICAL: after `collect`, do NOT run `process`, `embed`, or `full_cycle` - cron handles them automatically (process every 2 min, embed every 10 min). Just report how many signals were collected.
- Use `full_cycle` ONLY when user explicitly asks for it (e.g. "full cycle", "sync everything"). Never infer full_cycle from "add signals" or "collect" requests.
- If user says "add signals" / "collect signals" / "fetch data" / "добавь сигналы" - run `collect` only.
