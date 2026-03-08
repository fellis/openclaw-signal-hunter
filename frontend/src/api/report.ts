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
  searchQuery?: string,
  searchMode?: 'semantic' | 'text',
): Promise<ReportResponse> {
  const params: Record<string, unknown> = { ...filtersToParams(filters), sort_by: sortBy, sort_dir: sortDir }
  if (searchQuery?.trim()) params.q = searchQuery.trim()
  if (searchMode === 'text' || searchMode === 'semantic') params.search_mode = searchMode
  const qs = buildQueryString(params)
  const res = await fetch(`/api/report?${qs}`)
  if (!res.ok) throw new Error(`Report fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchClusters(
  category: string,
  filters: Partial<Filters> = {},
  searchQuery?: string,
  searchMode?: 'semantic' | 'text',
): Promise<ClustersResponse> {
  const params: Record<string, unknown> = { ...filtersToParams(filters), category }
  if (searchQuery?.trim()) params.q = searchQuery.trim()
  if (searchMode === 'text' || searchMode === 'semantic') params.search_mode = searchMode
  const qs = buildQueryString(params)
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

export interface FilterCounts {
  sources: Record<string, number>
  categories: Record<string, number>
  keywords: Record<string, number>
  intensities: Record<string, number>
}

export async function fetchFilterCounts(
  filters: Partial<Filters>,
): Promise<FilterCounts> {
  const empty: FilterCounts = { sources: {}, categories: {}, keywords: {}, intensities: {} }
  const qs = buildQueryString(filtersToParams(filters))
  const res = await fetch(`/api/filter-counts?${qs}`)
  if (!res.ok) return empty
  return res.json()
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
