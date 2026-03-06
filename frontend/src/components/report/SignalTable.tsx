import React, { useState, useCallback, useEffect } from 'react'
import { ChevronRight, ExternalLink, Loader2 } from 'lucide-react'
import { cn, formatRelative, SOURCE_LABELS, CATEGORY_COLORS, SOURCE_COLORS, intensityLabel } from '@/lib/utils'
import { fetchClusters, fetchSignals } from '@/api/report'
import type { Category, Cluster, Signal, Filters } from '@/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// value  - actual rank_score (sum for category/cluster, raw for signal)
// max    - max in current list, used for bar fill scaling
function RankBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-3)' }}>
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: 'var(--accent)' }} />
      </div>
      <span className="text-2xs tabular-nums font-medium" style={{ color: 'var(--text-2)' }}>
        {value.toFixed(1)}
      </span>
    </div>
  )
}

function SourcePills({ breakdown }: { breakdown: Record<string, number> }) {
  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(breakdown)
        .filter(([, n]) => n > 0)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5)
        .map(([src, n]) => (
          <span
            key={src}
            className="badge text-white"
            style={{ background: SOURCE_COLORS[src] ?? '#6b7280' }}
          >
            {SOURCE_LABELS[src] ?? src}: {n}
          </span>
        ))}
    </div>
  )
}

