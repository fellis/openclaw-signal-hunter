import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Loader2 } from 'lucide-react'
import FilterPanel from '@/components/report/FilterPanel'
import SignalTable from '@/components/report/SignalTable'
import { fetchReport, fetchStats, fetchRules } from '@/api/report'
import type { Category, Filters, Rule, StatsResponse } from '@/types'

const DEFAULT_FILTERS: Filters = {
  date_from: '',
  date_to: '',
  sources: [],
  categories: [],
  keywords: [],
  intensities: [],
  confidence_min: null,
  confidence_max: null,
}

interface PipelineStageProps {
  index: number
  label: string
  value: string
  sub: string
  pct?: number
  alert?: boolean
}

function PipelineStage({ index, label, value, sub, pct, alert }: PipelineStageProps) {
  const barColor = alert ? '#f97316' : 'var(--accent, #6366f1)'
  return (
    <div className="flex flex-col gap-1 min-w-[130px]" style={{ flex: '1 1 130px' }}>
      <div className="flex items-center gap-1.5 mb-0.5">
        <span
          className="text-2xs font-semibold rounded-full flex items-center justify-center shrink-0"
          style={{
            width: 18, height: 18,
            background: 'var(--bg-3, #1e1e2e)',
            color: 'var(--text-muted)',
            border: '1px solid var(--border)',
          }}
        >
          {index}
        </span>
        <span className="text-2xs uppercase tracking-wider font-medium" style={{ color: 'var(--text-muted)' }}>
          {label}
        </span>
      </div>
      <div className="text-base font-semibold tabular-nums leading-tight" style={{ color: alert ? '#f97316' : 'var(--text)' }}>
        {value}
      </div>
      <div className="text-2xs leading-tight" style={{ color: 'var(--text-muted)', minHeight: 14 }}>
        {sub}
      </div>
      {pct !== undefined && (
        <div className="mt-1 rounded-full overflow-hidden" style={{ height: 3, background: 'var(--border)' }}>
          <div style={{ width: `${Math.min(pct, 100)}%`, height: '100%', background: barColor, borderRadius: 9999 }} />
        </div>
      )}
    </div>
  )
}

