/**
 * Python subprocess runner.
 * Spawns the skill CLI and returns parsed JSON output.
 * All business logic stays in Python - this is a thin adapter.
 */

import { execFile } from 'child_process';
import * as path from 'path';

export interface RunnerConfig {
  pythonBin: string;
  skillDir: string;
}

export interface RunResult {
  success: boolean;
  data?: unknown;
  error?: string;
  rawOutput?: string;
}

/**
 * Run `python -m skill <command> [args...]` and parse JSON stdout.
 * Long-running commands (collect, process, embed) emit multiple JSON lines -
 * we return only the last one (the "done" summary).
 */
export async function runSkillCommand(
  config: RunnerConfig,
  command: string,
  ...args: string[]
): Promise<RunResult> {
  return new Promise((resolve) => {
    const scriptArgs = ['-m', 'skill', command, ...args];

    const child = execFile(
      config.pythonBin,
      scriptArgs,
      {
        cwd: config.skillDir,
        env: { ...process.env },
        maxBuffer: 20 * 1024 * 1024, // 20MB for large outputs
        timeout: 5 * 60 * 1000,       // 5 min timeout
      },
      (error, stdout, stderr) => {
        if (stderr) {
          // Python logging goes to stderr - forward to Node.js stderr for debugging
          process.stderr.write(`[signal-hunter][python] ${stderr}`);
        }

        if (error && !stdout) {
          resolve({ success: false, error: error.message });
          return;
        }

        const output = stdout.trim();
        if (!output) {
          resolve({ success: false, error: 'No output from Python process' });
          return;
        }

        // For streaming commands (collect/process/embed), take the last JSON line
        const lines = output.split('\n').filter(Boolean);
        const lastLine = lines[lines.length - 1];

        try {
          const data = JSON.parse(lastLine);
          resolve({ success: true, data, rawOutput: output });
        } catch {
          resolve({ success: true, data: { text: output }, rawOutput: output });
        }
      }
    );

    // Stream intermediate progress lines to stderr for debugging
    child.stdout?.on('data', (chunk: Buffer) => {
      const lines = chunk.toString().split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const parsed = JSON.parse(line);
          if (parsed.status === 'running') {
            process.stderr.write(`[signal-hunter] ${JSON.stringify(parsed)}\n`);
          }
        } catch {
          // not JSON line, ignore
        }
      }
    });
  });
}

/** Format a RunResult as text for the LLM response. */
export function formatResult(result: RunResult): string {
  if (!result.success) {
    return `Error: ${result.error ?? 'Unknown error'}`;
  }
  const data = result.data as Record<string, unknown>;
  if (data && typeof data === 'object' && 'text' in data) {
    return String(data.text);
  }
  return JSON.stringify(data, null, 2);
}
