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

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="px-4 py-3 rounded-lg border" style={{ borderColor: 'var(--border)', background: 'var(--bg-2)' }}>
      <div className="text-2xs uppercase tracking-wider mb-1" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="text-lg font-semibold tabular-nums" style={{ color: 'var(--text)' }}>{value}</div>
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

      {/* Stats strip */}
      {stats && (
        <div className="flex gap-3 px-4 py-3 border-b shrink-0 overflow-x-auto" style={{ borderColor: 'var(--border)' }}>
          <StatCard label="Total raw" value={stats.raw_total?.toLocaleString() ?? '—'} />
          <StatCard label="Relevant" value={stats.relevant_total?.toLocaleString() ?? '—'} />
          <StatCard label="Embedded" value={stats.embedded_total?.toLocaleString() ?? '—'} />
          <StatCard label="Queue" value={stats.pending_embeddings?.toLocaleString() ?? '—'} />
          <StatCard label="Keywords" value={stats.keywords_total ?? '—'} />
          <StatCard label="Avg rank" value={stats.avg_rank_score?.toFixed(3) ?? '—'} />
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
