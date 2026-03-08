/**
 * Serialize/parse URL search params for pages and filters.
 * All pages use ?lang=ru. Report uses filter params, Search uses q & mode, Logs use worker & level.
 */

import type { Filters } from '@/types'

const ARRAY_SEP = ','

export function filtersFromSearchParams(params: URLSearchParams): Filters {
  const arr = (key: string) => {
    const v = params.get(key)
    return v ? v.split(ARRAY_SEP).filter(Boolean) : []
  }
  const num = (key: string): number | null => {
    const v = params.get(key)
    if (v === '' || v === null) return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  const ints = arr('intensities').map(Number).filter(Number.isFinite)
  return {
    date_from: params.get('date_from') ?? '',
    date_to: params.get('date_to') ?? '',
    sources: arr('sources'),
    categories: arr('categories'),
    keywords: arr('keywords'),
    intensities: ints,
    confidence_min: num('confidence_min'),
    confidence_max: num('confidence_max'),
  }
}

export function filtersToSearchParams(f: Filters): URLSearchParams {
  const p = new URLSearchParams()
  if (f.date_from) p.set('date_from', f.date_from)
  if (f.date_to) p.set('date_to', f.date_to)
  if (f.sources.length) p.set('sources', f.sources.join(ARRAY_SEP))
  if (f.categories.length) p.set('categories', f.categories.join(ARRAY_SEP))
  if (f.keywords.length) p.set('keywords', f.keywords.join(ARRAY_SEP))
  if (f.intensities.length) p.set('intensities', f.intensities.join(ARRAY_SEP))
  if (f.confidence_min != null) p.set('confidence_min', String(f.confidence_min))
  if (f.confidence_max != null) p.set('confidence_max', String(f.confidence_max))
  return p
}

export type Page = 'report' | 'charts' | 'search' | 'logs'

export const PAGE_PATHS: Record<Page, string> = {
  report: '/report',
  charts: '/charts',
  search: '/search',
  logs: '/logs',
}

export function pageFromPath(pathname: string): Page {
  if (pathname.startsWith('/report')) return 'report'
  if (pathname.startsWith('/charts')) return 'charts'
  if (pathname.startsWith('/search')) return 'search'
  if (pathname.startsWith('/logs')) return 'logs'
  return 'report'
}
