/**
 * Signal Hunter plugin entry point for OpenClaw.
 * Registers tools and slash commands.
 * All business logic is in Python (skill/main.py).
 */

import * as path from 'path';
import { RunnerConfig, runSkillCommand } from './runner';
import { createTools } from './tools';

export interface PluginConfig {
  pythonBin?: string;
  skillDir?: string | null;
}

export interface PluginApi {
  registerTool: (tool: {
    name: string;
    description: string;
    parameters: object;
    execute: (id: string, params: unknown) => Promise<{ content: Array<{ type: string; text: string }> }>;
  }, opts?: { optional?: boolean }) => void;
  registerCommand?: (opts: {
    name: string;
    description: string;
    acceptsArgs?: boolean;
    requireAuth?: boolean;
    handler: (ctx: {
      senderId?: string;
      channel?: string;
      isAuthorizedSender?: boolean;
      args?: string;
      commandBody?: string;
      config?: unknown;
    }) => Promise<{ text: string }> | { text: string };
  }) => void;
  config?: { plugins?: { entries?: Record<string, { config?: PluginConfig }> } };
  workspacePath?: string;
}

function getConfig(api: PluginApi, configParam?: PluginConfig): PluginConfig {
  if (configParam && Object.keys(configParam).length > 0) return configParam;
  const cfg = api.config?.plugins?.entries?.['signal-hunter']?.config;
  return (cfg as PluginConfig) ?? {};
}

function resolveSkillDir(config: PluginConfig): string {
  if (config.skillDir) return config.skillDir;
  // When installed as a plugin, __dirname is signal-hunter/src/
  return path.resolve(__dirname, '..');
}

export default function register(api: PluginApi, configParam?: PluginConfig) {
  const config = getConfig(api, configParam);
  const skillDir = resolveSkillDir(config);
  const pythonBin = config.pythonBin ?? 'python3';

  const runnerConfig: RunnerConfig = { pythonBin, skillDir };
  const tools = createTools(runnerConfig);

  for (const tool of tools) {
    api.registerTool(
      {
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
        async execute(id, params) {
          return tool.execute(id, params);
        },
      },
    );
  }

  // Slash command: /sh <subcommand> [args]
  if (api.registerCommand) {
    api.registerCommand({
      name: 'sh',
      description: 'Signal Hunter: status | query <text> | collect | process | embed | update',
      acceptsArgs: true,
      requireAuth: true,
      handler: async (ctx) => {
        const body = (ctx.args ?? ctx.commandBody ?? '').trim();
        const [sub, ...rest] = body.split(/\s+/);
        const arg = rest.join(' ');

        switch (sub) {
          case 'status':
          case '': {
            const result = await runSkillCommand(runnerConfig, 'status');
            if (!result.success) return { text: `Error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            const sig = (d?.signals as Record<string, unknown>) ?? {};
            const costs = (d?.llm_cost_month_usd as Record<string, number>) ?? {};
            const keywords = (d?.keywords as string[]) ?? [];
            return {
              text: [
                `**Signal Hunter**`,
                `Keywords: ${keywords.join(', ') || 'none'}`,
                `Raw: ${sig.total_raw ?? 0} | Processed: ${sig.processed ?? 0} (relevant: ${sig.relevant ?? 0})`,
                `Embed pending: ${sig.embed_pending ?? 0}`,
                `LLM this month: $${(costs.total ?? 0).toFixed(4)}`,
              ].join('\n'),
            };
          }

          case 'query': {
            if (!arg) return { text: 'Usage: /sh query <your question>' };
            const result = await runSkillCommand(runnerConfig, 'query', arg);
            if (!result.success) return { text: `Query error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            return { text: String(d?.text ?? 'No results') };
          }

          case 'collect': {
            const result = await runSkillCommand(runnerConfig, 'collect');
            if (!result.success) return { text: `Collect error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            return { text: `Collected: ${d?.total ?? 0} new signals` };
          }

          case 'process': {
            const result = await runSkillCommand(runnerConfig, 'process');
            if (!result.success) return { text: `Process error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            return { text: `Processed: ${d?.total ?? 0} signals` };
          }

          case 'embed': {
            const result = await runSkillCommand(runnerConfig, 'embed');
            if (!result.success) return { text: `Embed error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            return { text: `Embedded: ${d?.total ?? 0} vectors` };
          }

          case 'update': {
            const result = await runSkillCommand(runnerConfig, 'full_cycle');
            if (!result.success) return { text: `Update error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            return {
              text: [
                `**Update complete:**`,
                `Collected: ${d?.collected ?? 0} | Processed: ${d?.processed ?? 0} | Embedded: ${d?.embedded ?? 0}`,
              ].join('\n'),
            };
          }

          case 'report': {
            if (!arg) return { text: 'Usage: /sh report <keyword>' };
            const result = await runSkillCommand(runnerConfig, 'generate_change_report', arg);
            if (!result.success) return { text: `Report error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            return { text: String(d?.text ?? 'No report') };
          }

          case 'sources': {
            const result = await runSkillCommand(runnerConfig, 'check_sources');
            if (!result.success) return { text: `Sources error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            const sources = (d?.sources as Record<string, unknown>[]) ?? [];
            const lines = sources.map((s) => {
              const icon = s.ready ? '✓' : (s.status === 'disabled' ? '○' : '✗');
              return `${icon} **${s.source}** ${s.limit_info ?? s.note ?? ''}`;
            });
            return { text: `**Sources:**\n${lines.join('\n') || 'none'}` };
          }

          case 'keywords': {
            const result = await runSkillCommand(runnerConfig, 'list_keywords');
            if (!result.success) return { text: `Keywords error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            const keywords = (d?.keywords as string[]) ?? [];
            return { text: keywords.length ? `Keywords: ${keywords.join(', ')}` : 'No keywords tracked.' };
          }

          case 'embedder': {
            const action = arg || 'status';
            const json = JSON.stringify({ action, lines: 50 });
            const result = await runSkillCommand(runnerConfig, 'embedder_service', json);
            if (!result.success) return { text: `Embedder error: ${result.error}` };
            const d = result.data as Record<string, unknown>;
            if (action === 'status') {
              const running = d?.running as boolean;
              const health = (d?.health as Record<string, unknown>) ?? {};
              return {
                text: running
                  ? `✓ Embedder running | model: ${health.model ?? '-'} | ready: ${health.ready ?? false}`
                  : `✗ Embedder down: ${(health.error as string) ?? 'unreachable'}`,
              };
            }
            if (action === 'logs') {
              return { text: String(d?.logs ?? 'no logs') };
            }
            return { text: `Embedder ${action}: ${(d?.success as boolean) ? 'done' : 'failed'} ${d?.output ?? ''}` };
          }

          default:
            return {
              text: `Unknown subcommand: ${sub}\nUsage: /sh status | query <text> | collect | process | embed | update | report <kw> | sources | keywords | embedder [status|start|stop|restart|logs|build]`,
            };
        }
      },
    });
  }
}
