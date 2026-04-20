import { Target, TrendingUp } from 'lucide-react'
import type { Decision } from '@/lib/types'
import {
  formatPct,
  hrConvictionBand,
  pConvictionBand,
  v5ArchetypeLabel,
} from '@/lib/v5Meta'

interface ConvictionPanelV5Props {
  decision: Decision
}

/**
 * Dual-conviction display: HR (0–20) + P (0–100) side-by-side with
 * archetype provenance, Wilson CI bounds, and GBM agreement.
 *
 * When the evaluation predates v5 (no conviction fields), renders a
 * gentle placeholder so historical pages still load cleanly.
 */
export function ConvictionPanelV5({ decision }: ConvictionPanelV5Props) {
  const hasV5 = decision.v5_scoring_version != null

  if (!hasV5) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Target className="h-4 w-4" />
          v5 conviction not available — evaluation predates v5 scoring
        </div>
      </div>
    )
  }

  const hrBand = hrConvictionBand(decision.hr_conviction)
  const pBand = pConvictionBand(decision.p_conviction)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Target className="h-5 w-5 text-amber-400" />
          <h3 className="text-lg font-semibold text-slate-100">Conviction (v5)</h3>
        </div>
        <span
          className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/50
                     px-2 py-0.5 text-xs font-medium text-slate-400"
          title="v5 dual-conviction scoring version"
        >
          {decision.v5_scoring_version}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {/* HR Conviction card */}
        <ConvictionCard
          title="HR Conviction"
          subtitle="P(MFE ≥ 200%) × fit × regime × 100"
          value={decision.hr_conviction}
          scaleMax={20}
          archetypeLabel={v5ArchetypeLabel(decision.hr_archetype_matched)}
          fit={decision.hr_archetype_fit}
          bandClass={hrBand.badgeClass}
          bandLabel={hrBand.label}
          bandBlurb={hrBand.blurb}
          footer={
            <WilsonInline
              point={decision.hr_p_point}
              lower={decision.hr_p_lower}
              upper={decision.hr_p_upper}
              n={decision.hr_n_trades}
              gbmScore={decision.gbm_hr_score}
            />
          }
        />

        {/* P Conviction card */}
        <ConvictionCard
          title="P Conviction"
          subtitle="Wilson_lower(P_win) × normalized P&L × fit × regime"
          value={decision.p_conviction}
          scaleMax={100}
          archetypeLabel={v5ArchetypeLabel(decision.p_archetype_matched)}
          fit={decision.p_archetype_fit}
          bandClass={pBand.badgeClass}
          bandLabel={pBand.label}
          bandBlurb={pBand.blurb}
          footer={
            <div className="flex items-center gap-3 text-xs text-slate-400">
              <span title="Wilson-lower win rate from rolling cohort">
                Win ≥{' '}
                <span className="font-mono text-slate-200">
                  {formatPct(decision.p_win_lower)}
                </span>
              </span>
              <span title="Mean P&L % in the matched archetype's cohort">
                Mean P&L{' '}
                <span className="font-mono text-slate-200">
                  {decision.p_mean_pnl_estimate != null
                    ? `${decision.p_mean_pnl_estimate >= 0 ? '+' : ''}${decision.p_mean_pnl_estimate.toFixed(1)}%`
                    : '—'}
                </span>
              </span>
              {decision.gbm_p_score != null && decision.gbm_p_score > 0 && (
                <span title="GBM co-scorer (caps at 0.7× on P conviction)">
                  GBM{' '}
                  <span className="font-mono text-slate-200">
                    {decision.gbm_p_score.toFixed(0)}
                  </span>
                </span>
              )}
            </div>
          }
        />
      </div>

      {/* Regime context */}
      {decision.regime_alignment != null && (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <TrendingUp className="h-3 w-3" />
          Regime alignment:{' '}
          <span className="font-mono text-slate-300">
            ×{decision.regime_alignment.toFixed(2)}
          </span>
          <span className="text-slate-600">
            (bullish-calm &amp; CALL → 1.3, bearish-fear &amp; PUT → 1.3, chop → 0.85)
          </span>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Subcomponents
// ============================================================================

interface ConvictionCardProps {
  title: string
  subtitle: string
  value: number | null | undefined
  scaleMax: number
  archetypeLabel: string
  fit: number | null | undefined
  bandClass: string
  bandLabel: string
  bandBlurb: string
  footer: React.ReactNode
}

function ConvictionCard({
  title,
  subtitle,
  value,
  scaleMax,
  archetypeLabel,
  fit,
  bandClass,
  bandLabel,
  bandBlurb,
  footer,
}: ConvictionCardProps) {
  const pct = value != null ? Math.min(100, (value / scaleMax) * 100) : 0
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-slate-200">{title}</div>
          <div className="mt-0.5 text-[11px] text-slate-500">{subtitle}</div>
        </div>
        <span
          className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${bandClass}`}
          title={bandBlurb}
        >
          {bandLabel}
        </span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className="font-mono text-3xl font-bold text-slate-100">
          {value != null ? value.toFixed(1) : '—'}
        </span>
        <span className="text-sm text-slate-500">/ {scaleMax}</span>
      </div>

      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full bg-gradient-to-r from-emerald-500/60 to-amber-400/80 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-3 text-xs text-slate-400">
        <span className="text-slate-500">Archetype:</span>{' '}
        <span className="text-slate-200">{archetypeLabel}</span>
        {fit != null && archetypeLabel !== '—' && (
          <span className="ml-2 text-slate-500">
            fit {fit.toFixed(0)}
          </span>
        )}
      </div>

      <div className="mt-2">{footer}</div>
    </div>
  )
}

interface WilsonInlineProps {
  point?: number | null
  lower?: number | null
  upper?: number | null
  n?: number | null
  gbmScore?: number | null
}

function WilsonInline({ point, lower, upper, n, gbmScore }: WilsonInlineProps) {
  if (point == null) {
    return (
      <div className="text-xs text-slate-500">
        {gbmScore != null && gbmScore > 0 ? (
          <span title="From GBM co-scorer (no archetype match)">
            GBM only · score{' '}
            <span className="font-mono text-slate-300">{gbmScore.toFixed(0)}</span>
          </span>
        ) : (
          'No archetype match'
        )}
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
      <span title="Point estimate of P(HR200)">
        P̂{' '}
        <span className="font-mono text-slate-200">{formatPct(point)}</span>
      </span>
      <span title="Wilson 95% confidence interval — the LOWER bound drives conviction">
        CI{' '}
        <span className="font-mono text-slate-200">
          {formatPct(lower)} – {formatPct(upper)}
        </span>
      </span>
      {n != null && (
        <span title="Effective sample size for the rolling rate estimate">
          n={n}
        </span>
      )}
      {gbmScore != null && gbmScore > 0 && (
        <span title="GBM co-scorer (caps at 0.5× on HR conviction)">
          GBM{' '}
          <span className="font-mono text-slate-200">{gbmScore.toFixed(0)}</span>
        </span>
      )}
    </div>
  )
}
