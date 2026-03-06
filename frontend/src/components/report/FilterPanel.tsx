import { useState, useEffect } from 'react'
import { Filter, X, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Filters } from '@/types'
import { fetchKeywords } from '@/api/report'

const SOURCES = [
  'github_issue', 'github_discussion', 'hn_post',
  'so_question', 'reddit_post', 'hf_discussion', 'hf_paper',
]
const SOURCE_LABELS: Record<string, string> = {
  github_issue: 'GitHub Issues', github_discussion: 'GitHub Discussions',
  hn_post: 'Hacker News', so_question: 'Stack Overflow',
  reddit_post: 'Reddit', hf_discussion: 'HuggingFace', hf_paper: 'HF Papers',
}
const CATEGORIES = [
  'pain_point', 'feature_request', 'adoption_signal',
  'comparison', 'migration', 'breaking_change', 'new_release',
]
interface Props {
  filters: Filters
  onChange: (f: Partial<Filters>) => void
}

function MultiSelect({
  label, options, selected, onChange, labelMap,
}: {
  label: string
  options: string[]
  selected: string[]
  onChange: (v: string[]) => void
  labelMap?: Record<string, string>
}) {
  const [open, setOpen] = useState(false)
  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter(s => s !== v) : [...selected, v])

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border transition-colors',
          selected.length > 0
            ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent)]/10'
            : 'border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--bg-3)]',
        )}
      >
        {label}
        {selected.length > 0 && (
          <span className="rounded px-1 text-2xs font-semibold" style={{ background: 'var(--accent)', color: 'white' }}>
            {selected.length}
          </span>
        )}
        <ChevronDown size={11} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            className="absolute top-full left-0 mt-1 z-20 min-w-44 rounded-md border shadow-xl py-1 max-h-64 overflow-y-auto"
            style={{ background: 'var(--bg-2)', borderColor: 'var(--border)' }}
          >
            {options.map(opt => (
              <label
                key={opt}
                className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-[var(--bg-3)] text-xs"
                style={{ color: 'var(--text)' }}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(opt)}
                  onChange={() => toggle(opt)}
                  className="accent-[var(--accent)] w-3 h-3"
                />
                {labelMap?.[opt] ?? opt}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function RangeFilter({
  label, min, max, step, valueMin, valueMax, onChangeMin, onChangeMax,
}: {
  label: string
  min: number; max: number; step: number
  valueMin: number | null; valueMax: number | null
  onChangeMin: (v: number | null) => void
  onChangeMax: (v: number | null) => void
}) {
  const [open, setOpen] = useState(false)
  const active = valueMin !== null || valueMax !== null

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border transition-colors',
          active
            ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent)]/10'
            : 'border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--bg-3)]',
        )}
      >
        {label}
        {active && <span className="text-2xs">({valueMin ?? min}–{valueMax ?? max})</span>}
        <ChevronDown size={11} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            className="absolute top-full left-0 mt-1 z-20 w-52 rounded-md border shadow-xl p-3"
            style={{ background: 'var(--bg-2)', borderColor: 'var(--border)' }}
          >
            <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>
              {label}
            </div>
            <div className="flex gap-2 items-center">
              <input
                type="number"
                className="input text-xs w-20"
                placeholder={String(min)}
                min={min} max={max} step={step}
                value={valueMin ?? ''}
                onChange={e => onChangeMin(e.target.value ? Number(e.target.value) : null)}
              />
              <span style={{ color: 'var(--text-muted)' }}>–</span>
              <input
                type="number"
                className="input text-xs w-20"
                placeholder={String(max)}
                min={min} max={max} step={step}
                value={valueMax ?? ''}
                onChange={e => onChangeMax(e.target.value ? Number(e.target.value) : null)}
              />
            </div>
            <button
              className="mt-2 text-2xs text-[var(--text-muted)] hover:text-[var(--text)]"
              onClick={() => { onChangeMin(null); onChangeMax(null) }}
            >
              Clear
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export default function FilterPanel({ filters, onChange }: Props) {
  const [keywords, setKeywords] = useState<string[]>([])

  useEffect(() => {
    fetchKeywords().then(setKeywords)
  }, [])

  const activeCount = [
    filters.sources.length,
    filters.categories.length,
    filters.keywords.length,
    filters.intensity_min !== null || filters.intensity_max !== null ? 1 : 0,
    filters.confidence_min !== null || filters.confidence_max !== null ? 1 : 0,
    filters.date_from ? 1 : 0,
    filters.date_to ? 1 : 0,
  ].reduce((a, b) => a + b, 0)

  const clearAll = () =>
    onChange({
      sources: [], categories: [], keywords: [],
      intensity_min: null, intensity_max: null,
      confidence_min: null, confidence_max: null,
      date_from: '', date_to: '',
    })

  return (
    <div
      className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b"
      style={{ borderColor: 'var(--border)', background: 'var(--bg)' }}
    >
      <span className="flex items-center gap-1 text-xs font-medium shrink-0" style={{ color: 'var(--text-muted)' }}>
        <Filter size={12} />
        Filters
      </span>

      {/* Date range */}
      <div className="flex items-center gap-1">
        <input
          type="date"
          className="input text-xs py-0.5 w-32"
          value={filters.date_from}
          onChange={e => onChange({ date_from: e.target.value })}
          placeholder="From"
        />
        <span style={{ color: 'var(--text-muted)' }} className="text-xs">–</span>
        <input
          type="date"
          className="input text-xs py-0.5 w-32"
          value={filters.date_to}
          onChange={e => onChange({ date_to: e.target.value })}
          placeholder="To"
        />
      </div>

      <div className="w-px h-4 shrink-0" style={{ background: 'var(--border)' }} />

      <MultiSelect
        label="Source"
        options={SOURCES}
        selected={filters.sources}
        onChange={v => onChange({ sources: v })}
        labelMap={SOURCE_LABELS}
      />
      <MultiSelect
        label="Category"
        options={CATEGORIES}
        selected={filters.categories}
        onChange={v => onChange({ categories: v })}
      />
      <MultiSelect
        label="Keyword"
        options={keywords}
        selected={filters.keywords}
        onChange={v => onChange({ keywords: v })}
      />
      <RangeFilter
        label="Intensity"
        min={1} max={5} step={1}
        valueMin={filters.intensity_min}
        valueMax={filters.intensity_max}
        onChangeMin={v => onChange({ intensity_min: v })}
        onChangeMax={v => onChange({ intensity_max: v })}
      />
      <RangeFilter
        label="Confidence"
        min={0} max={1} step={0.05}
        valueMin={filters.confidence_min}
        valueMax={filters.confidence_max}
        onChangeMin={v => onChange({ confidence_min: v })}
        onChangeMax={v => onChange({ confidence_max: v })}
      />

      {activeCount > 0 && (
        <button
          onClick={clearAll}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors"
          style={{ color: 'var(--text-muted)' }}
          title="Clear all filters"
        >
          <X size={11} />
          Clear ({activeCount})
        </button>
      )}
    </div>
  )
}
