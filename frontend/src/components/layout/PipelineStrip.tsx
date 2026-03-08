import { useCallback, useEffect, useRef, useState } from 'react'
import type { StatsResponse } from '@/types'
import { fetchKeywordsStatus, recollectKeywords, type KeywordStatus } from '@/api/report'

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
            width: 18,
            height: 18,
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
      <div
        className="text-base font-semibold tabular-nums leading-tight"
        style={{ color: alert ? '#f97316' : 'var(--text)', transition: 'opacity 0.2s ease' }}
      >
        {value}
      </div>
      <div className="text-2xs leading-tight" style={{ color: 'var(--text-muted)', minHeight: 14 }}>
        {sub}
      </div>
      {pct !== undefined && (
        <div className="mt-1 rounded-full overflow-hidden" style={{ height: 3, background: 'var(--border)' }}>
          <div
            style={{
              width: `${Math.min(pct, 100)}%`,
              height: '100%',
              background: barColor,
              borderRadius: 9999,
              transition: 'width 0.4s ease',
            }}
          />
        </div>
      )}
    </div>
  )
}

function PipelineArrow() {
  return (
    <div className="flex items-center shrink-0 self-start mt-5" style={{ color: 'var(--text-muted)' }}>
      <svg width="16" height="10" viewBox="0 0 16 10" fill="none">
        <path
          d="M0 5h13M9 1l5 4-5 4"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  )
}

interface PipelineStageClassifyProps {
  index: number
  rawTotal: number
  classifiedByEmbeddings: number
  classifiedByLlm: number
  unprocessed: number
  borderlinePending: number
}

