import type { SearchResult, Filters } from '@/types'
import { buildQueryString } from '@/lib/utils'

export async function semanticSearch(
  q: string,
  filters: Partial<Filters> = {},
  topK = 50,
  threshold = 0.45,
  lang = 'en',
): Promise<{ results: SearchResult[]; total: number; query: string }> {
  const clamp01 = (v: number | null | undefined) =>
    v == null ? '' : Math.max(0, Math.min(1, v))
  const qs = buildQueryString({
    q,
    top_k: topK,
    threshold,
    sources: filters.sources || [],
    keywords: filters.keywords || [],
    intensities: filters.intensities || [],
    confidence_min: clamp01(filters.confidence_min),
    confidence_max: clamp01(filters.confidence_max),
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    lang,
  })
  const res = await fetch(`/api/search/semantic?${qs}`)
  if (!res.ok) throw new Error(`Semantic search failed: ${res.status}`)
  return res.json()
}

export async function textSearch(
  q: string,
  filters: Partial<Filters> = {},
  limit = 50,
  lang = 'en',
): Promise<{ results: SearchResult[]; total: number; query: string }> {
  const qs = buildQueryString({
    q,
    limit,
    sources: filters.sources || [],
    keywords: filters.keywords || [],
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    lang,
  })
  const res = await fetch(`/api/search/text?${qs}`)
  if (!res.ok) throw new Error(`Text search failed: ${res.status}`)
  return res.json()
}
