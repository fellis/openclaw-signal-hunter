# Signal Hunter - OpenClaw Plugin

Market intelligence for AI/ML builders. Monitors GitHub, Hugging Face, Hacker News, Stack Overflow and Reddit for signals: developer pain points, feature requests, tool comparisons. Manages everything through a chat interface via [OpenClaw](https://github.com/openclaw).

---

## What it does

You type a keyword ("RAG", "ollama", "LangChain") in chat. Signal Hunter:

1. **Discovers** where the topic is discussed (repos, subreddits, HF models, SO tags) via real API calls - no LLM guessing
2. **Proposes a collection plan** - which repos/subreddits/models to monitor, enriched with LLM-suggested aliases and search queries
3. **Collects automatically** - every 24h per keyword via a dedicated Collect Worker cron (GitHub, HF, HN, SO, Reddit), expanding the plan with newly appeared repos/spaces on each run
4. **Classifies** every signal using embedding cosine similarity against your extraction rules - fast local inference, no GPU required for classify. LLM generates a short summary only for relevant signals
5. **Embeds** relevant signals into Qdrant with `bge-m3` via a persistent Docker service (always warm, no per-request model load)
6. **Answers questions** in natural language: "what are the top complaints about RAG retrieval this month?"
7. **Generates change reports** - weekly/monthly deltas with what's new and what grew

Everything runs on a VPS, fully offline (except for API calls to data sources and the LLM providers you configure).

---

## Stack

| Component | Role |
|---|---|
| **Python 3.11+** | Core skill logic |
| **TypeScript** | OpenClaw plugin adapter (thin wrapper) |
| **PostgreSQL 16** | Structured storage: signals, profiles, cursors, LLM cost log |
| **Qdrant** | Vector search (cosine, 1024 dims) |
| **BAAI/bge-m3** | Cross-lingual embeddings via `sentence-transformers` |
| **Embedder service** | FastAPI Docker container - bge-m3 loaded once, serves HTTP /embed |
| **Local LLM** | Summary generation, rule suggestions, keyword enrichment (OpenAI-compatible endpoint) |
| **Claude (Anthropic)** | Queries, resolution strategy (configurable) |
| **Docker Compose** | PostgreSQL + Qdrant + Embedder service |

---

## Architecture

```
OpenClaw chat
     │
     ▼
src/index.ts          ← OpenClaw plugin entry (register tools + /sh command)
src/tools.ts          ← tool definitions (thin TS wrappers)
src/runner.ts         ← spawns: python -m skill <command> [args]
     │
     ▼  JSON via stdout
skill/main.py         ← CLI dispatcher
     │
     ├── core/resolver.py      ← keyword discovery + LLM enrichment
     ├── core/orchestrator.py  ← collect → embed pipeline
     ├── core/embed_processor.py ← embedding-based classify (no LLM, default mode)
     ├── core/embed_worker.py  ← embed worker: runs EmbedProcessor per cron tick
     ├── core/processor.py     ← LLM classification fallback (mode: "llm")
     ├── core/embedder.py      ← HTTP client → embedder service → Qdrant (Outbox pattern)
     ├── core/llm_router.py    ← routes ops to local/Claude by config
     ├── core/llm_worker.py    ← LLM task queue worker (resolve + summarize_batch only)
     │
     ├── collectors/
     │   ├── github.py         ← GitHub Issues (repo-scoped, cursor on updated_at)
     │   ├── huggingface.py    ← HF model/space discussions + papers
     │   ├── hackernews.py     ← Algolia HN API (no auth)
     │   ├── stackoverflow.py  ← Stack Exchange API v2.3
     │   └── reddit.py         ← Reddit JSON API / OAuth (60 req/min with token)
     │
     └── storage/
         ├── postgres.py       ← all SQL (raw_signals, processed_signals, llm_task_queue...)
         ├── vector.py         ← Qdrant wrapper
         └── config_manager.py ← atomic config.json writes (temp file + rename)

Docker Compose services:
     ├── postgres:5433         ← PostgreSQL 16
     ├── qdrant:6333           ← Qdrant vector DB
     └── embedder:6335         ← FastAPI + bge-m3 (always warm, restart: unless-stopped)
           embedder_service.py ← /embed (batch) + /embed-query + /health
```

**Design principles:**
- Each collector is a self-contained module implementing `BaseCollector`
- Business logic stays in Python; TypeScript only handles IPC
- Discovery-first: LLM enriches only facts confirmed by API calls, never guesses
- **LLM Task Queue:** all LLM calls go through `llm_task_queue` - one task at a time, no GPU contention. Priority: resolve(50) > summarize_batch(90)
- **Three separate workers:** Embed Worker (every minute) classifies signals via embeddings (no LLM); LLM Worker (every minute) handles resolve + summarize; Collect Worker (every 5 min) handles API collection - they never block each other
- **Auto-discovery of new sources:** GitHub and HuggingFace collectors extend plans with newly appeared repos/spaces (`discover_new_sources`) on each collect cycle - no manual re-resolve needed
- **Daily collection per keyword:** Collect Worker locks each keyword with `last_collected_at = now()` before collecting; re-triggers only after 24h; stalest keywords first
- Outbox pattern for embedding queue (PostgreSQL → Qdrant, crash-safe)
- Embedder runs as a persistent Docker service: model loads once at startup, all encode calls go via HTTP - no per-run model reload overhead
- Anti-hallucination gate on query answers: URLs not in source data are stripped
- `config.json` is excluded from git - live rules and credentials survive `git pull`
- flock-based process lock prevents parallel cron runs from duplicating work

---

## Prerequisites

- VPS or local machine with Python 3.11+
- Docker + Docker Compose (for PostgreSQL, Qdrant, and the Embedder service)
- [OpenClaw](https://github.com/openclaw) installed
- Local LLM with OpenAI-compatible API (e.g. [Ollama](https://ollama.com) with Devstral, Mistral, etc.)
- Anthropic API key (for queries and keyword resolution strategy)

---

## Installation

### 1. Clone and place the plugin

```bash
git clone https://github.com/fellis/openclaw-signal-hunter.git
cd openclaw-signal-hunter
```

Place (or symlink) the directory into your OpenClaw extensions folder:

```bash
ln -s $(pwd) ~/.openclaw/extensions/signal-hunter
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# PostgreSQL (docker-compose exposes 5433 on host)
DATABASE_URL=postgresql://signal:signal@localhost:5433/signal_hunter

# Qdrant
QDRANT_URL=http://localhost:6333

# GitHub (optional - public rate limit 60 req/hr works for testing)
GITHUB_TOKEN=ghp_your_token_here

# Local LLM (OpenAI-compatible endpoint)
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_API_KEY=local
LOCAL_LLM_MODEL=devstral

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-your_key_here
```

### 3. Create config.json

`config.json` is excluded from git (contains live rules and credentials). Copy the example:

```bash
cp config.example.json config.json
```

> **Important:** never `git pull` without knowing that `config.json` won't be touched. It's in `.gitignore` - safe to pull.

### 4. Start infrastructure

```bash
docker compose up -d
```

This starts three services:
- **PostgreSQL 16** (port 5433) - schema applied automatically on first start
- **Qdrant** (port 6333) - vector storage, data persisted in `qdrantdata` volume
- **Embedder** (port 6335) - FastAPI + bge-m3; downloads model on first start (~570MB, cached in `hf_cache` volume)

Check embedder is ready:

```bash
curl http://localhost:6335/health
# {"status":"ok","model":"BAAI/bge-m3","ready":true}
```

### 5. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 6. Register the plugin in OpenClaw

Add to your `~/.openclaw/openclaw.json` under `plugins`:

```json
{
  "id": "signal-hunter",
  "path": "~/.openclaw/extensions/signal-hunter",
  "config": {
    "pythonBin": "python3",
    "skillDir": null
  }
}
```

Reload OpenClaw. The plugin registers 25 tools and the `/sh` slash command.

---

## Usage walkthrough

Here is a complete scenario: tracking developer pain points around **RAG** and **ollama**.

### Step 1 - Check source readiness

```
You: source status
```

```
ClawBot:
✓ github        - 5000 req/hr (authenticated)
✓ hackernews    - no auth required
✓ stackoverflow - 300 req/day without key (10000 with key)
✓ huggingface   - public API (set HF_TOKEN for higher limits)
✗ reddit        - disabled (needs OAuth credentials)
✗ producthunt   - disabled (needs OAuth app)
```

```
You: how do I set up GitHub?
```

```
ClawBot:
1. Go to github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Name: signal-hunter, Scopes: public_repo
4. Copy the token and type: set credentials github {"api_token": "ghp_xxx"}
```

---

### Step 2 - Configure LLM providers

```
You: show providers
```

```
ClawBot:
Provider | Type           | Model             | Operations
---------|----------------|-------------------|---------------------------
local    | openai_compat  | devstral          | summarize, suggest_rules, resolve_enrich
claude   | anthropic      | claude-haiku-4-5  | resolve_strategy, query
```

To route an operation to a different provider:

```
You: use claude for classification
```

```
ClawBot: ✓ llm_routing.process → claude
```

---

### Step 3 - Add keywords and discover

```
You: start monitoring RAG, ollama, LangChain
```

Signal Hunter calls each source API (no LLM guessing) and returns discovered resources:

```
ClawBot:
DISCOVERY: RAG
  GitHub: langchain-ai/langchain (88k stars), run-llama/llama_index (35k), ...18 more
  HuggingFace: BAAI/bge-m3 (model), deepset/roberta-base-squad2, ...8 more
  Reddit: r/LocalLLaMA, r/LangChain, r/MachineLearning (LLM-suggested)
  HN: 89 threads in last 90 days
  SO: 1820 questions with tag [rag]

Proposed collection plan:
  RAG: github(20 repos), huggingface(10 models), reddit(8 subs), hn, so

Approve all? Or adjust targets?
```

```
You: for RAG remove MachineLearning - too broad. Approve the rest.
```

```
ClawBot: ✓ Plans saved for 3 keywords.
```

---

### Step 4 - Collection happens automatically

No manual step needed. After keywords are resolved and plans approved, the **Collect Worker** (runs every 5 minutes) picks the stalest uncollected keyword and fetches new signals for it. Three workers run in parallel without blocking each other:

```
Collect Worker tick (every 5 min):
  → picks stalest keyword not collected in 24h (e.g. "RAG")
  → locks it: last_collected_at = now()
  → fetches GitHub / HF / HN / SO / Reddit
  → next tick picks "ollama" (RAG already locked)

Embed Worker tick (every 1 min, independent):
  → fetches unprocessed raw signals (batch_size signals per batch)
  → embeds via local bge-m3 HTTP service
  → classifies by cosine similarity against rule vectors
  → saves to processed_signals with summary=null (fast, no LLM)

LLM Worker tick (every 1 min, independent):
  → resolve pending keywords (LLM)
  → summarize_batch: generate summaries for classified signals (LLM)
    → adds to embedding_queue after summary is ready
```

First run fetches 90 days of history per source. Subsequent runs are incremental (cursor-based). Check status:

```
You: статус очереди
```

```
ClawBot: LLM queue: 0 pending, 0 running. Last collect: RAG 2m ago, ollama in progress.
```

---

### Step 5 - Suggest and approve classification rules

```
You: suggest rules for RAG
```

The LLM analyzes actual posts from the database and proposes rules grounded in real content:

```
ClawBot:
Suggested extraction_rules for "RAG" (from 1001 real posts):

1. retrieval_failure (priority 5)
   When retrieval returns irrelevant docs or causes hallucinations
   Real examples: "RAG keeps returning wrong documents even with good embeddings"
                  "chunks are too large and context gets diluted"

2. chunking_strategy (priority 4)
   Questions and pain around document splitting strategies

3. embedding_model_choice (priority 4)
   Comparisons and selection of embedding models

4. reranking (priority 3)
   Requests and pain around the reranking step

5. evaluation_difficulty (priority 3)
   Difficulty evaluating RAG pipeline quality

Approve all?
```

```
You: approve
```

```
ClawBot: ✓ 5 rules saved to config.json
```

---

### Step 6 - Process and embed

```
You: process
```

```
ClawBot: Classifying 1688 signals (embed mode)...
✓ Done. 1453 classified (relevant: 734, irrelevant: 719)
  - Classify: embedding cosine similarity (~0.1s/signal, no LLM)
  - Summaries: queued for async generation by LLM Worker
```

> In practice you don't need to call `process` manually - the Embed Worker cron handles it automatically every minute.

Embedding happens automatically via the cron job (every 10 minutes). To run immediately:

```
You: embed
```

```
ClawBot: ✓ 734 vectors indexed into Qdrant (bge-m3, 1024 dims)
```

---

### Step 7 - Query the database

```
You: what are the top pain points with RAG retrieval this month?
```

Signal Hunter embeds the question via the embedder service, searches Qdrant for the most similar signals, re-ranks by `rank_score` (engagement + quality + time decay), then uses Claude to synthesize:

```
ClawBot:
Top RAG retrieval pain points (from 23 signals, last 30 days):

1. **Context dilution with large chunks** (14 signals)
   Developers report that chunks over 512 tokens cause the LLM to lose focus.
   Source: https://github.com/langchain-ai/langchain/issues/12847

2. **Reranking latency in production** (9 signals)
   Cross-encoder rerankers add 300-800ms per query - too slow for real-time.
   Source: https://reddit.com/r/LocalLLaMA/comments/...

3. **Multilingual retrieval degradation** (7 signals)
   When source documents are in mixed languages, recall drops ~40% with English-only embeddings.
   Source: https://stackoverflow.com/q/...
```

---

### Step 8 - Change reports

```
You: what changed for ollama this week?
```

```
ClawBot:
## ollama - weekly signal report (Feb 27 - Mar 5)

**New signals: 47** (+12% vs last week)

Top new issues:
- GPU memory not released after model unload (score: 245) - github.com/ollama/ollama/issues/...
- Windows CUDA support broken after update (score: 189)
- Feature request: model quantization API (score: 134)

By category:
- bug_report: 21 (+5)
- feature_request: 14 (+3)
- performance: 8 (-1)
- documentation: 4 (+2)

**Trend:** Bug reports increased 31% - possible regression in recent release.
```

To customize the report format:

```
You: show me how the report would look in table format
You: this looks good, save this as the report template for ollama
```

---

### Step 9 - Automation (cron)

Four cron jobs run continuously and independently:

| Cron | Schedule | What it does |
|---|---|---|
| **LLM Worker** | `* * * * *` (every 1 min) | resolve keywords + summarize_batch (LLM only, no collection) |
| **Embed Worker** | `* * * * *` (every 1 min) | classify raw signals via embeddings (no LLM, no GPU) |
| **Collect Worker** | `*/5 * * * *` (every 5 min) | picks 1 stalest keyword, fetches signals (no LLM) |
| **Embed** | `*/10 * * * *` (every 10 min) | vectorizes summarized signals into Qdrant |

LLM Worker task priorities (sequential, one at a time):
- `resolve` (priority 50) - keyword enrichment + auto-approved collection plan
- `summarize_batch` (priority 90) - generate summaries for classified signals (auto-enqueued)

Embed Worker per tick (no LLM, runs independently):
- Fetches raw signals with no `processed_signals` row (up to `batch_size * max_batches_per_run`)
- Embeds via local bge-m3 HTTP service
- Classifies by cosine similarity against rule vectors (is_relevant, matched_rules, confidence, intensity)
- Saves with `summary=null` - summary is generated asynchronously by LLM Worker

Collect Worker per tick:
- Picks the single stalest keyword not collected in 24h
- Locks it (sets `last_collected_at = now()`) to prevent double-trigger
- Fetches GitHub, HF, HN, SO, Reddit - expands plan with new repos/spaces
- Multiple ticks can run concurrently on different keywords safely

All cron jobs run silently (`delivery.mode: none`) - no Telegram noise.

**Full automated lifecycle after adding a keyword:**
```
queue_resolve → resolve (LLM, ~1 min) → auto-approved plan
             → Collect Worker picks it (daily, 90d backfill on first run, incremental after)
             → Embed Worker classifies instantly (no LLM) → summary=null
             → LLM Worker summarize_batch → adds to embedding_queue
             → Embed cron (every 10 min) → Qdrant
             → GitHub/HF plan auto-expanded with new repos on each collect
```

**Adding many keywords at once (bulk queue):**
```
You: добавь в очередь LangGraph, CrewAI, AutoGen, PydanticAI, Semantic Kernel
```
The bot queues all keywords. The worker resolves one per minute and auto-approves collection plans. Check progress:
```
You: что в очереди?
```

---

### Step 10 - Manage the embedder service

The embedder runs as a Docker container. Manage it from chat:

```
You: embedder status
```

```
ClawBot:
✓ Embedder service: running
Model: BAAI/bge-m3 | Ready: true

Docker: signal-hunter-embedder-1  Up 2 hours (healthy)
```

Other actions:
```
You: restart embedder      → restarts the container
You: embedder logs         → last 50 lines of logs
You: rebuild embedder      → rebuilds Docker image (after code changes)
```

Or via slash command: `/sh embedder [status|start|stop|restart|logs|build]`

---

## All available commands

| Command | Description |
|---|---|
| `status` | System stats: signal counts, embed queue, monthly LLM cost |
| `check_sources` | API readiness and rate limits for all sources |
| `get_setup_guide <source>` | Step-by-step credential instructions |
| `set_credentials <json>` | Save API credentials to config |
| `resolve <keyword>` | Discover + propose collection plan (one keyword, interactive) |
| `queue_resolve <json>` | Add many keywords to background queue `{"keywords": [...]}` |
| `approve_plan <json>` | Save approved collection plan |
| `update_plan <json>` | Add or remove targets from a plan |
| `refresh_profile <keyword>` | Re-run discovery, update cached profile |
| `list_keywords` | List all tracked keywords |
| `delete_keywords <json>` | Delete keywords and their plans `{"keywords": [...]}` (confirm first) |
| `run_worker` | Process LLM task queue - resolve + summarize_batch (cron every 1 min) |
| `run_embed_worker` | Classify raw signals via embeddings - no LLM (cron every 1 min) |
| `run_collect_worker` | Collect signals for the stalest keyword (cron every 5 min) |
| `queue_status` | Show LLM task queue: pending / running / failed |
| `set_worker_interval <json>` | Configure worker cron interval `{"interval_seconds": 60}` |
| `retry_failed` | Reset all failed LLM tasks back to pending |
| `embed` | Vectorize pending signals into Qdrant (runs automatically via cron) |
| `set_embed_schedule <json>` | Configure max items per embed cron run |
| `reprocess <json>` | Delete and reclassify signals for a keyword |
| `suggest_rules <keyword>` | Analyze real posts, suggest extraction rules |
| `approve_rules <json>` | Save approved rules to config |
| `embedder_service <json>` | Manage embedder Docker container (status/start/stop/restart/logs/build) |
| `query <prompt>` | Semantic search + LLM synthesis |
| `generate_change_report <keyword>` | Delta report since last snapshot |
| `preview_change_report <json>` | Sample report using custom format instructions |
| `approve_report_template <json>` | Save approved report format |
| `list_providers` | Show LLM providers and routing |
| `set_routing <json>` | Change LLM provider for an operation |

All commands are also available as OpenClaw tools (prefix `signal_hunter_`) and via the `/sh` slash command for quick access.

---

## Configuration

`config.json` stores all runtime settings. It is **excluded from git** - `git pull` never overwrites it. Use `config.example.json` as the reference template for a fresh install.

Key sections:

```json
{
  "sources": {
    "github":        { "enabled": true,  "credentials": {"api_token": "${GITHUB_TOKEN}"} },
    "hackernews":    { "enabled": true,  "credentials": {} },
    "stackoverflow": { "enabled": true,  "credentials": {} },
    "huggingface":   { "enabled": true,  "credentials": {} },
    "reddit":        { "enabled": false, "credentials": {} },
    "producthunt":   { "enabled": false, "credentials": {} }
  },
  "extraction_rules": [],
  "processor": {
    "mode": "embed",
    "relevance_threshold": 0.40,
    "rule_threshold": 0.50,
    "embed_batch_size": 32,
    "summary_batch_size": 5,
    "summary_fetch_limit": 50,
    "batch_size": 50,
    "max_batches_per_run": 5,
    "max_body_chars": 1000
  },
  "embedder": {
    "model": "BAAI/bge-m3",
    "dimensions": 1024,
    "batch_size": 64,
    "device": "cpu",
    "service_url": "http://localhost:6335",
    "max_items_per_run": 128
  },
  "llm_routing": {
    "process":          "local",
    "suggest_rules":    "local",
    "resolve_enrich":   "local",
    "resolve_strategy": "claude",
    "query":            "claude"
  },
  "change_report": {
    "top_n_new": 10,
    "instructions": "",
    "approved_template": null
  },
  "report": {
    "language": "ru",
    "top_n": 20,
    "similarity_threshold": 0.5
  }
}
```

> **Note:** `llm_routing.process` is only used when `mode: "llm"` (fallback). In `embed` mode classification is done by cosine similarity - no LLM call for classify at all.

**`processor` settings explained:**
- `mode: "embed"` - use embedding cosine similarity for classify + LLM for summary only. Set to `"llm"` to revert to full LLM classification.
- `relevance_threshold: 0.40` - minimum cosine similarity to a signal to even consider it. Signal is only marked relevant if it also matches at least one rule (see below).
- `rule_threshold: 0.50` - minimum similarity to assign a rule to a signal. A signal is `is_relevant=true` only if at least one rule passes this threshold - prevents generic on-topic content without a concrete category from flooding the feed.
- `embed_batch_size: 32` - signals per embedder HTTP call during classify.
- `summary_batch_size: 5` - relevant signals per LLM call in summarize_batch worker.
- `summary_fetch_limit: 50` - signals fetched per summarize_batch worker tick.
- `batch_size: 50` - raw signals fetched per Embed Worker batch.
- `max_batches_per_run: 5` - max batches the Embed Worker processes per cron tick (50 × 5 = 250 signals/min max).

**`embedder` settings explained:**
- `service_url` - HTTP endpoint of the embedder Docker container. If unreachable, falls back to loading bge-m3 locally.
- `max_items_per_run: 128` - signals to embed per cron run. At ~100ms/signal with the service: 128 × 100ms ≈ 13s per run.

All writes to `config.json` are atomic (temp file + fsync + rename) - safe for concurrent processes.

Sensitive values (API keys, DB passwords) live only in `.env` and are never written to `config.json`.

---

## Embedder service

The embedder is a separate FastAPI Docker container (`embedder_service.py`) that keeps `bge-m3` loaded in memory permanently. This eliminates the 10-30s model load overhead that would otherwise occur on every `sh_embed` or `sh_query` call.

```
POST /embed        {"texts": [...], "normalize": true}  → {"vectors": [[...]]}
POST /embed-query  {"text": "...", "normalize": true}   → {"vector": [...]}
GET  /health                                            → {"status": "ok", "ready": true}
```

The `Embedder` class in `core/embedder.py` calls the service via HTTP when `service_url` is configured, and falls back to loading the model locally if the service is down.

The model is cached in the `hf_cache` Docker volume - not re-downloaded on container restart.

---

## Database schema

PostgreSQL tables:

| Table | Purpose |
|---|---|
| `raw_signals` | Collected posts/issues/threads |
| `processed_signals` | Embedding classification results (is_relevant, matched_rules, confidence, intensity, summary) |
| `embedding_queue` | Outbox: pending Qdrant upserts |
| `collection_cursors` | Incremental collection state per target |
| `keyword_profiles` | Discovered + enriched keyword metadata (`last_collected_at` tracks daily collect) |
| `keyword_collection_plans` | Approved collection plans |
| `change_report_snapshots` | Saved report history |
| `llm_task_queue` | LLM task queue: resolve(50) / summarize_batch(90), priority-ordered |
| `llm_usage_log` | Token usage and cost per operation |

---

## Rank score formula

```
rank_score = (0.3 * log10(1 + engagement) + 0.7 * (intensity/5) * confidence)
             * 0.5^(hours_since_post / 168)
```

Weights: engagement 30%, quality (intensity × confidence) 70%, half-life 7 days.

---

---

## Troubleshooting

### Embed Worker cron always skipped (`lastStatus: "skipped"`)

**Symptom:** Raw signals accumulate unprocessed. `~/.openclaw/cron/jobs.json` shows the Embed Worker with `lastStatus: "skipped"`, sometimes with `lastError: "empty-heartbeat-file"`.

**Root cause:** The Embed Worker cron was created with `sessionTarget: "main"`. With that setting OpenClaw checks the agent's `HEARTBEAT.md` file before triggering the job. The heartbeat file is intentionally empty ("keep empty to skip heartbeat calls"), so OpenClaw treats the session as inactive and skips the job every time.

**Fix:** Open `~/.openclaw/cron/jobs.json` and find the job with `"name": "Signal Hunter - Embed Worker"`. Set it to:

```json
{
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "Run signal_hunter_run_embed_worker to classify pending signals. Report briefly: how many classified, how many remaining."
  },
  "delivery": { "mode": "none" }
}
```

Then restart the OpenClaw container. The worker will create a fresh isolated session on each cron tick - no heartbeat dependency.

**Correct configuration for all Signal Hunter cron jobs:**

| Job | sessionTarget | payload.kind | delivery.mode |
|---|---|---|---|
| Embed Worker | `isolated` | `agentTurn` | `none` |
| LLM Worker | `isolated` | `agentTurn` | `none` |
| Collect Worker | `isolated` | `agentTurn` | `none` |
| Auto Embed | `isolated` | `agentTurn` | `none` |

> Note: OpenClaw may overwrite `delivery.mode` back to `"announce"` after the first cron run. If skipping resumes, re-apply the fix.

### Keyword resolve tasks stuck in `failed` with `Internal Server Error`

**Symptom:** `llm_task_queue` shows multiple `resolve` tasks with `status=failed`, `error="Internal Server Error"` and `retry_count=3`.

**Root cause:** The local LLM endpoint is returning HTTP 500. Most common cause: the discovery prompt is too large for the model's context window. GitHub repos can have README-length descriptions (5000+ chars), making prompts exceed 50K+ tokens for popular keywords like GPT-4.1, Gemini, etc.

**Fix:** Already patched in `core/resolver.py` - `_slim_for_llm()` caps all string fields at 1000 chars before building the prompt (~2-5K tokens, well within any 32K+ context window).

To reset stuck tasks and retry:

```sql
UPDATE llm_task_queue
SET status = 'pending', retry_count = 0, error = NULL
WHERE status = 'failed' AND task_type = 'resolve';
```

---

## License

MIT
