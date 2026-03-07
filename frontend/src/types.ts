export interface Rule {
  name: string
  description: string
  priority: number
}

export interface Category {
  name: string
  count: number
  rank_score: number      // sum of all signal rank_scores (primary sort key)
  avg_rank_score: number  // average per signal (shown in bar)
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
  rank_score: number      // sum of all signal rank_scores
  avg_rank_score: number  // average per signal (shown in bar)
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
  title_original: string
  url: string
  source: string
  author: string
  score: number
  comments_count: number
  views_count: number
  created_at: string | null
  collected_at: string | null
  summary: string | null
  summary_original: string | null
  translation_available: boolean
  rank_score: number
  intensity: number
  confidence: number
  language: string
  matched_rules: string[]
  keywords: string[]
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
  classified_by_embeddings: number
  classified_by_llm: number
  embedded_total: number
  pending_embeddings: number
  keywords_total: number
  keywords_run_24h: number
  new_signals_24h: number
  borderline_pending: number
  summarized_total: number
  summary_pending: number
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
  intensities: number[]
  confidence_min: number | null
  confidence_max: number | null
}
