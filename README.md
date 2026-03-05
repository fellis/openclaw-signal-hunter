# Signal Hunter - OpenClaw Plugin

Market intelligence for AI/ML builders. Monitors GitHub, Hugging Face, Hacker News, Stack Overflow and Reddit for signals: developer pain points, feature requests, tool comparisons. Manages everything through a chat interface via [OpenClaw](https://github.com/openclaw).

---

## What it does

You type a keyword ("RAG", "ollama", "LangChain") in chat. Signal Hunter:

1. **Discovers** where the topic is discussed (repos, subreddits, HF models, SO tags) via real API calls - no LLM guessing
2. **Proposes a collection plan** - which repos/subreddits/models to monitor, enriched with LLM-suggested relevant subreddits
3. **Collects incrementally** (GitHub issues, HF discussions, HN threads, SO questions, Reddit posts) using cursors
4. **Classifies** every signal with a local LLM using your extraction rules (pain points, feature requests, comparisons, adoption...)
5. **Embeds** relevant signals into Qdrant with `bge-m3`
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
| **Local LLM** | Classification, rule suggestions (OpenAI-compatible endpoint) |
| **Claude (Anthropic)** | Queries, resolution strategy (configurable) |
| **Docker Compose** | PostgreSQL + Qdrant services |

---

## Architecture

```
OpenClaw chat
     │
     ▼
src/index.ts          ← OpenClaw plugin entry (register tools + /sh command)
src/tools.ts          ← 22 tool definitions (thin TS wrappers)
src/runner.ts         ← spawns: python -m skill <command> [args]
     │
     ▼  JSON via stdout
skill/main.py         ← CLI dispatcher (22 commands)
     │
     ├── core/resolver.py      ← keyword discovery + LLM enrichment
     ├── core/orchestrator.py  ← collect → process → embed pipeline
     ├── core/processor.py     ← LLM classification (token-aware batching)
     ├── core/embedder.py      ← bge-m3 → Qdrant (Outbox pattern)
     ├── core/llm_router.py    ← routes ops to local/Claude by config
     │
     ├── collectors/
     │   ├── github.py         ← GitHub Issues (repo-scoped, cursor on updated_at)
     │   ├── huggingface.py    ← HF model/space discussions + papers
     │   ├── hackernews.py     ← Algolia HN API (no auth)
     │   ├── stackoverflow.py  ← Stack Exchange API v2.3
     │   └── reddit.py         ← Reddit JSON API / OAuth (60 req/min with token)
     │
     └── storage/
         ├── postgres.py       ← all SQL (raw_signals, processed_signals, cursors...)
         ├── vector.py         ← Qdrant wrapper
         └── config_manager.py ← atomic config.json writes (temp file + rename)
```

**Design principles:**
- Each collector is a self-contained module implementing `BaseCollector`
- Business logic stays in Python; TypeScript only handles IPC
- Discovery-first: LLM enriches only facts confirmed by API calls, never guesses
- Token-aware batching for LLM classification (validated: ~20K tokens per batch)
- Outbox pattern for embedding queue (PostgreSQL → Qdrant, crash-safe)
- Anti-hallucination gate on query answers: URLs not in source data are stripped

---

## Prerequisites

- VPS or local machine with Python 3.11+
- Docker + Docker Compose (for PostgreSQL and Qdrant)
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

### 3. Start infrastructure

```bash
docker compose up -d
```

This starts PostgreSQL 16 (port 5433) and Qdrant (port 6333). The schema is applied automatically on first start.

### 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 5. Register the plugin in OpenClaw

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

Reload OpenClaw. The plugin registers 22 tools and the `/sh` slash command.

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

```
You: how do I set up Hugging Face?
```

```
ClawBot:
Works without a token for public content.
For higher rate limits:
1. Go to https://huggingface.co/settings/tokens
2. Click "New token", Role: read
3. set credentials huggingface {"api_token": "hf_xxx"}
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
   Real examples: "what's the optimal chunk size for technical docs?"
                  "sliding window chunking loses context between chunks"

3. embedding_model_choice (priority 4)
   Comparisons and selection of embedding models
   Real examples: "OpenAI ada vs bge-m3 for multilingual RAG"

4. reranking (priority 3)
   Requests and pain around the reranking step
   Real examples: "cohere reranker doubles cost but worth it?"

5. evaluation_difficulty (priority 3)
   Difficulty evaluating RAG pipeline quality
   Real examples: "how do you even measure RAG quality without ground truth?"

Approve all?
```

```
You: approve, but rename evaluation_difficulty to rag_eval
```

```
ClawBot: ✓ 5 rules saved to config.json
```

---

### Step 6 - Process (LLM classification)

```
You: process
```

```
ClawBot: Classifying 2294 signals with local LLM... (token-aware batching, ~20K tokens/batch)
✓ Done. 1847 classified (relevant: 934, irrelevant: 913)
```

Then embed for semantic search:

```
You: embed
```

```
ClawBot: ✓ 934 vectors indexed into Qdrant (bge-m3, 1024 dims)
```

---

### Step 7 - Query the database

```
You: what are the top pain points with RAG retrieval this month?
```

Signal Hunter embeds the question, searches Qdrant for the most similar signals, re-ranks by `rank_score` (engagement + quality + time decay), then uses Claude to synthesize:

```
ClawBot:
Top RAG retrieval pain points (from 23 signals, last 30 days):

1. **Context dilution with large chunks** (14 signals)
   Developers report that chunks over 512 tokens cause the LLM to lose focus on the relevant part.
   Source: https://github.com/langchain-ai/langchain/issues/12847

2. **Reranking latency in production** (9 signals)
   Cross-encoder rerankers add 300-800ms per query - too slow for real-time applications.
   Source: https://reddit.com/r/LocalLLaMA/comments/...

3. **Multilingual retrieval degradation** (7 signals)
   When source documents are in mixed languages, recall drops by ~40% with English-only embeddings.
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
```

After previewing, approve the format:

```
You: this looks good, save this as the report template for ollama
```

---

### Step 9 - Full cycle (scheduled)

For daily/weekly automation, run the full pipeline in one command:

```
You: /sh update
```

Or via CLI directly:

```bash
cd /path/to/signal-hunter
python -m skill full_cycle
```

---

## All available commands

| Command | Description |
|---|---|
| `status` | System stats: signal counts, embed queue, monthly LLM cost |
| `check_sources` | API readiness and rate limits for all sources |
| `get_setup_guide <source>` | Step-by-step credential instructions |
| `set_credentials <json>` | Save API credentials to config |
| `resolve <keyword>` | Discover + propose collection plan |
| `approve_plan <json>` | Save approved collection plan |
| `update_plan <json>` | Add or remove targets from a plan |
| `refresh_profile <keyword>` | Re-run discovery, update cached profile |
| `list_keywords` | List all tracked keywords |
| `collect` | Collect from all approved plans (incremental) |
| `process` | LLM classification of unprocessed signals |
| `embed` | Vectorize pending signals into Qdrant |
| `full_cycle` | collect + process + embed in sequence |
| `reprocess <json>` | Delete and reclassify signals for a keyword |
| `suggest_rules <keyword>` | Analyze real posts, suggest extraction rules |
| `approve_rules <json>` | Save approved rules to config |
| `query <prompt>` | Semantic search + LLM synthesis |
| `generate_change_report <keyword>` | Delta report since last snapshot |
| `preview_change_report <json>` | Sample report using custom format instructions |
| `approve_report_template <json>` | Save approved report format |
| `list_providers` | Show LLM providers and routing |
| `set_routing <json>` | Change LLM provider for an operation |

All commands are also available as OpenClaw tools (prefix `signal_hunter_`) and via the `/sh` slash command for quick access.

---

## Configuration

`config.json` stores all settings. Key sections:

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
    "max_tokens_per_batch": 20000
  },
  "llm_routing": {
    "process":          "local",
    "suggest_rules":    "local",
    "resolve_enrich":   "local",
    "resolve_strategy": "claude",
    "query":            "claude"
  },
  "embedder": {
    "model": "BAAI/bge-m3",
    "dimensions": 1024,
    "device": "cpu"
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

All writes to `config.json` are atomic (temp file + fsync + rename) - safe for concurrent processes.

Sensitive values (API keys, DB passwords) live only in `.env` and are never written to `config.json`.

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
| `llm_usage_log` | Token usage and cost per operation |

---

## Rank score formula

```
rank_score = (0.3 * log10(1 + engagement) + 0.7 * (intensity/5) * confidence)
             * 0.5^(hours_since_post / 168)
```

Weights: engagement 30%, quality (intensity × confidence) 70%, half-life 7 days.

---

## License

MIT
