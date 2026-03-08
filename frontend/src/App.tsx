import { useState, useEffect } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import Report from '@/pages/Report'
import Charts from '@/pages/Charts'
import Search from '@/pages/Search'
import WorkersLogs from '@/pages/WorkersLogs'

export type Page = 'report' | 'charts' | 'search' | 'logs'
export type Lang = 'en' | 'ru'

export default function App() {
  const [page, setPage] = useState<Page>('report')
  const [lang, setLang] = useState<Lang>(() =>
    (localStorage.getItem('lang') as Lang) || 'en'
  )
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem('theme')
    return stored ? stored === 'dark' : true
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => {
    localStorage.setItem('lang', lang)
  }, [lang])

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>
      <Sidebar page={page} setPage={setPage} dark={dark} setDark={setDark} lang={lang} setLang={setLang} />
      <main className="flex-1 overflow-auto">
        {page === 'report' && <Report lang={lang} />}
        {page === 'charts' && <Charts />}
        {page === 'search' && <Search lang={lang} />}
        {page === 'logs' && <WorkersLogs />}
      </main>
    </div>
  )
}
