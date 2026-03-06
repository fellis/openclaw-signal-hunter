import { useState, useEffect } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import Report from '@/pages/Report'
import Charts from '@/pages/Charts'
import Search from '@/pages/Search'

export type Page = 'report' | 'charts' | 'search'

export default function App() {
  const [page, setPage] = useState<Page>('report')
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem('theme')
    return stored ? stored === 'dark' : true
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }, [dark])

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>
      <Sidebar page={page} setPage={setPage} dark={dark} setDark={setDark} />
      <main className="flex-1 overflow-auto">
        {page === 'report' && <Report />}
        {page === 'charts' && <Charts />}
        {page === 'search' && <Search />}
      </main>
    </div>
  )
}
