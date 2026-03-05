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
        'Get Signal Hunter system status: tracked keywords, signal counts, ' +
        'embedding queue, monthly LLM cost. ' +
        'Triggers: "signal hunter status", "how many signals", "sh status".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'status');
        if (!result.success) return text(`Status failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const sig = d?.signals as Record<string, unknown> ?? {};
        const costs = d?.llm_cost_month_usd as Record<string, number> ?? {};
        const keywords = (d?.keywords as string[]) ?? [];
        const lines = [
          `**Signal Hunter Status**`,
          `Keywords: ${keywords.join(', ') || 'none'}`,
          `Raw signals: ${sig.total_raw ?? 0}`,
          `Processed: ${sig.processed ?? 0} (relevant: ${sig.relevant ?? 0})`,
          `Unprocessed: ${sig.unprocessed ?? 0} | Embed pending: ${sig.embed_pending ?? 0}`,
          `LLM cost this month: $${(costs.total ?? 0).toFixed(4)}`,
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
    // Collect
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_collect',
      description:
        'Collect new signals from all approved collection plans. ' +
        'Runs incrementally (uses cursor). Takes 1-3 minutes. ' +
        'Triggers: "collect signals", "fetch new data", "update signals".',
      parameters: {
        type: 'object',
        properties: {
          background: {
            type: 'boolean',
            description: 'Run in background without waiting (default true)',
            default: true,
          },
        },
      },
      async execute() {
        const result = await runSkillCommand(cfg, 'collect');
        if (!result.success) return text(`Collect failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`Collection done. New signals: **${d?.total ?? 0}**`);
      },
    },

    // ----------------------------------------------------------------
    // Process
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_process',
      description:
        'Run LLM classification on all unprocessed raw signals. ' +
        'Uses local LLM with token-aware batching. Takes several minutes for large batches. ' +
        'Triggers: "process signals", "classify signals", "run LLM on new data".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'process');
        if (!result.success) return text(`Process failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`Processing done. Signals classified: **${d?.total ?? 0}**`);
      },
    },

    // ----------------------------------------------------------------
    // Embed
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_embed',
      description:
        'Vectorize pending relevant signals with bge-m3 and index into Qdrant. ' +
        'Required after process to enable semantic search. ' +
        'Triggers: "embed signals", "update vector index", "index signals".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'embed');
        if (!result.success) return text(`Embed failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`Embedding done. Vectors upserted: **${d?.total ?? 0}**`);
      },
    },

    // ----------------------------------------------------------------
    // Full cycle
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_full_cycle',
      description:
        'Run full pipeline: collect → process → embed. ' +
        'Use for scheduled daily/weekly updates. Takes 5-15 minutes. ' +
        'Triggers: "run full update", "daily update", "sync signal hunter".',
      parameters: { type: 'object', properties: {} },
      async execute() {
        const result = await runSkillCommand(cfg, 'full_cycle');
        if (!result.success) return text(`Full cycle failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const lines = [
          `**Full cycle complete:**`,
          `Collected: ${d?.collected ?? 0} new signals`,
          `Processed: ${d?.processed ?? 0} signals`,
          `Embedded: ${d?.embedded ?? 0} vectors`,
        ];
        return text(lines.join('\n'));
      },
    },

    // ----------------------------------------------------------------
    // Suggest rules
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_suggest_rules',
      description:
        'Ask LLM to suggest extraction/classification rules for a keyword. ' +
        'Show rules to user for review before approving. ' +
        'Triggers: "suggest rules for cursor.ai", "create classification rules", "what rules to use".',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: 'Topic/product to generate rules for' },
        },
        required: ['keyword'],
      },
      async execute(_id, params) {
        const p = params as { keyword: string };
        const result = await runSkillCommand(cfg, 'suggest_rules', p.keyword);
        if (!result.success) return text(`Suggest rules failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        const rules = d?.suggested_rules;
        if (Array.isArray(rules)) {
          const lines = [
            `**Suggested rules for "${p.keyword}":**`,
            ``,
            ...rules.map((r: Record<string, unknown>, i: number) =>
              `${i + 1}. **${r.name}** (priority ${r.priority ?? 1})\n   ${r.description}`
            ),
            ``,
            `To save these rules, call \`signal_hunter_approve_rules\` (no parameters needed).`,
          ];
          return text(lines.join('\n'));
        }
        return text(String(rules));
      },
    },

    // ----------------------------------------------------------------
    // Approve rules
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_approve_rules',
      description:
        'Save the extraction rules that were suggested by signal_hunter_suggest_rules. ' +
        'Rules are automatically saved in pending state by suggest_rules - no parameters needed. ' +
        'Just call this after the user confirms they want to save the suggested rules. ' +
        'Triggers: "approve rules", "save these rules", "confirm rules", "да сохрани правила".',
      parameters: {
        type: 'object',
        properties: {},
      },
      async execute() {
        const result = await runSkillCommand(cfg, 'approve_rules');
        if (!result.success) return text(`Approve rules failed: ${result.error}`);
        const d = result.data as Record<string, unknown>;
        return text(`Rules saved: **${d?.rules_saved ?? 0}** rules in config.json`);
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
        'Triggers: "use claude for classification", "use local for query", "route process to claude".',
      parameters: {
        type: 'object',
        properties: {
          operation: {
            type: 'string',
            description: 'Operation: process | suggest_rules | resolve_enrich | resolve_strategy | query',
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
    // Set process schedule
    // ----------------------------------------------------------------
    {
      name: 'signal_hunter_set_process_schedule',
      description:
        'Configure auto-processing schedule: how many batches per cron run and how many signals per batch. ' +
        'Default: 3 batches per run, 10 signals per batch, cron every 5 min. ' +
        'Returns cron_job_id to then update the cron interval via cron.update. ' +
        'Triggers: "обработай 3 батча каждые 5 минут", "поставь 10 сигналов в батче", ' +
        '"set batches to 5", "change auto-processing schedule", ' +
        '"сколько сигналов в батче", "how many signals per batch". ' +
        'WORKFLOW: 1) call this tool with batches_per_run and/or signals_per_batch, ' +
        '2) then call cron.update with the returned cron_job_id to change the cron interval. ' +
        'NOTE: signals_per_batch default 10 is safe for the current LLM server (nginx timeout 60s). ' +
        'Increasing above 15 may cause timeouts.',
      parameters: {
        type: 'object',
        properties: {
          batches_per_run: {
            type: 'number',
            description:
              'Number of LLM batches to run per cron execution (default 3). ' +
              'null = process all unprocessed signals per run.',
          },
          signals_per_batch: {
            type: 'number',
            description:
              'Max signals per LLM batch (default 10). ' +
              'Controls LLM response time - keep at 10 unless nginx timeout is increased. ' +
              'At ~37 tok/s: 10 signals * 200 output tokens = ~54s (safe under 60s limit).',
          },
        },
        required: [],
      },
      async execute(_id, params) {
        const p = params as { batches_per_run?: number | null; signals_per_batch?: number };
        const json = JSON.stringify({
          batches_per_run: p.batches_per_run ?? 3,
          signals_per_batch: p.signals_per_batch ?? 10,
        });
        const result = await runSkillCommand(cfg, 'set_process_schedule', json);
        return text(formatResult(result));
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
