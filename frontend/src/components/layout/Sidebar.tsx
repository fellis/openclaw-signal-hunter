import { NavLink } from 'react-router-dom'
import { BarChart2, Search, Zap, Moon, Sun, ScrollText } from 'lucide-react'
import type { Lang } from '@/App'
import { PAGE_PATHS, type Page } from '@/lib/urlParams'
import { cn } from '@/lib/utils'

interface Props {
  page: Page
  dark: boolean
  setDark: (d: boolean) => void
  lang: Lang
  setLang: (l: Lang) => void
}

const NAV = [
  { id: 'report' as Page, path: PAGE_PATHS.report, icon: Zap, label: 'Signals' },
  { id: 'charts' as Page, path: PAGE_PATHS.charts, icon: BarChart2, label: 'Charts' },
  { id: 'search' as Page, path: PAGE_PATHS.search, icon: Search, label: 'Search' },
  { id: 'logs' as Page, path: PAGE_PATHS.logs, icon: ScrollText, label: 'Logs' },
]

export default function Sidebar({ page, dark, setDark, lang, setLang }: Props) {
  return (
    <aside
      className="flex flex-col w-14 shrink-0 border-r"
      style={{ background: 'var(--bg-2)', borderColor: 'var(--border)' }}
    >
      {/* Logo */}
      <div className="flex items-center justify-center h-12 shrink-0 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="w-6 h-6 rounded-md flex items-center justify-center" style={{ background: 'var(--accent)' }}>
          <Zap size={13} className="text-white" />
        </div>
      </div>

      {/* Nav - use NavLink to preserve search params (e.g. lang) */}
      <nav className="flex-1 flex flex-col items-center gap-1 py-3">
        {NAV.map(({ id, path, icon: Icon, label }) => (
          <NavLink
            key={id}
            to={path}
            title={label}
            className={({ isActive }) =>
              cn(
                'w-9 h-9 rounded-md flex items-center justify-center transition-colors',
                isActive ? 'text-white' : 'text-[var(--text-muted)] hover:bg-[var(--bg-3)] hover:text-[var(--text)]',
              )
            }
            style={({ isActive }) => (isActive ? { background: 'var(--accent)' } : {})}
          >
            <Icon size={16} />
          </NavLink>
        ))}
      </nav>

      {/* Language toggle EN / RU */}
      <div className="flex flex-col items-center gap-1 pb-2">
        {(['en', 'ru'] as Lang[]).map((l) => (
          <button
            key={l}
            onClick={() => setLang(l)}
            title={l === 'en' ? 'English' : 'Русский'}
            className={cn(
              'w-9 h-7 rounded text-[11px] font-semibold transition-colors',
              lang === l
                ? 'text-white'
                : 'text-[var(--text-muted)] hover:bg-[var(--bg-3)] hover:text-[var(--text)]',
            )}
            style={lang === l ? { background: 'var(--accent)' } : {}}
          >
            {l.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Theme toggle */}
      <div className="flex items-center justify-center pb-4">
        <button
          onClick={() => setDark(!dark)}
          title={dark ? 'Light mode' : 'Dark mode'}
          className="w-9 h-9 rounded-md flex items-center justify-center text-[var(--text-muted)] hover:bg-[var(--bg-3)] hover:text-[var(--text)] transition-colors"
        >
          {dark ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
    </aside>
  )
}
