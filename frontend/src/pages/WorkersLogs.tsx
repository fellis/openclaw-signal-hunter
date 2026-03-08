import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { RefreshCw, Loader2, Trash2, Pause, Play, RotateCw } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import PipelineStrip from '@/components/layout/PipelineStrip'
import { fetchStats } from '@/api/report'
import { fetchWorkerStatus, fetchWorkerLogs, clearWorkersLogs, restartWorkers } from '@/api/workers'
import type { StatsResponse } from '@/types'
import type { WorkerStatusResponse, WorkerLogLine } from '@/types'

const POLL_LOGS_MS = 3000
const POLL_STATUS_MS = 10000

function formatInterval(sec: number): string {
  if (sec < 60) return `every ${sec}s`
  if (sec % 60 === 0) return `every ${sec / 60}m`
  return `every ${sec}s`
}

export default function WorkersLogs() {
  const [searchParams, setSearchParams] = useSearchParams()
  const workerFilter = searchParams.get('worker') ?? 'all'
  const levelFilter = searchParams.get('level') ?? 'all'

  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [totalSignals, setTotalSignals] = useState(0)
  const [status, setStatus] = useState<WorkerStatusResponse | null>(null)
  const [lines, setLines] = useState<WorkerLogLine[]>([])
  const [nextSince, setNextSince] = useState<number | undefined>(undefined)
  const [paused, setPaused] = useState(false)

  const setWorkerFilter = useCallback((v: string) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (v === 'all') next.delete('worker')
      else next.set('worker', v)
      return next
    }, { replace: true })
  }, [setSearchParams])

  const setLevelFilter = useCallback((v: string) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (v === 'all') next.delete('level')
      else next.set('level', v)
      return next
    }, { replace: true })
  }, [setSearchParams])
  const [loading, setLoading] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const logEndRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const nextSinceRef = useRef<number | undefined>(undefined)
  nextSinceRef.current = nextSince

  useEffect(() => {
    setLines([])
    setNextSince(undefined)
  }, [workerFilter, levelFilter])

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      fetchStats()
        .then((s) => {
          if (!cancelled) {
            setStats(s)
            setTotalSignals(s.relevant_total ?? 0)
          }
        })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 5000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchWorkerStatus()
      setStatus(data)
    } catch {
      setStatus(null)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    const id = setInterval(loadStatus, POLL_STATUS_MS)
    return () => clearInterval(id)
  }, [loadStatus])

  const loadLogs = useCallback(async () => {
    if (paused) return
    const since = nextSinceRef.current
    setLoading(true)
    setError(null)
    try {
      const data = await fetchWorkerLogs({
        tail: since === undefined ? 500 : undefined,
        since,
        worker: workerFilter !== 'all' ? workerFilter : undefined,
        level: levelFilter !== 'all' ? levelFilter : undefined,
      })
      // Apply worker/level filter on client so filter works even if backend query params are wrong
      let filtered = data.lines
      if (workerFilter !== 'all') filtered = filtered.filter((ln) => ln.worker === workerFilter)
      if (levelFilter !== 'all') filtered = filtered.filter((ln) => ln.level === levelFilter)
      if (since === undefined) {
        setLines(filtered)
      } else {
        setLines((prev) => [...prev, ...filtered])
      }
      if (data.next_since != null) setNextSince(data.next_since)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [paused, workerFilter, levelFilter])

  useEffect(() => {
    loadLogs()
    if (paused) return
    const id = setInterval(loadLogs, POLL_LOGS_MS)
    return () => clearInterval(id)
  }, [paused, workerFilter, levelFilter])

  useEffect(() => {
    if (autoScrollRef.current && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [lines])

  const handleClear = async () => {
    setError(null)
    try {
      await clearWorkersLogs()
      setLines([])
      setNextSince(undefined)
      loadLogs()
    } catch (e) {
      setError(String(e))
    }
  }

  const handleRestart = async () => {
    setRestarting(true)
    setError(null)
    try {
      await restartWorkers()
      setLines([])
      setNextSince(undefined)
      loadStatus()
      if (!paused) loadLogs()
    } catch (e) {
      setError(String(e))
    } finally {
      setRestarting(false)
    }
  }

  const workerOptions = [
    { id: 'all', label: 'All workers' },
    ...(status?.workers ?? []),
  ]
  const LEVEL_OPTIONS = ['all', 'info', 'warning', 'error']

  const levelColor: Record<string, string> = {
    info: 'var(--text-muted)',
    warning: '#f59e0b',
    error: '#ef4444',
  }

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Workers"
        subtitle="Live logs from worker runner"
        action={
          <button
            onClick={() => {
              loadStatus()
              if (!paused) loadLogs()
            }}
            disabled={loading}
            className="btn btn-ghost"
          >
            {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Refresh
          </button>
        }
      />
      <PipelineStrip stats={stats} totalSignals={totalSignals} />

      {/* Schedule and tasks */}
      {status && (
        <div
          className="flex flex-wrap gap-4 px-4 py-3 border-b shrink-0"
          style={{ borderColor: 'var(--border)', background: 'var(--bg-2)' }}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-2xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              Schedule
            </span>
            <span className="text-xs" style={{ color: 'var(--text)' }}>
              Translate, LLM, Embed, Vectorize: {formatInterval(status.schedule.run_worker_interval_sec)} · Collect:{' '}
              {formatInterval(status.schedule.run_collect_worker_interval_sec)}
            </span>
          </div>
          <div className="flex flex-wrap gap-4 text-xs" style={{ color: 'var(--text-muted)' }}>
            <span>
              LLM: pending {status.llm_queue.pending} · running {status.llm_queue.running} · failed{' '}
              {status.llm_queue.failed}
            </span>
            <span>Embed: unprocessed {status.embed_worker.unprocessed} · borderline {status.embed_worker.borderline_pending}</span>
            <span>Collect next: {status.collect_worker.next_keyword ?? '—'}</span>
            <span>Vectorize pending: {status.embed_vectorize.pending}</span>
            <span>Translate pending: {status.translation_worker?.pending ?? 0}</span>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-2 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <select
          value={workerFilter}
          onChange={(e) => setWorkerFilter(e.target.value)}
          className="text-xs rounded border px-2 py-1"
          style={{ background: 'var(--bg-2)', borderColor: 'var(--border)', color: 'var(--text)' }}
        >
          {workerOptions.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label}
            </option>
          ))}
        </select>
        <select
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value)}
          className="text-xs rounded border px-2 py-1"
          style={{ background: 'var(--bg-2)', borderColor: 'var(--border)', color: 'var(--text)' }}
        >
          {LEVEL_OPTIONS.map((l) => (
            <option key={l} value={l}>
              {l === 'all' ? 'All levels' : l}
            </option>
          ))}
        </select>
        <button
          onClick={handleClear}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border hover:opacity-80"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          <Trash2 size={12} />
          Clear
        </button>
        <button
          onClick={() => setPaused((p) => !p)}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border hover:opacity-80"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          {paused ? <Play size={12} /> : <Pause size={12} />}
          {paused ? 'Resume' : 'Pause'}
        </button>
        <button
          onClick={handleRestart}
          disabled={restarting}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: 'var(--border)', color: 'var(--accent)' }}
          title="Restart worker container (Docker)"
        >
          {restarting ? <Loader2 size={12} className="animate-spin" /> : <RotateCw size={12} />}
          Restart workers
        </button>
      </div>

      {/* Logs */}
      <div
        className="flex-1 overflow-auto min-h-0 font-mono text-xs px-4 py-2"
        style={{ color: 'var(--text)' }}
        onScroll={(e) => {
          const el = e.currentTarget
          autoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
        }}
      >
        {/* Sticky header - same column widths as rows */}
        <div
          className="sticky top-0 z-10 flex gap-2 py-1.5 border-b font-semibold shrink-0"
          style={{ background: 'var(--bg)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          <span className="shrink-0 tabular-nums w-[180px]">Time</span>
          <span className="shrink-0 uppercase w-14">Level</span>
          <span className="shrink-0 min-w-[8.5rem]">Worker</span>
          <span className="min-w-0">Message</span>
        </div>
        {error && (
          <div className="mb-2 p-2 rounded border text-red-500" style={{ borderColor: '#ef4444', background: '#ef444410' }}>
            {error}
          </div>
        )}
        {lines.length === 0 && !loading && (
          <div className="py-8 text-center" style={{ color: 'var(--text-muted)' }}>
            No log lines. Ensure the worker container is running and Docker socket is mounted.
          </div>
        )}
        {lines.map((ln, i) => (
          <div
            key={i}
            className="flex gap-2 py-0.5 border-b border-transparent hover:bg-[var(--bg-2)]"
            style={{ borderColor: 'var(--border)' }}
          >
            <span className="shrink-0 tabular-nums w-[180px]" style={{ color: 'var(--text-muted)' }}>
              {ln.ts || '\u00A0'}
            </span>
            <span
              className="shrink-0 uppercase font-semibold w-14"
              style={{ color: levelColor[ln.level] ?? 'var(--text-muted)' }}
            >
              {ln.level}
            </span>
            <span className="shrink-0 min-w-[8.5rem]" style={{ color: 'var(--accent)' }}>
              {workerOptions.find((w) => w.id === ln.worker)?.label ?? ln.worker}
            </span>
            <span className="min-w-0 break-all">{ln.message}</span>
          </div>
        ))}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}
