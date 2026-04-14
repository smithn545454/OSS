/**
 * Diary panel — renders Nightly Scribe entries on the Intelligence page.
 *
 * Two modes:
 *  - List view: reverse-chronological entries, headline + key metrics.
 *  - Detail view: single entry's full markdown body, with a Back button.
 */

import { useState } from 'react'
import { BookOpen, ChevronLeft, RefreshCw, AlertCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import clsx from 'clsx'

import {
  useDiaryEntries,
  useDiaryEntry,
  useGenerateDiaryEntry,
} from '@/hooks/useApi'
import type { DiaryEntrySummary } from '@/lib/api'

function MetricPill({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-oss-border bg-oss-bg px-2.5 py-1">
      <span className="text-xs text-oss-muted">{label}: </span>
      <span className="text-xs font-mono font-semibold text-oss-text">{value}</span>
    </div>
  )
}

function EntryCard({
  entry,
  onOpen,
}: {
  entry: DiaryEntrySummary
  onOpen: () => void
}) {
  const m = entry.metrics || {}
  return (
    <button
      type="button"
      onClick={onOpen}
      className={clsx(
        'w-full rounded-xl border border-oss-border bg-oss-surface p-4 text-left',
        'hover:border-oss-accent/40 hover:bg-oss-surface/80 transition-colors'
      )}
    >
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <span className="font-mono text-sm text-oss-accent">{entry.date}</span>
        {entry.policy_version && (
          <span className="text-xs text-oss-muted">{entry.policy_version}</span>
        )}
      </div>
      <p className="text-sm text-oss-text mb-3 leading-snug">{entry.summary || '—'}</p>
      <div className="flex flex-wrap gap-1.5">
        <MetricPill label="APPR" value={m.approves ?? 0} />
        <MetricPill label="WATCH" value={m.watches ?? 0} />
        <MetricPill label="REJ" value={m.rejects ?? 0} />
        {m.closed_total != null && m.closed_total > 0 && (
          <>
            <MetricPill label="W" value={m.closed_wins ?? 0} />
            <MetricPill label="L" value={m.closed_losses ?? 0} />
          </>
        )}
        {m.anomalies != null && m.anomalies > 0 && (
          <MetricPill label="anomalies" value={m.anomalies} />
        )}
      </div>
    </button>
  )
}

function DiaryDetail({
  date,
  onBack,
}: {
  date: string
  onBack: () => void
}) {
  const { data, isLoading, error } = useDiaryEntry(date)
  const regenerate = useGenerateDiaryEntry()

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-oss-muted hover:text-oss-text transition-colors"
        >
          <ChevronLeft className="h-4 w-4" />
          Back to diary
        </button>
        <button
          type="button"
          onClick={() => regenerate.mutate(date)}
          disabled={regenerate.isPending}
          className={clsx(
            'flex items-center gap-1.5 rounded-md border border-oss-border bg-oss-surface px-3 py-1.5 text-xs',
            'hover:border-oss-accent/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <RefreshCw
            className={clsx('h-3.5 w-3.5', regenerate.isPending && 'animate-spin')}
          />
          {regenerate.isPending ? 'Regenerating…' : 'Regenerate'}
        </button>
      </div>

      {regenerate.isError && (
        <div className="flex items-start gap-2 rounded-md border border-oss-reject/30 bg-oss-reject/5 p-3">
          <AlertCircle className="h-4 w-4 text-oss-reject mt-0.5 shrink-0" />
          <p className="text-xs text-oss-text">
            Regenerate failed: {String((regenerate.error as Error)?.message ?? 'unknown')}
          </p>
        </div>
      )}

      {isLoading && (
        <div className="h-64 animate-pulse rounded-xl border border-oss-border bg-oss-surface" />
      )}

      {error && !isLoading && (
        <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-6">
          <p className="text-oss-text">Failed to load entry for {date}.</p>
        </div>
      )}

      {data && !isLoading && (
        <article className="rounded-xl border border-oss-border bg-oss-surface p-6">
          <header className="mb-4 pb-4 border-b border-oss-border">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-mono text-sm text-oss-accent">{data.entry.date}</span>
              {data.entry.policy_version && (
                <span className="text-xs text-oss-muted">
                  policy {data.entry.policy_version}
                </span>
              )}
            </div>
          </header>
          {data.markdown ? (
            <div className="prose prose-invert prose-sm max-w-none prose-headings:text-oss-text prose-p:text-oss-text prose-strong:text-oss-text prose-li:text-oss-text prose-code:text-oss-accent">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data.markdown}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="text-sm text-oss-muted">
              Markdown body is missing. Try Regenerate.
            </p>
          )}
        </article>
      )}
    </div>
  )
}

export default function DiaryPanel() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const { data, isLoading, error } = useDiaryEntries({ limit: 60 })
  const generate = useGenerateDiaryEntry()

  if (selectedDate) {
    return (
      <DiaryDetail date={selectedDate} onBack={() => setSelectedDate(null)} />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-oss-muted">
          <BookOpen className="h-4 w-4" />
          <span>
            Nightly intelligence journal. Auto-generated weekdays after close.
          </span>
        </div>
        <button
          type="button"
          onClick={() => generate.mutate(undefined)}
          disabled={generate.isPending}
          className={clsx(
            'flex items-center gap-1.5 rounded-md border border-oss-border bg-oss-surface px-3 py-1.5 text-xs',
            'hover:border-oss-accent/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        >
          <RefreshCw
            className={clsx('h-3.5 w-3.5', generate.isPending && 'animate-spin')}
          />
          {generate.isPending ? 'Generating…' : 'Generate today'}
        </button>
      </div>

      {generate.isError && (
        <div className="flex items-start gap-2 rounded-md border border-oss-reject/30 bg-oss-reject/5 p-3">
          <AlertCircle className="h-4 w-4 text-oss-reject mt-0.5 shrink-0" />
          <p className="text-xs text-oss-text">
            Generate failed: {String((generate.error as Error)?.message ?? 'unknown')}
          </p>
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-28 animate-pulse rounded-xl border border-oss-border bg-oss-surface"
            />
          ))}
        </div>
      )}

      {error && !isLoading && (
        <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-6">
          <p className="text-oss-text">Failed to load diary entries.</p>
        </div>
      )}

      {data && !isLoading && data.count === 0 && (
        <div className="rounded-xl border border-oss-border bg-oss-surface p-8 text-center">
          <BookOpen className="h-12 w-12 text-oss-muted mx-auto mb-4" />
          <p className="text-oss-text">No diary entries yet.</p>
          <p className="text-sm text-oss-muted mt-2">
            Click <span className="font-semibold">Generate today</span> or wait for the
            nightly 5:30pm ET EventBridge trigger.
          </p>
        </div>
      )}

      {data && !isLoading && data.count > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          {data.entries.map((entry) => (
            <EntryCard
              key={`${entry.date}#${entry.entry_type}`}
              entry={entry}
              onOpen={() => setSelectedDate(entry.date)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
