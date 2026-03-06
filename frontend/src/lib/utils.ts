import { clsx, type ClassValue } from 'clsx'

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs)
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function formatRelative(iso: string | null): string {
  if (!iso) return '—'
  const now = Date.now()
  const then = new Date(iso).getTime()
  const diff = now - then
  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  if (mins < 60) return `${mins}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 30) return `${days}d ago`
  return formatDate(iso)
}

export const SOURCE_LABELS: Record<string, string> = {
  github_issue: 'GH Issue',
  github_discussion: 'GH Discussion',
  hn_post: 'HN',
  so_question: 'SO',
  reddit_post: 'Reddit',
  reddit_comment: 'Reddit',
  hf_discussion: 'HF',
  hf_paper: 'HF Paper',
}

export const SOURCE_COLORS: Record<string, string> = {
  github_issue: '#6e40c9',
  github_discussion: '#8957e5',
  hn_post: '#ff6600',
  so_question: '#f48024',
  reddit_post: '#ff4500',
  reddit_comment: '#ff4500',
  hf_discussion: '#ffbd59',
  hf_paper: '#ffd700',
}

export const CATEGORY_COLORS: Record<string, string> = {
  pain_point: '#ef4444',
  pain_point_ai_agent: '#ef4444',
  feature_request: '#3b82f6',
  feature_request_ai_agent: '#3b82f6',
  adoption_signal: '#22c55e',
  adoption_ai_agent: '#22c55e',
  comparison: '#a855f7',
  comparison_ai_agent: '#a855f7',
  migration: '#f59e0b',
  breaking_change: '#f97316',
  new_release: '#06b6d4',
  security_ai_agent: '#f43f5e',
  cost_ai_agent: '#eab308',
  integration_ai_agent: '#14b8a6',
  customization_ai_agent: '#8b5cf6',
  uncategorized: '#6b7280',
}

const _FALLBACK_PALETTE = [
  '#6366f1', '#ec4899', '#10b981', '#f59e0b', '#3b82f6',
  '#ef4444', '#8b5cf6', '#06b6d4', '#84cc16', '#f97316',
]

function _strHash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

export function getCategoryColor(name: string): string {
  return CATEGORY_COLORS[name] ?? _FALLBACK_PALETTE[_strHash(name) % _FALLBACK_PALETTE.length]
}

export function formatCategoryName(name: string): string {
  return name.replace(/_ai_agent$/i, '').replace(/_/g, ' ')
}

export function intensityLabel(v: number): string {
  return ['', 'Minimal', 'Low', 'Medium', 'High', 'Critical'][Math.round(v)] ?? String(v)
}

export function buildQueryString(params: Record<string, unknown>): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    if (Array.isArray(v)) {
      v.forEach(item => qs.append(k, String(item)))
    } else {
      qs.set(k, String(v))
    }
  }
  return qs.toString()
}