function CategoryBadge({ name }: { name: string }) {
  return (
    <span
      className="badge"
      style={{
        background: `${CATEGORY_COLORS[name] ?? '#6b7280'}20`,
        color: CATEGORY_COLORS[name] ?? '#6b7280',
        border: `1px solid ${CATEGORY_COLORS[name] ?? '#6b7280'}40`,
      }}
    >
      {name.replace(/_/g, ' ')}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Column header
// ---------------------------------------------------------------------------

type SortKey = 'rank_score' | 'avg_rank_score' | 'avg_intensity' | 'avg_confidence' | 'count' | 'last_signal_at'
type SortKeyL3 = 'rank_score' | 'intensity' | 'confidence' | 'score' | 'comments_count' | 'created_at' | 'collected_at'

function SortHeader({
  label, col, sortBy, sortDir, onSort,
}: {
  label: string
  col: string
  sortBy: string
  sortDir: 'asc' | 'desc'
  onSort: (col: string) => void
}) {
  const active = sortBy === col
  return (
    <button
      onClick={() => onSort(col)}
      className={cn(
        'text-left text-2xs font-semibold uppercase tracking-wider transition-colors',
        active ? '' : 'opacity-60 hover:opacity-100',
      )}
      style={{ color: active ? 'var(--accent)' : 'var(--text-muted)' }}
    >
      {label}
      {active && <span className="ml-0.5">{sortDir === 'desc' ? ' ↓' : ' ↑'}</span>}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Signal row (level 3)
// ---------------------------------------------------------------------------

function SignalRow({ signal, lang = 'en' }: { signal: Signal; lang?: string }) {
  const showLangBadge = lang !== 'en'
  return (
    <tr
      className="table-row-hover border-b"
      style={{ borderColor: 'var(--border)' }}
    >
      <td className="pl-16 pr-3 py-2.5 w-0">
        <div className="w-1" />
      </td>
      <td className="pr-4 py-2.5" style={{ minWidth: 320 }}>
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <a
              href={signal.url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium hover:underline flex items-center gap-1"
              style={{ color: 'var(--text)', fontSize: 12 }}
            >
              <span className="truncate block max-w-xs">{signal.title}</span>
              <ExternalLink size={10} className="shrink-0 opacity-40" />
              {showLangBadge && (
                <span
                  title={signal.translation_available ? 'Translated' : 'Translation pending'}
                  className="ml-0.5 inline-flex items-center px-1 py-0 rounded text-2xs font-bold shrink-0"
                  style={{
                    background: signal.translation_available ? 'var(--accent)20' : 'var(--bg-3)',
                    color: signal.translation_available ? 'var(--accent)' : 'var(--text-muted)',
                    border: `1px solid ${signal.translation_available ? 'var(--accent)' : 'var(--border)'}`,
                  }}
                >
                  {signal.translation_available ? lang.toUpperCase() : '~'}
                </span>
              )}
            </a>
            {signal.summary && (
              <p className="mt-0.5 text-2xs leading-relaxed line-clamp-2" style={{ color: 'var(--text-muted)' }}>
                {signal.summary}
              </p>
            )}
            {signal.keywords && signal.keywords.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {signal.keywords.slice(0, 4).map(kw => (
                  <span
                    key={kw}
                    className="inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-medium"
                    style={{ background: 'var(--bg-3)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                  >
                    {kw}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="pr-4 py-2.5">
        <span className="badge" style={{ background: `${SOURCE_COLORS[signal.source] ?? '#6b7280'}20`, color: SOURCE_COLORS[signal.source] ?? '#6b7280' }}>
          {SOURCE_LABELS[signal.source] ?? signal.source}
        </span>
      </td>
        <td className="pr-4 py-2.5">
          <RankBar value={signal.rank_score} max={1} />
        </td>
      <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
        {intensityLabel(signal.intensity)}
      </td>
      <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
        {(signal.confidence * 100).toFixed(0)}%
      </td>
      <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
        {signal.score}
      </td>
      <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
        {signal.comments_count}
      </td>
      <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
        {formatRelative(signal.created_at)}
      </td>
      <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
        {formatRelative(signal.collected_at)}
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// Cluster row (level 2) + lazy-loaded signals
// ---------------------------------------------------------------------------

function ClusterRow({
  cluster,
  maxRankScore,
  sortBy,
  sortDir,
  onSort,
  lang = 'en',
}: {
  cluster: Cluster
  maxRankScore: number
  sortBy: SortKeyL3
  sortDir: 'asc' | 'desc'
  onSort: (col: string) => void
  lang?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [signals, setSignals] = useState<Signal[] | null>(null)
  const [loading, setLoading] = useState(false)

  const expand = useCallback(async () => {
    if (!expanded && signals === null) {
      setLoading(true)
      try {
        const data = await fetchSignals(cluster.signal_ids, sortBy, sortDir, lang)
        setSignals(data.signals)
      } finally {
        setLoading(false)
      }
    }
    setExpanded(prev => !prev)
  }, [expanded, signals, cluster.signal_ids, sortBy, sortDir, lang])

  // Reload signals when lang changes while cluster is expanded
  useEffect(() => {
    if (expanded && signals !== null) {
      fetchSignals(cluster.signal_ids, sortBy, sortDir, lang)
        .then(data => setSignals(data.signals))
        .catch(() => {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang])

  return (
    <>
      <tr
        className="table-row-hover border-b cursor-pointer"
        style={{ borderColor: 'var(--border)' }}
        onClick={expand}
      >
        <td className="pl-10 pr-3 py-2.5 w-0">
          <ChevronRight
            size={13}
            className="transition-transform"
            style={{ color: 'var(--text-muted)', transform: expanded ? 'rotate(90deg)' : undefined }}
          />
        </td>
        <td className="pr-4 py-2.5" style={{ minWidth: 320 }}>
          <div className="flex items-center gap-2">
            <span className="font-medium text-xs" style={{ color: 'var(--text-2)' }}>
              {cluster.name}
            </span>
            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{cluster.count}</span>
            {loading && <Loader2 size={11} className="animate-spin" style={{ color: 'var(--text-muted)' }} />}
          </div>
        </td>
        <td className="pr-4 py-2.5">
          <SourcePills breakdown={cluster.sources_breakdown} />
        </td>
        <td className="pr-4 py-2.5">
          <RankBar value={cluster.rank_score} max={maxRankScore} />
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {intensityLabel(cluster.avg_intensity)}
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {(cluster.avg_confidence * 100).toFixed(0)}%
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {cluster.avg_score.toFixed(0)}
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {cluster.avg_comments.toFixed(0)}
        </td>
        <td className="pr-4 py-2.5" />
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
          {formatRelative(cluster.last_signal_at)}
        </td>
      </tr>

      {expanded && signals && signals.map(s => (
        <SignalRow key={s.raw_signal_id} signal={s} lang={lang} />
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Category row (level 1) + lazy-loaded clusters
// ---------------------------------------------------------------------------

function CategoryRow({
  category,
  maxRankScore,
  sortBy,
  sortDir,
  onSort,
  filters,
  lang = 'en',
}: {
  category: Category
  maxRankScore: number
  sortBy: SortKey
  sortDir: 'asc' | 'desc'
  onSort: (col: string) => void
  filters: Filters
  lang?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [clusters, setClusters] = useState<Cluster[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [clusterSort, setClusterSort] = useState<SortKeyL3>('collected_at')
  const [clusterSortDir, setClusterSortDir] = useState<'asc' | 'desc'>('desc')

  const expand = useCallback(async () => {
    if (!expanded && clusters === null) {
      setLoading(true)
      try {
        const data = await fetchClusters(category.name, filters)
        setClusters(data.clusters)
      } finally {
        setLoading(false)
      }
    }
    setExpanded(prev => !prev)
  }, [expanded, clusters, category.name, filters])

  const handleClusterSort = (col: string) => {
    if (col === clusterSort) setClusterSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setClusterSort(col as SortKeyL3); setClusterSortDir('desc') }
  }

  return (
    <>
      <tr
        className="table-row-hover border-b cursor-pointer"
        style={{ borderColor: 'var(--border)' }}
        onClick={expand}
      >
        <td className="pl-4 pr-3 py-2.5 w-0">
          <ChevronRight
            size={13}
            className="transition-transform"
            style={{ color: 'var(--text-muted)', transform: expanded ? 'rotate(90deg)' : undefined }}
          />
        </td>
        <td className="pr-4 py-2.5" style={{ minWidth: 320 }}>
          <div className="flex items-center gap-2">
            <CategoryBadge name={category.name} />
            <span className="text-2xs font-medium" style={{ color: 'var(--text-muted)' }}>
              {category.count.toLocaleString()}
            </span>
            {loading && <Loader2 size={11} className="animate-spin" style={{ color: 'var(--text-muted)' }} />}
          </div>
        </td>
        <td className="pr-4 py-2.5">
          <SourcePills breakdown={category.sources_breakdown} />
        </td>
        <td className="pr-4 py-2.5">
          <RankBar value={category.rank_score} max={maxRankScore} />
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-2)' }}>
          {intensityLabel(category.avg_intensity)}
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-2)' }}>
          {(category.avg_confidence * 100).toFixed(0)}%
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-2)' }}>
          {category.avg_score.toFixed(0)}
        </td>
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-2)' }}>
          {category.avg_comments.toFixed(0)}
        </td>
        <td className="pr-4 py-2.5" />
        <td className="pr-4 py-2.5 text-xs tabular-nums" style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
          {formatRelative(category.last_signal_at)}
        </td>
      </tr>

      {expanded && clusters && (() => {
        const maxClusters = Math.max(...clusters.map(c => c.rank_score), 1)
        return clusters.map(c => (
          <ClusterRow
            key={c.id}
            cluster={c}
            maxRankScore={maxClusters}
            sortBy={clusterSort}
            sortDir={clusterSortDir}
            onSort={handleClusterSort}
            lang={lang}
          />
        ))
      })()}
    </>
  )
}

// ---------------------------------------------------------------------------
// Main table
// ---------------------------------------------------------------------------

interface TableProps {
  categories: Category[]
  filters: Filters
  lang?: string
}

export default function SignalTable({ categories, filters, lang = 'en' }: TableProps) {
  const [sortBy, setSortBy] = useState<SortKey>('rank_score')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const handleSort = (col: string) => {
    if (col === sortBy) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortBy(col as SortKey); setSortDir('desc') }
  }

  const maxRankScore = Math.max(...categories.map(c => c.rank_score), 1)

  const sorted = [...categories].sort((a, b) => {
    const av = a[sortBy] ?? 0
    const bv = b[sortBy] ?? 0
    if (typeof av === 'string' && typeof bv === 'string')
      return sortDir === 'desc' ? bv.localeCompare(av) : av.localeCompare(bv)
    return sortDir === 'desc' ? (bv as number) - (av as number) : (av as number) - (bv as number)
  })

  const colProps = { sortBy, sortDir, onSort: handleSort }

  const thStyle: React.CSSProperties = {
    position: 'sticky',
    top: 0,
    background: 'var(--bg-2)',
    zIndex: 10,
    borderBottom: '1px solid var(--border)',
  }

  return (
    <div className="overflow-x-auto" style={{ maxHeight: 'calc(100vh - 200px)', overflowY: 'auto' }}>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="w-0 pl-4 py-2.5" style={thStyle} />
            <th className="text-left pl-0 pr-4 py-2.5" style={{ ...thStyle, minWidth: 320 }}>
              <SortHeader label="Category / Cluster / Signal" col="count" {...colProps} />
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <span className="text-2xs font-semibold uppercase tracking-wider opacity-60" style={{ color: 'var(--text-muted)' }}>
                Sources
              </span>
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <SortHeader label="Rank Score (Σ)" col="rank_score" {...colProps} />
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <SortHeader label="Intensity" col="avg_intensity" {...colProps} />
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <SortHeader label="Confidence" col="avg_confidence" {...colProps} />
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <span className="text-2xs font-semibold uppercase tracking-wider opacity-60" style={{ color: 'var(--text-muted)' }}>
                Avg Score
              </span>
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <span className="text-2xs font-semibold uppercase tracking-wider opacity-60" style={{ color: 'var(--text-muted)' }}>
                Avg Comments
              </span>
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <SortHeader label="Created" col="last_signal_at" {...colProps} />
            </th>
            <th className="text-left pr-4 py-2.5" style={thStyle}>
              <SortHeader label="Collected" col="last_signal_at" {...colProps} />
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(cat => (
            <CategoryRow
              key={cat.name}
              category={cat}
              maxRankScore={maxRankScore}
              sortBy={sortBy}
              sortDir={sortDir}
              onSort={handleSort}
              filters={filters}
              lang={lang}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
