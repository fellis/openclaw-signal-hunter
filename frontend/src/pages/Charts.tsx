import { useState, useEffect } from 'react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend, RadarChart,
  PolarGrid, PolarAngleAxis, Radar,
} from 'recharts'
import { Loader2 } from 'lucide-react'
import { fetchTimeline, fetchSourcesBreakdown, fetchCategoriesBreakdown } from '@/api/report'
import type { TimelinePoint, SourceBreakpoint } from '@/types'
import { SOURCE_LABELS, SOURCE_COLORS, CATEGORY_COLORS } from '@/lib/utils'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{ borderColor: 'var(--border)', background: 'var(--bg-2)' }}
    >
      <h2 className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: 'var(--text-muted)' }}>
        {title}
      </h2>
      {children}
    </div>
  )
}

const TOOLTIP_STYLE = {
  backgroundColor: 'var(--bg-3)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  fontSize: 11,
  color: 'var(--text)',
}

const AXIS_STYLE = { fontSize: 11, fill: 'var(--text-muted)' }

export default function Charts() {
  const [timeline, setTimeline] = useState<TimelinePoint[]>([])
  const [sources, setSources] = useState<SourceBreakpoint[]>([])
  const [categories, setCategories] = useState<{ category: string; count: number; avg_rank_score: number; avg_intensity: number; avg_confidence: number }[]>([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetchTimeline(days),
      fetchSourcesBreakdown(),
      fetchCategoriesBreakdown(),
    ]).then(([tl, src, cat]) => {
      setTimeline(tl)
      setSources(src)
      setCategories(cat)
    }).finally(() => setLoading(false))
  }, [days])

  // Pivot timeline: {day: 'x', source1: n, source2: n, ...}
  const pivotTimeline = (() => {
    const dayMap: Record<string, Record<string, string | number>> = {}
    for (const pt of timeline) {
      if (!dayMap[pt.day]) dayMap[pt.day] = { day: pt.day }
      dayMap[pt.day][pt.source_type] = pt.count
    }
    return Object.values(dayMap).sort((a, b) =>
      String(a.day).localeCompare(String(b.day)),
    )
  })()

  const allSources = [...new Set(timeline.map(t => t.source_type))]

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-muted)' }}>
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Loading charts…</span>
      </div>
    )
  }

  return (
    <div className="p-4 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>Charts</h1>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Period:</span>
          {[7, 14, 30, 90].map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2 py-1 rounded text-xs border transition-colors ${days === d ? 'text-white border-transparent' : 'border-[var(--border)] hover:bg-[var(--bg-3)]'}`}
              style={days === d ? { background: 'var(--accent)', borderColor: 'var(--accent)' } : { color: 'var(--text-muted)' }}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Timeline */}
      <Section title={`Signal volume · last ${days} days`}>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={pivotTimeline}>
            <defs>
              {allSources.map(src => (
                <linearGradient key={src} id={`grad-${src}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={SOURCE_COLORS[src] ?? '#6b7280'} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={SOURCE_COLORS[src] ?? '#6b7280'} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="day" tick={AXIS_STYLE} tickLine={false} axisLine={false}
              tickFormatter={v => v.slice(5)} />
            <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} width={30} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {allSources.map(src => (
              <Area
                key={src}
                type="monotone"
                dataKey={src}
                name={SOURCE_LABELS[src] ?? src}
                stroke={SOURCE_COLORS[src] ?? '#6b7280'}
                fill={`url(#grad-${src})`}
                strokeWidth={1.5}
                dot={false}
                stackId="1"
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </Section>

      <div className="grid grid-cols-2 gap-4">
        {/* Source breakdown */}
        <Section title="Signals by source">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={sources} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
              <XAxis type="number" tick={AXIS_STYLE} tickLine={false} axisLine={false} />
              <YAxis
                dataKey="source_type"
                type="category"
                tick={AXIS_STYLE}
                tickLine={false}
                axisLine={false}
                width={80}
                tickFormatter={v => SOURCE_LABELS[v] ?? v}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(v: number, name: string) => [v.toLocaleString(), name]}
              />
              <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                {sources.map(entry => (
                  <rect
                    key={entry.source_type}
                    fill={SOURCE_COLORS[entry.source_type] ?? '#6b7280'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>

        {/* Category radar */}
        <Section title="Category intensity vs confidence">
          <ResponsiveContainer width="100%" height={200}>
            <RadarChart data={categories.slice(0, 8)}>
              <PolarGrid stroke="var(--border)" />
              <PolarAngleAxis
                dataKey="category"
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                tickFormatter={v => v.replace(/_/g, ' ')}
              />
              <Radar
                name="Intensity"
                dataKey="avg_intensity"
                stroke="#5E6AD2"
                fill="#5E6AD2"
                fillOpacity={0.3}
              />
              <Radar
                name="Confidence"
                dataKey="avg_confidence"
                stroke="#22c55e"
                fill="#22c55e"
                fillOpacity={0.2}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
            </RadarChart>
          </ResponsiveContainer>
        </Section>
      </div>

      {/* Category bar */}
      <Section title="Signals by category">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={categories}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="category"
              tick={AXIS_STYLE}
              tickLine={false}
              axisLine={false}
              tickFormatter={v => v.replace(/_/g, ' ')}
            />
            <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} width={40} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Bar
              dataKey="count"
              radius={[3, 3, 0, 0]}
              fill="var(--accent)"
            />
          </BarChart>
        </ResponsiveContainer>
      </Section>
    </div>
  )
}
