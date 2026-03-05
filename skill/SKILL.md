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
- `resolve <keyword>` → discovery + plan proposal for ONE keyword (show to user for approval)
- `queue_resolve <json>` → add many keywords to queue `{"keywords": ["LangGraph", "CrewAI", ...]}` - worker auto-approves
- `approve_plan <json>` → save approved plan `{"canonical_name": "...", "plans": {...}}`
- `update_plan <json>` → update plan `{"canonical_name": "...", "add": [...], "remove": [...], "collector": "github"}`
- `refresh_profile <keyword>` → re-run discovery and update KeywordProfile
- `list_keywords` → list all tracked keywords

## Collection and processing (cron)
- `collect` → collect from all approved plans (incremental, uses cursors)
- `embed` → index pending signals into Qdrant (runs automatically via cron every 10 min)
- `reprocess <json>` → reclassify signals `{"keyword": "...", "rules": ["rule1"]}` (rules optional)

## LLM Task Queue (replaces direct process/resolve cron)
- `run_worker` → execute next pending LLM task (called by cron every minute)
- `queue_status` → show current queue: pending, running, failed tasks
- `set_worker_interval <json>` → configure worker cron `{"interval_seconds": 60}` → returns cron_job_id

LLM task priorities:
- priority 50: `resolve` - keyword enrichment (queued by queue_resolve)
- priority 90: `process_batch` - signal classification (auto-enqueued by worker when signals exist)

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
- Run long operations (collect, reprocess) via OpenClaw exec with background=true
- Notify user with result after long operation completes
- Use atomic write for config.json (built into skill)
- Never show full credentials to user - only ready/not_ready status
- CRITICAL: after `collect`, do NOT trigger any LLM operations. The worker cron handles classification automatically. Just report how many signals were collected.
- CRITICAL: do NOT call `process` or `full_cycle` - these commands no longer exist. Classification happens via the worker queue (run_worker cron).
- If user says "add signals" / "collect signals" / "добавь сигналы" - run `collect` only.
- If user adds ONE keyword: use `resolve` (interactive, shows plan for approval).
- If user adds MANY keywords (2+): use `queue_resolve` (background, auto-approves, no user wait).
