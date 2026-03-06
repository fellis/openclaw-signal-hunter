export interface Category {
  name: string
  count: number
  avg_rank_score: number
  avg_intensity: number
  avg_confidence: number
  avg_score: number
  avg_comments: number
  last_signal_at: string | null
  sources_breakdown: Record<string, number>
  signal_ids: string[]
}

export interface Cluster {
  id: number
  name: string
  count: number
  avg_rank_score: number
  avg_intensity: number
  avg_confidence: number
  avg_score: number
  avg_comments: number
  last_signal_at: string | null
  sources_breakdown: Record<string, number>
  signal_ids: string[]
}

export interface Signal {
  raw_signal_id: string
  title: string
  url: string
  source: string
  author: string
  score: number
  comments_count: number
  views_count: number
  created_at: string | null
  collected_at: string | null
  summary: string | null
  rank_score: number
  intensity: number
  confidence: number
  language: string
  matched_rules: string[]
}

export interface SearchResult extends Signal {
  similarity?: number
  combined_score?: number
  query?: string
}

export interface ReportResponse {
  total_signals: number
  categories: Category[]
}

export interface ClustersResponse {
  clusters: Cluster[]
}

export interface SignalsResponse {
  signals: Signal[]
}

export interface StatsResponse {
  raw_total: number
  relevant_total: number
  irrelevant_total: number
  processed_total: number
  unprocessed: number
  embedded_total: number
  pending_embeddings: number
  keywords_total: number
  avg_rank_score: number
}

export interface TimelinePoint {
  day: string
  source_type: string
  count: number
  avg_rank_score: number
}

export interface SourceBreakpoint {
  source_type: string
  count: number
  avg_rank_score: number
  avg_intensity: number
}

export interface Filters {
  date_from: string
  date_to: string
  sources: string[]
  categories: string[]
  keywords: string[]
  intensity_min: number | null
  intensity_max: number | null
  confidence_min: number | null
  confidence_max: number | null
  languages: string[]
}