function PipelineArrow() {
  return (
    <div className="flex items-center shrink-0 self-start mt-5" style={{ color: 'var(--text-muted)' }}>
      <svg width="16" height="10" viewBox="0 0 16 10" fill="none">
        <path d="M0 5h13M9 1l5 4-5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  )
}

interface PipelineStageClassifyProps {
  index: number
  processedTotal: number
  rawTotal: number
  classifiedByEmbeddings: number
  classifiedByLlm: number
  unprocessed: number
  borderlinePending: number
}

function PipelineStageClassify({
  index,
  processedTotal,
  rawTotal,
  classifiedByEmbeddings,
  classifiedByLlm,
  unprocessed,
  borderlinePending,
}: PipelineStageClassifyProps) {
  return (
    <div className="flex flex-col gap-1 min-w-[160px]" style={{ flex: '1 1 160px' }}>
      <div className="flex items-center gap-1.5 mb-0.5">
        <span
          className="text-2xs font-semibold rounded-full flex items-center justify-center shrink-0"
          style={{
            width: 18, height: 18,
            background: 'var(--bg-3, #1e1e2e)',
            color: 'var(--text-muted)',
            border: '1px solid var(--border)',
          }}
        >
          {index}
        </span>
        <span className="text-2xs uppercase tracking-wider font-medium" style={{ color: 'var(--text-muted)' }}>
          Classify
        </span>
      </div>
      <div className="text-base font-semibold tabular-nums leading-tight" style={{ color: 'var(--text)' }}>
        {processedTotal.toLocaleString()} / {rawTotal.toLocaleString()}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
        <div>embeddings: {classifiedByEmbeddings.toLocaleString()}</div>
        <div>LLM: {classifiedByLlm.toLocaleString()}</div>
        <div>queue embeddings: {unprocessed.toLocaleString()}</div>
        <div>queue LLM: {borderlinePending.toLocaleString()}</div>
      </div>
    </div>
  )
}

export default function Report({ lang = 'en' }: { lang?: string }) {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [categories, setCategories] = useState<Category[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchReport(filters)
      setCategories(data.categories)
      setTotal(data.total_signals)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    fetchStats().then(setStats).catch(() => {})
    fetchRules().then(setRules).catch(() => {})
  }, [])

  const updateFilters = (partial: Partial<Filters>) =>
    setFilters(prev => ({ ...prev, ...partial }))

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <div>
          <h1 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>Signals</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {total.toLocaleString()} relevant signals · {categories.length} categories
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="btn btn-ghost"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          Refresh
        </button>
      </div>

      {/* Pipeline strip */}
      {stats && (
        <div
          className="flex items-start gap-2 px-4 py-3 border-b shrink-0 overflow-x-auto"
          style={{ borderColor: 'var(--border)', background: 'var(--bg-1, var(--bg))' }}
        >
          <PipelineStage
            index={1}
            label="Keywords"
            value={`${stats.keywords_run_24h ?? 0} / ${stats.keywords_total ?? 0}`}
            sub="обработано за 24ч"
            pct={stats.keywords_total ? (stats.keywords_run_24h / stats.keywords_total) * 100 : 0}
          />
          <PipelineArrow />
          <PipelineStage
            index={2}
            label="Collect"
            value={`+${stats.new_signals_24h?.toLocaleString() ?? 0}`}
            sub={`новых за 24ч · всего ${stats.raw_total?.toLocaleString() ?? 0}`}
          />
          <PipelineArrow />
          <PipelineStageClassify
            index={3}
            processedTotal={stats.processed_total ?? 0}
            rawTotal={stats.raw_total ?? 0}
            classifiedByEmbeddings={stats.classified_by_embeddings ?? 0}
            classifiedByLlm={stats.classified_by_llm ?? 0}
            unprocessed={stats.unprocessed ?? 0}
            borderlinePending={stats.borderline_pending ?? 0}
          />
          <PipelineArrow />
          <PipelineStage
            index={4}
            label="Summarize"
            value={`${stats.summarized_total?.toLocaleString() ?? 0} / ${((stats.summarized_total ?? 0) + (stats.summary_pending ?? 0)).toLocaleString()}`}
            sub="с summary"
            pct={(stats.summarized_total ?? 0) + (stats.summary_pending ?? 0) > 0
              ? (stats.summarized_total / ((stats.summarized_total ?? 0) + (stats.summary_pending ?? 0))) * 100
              : 0}
          />
          <PipelineArrow />
          <PipelineStage
            index={5}
            label="Vectorize"
            value={`${stats.embedded_total?.toLocaleString() ?? 0} / ${((stats.embedded_total ?? 0) + (stats.pending_embeddings ?? 0)).toLocaleString()}`}
            sub="в Qdrant"
            pct={(stats.embedded_total ?? 0) + (stats.pending_embeddings ?? 0) > 0
              ? (stats.embedded_total / ((stats.embedded_total ?? 0) + (stats.pending_embeddings ?? 0))) * 100
              : 0}
          />
        </div>
      )}

      {/* Filters */}
      <FilterPanel filters={filters} onChange={updateFilters} rules={rules} />

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {error && (
          <div className="m-4 p-3 rounded-md border text-xs" style={{ borderColor: '#ef4444', color: '#ef4444', background: '#ef444410' }}>
            {error}
          </div>
        )}

        {loading && categories.length === 0 ? (
          <div className="flex items-center justify-center h-48 gap-2" style={{ color: 'var(--text-muted)' }}>
            <Loader2 size={16} className="animate-spin" />
            <span className="text-sm">Loading signals…</span>
          </div>
        ) : categories.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-sm" style={{ color: 'var(--text-muted)' }}>
            No signals found for current filters.
          </div>
        ) : (
          <SignalTable categories={categories} filters={filters} lang={lang} rules={rules} />
        )}
      </div>
    </div>
  )
}
