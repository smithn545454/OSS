import { useNavigate } from 'react-router-dom'
import type { ApproveEvaluation } from '@/lib/types'
import { UrgencyBadge } from '@/components/opportunities/UrgencyBadge'
import { ScannerBadge } from '@/components/opportunities/ScannerBadge'
import { ConvergenceBadge } from '@/components/opportunities/ConvergenceBadge'
import { OptionTypeBadge } from '@/components/opportunities/OptionTypeBadge'
import { ConvictionGauge } from '@/components/opportunities/ConvictionGauge'
import clsx from 'clsx'

interface OppCardProps {
  evaluation: ApproveEvaluation
  rank: number
  className?: string
}

const RANK_COLORS: Record<number, string> = {
  1: 'text-amber-400',
  2: 'text-slate-300',
  3: 'text-amber-600',
}

function formatExpiry(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function OppCard({ evaluation, rank, className }: OppCardProps) {
  const navigate = useNavigate()
  const isTopPick = rank === 1

  const premium = evaluation.mid ?? 0
  const contractCost = premium * 100
  const score = evaluation.convictionScore ?? 0
  const absDelta = Math.abs(evaluation.delta ?? 0)
  const moneynessPct = evaluation.moneyness_pct ?? 0

  const handleClick = () => {
    navigate(`/evaluation/${evaluation.underlying_ticker}/${evaluation.evaluation_id}`)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleClick()
    }
  }

  return (
    <article
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
      aria-label={`${evaluation.underlying_ticker} ${evaluation.option_type} at ${evaluation.strike}, ranked ${rank}, conviction ${Math.round(score)}`}
      className={clsx(
        'relative rounded-lg border transition-all cursor-pointer',
        'bg-[var(--bg-secondary)] border-[var(--border-default)]',
        'hover:border-[var(--border-active)] hover:bg-[var(--bg-hover)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]',
        'p-3 sm:p-4',
        isTopPick && 'border-l-2 border-l-[var(--accent-primary)] shadow-[0_0_20px_rgba(0,229,204,0.08)]',
        className,
      )}
    >
      {/* Mobile layout (default) / Desktop layout (lg+) */}
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-4">
        {/* Row 1: Rank + Ticker + Type + Gauge */}
        <div className="flex items-center gap-3 lg:contents">
          {/* Rank */}
          <div className="flex flex-col items-center shrink-0 lg:w-16">
            <span
              className={clsx(
                'text-lg font-bold font-mono',
                RANK_COLORS[rank] ?? 'text-[var(--text-muted)]',
              )}
            >
              #{rank}
            </span>
            {isTopPick && (
              <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--accent-primary)]">
                TOP PICK
              </span>
            )}
          </div>

          {/* Ticker + Type */}
          <div className="flex items-center gap-2 min-w-0 lg:flex-1">
            <span className="text-base sm:text-lg font-bold text-[var(--text-primary)] font-mono">
              {evaluation.underlying_ticker}
            </span>
            <OptionTypeBadge type={evaluation.option_type} />
          </div>

          {/* Conviction gauge - right aligned on mobile */}
          <div className="ml-auto lg:ml-0 lg:order-last shrink-0">
            <ConvictionGauge score={score} />
          </div>
        </div>

        {/* Row 2: Contract details + metrics */}
        <div className="flex items-center gap-1.5 text-sm text-[var(--text-secondary)] font-mono lg:w-48 flex-wrap">
          <span>${evaluation.strike}</span>
          <span className="text-[var(--text-muted)]">&middot;</span>
          <span>{formatExpiry(evaluation.expiration_date)}</span>
          <span className="text-[var(--text-muted)]">&middot;</span>
          <span>${premium.toFixed(2)}</span>
          <span className="text-[var(--text-muted)] text-xs">
            (${contractCost.toLocaleString('en-US', { maximumFractionDigits: 0 })})
          </span>
          <span className="text-[var(--text-muted)]">&middot;</span>
          <span className="text-xs">
            <span className="text-[var(--text-muted)]">&Delta;</span>{' '}
            {absDelta.toFixed(2)}
          </span>
          <span className="text-[var(--text-muted)]">&middot;</span>
          <span className={clsx(
            'text-xs',
            moneynessPct > 0 ? 'text-[var(--text-muted)]' : 'text-amber-400',
          )}>
            {moneynessPct > 0 ? `${moneynessPct.toFixed(1)}% OTM` : moneynessPct === 0 ? 'ATM' : 'ITM'}
          </span>
        </div>

        {/* Row 3: Badges */}
        <div className="flex items-center gap-1.5 flex-wrap lg:flex-1">
          {evaluation.scannerSource.map((scanner) => (
            <ScannerBadge key={scanner} scanner={scanner} />
          ))}
          <ConvergenceBadge count={evaluation.scannerConvergence} />
          <UrgencyBadge urgency={evaluation.urgency} size="small" />
          {(evaluation.approvalCount ?? 1) > 1 && (
            <span
              title={`Approved in ${evaluation.approvalCount} separate pipeline scans`}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold text-[var(--accent-primary)] bg-[var(--bg-tertiary)] rounded-full border border-[var(--border-default)]"
            >
              {evaluation.approvalCount}x scans
            </span>
          )}
          {evaluation.matchedRules && evaluation.matchedRules.length > 0 && (
            <span
              title={evaluation.matchedRules.map(r => r.name).join(', ')}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold text-violet-400 bg-violet-400/10 rounded-full border border-violet-400/20 whitespace-nowrap"
            >
              {evaluation.matchedRules.length === 1
                ? evaluation.matchedRules[0].name.length > 30
                  ? evaluation.matchedRules[0].name.slice(0, 28) + '...'
                  : evaluation.matchedRules[0].name
                : `${evaluation.matchedRules.length} setup matches`}
            </span>
          )}
        </div>
      </div>

      {/* Headline */}
      {evaluation.headline && (
        <p className="mt-2 text-xs text-[var(--text-tertiary)] leading-relaxed line-clamp-1 lg:line-clamp-none lg:ml-16">
          {evaluation.headline}
        </p>
      )}
    </article>
  )
}
