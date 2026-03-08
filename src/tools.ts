/**
 * Signal Hunter tool definitions.
 * Each tool is a thin wrapper: it calls Python via runner and returns text to the LLM.
 */

import { RunnerConfig, formatResult, runSkillCommand } from './runner';

export interface Tool {
  name: string;
  description: string;
  parameters: object;
  execute: (id: string, params: unknown) => Promise<{ content: Array<{ type: 'text'; text: string }> }>;
}

function text(t: string): { content: Array<{ type: 'text'; text: string }> } {
  return { content: [{ type: 'text', text: t }] };
}

export function createTools(cfg: RunnerConfig): Tool[] {
  return [
    // ----------------------------------------------------------------
    // Query / search
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_query',
      description:
        'Search market intelligence signals with a natural language query. ' +
        'Uses semantic search (bge-m3 + Qdrant) + Claude synthesis. ' +
        'Triggers: "what are users complaining about X", "show me signals about Y", ' +
        '"what is trending in Z", "find adoption signals for W".',
      parameters: {
        type: 'object',
        properties: {
          prompt: {
            type: 'string',
            description: 'Natural language query about market signals (any language)',
          },
        },
        required: ['prompt'],
      },
      async execute(_id, params) {
        const p = params as { prompt: string };
        const result = await runSkillCommand(cfg, 'query', p.prompt);
        if (!result.success) return text(`Query failed: ${result.error}`);
        const data = result.data as Record<string, unknown>;
        return text(String(data?.text ?? formatResult(result)));
      },
    },

    // ----------------------------------------------------------------
    // Status
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_status',
      description:
        'Get Signal Hunter full status: tracked keywords, signal counts, embedding queue, ' +
        'monthly LLM cost, AND current processor/filter configuration. ' +
        'Use this to show processing settings (signals_per_batch, batches_per_run, etc.). ' +
        'Triggers: "signal hunter status", "how many signals", "sh status", ' +
        '"покажи настройки", "покажи конфиг", "настройки обработки", "show config".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'status');
        if (!result.success) return text(`Status failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const sig = (d?.signals as Record<string, unknown>) ?? {};
        const costs = (d?.llm_cost_month_usd as Record<string, number>) ?? {};
        const keywords = (d?.keywords as string[]) ?? [];
        const proc = (d?.processor_config as Record<string, unknown>) ?? {};
        const filters = (d?.filters_config as Record<string, unknown>) ?? {};
        const lines = [
          `**Signal Hunter Status**`,
          `Keywords: ${keywords.join(', ') || 'none'}`,
          `Raw signals: ${sig.total_raw ?? 0}`,
          `Processed: ${sig.processed ?? 0} (relevant: ${sig.relevant ?? 0})`,
          `Unprocessed: ${sig.unprocessed ?? 0} | Embed pending: ${sig.embed_pending ?? 0}`,
          `LLM cost this month: $${(costs.total ?? 0).toFixed(4)}`,
          ``,
          `**Processor config:**`,
          `  signals_per_batch: ${proc.signals_per_batch ?? 10}`,
          `  batches_per_run: ${proc.batches_per_run ?? 3}`,
          `  max_tokens_per_batch: ${proc.max_tokens_per_batch ?? 10000}`,
          `  max_body_chars: ${proc.max_body_chars ?? 1000}`,
          ``,
          `**Filters:**`,
          `  min_score: ${filters.min_score ?? 0} | max_age_days: ${filters.max_age_days ?? 90}`,
        ];
        return text(lines.join('\n'));
      },
    },

    // ----------------------------------------------------------------
    // Resolve keyword
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_resolve',
      description:
        'Discover and profile a new keyword: find real repos/threads/subreddits via APIs, ' +
        'enrich with LLM, propose a collection plan. ' +
        'Always run this before starting to track a new technology or product. ' +
        'Triggers: "track cursor.ai", "add vllm to monitoring", "resolve langchain".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Technology, product, or topic to track' },
        },
        required: ['keyword'],
      },
      async execute(_id, params) {
        const p = params as { keyword: string };
        const result = await runSkillCommand(cfg, 'resolve', p.keyword);
        if (!result.success) return text(`Resolve failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const proposals = d?.proposed_plan as Record<string, unknown[]> ?? {};
        const lines = [
          `**Keyword Resolved: ${d?.canonical_name ?? p.keyword}**`,
          `Type: ${d?.keyword_type ?? '-'} | ${d?.description ?? ''}`,
          `Aliases: ${(d?.aliases as string[] ?? []).join(', ') || 'none'}`,
          ``,
          `**Proposed collection plan:**`,
          ...Object.entries(proposals).map(([src, targets]) =>
            `• ${src}: ${(targets as unknown[]).length} targets`
          ),
          ``,
          `To approve: \`/sh approve_plan ${d?.canonical_name ?? p.keyword}\``,
          ``,
          `Full plan: \`\`\`json\n${JSON.stringify(proposals, null, 2)}\n\`\`\``,
        ];
        return text(lines.join('\n'));
      },
    },

    // ----------------------------------------------------------------
    // Approve plan
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_approve_plan',
      description:
        'Save the collection plan that was proposed by signal_hunter_resolve. ' +
        'The plan is saved automatically in pending state by resolve - just provide canonical_name. ' +
        'Triggers: "approve plan for cursor.ai", "save collection plan", "confirm targets", "да approve".',
      parameters: {
        type: 'object',
        properties: {
          canonical_name: {
            type: 'string',
            description: 'Keyword canonical name (from resolve output, lowercase)',
          },
        },
        required: ['canonical_name'],
      },
      async execute(_id, params) {
        const p = params as { canonical_name: string };
        const json = JSON.stringify({ canonical_name: p.canonical_name });
        const result = await runSkillCommand(cfg, 'approve_plan', json);
        return text(formatResult(result));
      },
    },

    // ----------------------------------------------------------------
    // Embed
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_embed',
      description:
        'Vectorize pending relevant signals with bge-m3 and index into Qdrant. ' +
        'Run by the worker runner every minute (or on demand). Call manually for immediate index update. ' +
        'Triggers: "embed now", "update vector index now", "index signals immediately".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'embed');
        if (!result.success) return text(`Embed failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`Embedding done. Vectors upserted: **${d?.total ?? 0}**`);
      },
    },

    // ----------------------------------------------------------------
    // Check sources
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_check_sources',
      description:
        'Check API credentials and rate limits for all data sources. ' +
        'Triggers: "check sources", "source status", "api limits", "is github configured".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'check_sources');
        if (!result.success) return text(`Check sources failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const sources = (d?.sources as Record<string, unknown>[]) ?? [];
        const lines = sources.map((s) => {
          const ready = s.ready ? '✓' : '✗';
          const info = s.limit_info ? ` (${s.limit_info})` : '';
          const note = s.note ? ` - ${s.note}` : '';
          return `${ready} **${s.source}**${info}${note}`;
        });
        return text(`**Source status:**\n${lines.join('\n') || 'No sources configured'}`);
      },
    },

    // ----------------------------------------------------------------
    // List keywords
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_list_keywords',
      description:
        'List all tracked keywords in Signal Hunter. ' +
        'Triggers: "list keywords", "what am I tracking", "show keywords".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'list_keywords');
        if (!result.success) return text(`List keywords failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const keywords = (d?.keywords as string[]) ?? [];
        if (!keywords.length) return text('No keywords tracked yet. Use `signal_hunter_resolve` to add one.');
        return text(`**Tracked keywords (${d?.total ?? 0}):**\n${keywords.map((k) => `• ${k}`).join('\n')}`);
      },
    },

    // ----------------------------------------------------------------
    // Reprocess
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_reprocess',
      description:
        'Delete and reclassify signals for a keyword using current extraction rules. ' +
        'Use when rules changed. Optionally filter by specific rule names. ' +
        'Triggers: "reprocess RAG", "reclassify signals for ollama", "apply new rules to RAG".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Keyword to reprocess' },
          rules: {
            type: 'array',
            items: { type: 'string' },
            description: 'Specific rule names to reprocess (omit for all)',
          },
        },
        required: ['keyword'],
      },
      async execute(_id, params) {
        const p = params as { keyword: string; rules?: string[] };
        const json = JSON.stringify({ keyword: p.keyword, rules: p.rules ?? null });
        const result = await runSkillCommand(cfg, 'reprocess', json);
        if (!result.success) return text(`Reprocess failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`Reprocess done. Deleted: **${d?.deleted ?? 0}**, reclassified: **${d?.reprocessed ?? 0}**`);
      },
    },

    // ----------------------------------------------------------------
    // Update plan
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_update_plan',
      description:
        'Add or remove targets in an existing collection plan for a keyword. ' +
        'Triggers: "add langchain-ai/langchain to RAG plan", "remove reddit from ollama", ' +
        '"add repo X to monitoring for Y".',
      parameters: {
        type: 'object',
        properties: {
          canonical_name: { type: 'string', description: 'Keyword canonical name' },
          collector: { type: 'string', description: 'Collector name (github, reddit, hackernews, stackoverflow)', default: 'github' },
          add: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                query: { type: 'string' },
                scope: { type: 'string' },
                params: { type: 'object' },
              },
              required: ['query', 'scope'],
            },
            description: 'Targets to add: [{query, scope, params}]',
          },
          remove: {
            type: 'array',
            items: { type: 'string' },
            description: 'Query strings to remove from plan',
          },
        },
        required: ['canonical_name'],
      },
      async execute(_id, params) {
        const p = params as { canonical_name: string; collector?: string; add?: unknown[]; remove?: string[] };
        const json = JSON.stringify({
          canonical_name: p.canonical_name,
          collector: p.collector ?? 'github',
          add: p.add ?? [],
          remove: p.remove ?? [],
        });
        const result = await runSkillCommand(cfg, 'update_plan', json);
        return text(formatResult(result));
      },
    },

    // ----------------------------------------------------------------
    // Set source credentials
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_set_credentials',
      description:
        'Save API credentials for a data source and verify readiness. ' +
        'Triggers: "github token=xxx", "set reddit credentials", "configure stackoverflow key".',
      parameters: {
        type: 'object',
        properties: {
          source: { type: 'string', description: 'Source name: github, reddit, stackoverflow, producthunt, huggingface' },
          credentials: { type: 'object', description: 'Credentials dict, e.g. {"api_token": "ghp_xxx"}' },
        },
        required: ['source', 'credentials'],
      },
      async execute(_id, params) {
        const p = params as { source: string; credentials: object };
        const json = JSON.stringify({ source: p.source, credentials: p.credentials });
        const result = await runSkillCommand(cfg, 'set_credentials', json);
        if (!result.success) return text(`Set credentials failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const ready = d?.ready ? '✓' : '✗';
        return text(`${ready} **${p.source}**: ${d?.ready ? 'ready' : 'not ready'} - ${d?.limit_info ?? d?.note ?? ''}`);
      },
    },

    // ----------------------------------------------------------------
    // Get setup guide
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_get_setup_guide',
      description:
        'Get step-by-step instructions for obtaining credentials for a data source. ' +
        'Triggers: "how to configure reddit", "how to get github token", "setup stackoverflow".',
      parameters: {
        type: 'object',
        properties: {
          source: { type: 'string', description: 'Source name: github, reddit, hackernews, stackoverflow' },
        },
        required: ['source'],
      },
      async execute(_id, params) {
        const p = params as { source: string };
        const result = await runSkillCommand(cfg, 'get_setup_guide', p.source);
        if (!result.success) return text(`Guide failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const steps = (d?.steps as string[]) ?? [];
        return text(`**Setup guide for ${p.source}:**\n\n${steps.join('\n')}`);
      },
    },

    // ----------------------------------------------------------------
    // Refresh profile
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_refresh_profile',
      description:
        'Re-run discovery and update cached KeywordProfile for a keyword. ' +
        'Use when repositories or communities have grown since last resolve. ' +
        'Triggers: "refresh profile for RAG", "update keyword profile", "re-discover ollama".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Keyword to refresh' },
        },
        required: ['keyword'],
      },
      async execute(_id, params) {
        const p = params as { keyword: string };
        const result = await runSkillCommand(cfg, 'refresh_profile', p.keyword);
        return text(formatResult(result));
      },
    },

    // ----------------------------------------------------------------
    // List providers
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_list_providers',
      description:
        'Show LLM providers and current routing configuration. ' +
        'Triggers: "show providers", "list LLM providers", "what model for classification".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'list_providers');
        if (!result.success) return text(`List providers failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const providers = (d?.providers as Record<string, unknown>[]) ?? [];
        const lines = [
          '**LLM Providers:**',
          '',
          ...providers.map((p) =>
            `• **${p.name}** (${p.type}): model=${p.model} | operations: ${(p.operations as string[]).join(', ') || 'none'}`
          ),
        ];
        return text(lines.join('\n'));
      },
    },

    // ----------------------------------------------------------------
    // Set routing
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_set_routing',
      description:
        'Change LLM provider for a specific operation. ' +
        'Triggers: "use claude for query", "use local for resolve_enrich", "route borderline_relevance to local".',
      parameters: {
        type: 'object',
        properties: {
          operation: {
            type: 'string',
            description: 'Operation: borderline_relevance | summarize_batch | resolve_enrich | query',
          },
          provider: { type: 'string', description: 'Provider: local | claude' },
        },
        required: ['operation', 'provider'],
      },
      async execute(_id, params) {
        const p = params as { operation: string; provider: string };
        const json = JSON.stringify({ operation: p.operation, provider: p.provider });
        const result = await runSkillCommand(cfg, 'set_routing', json);
        return text(formatResult(result));
      },
    },

    // ----------------------------------------------------------------
    // LLM Worker - queue resolve
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_queue_resolve',
      description:
        'Add a list of keywords to the LLM task queue for background resolve and auto-approve. ' +
        'The worker runner runs the worker every minute; one keyword per run. ' +
        'Keywords that already have a profile are skipped automatically. ' +
        'Use this instead of signal_hunter_resolve when adding many keywords at once. ' +
        'Triggers: "добавь ключевики в очередь", "поставь в очередь resolve", ' +
        '"queue resolve for LangGraph and CrewAI", "bulk add keywords", ' +
        '"добавь список ключевиков".',
      parameters: {
        type: 'object',
        properties: {
          keywords: {
            type: 'array',
            items: { type: 'string' },
            description: 'List of keywords to resolve and auto-approve',
          },
        },
        required: ['keywords'],
      },
      async execute(_id, params) {
        const p = params as { keywords: string[] };
        const json = JSON.stringify({ keywords: p.keywords });
        const result = await runSkillCommand(cfg, 'queue_resolve', json);
        if (!result.success) return text(`Queue resolve failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(
          `**Queue resolve:** ${d?.queued ?? 0} keywords added to queue` +
          (d?.skipped_existing ? `, ${d.skipped_existing} already resolved (skipped)` : '') +
          `.\n\n${d?.note ?? ''}`
        );
      },
    },

            // ----------------------------------------------------------------
    // LLM Worker - run worker (called by worker runner or manually)
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_run_worker',
      description:
        'Process the next pending LLM task (resolve or summarize_batch). ' +
        'Called by worker runner every minute or manually. ' +
        'Picks the highest-priority pending task, executes it, and reports the result. ' +
        'If queue is empty or another task is already running, exits immediately. ' +
        'User triggers: "run worker", "запусти воркер", "обработай следующую задачу из очереди".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'run_worker');
        if (!result.success) return text(`Worker failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        if (d?.status === 'idle') return text(`Worker: queue is empty or task already running.`);
        return text(
          `**Worker done:** ${d?.task_type ?? '?'} - ${d?.keyword ?? d?.status ?? 'ok'}`
        );
      },
    },

    // ----------------------------------------------------------------
    // Embed Worker - classify signals via embeddings (called by worker runner or manually)
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_run_embed_worker',
      description:
        'Embed worker: classifies raw signals using vector similarity (no LLM, no GPU). ' +
        'Fetches unprocessed signals, embeds them via local bge-m3 service, ' +
        'classifies by cosine similarity against rule vectors, saves with summary=null. ' +
        'Summaries are generated separately by the LLM worker (summarize_batch). ' +
        'Called by worker runner every minute or manually. ' +
        'User triggers: "run embed worker", "запусти embed воркер", "классифицируй сигналы".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'run_embed_worker');
        if (!result.success) return text(`Embed worker failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        if (d?.status === 'idle') return text(`Embed worker: no unprocessed signals.`);
        return text(
          `**Embed worker done:** classified=${d?.classified ?? 0}, remaining=${d?.remaining ?? 0}`
        );
      },
    },

    // ----------------------------------------------------------------
    // Collect Worker - run collect worker (called by worker runner or manually)
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_run_collect_worker',
      description:
        'Collect worker: picks the single stalest keyword (not collected in last 24h) ' +
        'and fetches new signals for it from GitHub, Reddit, HN, SO, HuggingFace. ' +
        'No LLM used - pure API calls. Runs independently from the LLM worker. ' +
        'Called by worker runner every 5 min or manually. ' +
        'User triggers: "run collect worker", "запусти collect воркер", "собери сигналы".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'run_collect_worker');
        if (!result.success) return text(`Collect worker failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        if (d?.status === 'idle') return text(`Collect worker: all keywords up to date.`);
        return text(
          `**Collect done:** ${d?.keyword ?? '?'} - ${d?.total ?? 0} new signals`
        );
      },
    },

    // ----------------------------------------------------------------
    // LLM Worker - retry failed tasks
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_retry_failed',
      description:
        'Reset all failed LLM queue tasks back to pending so the worker retries them. ' +
        'Useful when the LLM was temporarily unavailable and some resolve tasks failed. ' +
        'Triggers: "перезапусти failed задачи", "retry failed", "повтори неудачные задачи", ' +
        '"сбрось ошибки в очереди", "reset failed queue tasks".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'retry_failed');
        if (!result.success) return text(`Retry failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`**Retry failed tasks:** ${d?.reset ?? 0} task(s) reset to pending.`);
      },
    },

    // ----------------------------------------------------------------
    // LLM Worker - queue status
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_queue_status',
      description:
        'Show the current LLM task queue: pending, running, and failed tasks. ' +
        'Triggers: "что в очереди", "сколько задач осталось", "покажи очередь", ' +
        '"queue status", "how many keywords left to resolve".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'queue_status');
        if (!result.success) return text(`Queue status failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const tasks = d?.tasks as Record<string, Array<{ task_type: string; payload: Record<string, unknown>; error?: string }>> ?? {};
        const lines: string[] = [
          `**LLM Task Queue:**`,
          `Pending: **${d?.pending ?? 0}** | Running: **${d?.running ?? 0}** | Failed: **${d?.failed ?? 0}**`,
        ];
        if ((d?.pending as number) > 0) {
          const pending = tasks['pending'] ?? [];
          lines.push(`\n**Pending:**`);
          pending.slice(0, 10).forEach(t => {
            const label = t.task_type === 'resolve' ? t.payload?.keyword : t.task_type;
            lines.push(`- ${label}`);
          });
          if (pending.length > 10) lines.push(`- ...and ${pending.length - 10} more`);
        }
        if ((d?.failed as number) > 0) {
          const failed = tasks['failed'] ?? [];
          lines.push(`\n**Failed:**`);
          failed.forEach(t => {
            const label = t.task_type === 'resolve' ? t.payload?.keyword : t.task_type;
            lines.push(`- ${label}: ${t.error ?? 'unknown error'}`);
          });
        }
        return text(lines.join('\n'));
      },
    },

    // ----------------------------------------------------------------
    // Delete keywords
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_delete_keywords',
      description:
        'Delete one or more keywords from the system (removes profile, collection plans, report snapshots). ' +
        'IMPORTANT - ALWAYS follow this confirmation workflow before setting confirmed=true: ' +
        '1) Call with confirmed=false to get a preview of what will be deleted. ' +
        '2) Show the user the full list of keywords that WILL be deleted. ' +
        '3) Ask the user explicitly: "Подтверди удаление X ключевиков: [список]. Это действие необратимо." ' +
        '4) Only after the user explicitly confirms - call again with confirmed=true. ' +
        'Triggers: "удали ключевик", "удали все кроме X", "remove keyword", ' +
        '"delete keywords", "убери из отслеживания", "удали из системы".',
      parameters: {
        type: 'object',
        properties: {
          keywords: {
            type: 'array',
            items: { type: 'string' },
            description: 'List of canonical keyword names to delete',
          },
          confirmed: {
            type: 'boolean',
            description: 'Must be true to actually delete. Use false first to get a preview.',
          },
        },
        required: ['keywords', 'confirmed'],
      },
      async execute(_id, params) {
        const p = params as { keywords: string[]; confirmed: boolean };
        const json = JSON.stringify({ keywords: p.keywords, confirmed: p.confirmed });
        const result = await runSkillCommand(cfg, 'delete_keywords', json);
        if (!result.success) return text(`Delete keywords failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        if (d?.status === 'preview') {
          const willDelete = (d.will_delete as string[]) ?? [];
          const notFound = (d.not_found as string[]) ?? [];
          const lines = [
            `**Preview - nothing deleted yet.**`,
            `Will delete **${d.count}** keyword(s):`,
            ...willDelete.map(k => `- ${k}`),
          ];
          if (notFound.length) {
            lines.push(`\nNot found (skipped): ${notFound.join(', ')}`);
          }
          lines.push(`\nЭто действие необратимо. Подтверди удаление?`);
          return text(lines.join('\n'));
        }
        const keywords = (d?.keywords as string[]) ?? [];
        const notFound = (d?.not_found as string[]) ?? [];
        const lines = [`**Deleted ${d?.deleted} keyword(s):**`, ...keywords.map(k => `- ${k}`)];
        if (notFound.length) lines.push(`\nNot found: ${notFound.join(', ')}`);
        return text(lines.join('\n'));
      },
    },

    // ----------------------------------------------------------------
    // Set embed schedule
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_set_embed_schedule',
      description:
        'Configure how many signals to vectorize per embed run (used by worker runner and manual embed). ' +
        'Triggers: "set embed schedule", "сколько эмбедить за раз", "настрой расписание эмбеддинга".',
      parameters: {
        type: 'object',
        properties: {
          max_items_per_run: {
            type: 'number',
            description:
              'Max signals to embed per run (default 128). 128 items = ~10-15s with bge-m3. Set to 512 to drain large queues faster.',
          },
        },
        required: [],
      },
      async execute(_id, params) {
        const p = params as { max_items_per_run?: number };
        const json = JSON.stringify({ max_items_per_run: p.max_items_per_run ?? 128 });
        const result = await runSkillCommand(cfg, 'set_embed_schedule', json);
        if (!result.success) return text(`Set embed schedule failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(String(d?.message ?? `Config saved: max_items_per_run=${d?.max_items_per_run ?? 128}`));
      },
    },

    // ----------------------------------------------------------------
    // Generate change report
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_generate_change_report',
      description:
        'Generate a delta report since last report snapshot. ' +
        'Shows new signals, what grew, volume changes by rule. ' +
        'Triggers: "generate report for RAG", "what changed for ollama", "weekly report".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Keyword to generate report for' },
        },
        required: ['keyword'],
      },
      async execute(_id, params) {
        const p = params as { keyword: string };
        const result = await runSkillCommand(cfg, 'generate_change_report', p.keyword);
        if (!result.success) return text(`Report failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(String(d?.text ?? 'No report generated'));
      },
    },

    // ----------------------------------------------------------------
    // Preview change report
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_preview_change_report',
      description:
        'Generate a sample change report based on user instructions for approval. ' +
        'Use real recent data. After user approves, call signal_hunter_approve_report_template. ' +
        'Triggers: "show me how the report would look", "preview report format", "draft report for RAG".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Keyword' },
          instructions: { type: 'string', description: 'Format instructions in free text' },
        },
        required: ['keyword', 'instructions'],
      },
      async execute(_id, params) {
        const p = params as { keyword: string; instructions: string };
        const json = JSON.stringify({ keyword: p.keyword, instructions: p.instructions });
        const result = await runSkillCommand(cfg, 'preview_change_report', json);
        if (!result.success) return text(`Preview failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(
          `**Preview report for "${p.keyword}":**\n\n${String(d?.text ?? '')}\n\n` +
          `Approve this format? Call \`signal_hunter_approve_report_template\` with the template text.`
        );
      },
    },

    // ----------------------------------------------------------------
    // Embedder service management
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_embedder_service',
      description:
        'Manage the embedder Docker container (bge-m3 always-warm service). ' +
        'Actions: status - check if running and healthy; start - start container; ' +
        'stop - stop container; restart - restart container; logs - view recent logs; ' +
        'build - rebuild Docker image after code changes. ' +
        'Triggers: "embedder status", "start embedder", "stop embedder", ' +
        '"restart embedder", "embedder logs", "rebuild embedder", ' +
        '"статус эмбеддера", "запусти эмбеддер", "логи эмбеддера".',
      parameters: {
        type: 'object',
        properties: {
          action: {
            type: 'string',
            enum: ['status', 'start', 'stop', 'restart', 'logs', 'build'],
            description: 'Action to perform on the embedder service',
          },
          lines: {
            type: 'number',
            description: 'Number of log lines to return (only for action=logs, default 50)',
          },
        },
        required: ['action'],
      },
      async execute(_id, params) {
        const p = params as { action: string; lines?: number };
        const json = JSON.stringify({ action: p.action, lines: p.lines ?? 50 });
        const result = await runSkillCommand(cfg, 'embedder_service', json);
        if (!result.success) return text(`Embedder service error: ${result.error}`);
        const d = result.data as Record<string, unknown>;

        if (p.action === 'status') {
          const running = d?.running as boolean;
          const health = (d?.health as Record<string, unknown>) ?? {};
          const icon = running ? '✓' : '✗';
          const lines = [
            `${icon} **Embedder service:** ${running ? 'running' : 'down'}`,
            running ? `Model: ${health.model ?? 'unknown'} | Ready: ${health.ready ?? false}` : `Error: ${health.error ?? 'unreachable'}`,
            ``,
            `Docker: ${d?.docker_ps ?? '-'}`,
          ];
          return text(lines.join('\n'));
        }

        if (p.action === 'logs') {
          return text(`**Embedder logs:**\n\`\`\`\n${d?.logs ?? 'no logs'}\n\`\`\``);
        }

        const success = d?.success as boolean;
        const icon = success ? '✓' : '✗';
        return text(`${icon} **Embedder ${p.action}:** ${success ? 'done' : 'failed'}\n${d?.output ?? ''}`);
      },
    },

    // ----------------------------------------------------------------
    // Approve report template
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_approve_report_template',
      description:
        'Save the change report template that was shown by signal_hunter_preview_change_report. ' +
        'The template is saved automatically in pending state - no template text needed as param. ' +
        'Optionally pass instructions to customize. ' +
        'Triggers: "approve this format", "save report template", "confirm report format", "да сохрани шаблон".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Keyword (optional, taken from preview if omitted)' },
          instructions: { type: 'string', description: 'Optional updated instructions for future reports' },
        },
        required: [],
      },
      async execute(_id, params) {
        const p = params as { keyword?: string; instructions?: string };
        const json = JSON.stringify({ keyword: p.keyword ?? '', instructions: p.instructions ?? '' });
        const result = await runSkillCommand(cfg, 'approve_report_template', json);
        return text(formatResult(result));
      },
    },
  ];
}
