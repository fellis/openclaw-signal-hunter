import { useMemo } from 'react'
import PageHeader from '@/components/layout/PageHeader'
import type { Lang } from '@/App'

type Part = { type: 'p'; text: string } | { type: 'ul'; items: string[] }

interface SectionContent {
  title: { en: string; ru: string }
  parts: { en: Part[]; ru: Part[] }
}

const SECTIONS: SectionContent[] = [
  {
    title: { en: 'What is Signal Hunter?', ru: 'Что такое Signal Hunter?' },
    parts: {
      en: [
        {
          type: 'p',
          text: 'Signal Hunter is a market intelligence tool for AI/ML builders. It monitors GitHub, Hugging Face, Hacker News, Stack Overflow, and Reddit for signals such as developer pain points, feature requests, bug reports, and tool comparisons. You add keywords (e.g. "RAG", "ollama", "LangChain"); the system discovers where each topic is discussed, collects posts and issues automatically, classifies them by type of signal, and lets you browse and search everything in this web report. Management (adding keywords, running workers, querying in natural language) is done via the OpenClaw chat interface; this UI is for viewing and filtering the collected signals.',
        },
      ],
      ru: [
        {
          type: 'p',
          text: 'Signal Hunter — инструмент рыночной разведки для разработчиков в области AI/ML. Он отслеживает GitHub, Hugging Face, Hacker News, Stack Overflow и Reddit и собирает сигналы: боли разработчиков, запросы функций, баг-репорты, сравнения инструментов. Вы добавляете ключевые слова (например, "RAG", "ollama", "LangChain"); система находит, где обсуждается тема, автоматически собирает посты и тикеты, классифицирует их по типу сигнала и даёт просматривать и искать всё в этом веб-отчёте. Управление (добавление ключевых слов, запуск воркеров, запросы на естественном языке) выполняется через чат OpenClaw; этот интерфейс предназначен для просмотра и фильтрации собранных сигналов.',
        },
      ],
    },
  },
  {
    title: { en: 'How it works', ru: 'Как это устроено' },
    parts: {
      en: [
        {
          type: 'p',
          text: 'Keywords are resolved once (discovery + collection plan). The Collect Worker runs every 5 minutes and, once per 24 hours per keyword, fetches new data from the configured sources. Raw items are classified by an Embed Worker (embeddings + rules; borderline cases go to an LLM). Relevant signals get a short summary from the LLM and are then embedded into a vector database (Qdrant) for semantic search. You can ask questions in natural language in chat; the report here shows the same data in a structured way: by category (signal type), then by cluster, then individual signals.',
        },
      ],
      ru: [
        {
          type: 'p',
          text: 'Ключевые слова один раз проходят резолв (discovery и план сбора). Collect Worker запускается каждые 5 минут и не чаще чем раз в 24 часа по каждому ключевому слову забирает новые данные из настроенных источников. Сырые записи классифицирует Embed Worker (эмбеддинги и правила; пограничные случаи отправляются в LLM). По релевантным сигналам LLM генерирует краткое саммари, после чего они попадают в векторную БД (Qdrant) для семантического поиска. В чате можно задавать вопросы на естественном языке; в этом отчёте те же данные показаны структурированно: по категориям (тип сигнала), затем по кластерам, затем по отдельным сигналам.',
        },
      ],
    },
  },
  {
    title: { en: 'Pipeline', ru: 'Пайплайн' },
    parts: {
      en: [
        {
          type: 'p',
          text: 'The strip at the top of the Report page shows the pipeline in five steps.',
        },
        {
          type: 'ul',
          items: [
            'Step 1 — Keywords: Number of keywords collected in the last 24 hours vs total. You can trigger a recollect for selected keywords (button next to "Keywords").',
            'Step 2 — Collect: New raw signals in the last 24 hours and total raw count.',
            'Step 3 — Classify: How many items were classified by embeddings vs by LLM; how many are still in the embedding queue (unprocessed) and in the LLM queue (borderline).',
            'Step 4 — Summarize: How many relevant signals have a summary; progress bar vs total relevant.',
            'Step 5 — Vectorize: How many summarized signals are already in Qdrant; progress bar vs summarized.',
          ],
        },
        {
          type: 'p',
          text: 'Workers run on the server (embed, LLM, collect, auto-embed) on a schedule; this UI only displays the counts.',
        },
      ],
      ru: [
        {
          type: 'p',
          text: 'Полоска вверху страницы Report показывает пайплайн в пять шагов.',
        },
        {
          type: 'ul',
          items: [
            'Шаг 1 — Keywords: Сколько ключевых слов собрано за последние 24 часа и сколько всего. Можно запустить пересбор по выбранным ключевым словам (кнопка рядом с "Keywords").',
            'Шаг 2 — Collect: Сколько новых сырых сигналов за 24 часа и общее число сырых.',
            'Шаг 3 — Classify: Сколько записей классифицировано эмбеддингами, сколько LLM; сколько ещё в очереди эмбеддингов (unprocessed) и в очереди LLM (borderline).',
            'Шаг 4 — Summarize: Сколько релевантных сигналов уже имеют саммари; прогресс относительно общего числа релевантных.',
            'Шаг 5 — Vectorize: Сколько саммаризованных сигналов уже в Qdrant; прогресс относительно саммаризованных.',
          ],
        },
        {
          type: 'p',
          text: 'Воркеры работают на сервере (embed, LLM, collect, auto-embed) по расписанию; в интерфейсе отображаются только счётчики.',
        },
      ],
    },
  },
  {
    title: { en: 'Filters and search', ru: 'Фильтры и поиск' },
    parts: {
      en: [
        {
          type: 'p',
          text: 'In the Report filter bar you can narrow results by:',
        },
        {
          type: 'ul',
          items: [
            'Date range: date_from and date_to (inclusive).',
            'Sources: GitHub Issues, GitHub Discussions, Hacker News, Stack Overflow, Reddit, HuggingFace (discussions, papers). Multiple selection.',
            'Categories: Signal types (e.g. pain_point, feature_request, bug_report, adoption_signal, comparison, use_case, pricing_concern, positive_feedback, market_observation, security_concern). Multiple selection.',
            'Keywords: Tracked keywords that were matched for the signal. Multiple selection.',
            'Intensity: 1–5 (strength of the signal). Multiple selection.',
            'Confidence: Min/max classification confidence (0–1).',
          ],
        },
        {
          type: 'p',
          text: 'Filters are reflected in the URL so you can share or bookmark a view.',
        },
        {
          type: 'p',
          text: 'Search: In the same bar you can switch between Semantic (vector similarity to your phrase) and Text (full-text). Enter at least 2 characters and press Enter or blur; results stay in the same category/cluster structure but only include hits. Clearing the search shows all signals again within the current filters.',
        },
      ],
      ru: [
        {
          type: 'p',
          text: 'В панели фильтров Report можно сузить выборку по:',
        },
        {
          type: 'ul',
          items: [
            'Диапазон дат: date_from и date_to (включительно).',
            'Источники: GitHub Issues, GitHub Discussions, Hacker News, Stack Overflow, Reddit, HuggingFace (обсуждения, статьи). Множественный выбор.',
            'Категории: Типы сигналов (pain_point, feature_request, bug_report, adoption_signal, comparison, use_case, pricing_concern, positive_feedback, market_observation, security_concern). Множественный выбор.',
            'Keywords: Ключевые слова, по которым собран сигнал. Множественный выбор.',
            'Intensity: 1–5 (сила сигнала). Множественный выбор.',
            'Confidence: Мин/макс уверенности классификации (0–1).',
          ],
        },
        {
          type: 'p',
          text: 'Состояние фильтров записывается в URL, чтобы можно было поделиться ссылкой или сохранить вид.',
        },
        {
          type: 'p',
          text: 'Поиск: В той же панели можно выбрать Semantic (похожесть по смыслу на фразу) или Text (полнотекстовый). Введите минимум 2 символа и нажмите Enter или уберите фокус; результаты остаются в той же структуре категория/кластер, но показывают только совпадения. Очистка поиска снова показывает все сигналы в рамках текущих фильтров.',
        },
      ],
    },
  },
  {
    title: { en: 'What you see in the results', ru: 'Что вы видите в результатах' },
    parts: {
      en: [
        {
          type: 'p',
          text: 'The Report page has a three-level drill-down:',
        },
        {
          type: 'ul',
          items: [
            'Categories: Each category is a signal type (rule name). You see count, sum of rank_score, and a source breakdown (e.g. GitHub, HN, Reddit). Click a category to expand clusters.',
            'Clusters: Within a category, signals are grouped by similarity (e.g. HDBSCAN). Each cluster shows count, rank_score, and source breakdown. Click a cluster to load its signals.',
            'Signals: Each row is one post or issue: title (with optional translation badge), summary, link to source, date, intensity (1–5), confidence, matched rules, and keywords. Rank score combines engagement, quality, and time decay; it is used for ordering.',
          ],
        },
        {
          type: 'p',
          text: 'You can change sort order (e.g. by rank_score, date) and direction in the signals list. The language toggle in the sidebar switches between original (EN) and translated (e.g. RU) text where available.',
        },
      ],
      ru: [
        {
          type: 'p',
          text: 'На странице Report трёхуровневая навигация:',
        },
        {
          type: 'ul',
          items: [
            'Категории: Каждая категория — тип сигнала (имя правила). Показаны количество, сумма rank_score и разбивка по источникам (GitHub, HN, Reddit и т.д.). По клику категория раскрывается в кластеры.',
            'Кластеры: Внутри категории сигналы сгруппированы по сходству (например, HDBSCAN). У кластера — количество, rank_score и разбивка по источникам. По клику подгружаются сигналы кластера.',
            'Сигналы: В каждой строке — один пост или тикет: заголовок (с опциональным бейджем перевода), саммари, ссылка на источник, дата, intensity (1–5), confidence, совпавшие правила и ключевые слова. Rank score объединяет вовлечённость, качество и затухание по времени и используется для сортировки.',
          ],
        },
        {
          type: 'p',
          text: 'В списке сигналов можно менять поле сортировки (например, rank_score, дата) и направление. Переключатель языка в сайдбаре переключает между оригиналом (EN) и переводом (например, RU), где он есть.',
        },
      ],
    },
  },
  {
    title: { en: 'Logs', ru: 'Логи' },
    parts: {
      en: [
        {
          type: 'p',
          text: 'The Logs page shows output from the worker runner (embed, LLM, collect, auto-embed). Use the Worker filter to see only one worker or all. Use the Level filter to limit by log level (e.g. info, error). Logs stream in near real time; you can pause and resume, clear the view, restart workers, or retry failed LLM tasks. Restart and retry affect the server; use them when you know what you are doing (e.g. after a config change or to re-run failed borderline/summarize tasks).',
        },
      ],
      ru: [
        {
          type: 'p',
          text: 'На странице Logs выводится лог воркеров (embed, LLM, collect, auto-embed). Фильтр Worker позволяет смотреть один воркер или все. Фильтр Level ограничивает вывод по уровню (info, error и т.д.). Логи подгружаются почти в реальном времени; можно ставить на паузу, очищать экран, перезапускать воркеры или повторять упавшие LLM-задачи. Перезапуск и retry выполняются на сервере — используйте их осознанно (например, после смены конфига или чтобы заново отправить в очередь упавшие borderline/summarize задачи).',
        },
      ],
    },
  },
]