function PipelineStageClassify({
  index,
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
            width: 18,
            height: 18,
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
        {(classifiedByEmbeddings + classifiedByLlm - borderlinePending).toLocaleString()} /{' '}
        {rawTotal.toLocaleString()}
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

interface PipelineStripProps {
  stats: StatsResponse | null
  totalSignals: number
}

function hoursUntilNextCollect(lastCollectedAt: string | null): string {
  if (!lastCollectedAt) return 'ещё не собирался'
  const last = new Date(lastCollectedAt).getTime()
  const now = Date.now()
  const hoursSince = (now - last) / (1000 * 60 * 60)
  const hoursLeft = Math.max(0, 24 - hoursSince)
  if (hoursLeft <= 0) return 'через 0 ч'
  return `через ${Math.round(hoursLeft)} ч`
}

export default function PipelineStrip({ stats, totalSignals }: PipelineStripProps) {
  const [recollectOpen, setRecollectOpen] = useState(false)
  const [keywordsStatus, setKeywordsStatus] = useState<KeywordStatus[]>([])
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const recollectButtonRef = useRef<HTMLButtonElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)

  const loadStatus = useCallback(() => {
    fetchKeywordsStatus().then(setKeywordsStatus).catch(() => setKeywordsStatus([]))
  }, [])

  useEffect(() => {
    if (recollectOpen) {
      loadStatus()
      setSearch('')
      setSelected(new Set())
    }
  }, [recollectOpen, loadStatus])

  useEffect(() => {
    if (recollectOpen) {
      const t = requestAnimationFrame(() => {
        modalRef.current?.focus()
      })
      return () => cancelAnimationFrame(t)
    }
    recollectButtonRef.current?.focus()
  }, [recollectOpen])

  const filtered = keywordsStatus.filter((k) =>
    k.name.toLowerCase().includes(search.toLowerCase().trim()),
  )
  const selectable = filtered.filter((k) => !k.in_queue && !k.in_progress)
  const allSelectableChecked =
    selectable.length > 0 && selectable.every((k) => selected.has(k.name))

  const toggleSelect = (name: string) => {
    const kw = keywordsStatus.find((k) => k.name === name)
    if (kw?.in_queue || kw?.in_progress) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (allSelectableChecked) {
      setSelected((prev) => {
        const next = new Set(prev)
        selectable.forEach((k) => next.delete(k.name))
        return next
      })
    } else {
      setSelected((prev) => {
        const next = new Set(prev)
        selectable.forEach((k) => next.add(k.name))
        return next
      })
    }
  }

  const closeRecollectModal = useCallback(() => {
    if (!loading) {
      setRecollectOpen(false)
      recollectButtonRef.current?.focus()
    }
  }, [loading])

  const onRecollect = () => {
    const list = Array.from(selected)
    if (!list.length) return
    setLoading(true)
    recollectKeywords(list)
      .then((r) => {
        setMessage(r.message || `Recollect queued for ${r.keywords?.length ?? 0} keyword(s)`)
        setRecollectOpen(false)
        recollectButtonRef.current?.focus()
        setTimeout(() => setMessage(null), 4000)
      })
      .catch((e) => {
        setMessage(e?.message || 'Recollect failed')
        setTimeout(() => setMessage(null), 4000)
      })
      .finally(() => setLoading(false))
  }

  if (!stats) return null

  return (
    <>
      <div
        className="flex items-start gap-2 px-4 py-3 border-b shrink-0 overflow-x-auto"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-1, var(--bg))' }}
      >
      <div className="flex flex-col gap-1 min-w-[130px]" style={{ flex: '1 1 130px' }}>
        <div className="flex items-center gap-1.5 mb-0.5">
          <span
            className="text-2xs font-semibold rounded-full flex items-center justify-center shrink-0"
            style={{
              width: 18,
              height: 18,
              background: 'var(--bg-3, #1e1e2e)',
              color: 'var(--text-muted)',
              border: '1px solid var(--border)',
            }}
          >
            1
          </span>
          <span className="text-2xs uppercase tracking-wider font-medium" style={{ color: 'var(--text-muted)' }}>
            Keywords
          </span>
          <button
            ref={recollectButtonRef}
            type="button"
            onClick={() => setRecollectOpen(true)}
            title="Recollect"
            className="ml-1 p-0.5 rounded hover:bg-black/10 dark:hover:bg-white/10"
            style={{ color: 'var(--text-muted)' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
              <path d="M16 21h5v-5" />
            </svg>
          </button>
        </div>
        <div className="text-base font-semibold tabular-nums leading-tight" style={{ color: 'var(--text)' }}>
          {stats.keywords_run_24h ?? 0} / {stats.keywords_total ?? 0}
        </div>
        <div className="text-2xs leading-tight" style={{ color: 'var(--text-muted)', minHeight: 14 }}>
          обработано за 24ч
        </div>
        {stats.keywords_total ? (
          <div className="mt-1 rounded-full overflow-hidden" style={{ height: 3, background: 'var(--border)' }}>
            <div
              style={{
                width: `${Math.min((stats.keywords_run_24h ?? 0) / stats.keywords_total * 100, 100)}%`,
                height: '100%',
                background: 'var(--accent, #6366f1)',
                borderRadius: 9999,
                transition: 'width 0.4s ease',
              }}
            />
          </div>
        ) : null}
      </div>
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
        value={`${stats.summarized_total?.toLocaleString() ?? 0} / ${totalSignals.toLocaleString()}`}
        sub="с summary"
        pct={totalSignals > 0 ? ((stats.summarized_total ?? 0) / totalSignals) * 100 : 0}
      />
      <PipelineArrow />
      <PipelineStage
        index={5}
        label="Vectorize"
        value={`${stats.embedded_total?.toLocaleString() ?? 0} / ${totalSignals.toLocaleString()}`}
        sub="в Qdrant"
        pct={totalSignals > 0 ? ((stats.embedded_total ?? 0) / totalSignals) * 100 : 0}
      />
    </div>

      {recollectOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.5)' }}
          onClick={closeRecollectModal}
          onKeyDown={(e) => e.key === 'Escape' && closeRecollectModal()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="recollect-modal-title"
        >
          <div
            ref={modalRef}
            tabIndex={-1}
            className="rounded-lg shadow-xl max-h-[85vh] flex flex-col w-full max-w-md"
            style={{ background: 'var(--bg-1, #1e1e2e)', border: '1px solid var(--border)' }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === 'Escape' && closeRecollectModal()}
          >
            <div className="px-4 py-3 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
              <h2 id="recollect-modal-title" className="text-sm font-semibold" style={{ color: 'var(--text)' }}>Recollect</h2>
            </div>
            <div className="p-4 flex flex-col gap-3 overflow-hidden flex-1 min-h-0">
              <input
                type="text"
                placeholder="Search keywords..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full px-3 py-2 rounded text-sm"
                style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--text)' }}
              />
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="recollect-select-all"
                  checked={allSelectableChecked}
                  onChange={toggleSelectAll}
                  disabled={selectable.length === 0}
                  className="rounded"
                />
                <label htmlFor="recollect-select-all" className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                  Select all ({selectable.length} selectable)
                </label>
              </div>
              <div className="overflow-y-auto flex-1 min-h-0 border rounded py-1" style={{ borderColor: 'var(--border)', maxHeight: 280 }}>
                {filtered.length === 0 ? (
                  <div className="px-3 py-2 text-2xs" style={{ color: 'var(--text-muted)' }}>No keywords</div>
                ) : (
                  filtered.map((kw) => {
                    const disabled = kw.in_queue || kw.in_progress
                    const statusText = kw.in_progress
                      ? 'Сбор идёт'
                      : kw.in_queue
                        ? 'В очереди'
                        : hoursUntilNextCollect(kw.last_collected_at)
                    return (
                      <label
                        key={kw.name}
                        className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer ${disabled ? 'opacity-60' : ''}`}
                        style={{ color: disabled ? 'var(--text-muted)' : 'var(--text)' }}
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(kw.name)}
                          onChange={() => toggleSelect(kw.name)}
                          disabled={disabled}
                          className="rounded shrink-0"
                        />
                        <span className="truncate flex-1">{kw.name}</span>
                        <span className="text-2xs shrink-0" style={{ color: 'var(--text-muted)' }}>{statusText}</span>
                      </label>
                    )
                  })
                )}
              </div>
            </div>
            <div className="px-4 py-3 border-t flex justify-end gap-2 shrink-0" style={{ borderColor: 'var(--border)' }}>
              <button
                type="button"
                onClick={closeRecollectModal}
                disabled={loading}
                className="px-3 py-1.5 rounded text-sm"
                style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--text)' }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onRecollect}
                disabled={loading || selected.size === 0}
                className="px-3 py-1.5 rounded text-sm font-medium"
                style={{ background: 'var(--accent, #6366f1)', color: 'white' }}
              >
                {loading ? '...' : 'Recollect'}
              </button>
            </div>
          </div>
        </div>
      )}

      {message && (
        <div
          className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[60] px-4 py-2 rounded shadow-lg text-sm"
          style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--text)' }}
        >
          {message}
        </div>
      )}
    </>
  )
}
