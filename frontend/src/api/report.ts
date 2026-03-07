import type {
  ReportResponse,
  ClustersResponse,
  SignalsResponse,
  StatsResponse,
  TimelinePoint,
  SourceBreakpoint,
  Filters,
  Rule,
} from '@/types'
import { buildQueryString } from '@/lib/utils'

function filtersToParams(f: Partial<Filters>): Record<string, unknown> {
  return {
    date_from: f.date_from || '',
    date_to: f.date_to || '',
    sources: f.sources || [],
    categories: f.categories || [],
    keywords: f.keywords || [],
    intensities: f.intensities || [],
    confidence_min: f.confidence_min ?? '',
    confidence_max: f.confidence_max ?? '',
  }
}

export async function fetchReport(
  filters: Partial<Filters> = {},
  sortBy = 'avg_rank_score',
  sortDir = 'desc',
): Promise<ReportResponse> {
  const qs = buildQueryString({ ...filtersToParams(filters), sort_by: sortBy, sort_dir: sortDir })
  const res = await fetch(`/api/report?${qs}`)
  if (!res.ok) throw new Error(`Report fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchClusters(
  category: string,
  filters: Partial<Filters> = {},
): Promise<ClustersResponse> {
  const qs = buildQueryString({ ...filtersToParams(filters), category })
  const res = await fetch(`/api/report/clusters?${qs}`)
  if (!res.ok) throw new Error(`Clusters fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchSignals(
  ids: string[],
  sortBy = 'rank_score',
  sortDir = 'desc',
  lang = 'en',
): Promise<SignalsResponse> {
  const qs = buildQueryString({ ids, sort_by: sortBy, sort_dir: sortDir, lang })
  const res = await fetch(`/api/report/signals?${qs}`)
  if (!res.ok) throw new Error(`Signals fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchKeywords(): Promise<string[]> {
  const res = await fetch('/api/keywords')
  if (!res.ok) return []
  const data = await res.json()
  return data.keywords || []
}

export async function fetchRules(): Promise<Rule[]> {
  const res = await fetch('/api/rules')
  if (!res.ok) return []
  const data = await res.json()
  return data.rules || []
}

export async function fetchCategoryCounts(
  filters: Partial<Filters>,
): Promise<Record<string, number>> {
  const qs = buildQueryString({
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    sources: filters.sources || [],
    keywords: filters.keywords || [],
    intensities: filters.intensities || [],
    confidence_min: filters.confidence_min ?? '',
    confidence_max: filters.confidence_max ?? '',
  })
  const res = await fetch(`/api/categories/counts?${qs}`)
  if (!res.ok) return {}
  const data = await res.json()
  return Object.fromEntries((data.counts || []).map((c: { name: string; count: number }) => [c.name, c.count]))
}

export async function fetchSourceCounts(
  filters: Partial<Filters>,
): Promise<Record<string, number>> {
  const qs = buildQueryString({
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    categories: filters.categories || [],
    keywords: filters.keywords || [],
    intensities: filters.intensities || [],
    confidence_min: filters.confidence_min ?? '',
    confidence_max: filters.confidence_max ?? '',
  })
  const res = await fetch(`/api/sources/counts?${qs}`)
  if (!res.ok) return {}
  const data = await res.json()
  return Object.fromEntries((data.counts || []).map((c: { name: string; count: number }) => [c.name, c.count]))
}

export async function fetchKeywordCounts(
  filters: Partial<Filters>,
): Promise<Record<string, number>> {
  const qs = buildQueryString({
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    sources: filters.sources || [],
    categories: filters.categories || [],
    intensities: filters.intensities || [],
    confidence_min: filters.confidence_min ?? '',
    confidence_max: filters.confidence_max ?? '',
  })
  const res = await fetch(`/api/keywords/counts?${qs}`)
  if (!res.ok) return {}
  const data = await res.json()
  return Object.fromEntries((data.counts || []).map((c: { name: string; count: number }) => [c.name, c.count]))
}

export async function fetchStats(): Promise<StatsResponse> {
  const res = await fetch('/api/stats')
  if (!res.ok) throw new Error('Stats fetch failed')
  return res.json()
}

export async function fetchTimeline(
  days = 30,
  filters: Partial<Filters> = {},
): Promise<TimelinePoint[]> {
  const qs = buildQueryString({
    days,
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    sources: filters.sources || [],
    keywords: filters.keywords || [],
  })
  const res = await fetch(`/api/charts/timeline?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.data || []
}

export async function fetchSourcesBreakdown(
  filters: Partial<Filters> = {},
): Promise<SourceBreakpoint[]> {
  const qs = buildQueryString({
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    keywords: filters.keywords || [],
  })
  const res = await fetch(`/api/charts/sources?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.data || []
}

export async function fetchCategoriesBreakdown(
  filters: Partial<Filters> = {},
) {
  const qs = buildQueryString({
    date_from: filters.date_from || '',
    date_to: filters.date_to || '',
    keywords: filters.keywords || [],
  })
  const res = await fetch(`/api/charts/categories?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.data || []
}
