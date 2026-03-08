# Signal Hunter - OpenClaw Plugin

Market intelligence for AI/ML builders. Monitors GitHub, Hugging Face, Hacker News, Stack Overflow and Reddit for signals: developer pain points, feature requests, tool comparisons. Manages everything through a chat interface via [OpenClaw](https://github.com/openclaw).

---

## What it does

You type a keyword ("RAG", "ollama", "LangChain") in chat. Signal Hunter:

1. **Discovers** where the topic is discussed (repos, subreddits, HF models, SO tags) via real API calls - no LLM guessing
2. **Proposes a collection plan** - which repos/subreddits/models to monitor, enriched with LLM-suggested aliases and search queries
3. **Collects automatically** - every 24h per keyword via the Collect Worker (worker runner, every 5 min; GitHub, HF, HN, SO, Reddit), expanding the plan with newly appeared repos/spaces on each run
4. **Classifies** every signal using embedding cosine similarity against a universal set of signal-type rules (pain_point, feature_request, bug_report, adoption_signal, comparison, use_case, pricing_concern, positive_feedback, market_observation, security_concern). Supports per-rule thresholds and negative anchor penalty. Fast local inference, no GPU required for classify. LLM generates a short summary only for relevant signals
5. **Embeds** relevant signals into Qdrant with `bge-m3` via a persistent Docker service (always warm, no per-request model load)
6. **Answers questions** in natural language: "what are the top complaints about RAG retrieval this month?"
7. **Generates change reports** - weekly/monthly deltas with what's new and what grew
8. **Web report UI** - browse signals by category, cluster, and individual posts at port 8080. Semantic and full-text search live in the Report filter bar (no separate Search page). Date/source/intensity filters and EN/RU language toggle

Everything runs on a VPS, fully offline (except for API calls to data sources and the LLM providers you configure).

---

## Stack

| Component | Role |
|---|---|
| **Python 3.11+** | Core skill logic |
| **TypeScript** | OpenClaw plugin adapter (thin wrapper) |
| **PostgreSQL 16** | Structured storage: signals, profiles, cursors, translations, LLM cost log |
| **Qdrant** | Vector search (cosine, 1024 dims) |
| **BAAI/bge-m3** | Cross-lingual embeddings via `sentence-transformers` |
| **Embedder service** | Two FastAPI containers - `embedder:6335` for classify/query, `embedder-vectorizer:6336` for Qdrant upserts; model loaded once, both share `hf_cache` volume |
| **Local LLM** | Summary generation, rule suggestions, keyword enrichment (OpenAI-compatible endpoint) |
| **Claude (Anthropic)** | Queries, resolution strategy (configurable) |
| **MADLAD-400-3B** | Multilingual machine translation (CTranslate2, INT8). Translates signal titles and summaries to Russian. Runs as a separate FastAPI service, accessed via `llm-api` proxy |
| **Web Report** | React + Vite SPA served by FastAPI on port 8080. Three-level drill-down: categories → clusters → signals. Search (semantic/text) in Report filter bar; EN/RU toggle |
| **Docker Compose** | PostgreSQL + Qdrant + Embedder (classify) + Embedder-Vectorizer (upsert) + Web Report + signal-hunter-workers (runner) |

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
     ├── core/resolver.py        ← keyword discovery + LLM enrichment
     ├── core/orchestrator.py    ← collect → embed pipeline
     ├── core/embed_processor.py ← embedding-based classify; domain pre-filter (positive/negative anchors)
     │                              then rule matching; borderline signals → llm_task_queue for LLM
     │                              universal signal-type rules; neg_weight, neg_min_sim; strip_hn_prefix
     ├── core/embed_worker.py    ← embed worker: runs EmbedProcessor per worker runner tick
     ├── core/embedder.py        ← HTTP client → embedder service → Qdrant (Outbox pattern)
     ├── core/llm_router.py      ← routes ops to local/Claude by config
     ├── core/llm_worker.py      ← LLM task queue: resolve + borderline_relevance + summarize_batch
     ├── core/translate_worker.py ← translation worker: one batch per LLM Worker tick
     │                              translates title+summary → signal_translations table
     │
     ├── collectors/
     │   ├── github.py           ← GitHub Issues (repo-scoped, cursor on updated_at)
     │   ├── huggingface.py      ← HF model/space discussions + papers
     │   ├── hackernews.py       ← Algolia HN API (no auth)
     │   ├── stackoverflow.py    ← Stack Exchange API v2.3
     │   └── reddit.py           ← Reddit JSON API / OAuth (60 req/min with token)
     │
     ├── web_server/             ← FastAPI web report server (port 8080)
     │   ├── app.py              ← application entry point
     │   ├── db.py               ← psycopg2 helpers
     │   ├── routers/
     │   │   ├── report.py       ← /api/report, /api/report/clusters, /api/report/signals (optional q, search_mode)
     │   │   └── search.py       ← get_search_result_ids() used by report; /api/search/semantic, /api/search/text
     │   └── services/
     │       └── clustering.py   ← HDBSCAN/KMeans cluster strategies
     │
     ├── frontend/               ← React + Vite SPA (built into Dockerfile.web)
     │   └── src/
     │       ├── pages/          ← Report, Charts, WorkersLogs (search is a filter on Report)
     │       ├── components/     ← SignalTable (3-level drill-down), FilterPanel, ...
     │       └── api/            ← fetch helpers (report.ts, search.ts)
     │
     └── storage/
         ├── postgres.py         ← all SQL (raw_signals, processed_signals, signal_translations...)
         ├── vector.py           ← Qdrant wrapper
         └── config_manager.py  ← atomic config.json writes (temp file + rename)

Docker Compose services:
     ├── postgres:5433               ← PostgreSQL 16
     ├── qdrant:6333                 ← Qdrant vector DB
     ├── embedder:6335               ← FastAPI + bge-m3, used by classifier + query
     ├── embedder-vectorizer:6336    ← same image, dedicated to Qdrant upserts (outbox)
     │     embedder_service.py ← /embed (batch) + /embed-query + /health
     ├── web-report:8080             ← FastAPI + React UI (Dockerfile.web)
     └── signal-hunter-workers       ← worker runner (embed, LLM, collect, auto-embed)

External services (not in docker-compose):
     └── MADLAD-400-3B translator    ← CTranslate2 INT8 on a separate GPU server
           accessed via https://llm.aegisalpha.io/translator (llm-api proxy)
```

**Design principles:**
- Each collector is a self-contained module implementing `BaseCollector`
- Business logic stays in Python; TypeScript only handles IPC
- Discovery-first: LLM enriches only facts confirmed by API calls, never guesses
- **LLM Task Queue:** all LLM calls go through `llm_task_queue` - one task at a time, no GPU contention. Priority: resolve(50) > borderline_relevance(70) > summarize_batch(90). The LLM router retries up to 3 times on transient network errors (DNS, connection reset, server disconnect) with backoff before failing a task.
- **Worker runner (one process):** Embed Worker (every minute), LLM Worker (every minute), Collect Worker (every 5 min), Auto Embed (every minute) run from a single container/script. Embed Worker classifies via embeddings (domain pre-filter, borderline enqueued for LLM); LLM Worker handles resolve + borderline_relevance + summarize + translate; Auto Embed vectorizes into Qdrant via `embedder-vectorizer:6336`
- **Auto-discovery of new sources:** GitHub and HuggingFace collectors extend plans with newly appeared repos/spaces (`discover_new_sources`) on each collect cycle - no manual re-resolve needed
- **Daily collection per keyword:** Collect Worker locks each keyword with `last_collected_at = now()` before collecting; re-triggers only after 24h; stalest keywords first
- Outbox pattern for embedding queue (PostgreSQL → Qdrant, crash-safe)
- Embedder runs as a persistent Docker service: model loads once at startup, all encode calls go via HTTP - no per-run model reload overhead
- Anti-hallucination gate on query answers: URLs not in source data are stripped
- `config.json` is excluded from git - live rules and credentials survive `git pull`
- Lock file prevents duplicate worker runner instances

---

## Prerequisites

- VPS or local machine with Python 3.11+
- Docker + Docker Compose (for PostgreSQL, Qdrant, Embedder, Embedder-Vectorizer, Web Report)
- [OpenClaw](https://github.com/openclaw) installed
- Local LLM with OpenAI-compatible API (e.g. [Ollama](https://ollama.com) with Devstral, Mistral, etc.)
- Anthropic API key (for queries and keyword resolution strategy)
- MADLAD-400 translator service (optional, for EN/RU translations) - FastAPI + CTranslate2 container, accessible via HTTP with Bearer token

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

# Translation service (MADLAD-400-3B, optional)
# URL of the FastAPI translator endpoint (can be proxied via llm-api)
TRANSLATOR_URL=https://llm.aegisalpha.io/translator
TRANSLATOR_API_KEY=your_bearer_token_here
# Target language for translation worker (ISO 639-1, e.g. "ru", "de", "fr")
TARGET_LANG=ru
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

This starts six services:
- **PostgreSQL 16** (port 5433) - schema applied automatically on first start
- **Qdrant** (port 6333) - vector storage, data persisted in `qdrantdata` volume
- **Embedder** (port 6335) - FastAPI + bge-m3; used by Embed Worker (classification) and semantic query. Downloads model on first start (~570MB, cached in `hf_cache` volume)
- **Embedder-Vectorizer** (port 6336) - same image, dedicated to Auto Embed (Qdrant upserts). Shares `hf_cache` volume - no re-download
- **Web Report** (port 8080) - React + FastAPI; serves the web UI and `/api/*` endpoints
- **signal-hunter-workers** - single runner process: embed worker, LLM worker, collect worker, auto-embed (lock file prevents duplicates). Uses explicit DNS (8.8.8.8, 1.1.1.1) for reliable resolution when calling the local LLM or translator.

Check all services are ready:

```bash
curl http://localhost:6335/health
# {"status":"ok","model":"BAAI/bge-m3","ready":true}
curl http://localhost:6336/health
# {"status":"ok","model":"BAAI/bge-m3","ready":true}
curl http://localhost:8080/api/stats
# {"total_signals": ..., "relevant": ...}
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

### 7. Deploy to production (optional)

For VPS deployment (OVH or similar): see `docs/deploy-vps.md`. Typical flow: `git pull`, `docker compose build web-report` (when frontend or backend changed), `docker compose up -d web-report` (and optionally `signal-hunter-workers`). No search result cache; report/clusters run search on each request when `q` is set.

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
local    | openai_compat  | devstral          | borderline_relevance, summarize_batch, resolve_enrich
claude   | anthropic      | claude-haiku-4-5  | query
```

To route an operation to a different provider:

```
You: use claude for query
```

```
ClawBot: ✓ llm_routing.query → claude
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
  → translate_worker: translate title+summary for embedded signals
    → skips signals already in TARGET_LANG
    → stores results in signal_translations (signal_id / lang / field / text)
```

First run fetches 90 days of history per source. Subsequent runs are incremental (cursor-based). Check status:

```
You: статус очереди
```

```
ClawBot: LLM queue: 0 pending, 0 running. Last collect: RAG 2m ago, ollama in progress.
```

---

### Step 5 - Classification rules

Signal Hunter ships with a **universal set of 10 signal-type rules** that work across all keywords and domains without modification. They classify *what kind of signal it is*, not *what topic it is about*:

| Rule | What it captures |
|---|---|
| `pain_point` | Frustration, blocker, something broken causing friction |
| `feature_request` | Explicit request for a new capability or improvement |
| `bug_report` | Specific reproducible bug with error details or reproduction steps |
| `adoption_signal` | Evidence of real production usage or team migration |
| `comparison` | Explicit comparison between tools, frameworks, or approaches |
| `use_case` | Concrete real-world application of a developer tool or AI/ML technology |
| `pricing_concern` | Concern about cost, billing, API expense, or economic viability |
| `positive_feedback` | Strong explicit praise or success story |
| `market_observation` | Higher-level trend or pattern observed across many teams |
| `security_concern` | Discovered vulnerability, active exploit, or data exposure risk |

Each rule is defined with:
- `description` - primary embedding anchor
- `examples` - additional anchor phrases (each becomes a separate embedding vector; similarity is `max` across all anchors)
- `negative_examples` - used as penalty anchors: if a signal is too close to a negative example, its adjusted similarity drops

Rules are stored in `config.json` under `extraction_rules` (or loaded from `universal-rules.json`). The universal ruleset requires no per-keyword customization. All keywords (RAG, ollama, LangChain, etc.) are classified through the same ruleset - the matched rule tells you *what kind of signal it is*, the keywords in `raw_signals` tell you *what it is about*.

---

### Step 6 - Classify and embed

Classification is fully automatic: Embed Worker (worker runner, every minute) runs domain pre-filter + rule matching; borderline signals are enqueued for LLM Worker, which decides relevance and enqueues summarize. No manual "process" step.

- **Embed Worker:** domain score (positive/negative anchors) → auto-accept, auto-reject, or borderline → LLM task queue.
- **LLM Worker:** borderline_relevance (v6 prompt), then summarize_batch for relevant signals.

Embedding into Qdrant happens automatically via the Auto Embed loop (worker runner, every minute). To run immediately:

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

By signal type:
- bug_report: 21 (+5)
- feature_request: 14 (+3)
- pain_point: 8 (-1)
- adoption_signal: 4 (+2)

**Trend:** Bug reports increased 31% - possible regression in recent release.
```

To customize the report format:

```
You: show me how the report would look in table format
You: this looks good, save this as the report template for ollama
```

---

### Step 9 - Web report

Open the web UI in the browser:

```
http://your-vps:8080
```

The UI has three main sections:

- **Report** - three-level signal browser: categories → clusters → individual signals (title, summary, source, date, intensity). Signals load on demand when you expand. **Search** is built in: in the filter bar choose Semantic or Text, enter a query (min 2 chars), run on Enter or blur; results keep the same category/cluster structure. Filters: date range, source, category, keyword, intensity, confidence. Old `/search` URLs redirect to Report with the query.
- **Charts** - timeline and breakdown visualizations.
- **Workers Logs** - view and clear worker runner logs; restart workers.

**Language toggle (EN / RU):** Switch between English (original content) and Russian (machine-translated by MADLAD-400-3B). The toggle is in the sidebar. The selected language is persisted across sessions. Signals that have been translated show the language badge in the title row. Untranslated signals (translation still pending or worker not configured) fall back to the original English.

The translation worker runs as part of the LLM Worker tick (same runner) - no separate container needed.

---

### Step 10 - Automation (worker runner)

Workers run from a single process (container `signal-hunter-workers` or script `scripts/run_workers.sh`). At startup a lock file prevents duplicate instances; for development, restart the container or process manually.

| Command | Interval | What it does |
|---|---|---|
| **run_worker** | 60 s | resolve keywords + summarize_batch (LLM) + translate batch (MADLAD-400) |
| **run_embed_worker** | 60 s | classify raw signals via embeddings (no LLM, no GPU) |
| **run_collect_worker** | 300 s | picks 1 stalest keyword, fetches signals (no LLM) |
| **embed** | 60 s | vectorizes summarized signals into Qdrant |

LLM Worker task priorities (sequential, one at a time):
- `resolve` (priority 50) - keyword enrichment + auto-approved collection plan
- `borderline_relevance` (priority 70) - LLM relevance decision for borderline signals (hybrid mode)
- `summarize_batch` (priority 90) - generate summaries for classified signals (auto-enqueued)

If a task is stuck in `running` for more than 1 minute (e.g. previous tick died mid-call), the next tick resets it to pending. If another tick is still processing, this tick skips (info log); waiting a minute for the next tick is normal.

After the LLM task queue is drained each tick, `TranslateWorker.run()` is called automatically - one batch of 32 embedded signals (with summary, not yet translated to `TARGET_LANG`) is sent to the MADLAD-400 service. Results are stored in `signal_translations`.

Embed Worker per tick (no LLM, runs independently):
- Fetches raw signals with no `processed_signals` row (up to `batch_size * max_batches_per_run`)
- Strips HN noise prefixes ("Show HN:", "Ask HN:", etc.) from titles before embedding
- Embeds via local bge-m3 HTTP service
- Builds rule vectors once at init: description + examples = positive anchors; negative_examples = negative anchors
- Classifies by adjusted cosine similarity: `pos_sim - neg_weight * max(0, neg_sim - neg_min_sim)` per rule
- Applies per-rule thresholds (`rule_thresholds` in config) for finer precision control
- Saves with `summary=null` - summary is generated asynchronously by LLM Worker

Collect Worker per tick:
- Picks the single stalest keyword not collected in 24h
- Locks it (sets `last_collected_at = now()`) to prevent double-trigger
- Fetches GitHub, HF, HN, SO, Reddit - expands plan with new repos/spaces
- Multiple ticks can run concurrently on different keywords safely

Translate Worker per LLM Worker tick (runs after task queue is drained):
- Fetches up to 32 embedded signals where `summary IS NOT NULL` and `embedding_queue.status = 'done'`
- Skips signals already in `TARGET_LANG` (e.g. Russian signals when `TARGET_LANG=ru`)
- Skips signals already translated for `TARGET_LANG` in `signal_translations`
- Calls `POST TRANSLATOR_URL/translate` with Bearer token auth
- Upserts `(signal_id, lang, field, text)` rows into `signal_translations`
- Returns `{status, translated, rows_stored, remaining}` (logged by LLM Worker)

**Full automated lifecycle after adding a keyword:**
```
queue_resolve → resolve (LLM, ~1 min) → auto-approved plan
             → Collect Worker picks it (daily, 90d backfill on first run, incremental after)
             → Embed Worker classifies instantly (no LLM) → summary=null
             → LLM Worker summarize_batch → adds to embedding_queue
             → Auto Embed (worker runner, every 1 min) → Qdrant
             → LLM Worker translate_worker → signal_translations (EN→RU or other)
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

### Step 11 - Manage the embedder service

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
| `run_worker` | Process LLM task queue - resolve + summarize_batch (worker runner, 1 min) |
| `run_embed_worker` | Classify raw signals via embeddings - no LLM (worker runner, 1 min) |
| `run_collect_worker` | Collect signals for the stalest keyword (worker runner, 5 min) |
| `queue_status` | Show LLM task queue: pending / running / failed |
| `retry_failed` | Reset all failed LLM tasks back to pending |
| `embed` | Vectorize pending signals into Qdrant (worker runner or manual) |
| `set_embed_schedule <json>` | Configure max items per embed run `{"max_items_per_run": 128}` |
| `reprocess <json>` | Delete and reclassify signals for a keyword (Embed Worker re-runs on next tick) |
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
    "relevance_threshold": 0.40,
    "rule_threshold": 0.50,
    "rule_thresholds": { "security_concern": 0.57, "positive_feedback": 0.54 },
    "neg_weight": 0.4,
    "neg_min_sim": 0.3,
    "embed_batch_size": 32,
    "summary_batch_size": 5,
    "batch_size": 50,
    "max_batches_per_run": 5,
    "max_body_chars": 1000
  },
  "hybrid_relevance": {
    "enabled": true,
    "domain_high": 0.40,
    "domain_low": 0.28,
    "llm_task_priority": 70,
    "domain_anchors": { "positive": ["..."], "negative": ["..."] }
  },
  "embedder": {
    "model": "BAAI/bge-m3",
    "dimensions": 1024,
    "batch_size": 64,
    "device": "cpu",
    "service_url": "http://localhost:6335",
    "max_items_per_run": 128
  },
  "embedder_vectorizer": {
    "service_url": "http://localhost:6336",
    "batch_size": 64,
    "max_items_per_run": 512
  },
  "llm_routing": {
    "borderline_relevance": "local",
    "summarize_batch":      "local",
    "resolve_enrich":       "local",
    "query":                "claude"
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

**`processor` settings explained:**
- `relevance_threshold: 0.40` - minimum cosine similarity across all rules to consider a signal at all. Signal is only marked `is_relevant=true` if it also matches at least one rule.
- `rule_threshold: 0.50` - default minimum adjusted similarity to assign a rule to a signal. A signal is relevant only if at least one rule passes this threshold - prevents generic on-topic content without a concrete category from flooding the feed.
- `rule_thresholds` - per-rule overrides for `rule_threshold`. Useful to tighten noisy rules without affecting others. Example: `{"security_concern": 0.57}` raises the bar for that rule only.
- `neg_weight: 0.4` - weight of the negative anchor penalty. For each rule: `adjusted_sim = pos_sim - neg_weight * max(0, neg_sim - neg_min_sim)`. Set to `0.0` to disable.
- `neg_min_sim: 0.3` - floor for the negative penalty. Only negatives with similarity above this threshold count - prevents background noise from penalizing genuine signals.
- `embed_batch_size: 32` - signals per embedder HTTP call during classify.
- `summary_batch_size: 5` - relevant signals per LLM call in summarize_batch worker.
- `batch_size: 50` - raw signals fetched per Embed Worker batch.
- `max_batches_per_run: 5` - max batches the Embed Worker processes per run (50 × 5 = 250 signals/min max).

**`hybrid_relevance`** (domain pre-filter + LLM for borderline):
- `enabled: true` - use domain score (positive/negative anchors) before rule matching; signals between `domain_low` and `domain_high` go to LLM (borderline_relevance task).
- `domain_high: 0.40`, `domain_low: 0.28` - thresholds; above high = auto-accept, below low = auto-reject.
- `llm_task_priority: 70` - priority of borderline_relevance in llm_task_queue (resolve=50, summarize_batch=90).
- `domain_anchors.positive` / `domain_anchors.negative` - phrase lists for AI/ML relevance; see config.example.json.

**`embedder` settings explained:**
- `service_url` - HTTP endpoint used by Embed Worker (classify) and `signal_hunter_query`. If unreachable, falls back to loading bge-m3 locally.
- `max_items_per_run: 128` - signals to embed per run. At ~100ms/signal with the service: 128 × 100ms ≈ 13s per run.

**`embedder_vectorizer` settings explained:**
- `service_url` - dedicated endpoint for Auto Embed (Qdrant upserts). Points to `embedder-vectorizer` container on port 6336. Separating classification and vectorization prevents them from competing for the same HTTP service under load.
- `max_items_per_run: 512` - larger batch than `embedder` because vectorization is the bottleneck; Auto Embed runs every minute to keep up with the classification output.
- If `embedder_vectorizer` section is absent, `embed_pending` falls back to `embedder.service_url`.

All writes to `config.json` are atomic (temp file + fsync + rename) - safe for concurrent processes.

Sensitive values (API keys, DB passwords) live only in `.env` and are never written to `config.json`.

---

## Embedder services

Two FastAPI containers share the same `embedder_service.py` image but serve different purposes:

| Container | Port | Used by |
|---|---|---|
| `embedder` | 6335 | Embed Worker (classify), `signal_hunter_query` |
| `embedder-vectorizer` | 6336 | Auto Embed (`signal_hunter_embed`, Qdrant upserts) |

Keeping them separate prevents classification and vectorization from competing for the same HTTP service under load - critical when there is a large backlog in `embedding_queue`.

Both containers share the `hf_cache` Docker volume - model downloads once, both instances reuse it.

```
POST /embed        {"texts": [...], "normalize": true}  → {"vectors": [[...]]}
POST /embed-query  {"text": "...", "normalize": true}   → {"vector": [...]}
GET  /health                                            → {"status": "ok", "ready": true}
```

The `Embedder` class in `core/embedder.py` calls the service via HTTP when `service_url` is configured, and falls back to loading the model locally if the service is down.

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
| `llm_task_queue` | LLM task queue: resolve(50), borderline_relevance(70), summarize_batch(90); priority-ordered |
| `llm_usage_log` | Token usage and cost per operation |
| `signal_translations` | Machine-translated titles and summaries per language (signal_id / lang / field / text) |

`signal_translations` schema:

```sql
CREATE TABLE IF NOT EXISTS signal_translations (
    signal_id   UUID    NOT NULL REFERENCES raw_signals(id) ON DELETE CASCADE,
    lang        TEXT    NOT NULL,   -- 'ru', 'de', 'fr', ...
    field       TEXT    NOT NULL,   -- 'title', 'summary'
    text        TEXT    NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_id, lang, field)
);
```

The Web Report API joins `signal_translations` via `LEFT JOIN` when `lang != 'en'`. If no translation is available for a signal, the original English content is returned with `translation_available: false`.

---

## Web Report API

The `web-report` container exposes a REST API alongside the React SPA:

```
GET  /api/stats
     → {total_signals, relevant, embedded, ...}

GET  /api/report?lang=en&q=...&search_mode=semantic|text&date_from=...&date_to=...&sources=...&...
     → {total_signals, categories: [{name, count, rank_score, ...}]}
     Optional q + search_mode restrict results to search hits (no cached ID list).

GET  /api/report/clusters?category=...&lang=en&q=...&search_mode=...&...
     → {clusters: [{id, name, count, signal_ids, ...}]}

GET  /api/report/signals?ids=...&lang=en&sort_by=...&sort_dir=...
     → {signals: [{raw_signal_id, title, summary, url, ...}]}

GET  /api/search/semantic?q=...&lang=en&limit=20
GET  /api/search/text?q=...&lang=en&source=github&limit=20
```

All signal-returning endpoints accept `lang` (`en` or `ru`). When `lang=en`, original content is returned. When `lang=ru`, translated fields from `signal_translations` are used; missing translations fall back to originals with `translation_available: false`.

---

## Rank score formula

```
engagement_raw = score + 0.5 * comments_count
rank_score = (0.3 * log10(1 + engagement_raw) + 0.7 * (intensity/5) * confidence)
             * 0.5^(hours_since_post / 168)
```

Weights: engagement 30%, quality (intensity × confidence) 70%, half-life 7 days.

`score` is the platform's primary engagement metric (SO votes, HN points, Reddit karma, GitHub reactions). `comments_count` gets half the weight of `score` because for sources where reactions are rare (GitHub Issues typically score 0-3), comments are the real engagement signal. The combined formula handles all sources fairly.

In Qdrant queries: `combined_score = rank_score * similarity`.

---

---

## Troubleshooting

### Embedding queue not shrinking

If `embedding_queue` does not shrink: ensure the worker runner container is running (`docker compose ps signal-hunter-workers`) and check its logs (`docker compose logs -f signal-hunter-workers`). The runner calls `embed` every minute; OpenClaw cron is not used for Signal Hunter workers.

### Many borderline_relevance (or other LLM) tasks in `failed`

**Symptom:** Report shows "queue LLM" high and Summarize growing; `llm_task_queue` has many `status=failed` tasks.

**Cause:** Tasks failed after 3 retries (e.g. transient DNS "name resolution", connection reset, or an old code bug). The worker correctly processes only `pending` tasks, so it continues with summarize_batch while borderline tasks sit in `failed`.

**Fix:** Ensure the workers container uses explicit DNS (see `docker-compose.yml`: `dns: 8.8.8.8, 1.1.1.1`) and the code has LLM retry on transient errors (in `core/llm_router.py`). Then run `retry_failed` (chat or `python -m skill retry_failed`) to reset failed tasks to pending. See `docs/causes-failed-borderline-tasks.md` for details.

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
