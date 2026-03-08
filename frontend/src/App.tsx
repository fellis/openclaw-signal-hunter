import { useEffect, useState } from 'react'
import { Routes, Route, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import Sidebar from '@/components/layout/Sidebar'
import Report from '@/pages/Report'
import Charts from '@/pages/Charts'
import Search from '@/pages/Search'
import WorkersLogs from '@/pages/WorkersLogs'
import { pageFromPath, PAGE_PATHS } from '@/lib/urlParams'

export type Lang = 'en' | 'ru'

function useLangFromUrl(): [Lang, (l: Lang) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const lang = (searchParams.get('lang') === 'ru' ? 'ru' : 'en') as Lang

  useEffect(() => {
    localStorage.setItem('lang', lang)
  }, [lang])

  const setLang = (l: Lang) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (l === 'en') next.delete('lang')
      else next.set('lang', l)
      return next
    })
  }

  return [lang, setLang]
}

function useTheme() {
  const [dark, setDarkState] = useState(() => {
    const stored = localStorage.getItem('theme')
    return stored ? stored === 'dark' : true
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }, [dark])

  return [dark, setDarkState] as const
}

export default function App() {
  const location = useLocation()
  const page = pageFromPath(location.pathname)
  const [lang, setLang] = useLangFromUrl()
  const [dark, setDark] = useTheme()

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>
      <Sidebar page={page} dark={dark} setDark={setDark} lang={lang} setLang={setLang} />
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path={PAGE_PATHS.report} element={<Report lang={lang} />} />
          <Route path={PAGE_PATHS.charts} element={<Charts />} />
          <Route path={PAGE_PATHS.search} element={<Search lang={lang} />} />
          <Route path={PAGE_PATHS.logs} element={<WorkersLogs />} />
          <Route path="/" element={<Navigate to={PAGE_PATHS.report} replace />} />
          <Route path="*" element={<Navigate to={PAGE_PATHS.report} replace />} />
        </Routes>
      </main>
    </div>
  )
}
