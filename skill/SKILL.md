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

## Data pipeline (automatic - no manual collect needed)

Collection and classification are fully automatic, managed by the LLM Worker cron (every minute).

Lifecycle after adding a keyword:
1. `resolve` task (priority 50) - LLM enriches keyword, creates collection plan (auto-approved)
2. `collect_keyword` task (priority 70) - worker auto-enqueues collect for each keyword if
   last_collected_at is NULL or older than 24h; picks stalest keywords first, up to 3 per tick
3. `process_batch` task (priority 90) - auto-enqueued when unprocessed signals exist
4. Embedding cron (every 10 min) - embeds classified signals into Qdrant

## LLM Task Queue
- `run_worker` → process LLM task queue (called by cron every minute)
- `queue_status` → show current queue: pending, running, failed tasks
- `set_worker_interval <json>` → configure worker cron `{"interval_seconds": 60}` → returns cron_job_id
- `retry_failed` → reset all failed tasks back to pending

LLM task priorities (lower = higher priority):
- priority 50: `resolve` - keyword enrichment
- priority 70: `collect_keyword` - collect signals for one keyword (max once per 24h)
- priority 90: `process_batch` - signal classification

## Processing and embedding
- `embed` → manually index pending signals into Qdrant (runs automatically via cron every 10 min)
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
- Run long operations (reprocess) via OpenClaw exec with background=true
- Notify user with result after long operation completes
- Use atomic write for config.json (built into skill)
- Never show full credentials to user - only ready/not_ready status
- CRITICAL: do NOT call `collect` manually - collection is automatic. The worker cron collects each keyword at most once per 24h, picking the stalest first.
- CRITICAL: do NOT call `process` or `full_cycle` - these commands no longer exist. Classification happens via the worker queue (run_worker cron).
- CRITICAL: do NOT offer to manually trigger collection. Just tell the user "collection is automatic, happens daily per keyword".
- If user says "add signals" / "collect signals" / "добавь сигналы" - explain that collection is automatic and ask if they want to check queue status instead.
- If user adds ONE keyword: use `resolve` (interactive, shows plan for approval).
- If user adds MANY keywords (2+): use `queue_resolve` (background, auto-approves, no user wait).
