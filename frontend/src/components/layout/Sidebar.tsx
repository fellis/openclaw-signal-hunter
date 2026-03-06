import { BarChart2, Search, Zap, Moon, Sun } from 'lucide-react'
import type { Page } from '@/App'
import { cn } from '@/lib/utils'

interface Props {
  page: Page
  setPage: (p: Page) => void
  dark: boolean
  setDark: (d: boolean) => void
}

const NAV = [
  { id: 'report' as Page, icon: Zap, label: 'Signals' },
  { id: 'charts' as Page, icon: BarChart2, label: 'Charts' },
  { id: 'search' as Page, icon: Search, label: 'Search' },
]

export default function Sidebar({ page, setPage, dark, setDark }: Props) {
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

      {/* Nav */}
      <nav className="flex-1 flex flex-col items-center gap-1 py-3">
        {NAV.map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => setPage(id)}
            title={label}
            className={cn(
              'w-9 h-9 rounded-md flex items-center justify-center transition-colors',
              page === id
                ? 'text-white'
                : 'text-[var(--text-muted)] hover:bg-[var(--bg-3)] hover:text-[var(--text)]',
            )}
            style={page === id ? { background: 'var(--accent)' } : {}}
          >
            <Icon size={16} />
          </button>
        ))}
      </nav>

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
