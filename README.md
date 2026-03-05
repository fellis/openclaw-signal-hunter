# Signal Hunter - OpenClaw Plugin

Market intelligence for AI/ML builders. Monitors GitHub, Hugging Face, Hacker News, Stack Overflow and Reddit for signals: developer pain points, feature requests, tool comparisons. Manages everything through a chat interface via [OpenClaw](https://github.com/openclaw).

---

## What it does

You type a keyword ("RAG", "ollama", "LangChain") in chat. Signal Hunter:

1. **Discovers** where the topic is discussed (repos, subreddits, HF models, SO tags) via real API calls - no LLM guessing
2. **Proposes a collection plan** - which repos/subreddits/models to monitor, enriched with LLM-suggested relevant subreddits
3. **Collects incrementally** (GitHub issues, HF discussions, HN threads, SO questions, Reddit posts) using cursors
4. **Classifies** every signal with a local LLM using your extraction rules (pain points, feature requests, comparisons, adoption...)
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
| **Local LLM** | Classification, rule suggestions (OpenAI-compatible endpoint) |
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
     ├── core/orchestrator.py  ← collect → embed pipeline (process moved to worker)
     ├── core/processor.py     ← LLM classification (token-aware batching)
     ├── core/embedder.py      ← HTTP client → embedder service → Qdrant (Outbox pattern)
     ├── core/llm_router.py    ← routes ops to local/Claude by config
     ├── core/llm_worker.py    ← LLM task queue worker (one task per cron tick)
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
- Token-aware batching for LLM classification (validated: ~10 signals per batch)
- **LLM Task Queue:** all local LLM calls go through `llm_task_queue` table - one task at a time, no GPU contention. Priority: resolve(50) > process_batch(90).
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
local    | openai_compat  | devstral          | process, suggest_rules, resolve_enrich
claude   | anthropic      | claude-sonnet-4-5 | resolve_strategy, query
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

### Step 4 - Collect

```
You: collect
```

```
ClawBot: Running in background... (first run: 15-30 min for 90 days of history)
```

```
ClawBot:
✓ Collection done:

Keyword    | GitHub | HuggingFace | HN  | SO  | Reddit | Total
-----------|--------|-------------|-----|-----|--------|------
RAG        | 400    | 87          | 89  | 200 | 0*     | 776
ollama     | 200    | 43          | 43  | 98  | 0*     | 384
LangChain  | 200    | 61          | 67  | 200 | 0*     | 528

*Reddit disabled until OAuth credentials configured.
1688 raw signals in database.
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
ClawBot: Classifying 1688 signals with local LLM... (10 signals/batch, token-aware)
✓ Done. 1453 classified (relevant: 734, irrelevant: 719)
```

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

For continuous background operation, configure three cron jobs via OpenClaw:

**LLM Worker (classify signals + resolve keywords):**
```
You: настрой воркер
```
The bot creates a cron job `* * * * *` (every minute). The worker picks the next task from `llm_task_queue` and executes it - one task per tick, no GPU contention:
- `resolve` tasks (priority 50) - keyword enrichment from bulk queue
- `process_batch` tasks (priority 90) - signal classification, auto-enqueued when signals need processing

**Embedding (vectorize classified signals):**
```
You: set embed schedule - 128 items per run
```
Then via `cron.update` with `expr: "*/10 * * * *"` - runs every 10 minutes, embeds up to 128 signals per run.

**Collection (fetch new signals):**
```
You: set collect schedule
```
Then via `cron.update` with `expr: "0 8,20 * * *"` (twice a day at 08:00 and 20:00).

All cron jobs run silently (`delivery.mode: none`) - no Telegram noise.

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
| `queue_status` | Show LLM task queue: pending / running / failed |
| `run_worker` | Execute next LLM task from queue (called by cron every minute) |
| `set_worker_interval <json>` | Configure worker cron interval `{"interval_seconds": 60}` |
| `approve_plan <json>` | Save approved collection plan |
| `update_plan <json>` | Add or remove targets from a plan |
| `refresh_profile <keyword>` | Re-run discovery, update cached profile |
| `list_keywords` | List all tracked keywords |
| `collect` | Collect from all approved plans (incremental) |
| `embed` | Vectorize pending signals into Qdrant |
| `reprocess <json>` | Delete and reclassify signals for a keyword |
| `suggest_rules <keyword>` | Analyze real posts, suggest extraction rules |
| `approve_rules <json>` | Save approved rules to config |
| `set_embed_schedule <json>` | Configure max items per embed cron run |
| `set_collect_schedule <json>` | Configure collect cron schedule |
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
    "max_signals_per_batch": 10,
    "max_batches_per_run": 3,
    "max_tokens_per_batch": 20000,
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

**`processor` limits explained:**
- `max_signals_per_batch: 10` - signals per LLM call. At ~37 tok/s with 200 output tokens/signal: 10 × 200 = ~54s. Safe under a 60s nginx timeout. Increase only after raising `proxy_read_timeout`.
- `max_batches_per_run: 3` - LLM batches per cron execution. Controls how much work one cron run does (3 × 10 = 30 signals/run).
- `max_tokens_per_batch: 20000` - input token budget per LLM batch (body truncated via `max_body_chars`).

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
| `processed_signals` | LLM classification results |
| `embedding_queue` | Outbox: pending Qdrant upserts |
| `collection_cursors` | Incremental collection state per target |
| `keyword_profiles` | Discovered + enriched keyword metadata |
| `keyword_collection_plans` | Approved collection plans |
| `change_report_snapshots` | Saved report history |
| `llm_task_queue` | LLM task queue (resolve / process_batch), priority-ordered |
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

## License

MIT
