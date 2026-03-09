import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { RefreshCw, Loader2 } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import PipelineStrip from '@/components/layout/PipelineStrip'
import FilterPanel from '@/components/report/FilterPanel'
import SignalTable from '@/components/report/SignalTable'
import { fetchReport, fetchStats, fetchRules } from '@/api/report'
import { filtersFromSearchParams, filtersToSearchParams } from '@/lib/urlParams'
import type { Category, Filters, Rule, StatsResponse } from '@/types'

const STATS_POLL_MS = 5000

export default function Report({ lang = 'en' }: { lang?: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = useMemo(
    () => filtersFromSearchParams(searchParams),
    [searchParams.toString()]
  )
  const searchQuery = searchParams.get('q') ?? ''
  const searchMode = (searchParams.get('search_mode') === 'text' ? 'text' : 'semantic') as 'semantic' | 'text'

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
      const data = await fetchReport(
        filters,
        'avg_rank_score',
        'desc',
        searchQuery.trim() || undefined,
        searchQuery.trim() ? searchMode : undefined,
      )
      setCategories(data.categories)
      setTotal(data.total_signals)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [filters, searchQuery, searchMode])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    fetchRules().then(setRules).catch(() => {})
  }, [])

  // Global pipeline stats: fetch once, then every 5s (no filters; numbers update smoothly)
  useEffect(() => {
    let cancelled = false
    const tick = () => {
      fetchStats()
        .then((s) => { if (!cancelled) setStats(s) })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, STATS_POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const FILTER_KEYS = ['date_from', 'date_to', 'sources', 'categories', 'keywords', 'intensities', 'confidence_min', 'confidence_max']

  const updateFilters = useCallback((partial: Partial<Filters>) => {
    const next = { ...filters, ...partial }
    setSearchParams(prev => {
      const nextParams = new URLSearchParams(prev)
      FILTER_KEYS.forEach(k => nextParams.delete(k))
      filtersToSearchParams(next).forEach((v, k) => nextParams.set(k, v))
      return nextParams
    }, { replace: true })
  }, [filters, setSearchParams])

  const updateSearch = useCallback((q: string, mode: 'semantic' | 'text') => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (q.trim()) next.set('q', q.trim())
      else next.delete('q')
      if (mode === 'text') next.set('search_mode', 'text')
      else next.delete('search_mode')
      return next
    }, { replace: true })
  }, [setSearchParams])

  // Single URL update to clear all filters and search (avoids race with two setSearchParams)
  const clearAllFiltersAndSearch = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true })
  }, [setSearchParams])

  const subtitle = searchQuery.trim()
    ? `${total.toLocaleString()} results for "${searchQuery}" · ${categories.length} categories`
    : `${(stats?.relevant_total ?? total).toLocaleString()} relevant signals · ${categories.length} categories`

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Signals"
        subtitle={subtitle}
        action={
          <button onClick={load} disabled={loading} className="btn btn-ghost">
            {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Refresh
          </button>
        }
      />
      <PipelineStrip stats={stats} totalSignals={stats?.relevant_total ?? 0} />

      {/* Filters */}
      <FilterPanel
        filters={filters}
        onChange={updateFilters}
        rules={rules}
        searchQuery={searchQuery}
        searchMode={searchMode}
        onSearchChange={updateSearch}
        onClearAll={clearAllFiltersAndSearch}
      />

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
          <SignalTable
            categories={categories}
            filters={filters}
            lang={lang}
            rules={rules}
            searchQuery={searchQuery.trim() || undefined}
            searchMode={searchQuery.trim() ? searchMode : undefined}
          />
        )}
      </div>
    </div>
  )
}
