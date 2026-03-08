import { useState, useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search as SearchIcon, Loader2, ExternalLink, Zap, AlignLeft } from 'lucide-react'
import { semanticSearch, textSearch } from '@/api/search'
import type { SearchResult } from '@/types'
import { SOURCE_LABELS, SOURCE_COLORS, formatRelative, intensityLabel } from '@/lib/utils'
import { cn } from '@/lib/utils'

type Mode = 'semantic' | 'text'

function ResultRow({ result, mode }: { result: SearchResult; mode: Mode }) {
  return (
    <div
      className="px-4 py-3 border-b hover:bg-[var(--bg-2)] transition-colors"
      style={{ borderColor: 'var(--border)' }}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span
              className="badge"
              style={{
                background: `${SOURCE_COLORS[result.source] ?? '#6b7280'}20`,
                color: SOURCE_COLORS[result.source] ?? '#6b7280',
              }}
            >
              {SOURCE_LABELS[result.source] ?? result.source}
            </span>
            {mode === 'semantic' && result.similarity !== undefined && (
              <span className="text-2xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
                sim {(result.similarity * 100).toFixed(1)}%
              </span>
            )}
            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
              intensity: {intensityLabel(result.intensity)}
            </span>
            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
              rank {result.rank_score?.toFixed(2)}
            </span>
          </div>

          <a
            href={result.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 font-medium hover:underline mb-1"
            style={{ color: 'var(--text)', fontSize: 13 }}
          >
            {result.title}
            <ExternalLink size={11} className="opacity-40 shrink-0" />
          </a>

          {result.summary && (
            <p className="text-xs leading-relaxed line-clamp-3" style={{ color: 'var(--text-muted)' }}>
              {result.summary}
            </p>
          )}
        </div>

        <div className="shrink-0 flex flex-col gap-0.5 items-end" style={{ minWidth: 160 }}>
          <div className="flex gap-3">
            <div className="flex flex-col items-end">
              <span className="text-2xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>Published</span>
              <span className="text-xs tabular-nums" style={{ color: 'var(--text-2)' }}>{formatRelative(result.created_at)}</span>
            </div>
            <div className="flex flex-col items-end">
              <span className="text-2xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>Collected</span>
              <span className="text-xs tabular-nums" style={{ color: 'var(--accent)' }}>{formatRelative(result.collected_at)}</span>
            </div>
          </div>
          <div className="text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
            ↑{result.score} · {result.comments_count}💬
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Search({ lang = 'en' }: { lang?: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const qFromUrl = searchParams.get('q') ?? ''
  const modeFromUrl = (searchParams.get('mode') === 'text' ? 'text' : 'semantic') as Mode

  const [query, setQuery] = useState(qFromUrl)
  const [mode, setMode] = useState<Mode>(modeFromUrl)
  const [results, setResults] = useState<SearchResult[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  useEffect(() => {
    setQuery(qFromUrl)
    setMode(modeFromUrl)
  }, [qFromUrl, modeFromUrl])

  const updateUrl = useCallback((q: string, m: Mode) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (q.trim()) next.set('q', q.trim())
      else next.delete('q')
      if (m === 'text') next.set('mode', 'text')
      else next.delete('mode')
      return next
    }, { replace: true })
  }, [setSearchParams])

  const search = useCallback(async () => {
    if (!query.trim()) return
    updateUrl(query, mode)
    setLoading(true)
    setError(null)
    setSearched(true)
    try {
      const data = mode === 'semantic'
        ? await semanticSearch(query.trim(), {}, 50, 0.45, lang)
        : await textSearch(query.trim(), {}, 50, lang)
      setResults(data.results)
      setTotal(data.total)
    } catch (e) {
      setError(String(e))
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [query, mode, lang, updateUrl])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') search()
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <h1 className="text-sm font-semibold mb-3" style={{ color: 'var(--text)' }}>Search</h1>

        {/* Mode toggle + search bar */}
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border overflow-hidden shrink-0" style={{ borderColor: 'var(--border)' }}>
            <button
              onClick={() => { setMode('semantic'); updateUrl(query, 'semantic') }}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
                mode === 'semantic' ? 'text-white' : 'text-[var(--text-muted)] hover:bg-[var(--bg-3)]',
              )}
              style={mode === 'semantic' ? { background: 'var(--accent)' } : {}}
            >
              <Zap size={11} />
              Semantic
            </button>
            <button
              onClick={() => { setMode('text'); updateUrl(query, 'text') }}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors border-l',
                mode === 'text' ? 'text-white' : 'text-[var(--text-muted)] hover:bg-[var(--bg-3)]',
              )}
              style={{
                ...(mode === 'text' ? { background: 'var(--accent)' } : {}),
                borderColor: 'var(--border)',
              }}
            >
              <AlignLeft size={11} />
              Text
            </button>
          </div>

          <div className="flex-1 flex items-center gap-2">
            <div className="relative flex-1">
              <SearchIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40" style={{ color: 'var(--text-muted)' }} />
              <input
                className="input pl-9 pr-4"
                placeholder={
                  mode === 'semantic'
                    ? 'Ask anything: "production LLM memory issues"…'
                    : 'Search by title or body keywords…'
                }
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={handleKey}
                autoFocus
              />
            </div>
            <button onClick={search} disabled={loading || !query.trim()} className="btn btn-primary shrink-0">
              {loading ? <Loader2 size={13} className="animate-spin" /> : <SearchIcon size={13} />}
              Search
            </button>
          </div>
        </div>

        {mode === 'semantic' && (
          <p className="mt-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
            Semantic search uses vector similarity. Works best with natural language questions.
          </p>
        )}
      </div>

      {/* Results */}
      <div className="flex-1 overflow-auto">
        {error && (
          <div className="m-4 p-3 rounded-md border text-xs" style={{ borderColor: '#ef4444', color: '#ef4444', background: '#ef444410' }}>
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center h-48 gap-2" style={{ color: 'var(--text-muted)' }}>
            <Loader2 size={16} className="animate-spin" />
            <span className="text-sm">Searching…</span>
          </div>
        ) : searched && results.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-sm" style={{ color: 'var(--text-muted)' }}>
            No results for "{query}"
          </div>
        ) : results.length > 0 ? (
          <>
            <div className="px-4 py-2 text-xs border-b" style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
              {total} results for "{query}"
            </div>
            {results.map(r => (
              <ResultRow key={r.raw_signal_id} result={r} mode={mode} />
            ))}
          </>
        ) : !searched ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3" style={{ color: 'var(--text-muted)' }}>
            <SearchIcon size={32} strokeWidth={1} />
            <p className="text-sm">Start typing to search signals</p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