export default function Help({ lang = 'en' }: { lang?: Lang }) {
  const subtitle = useMemo(
    () => (lang === 'ru' ? 'Как устроен Signal Hunter и как пользоваться отчётом' : 'How Signal Hunter works and how to use the report'),
    [lang]
  )

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <PageHeader
        title="Help"
        subtitle={subtitle}
      />
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-4 py-6 pb-12">
          {SECTIONS.map((section, idx) => {
            const title = section.title[lang]
            const parts = section.parts[lang]
            return (
              <section
                key={idx}
                className="mb-8 rounded-xl border overflow-hidden"
                style={{
                  background: 'var(--bg-2)',
                  borderColor: 'var(--border)',
                }}
              >
                <div
                  className="flex items-center gap-3 px-4 py-3 border-b"
                  style={{ borderColor: 'var(--border)' }}
                >
                  <span
                    className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold shrink-0"
                    style={{ background: 'var(--accent)', color: 'white' }}
                  >
                    {idx + 1}
                  </span>
                  <h2
                    className="text-sm font-semibold tracking-tight"
                    style={{ color: 'var(--text)' }}
                  >
                    {title}
                  </h2>
                </div>
                <div className="px-4 py-4 space-y-4">
                  {parts.map((part, i) => {
                    if (part.type === 'p') {
                      return (
                        <p
                          key={i}
                          className="text-sm leading-relaxed"
                          style={{ color: 'var(--text-2)' }}
                        >
                          {part.text}
                        </p>
                      )
                    }
                    return (
                      <ul
                        key={i}
                        className="list-none space-y-2 pl-0"
                      >
                        {part.items.map((item, j) => (
                          <li
                            key={j}
                            className="flex gap-2.5 text-sm leading-relaxed"
                            style={{ color: 'var(--text-2)' }}
                          >
                            <span
                              className="shrink-0 w-1.5 h-1.5 rounded-full mt-1.5"
                              style={{ background: 'var(--accent)' }}
                            />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    )
                  })}
                </div>
              </section>
            )
          })}
        </div>
      </div>
    </div>
  )
}
