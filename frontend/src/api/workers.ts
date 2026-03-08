import type { WorkerLogLine, WorkerStatusResponse } from '@/types'
import { buildQueryString } from '@/lib/utils'

export async function fetchWorkerStatus(): Promise<WorkerStatusResponse> {
  const res = await fetch('/api/workers/status')
  if (!res.ok) throw new Error(`Workers status failed: ${res.status}`)
  return res.json()
}

export interface FetchWorkerLogsParams {
  tail?: number
  since?: number
  worker?: string
  level?: string
}

export interface WorkerLogsResponse {
  lines: WorkerLogLine[]
  next_since?: number
}

export async function fetchWorkerLogs(params: FetchWorkerLogsParams = {}): Promise<WorkerLogsResponse> {
  const qs = buildQueryString({
    tail: params.tail ?? 500,
    since: params.since ?? '',
    worker: params.worker ?? 'all',
    level: params.level ?? 'all',
  })
  const res = await fetch(`/api/workers/logs?${qs}`)
  if (!res.ok) throw new Error(`Workers logs failed: ${res.status}`)
  return res.json()
}

export async function restartWorkers(): Promise<{ status: string; message?: string }> {
  const res = await fetch('/api/workers/restart', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Restart failed: ${res.status}`)
  }
  return res.json()
}
