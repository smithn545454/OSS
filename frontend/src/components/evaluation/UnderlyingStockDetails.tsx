import { useState } from 'react'
import { useStockTechnicals } from '@/hooks/useApi'
import type { StockTechnicalsResponse, TapeSignal, TapeVerdict } from '@/lib/types'
import clsx from 'clsx'
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Activity,
  BarChart3,
  Target,
  ChevronDown,
  ChevronUp,
  ExternalLink,
} from 'lucide-react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(val: number | null | undefined, decimals = 2): string {
  if (val == null) return '—'
  return val.toFixed(decimals)
}

function fmtLarge(val: number | null | undefined): string {
  if (val == null) return '—'
  if (val >= 1_000_000_000_000) return `$${(val / 1_000_000_000_000).toFixed(1)}T`
  if (val >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(1)}B`
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(0)}M`
  return `$${val.toLocaleString()}`
}

function fmtVolume(val: number | null | undefined): string {
  if (val == null) return '—'
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`
  if (val >= 1_000) return `${(val / 1_000).toFixed(0)}K`
  return val.toLocaleString()
}

// ---------------------------------------------------------------------------
// Colors
// ---------------------------------------------------------------------------

function verdictColor(verdict: TapeVerdict): string {
  switch (verdict) {
    case 'BULLISH':
      return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    case 'LEAN_BULLISH':
      return 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
    case 'NEUTRAL':
      return 'bg-amber-500/10 text-amber-400 border-amber-500/20'
    case 'LEAN_BEARISH':
      return 'bg-rose-500/10 text-rose-300 border-rose-500/20'
    case 'BEARISH':
      return 'bg-rose-500/15 text-rose-400 border-rose-500/30'
  }
}

function verdictLabel(verdict: TapeVerdict): string {
  return verdict.replace('_', ' ')
}

function changeColor(val: number | null | undefined): string {
  if (val == null) return 'text-oss-muted'
  if (val > 0) return 'text-emerald-400'
  if (val < 0) return 'text-rose-400'
  return 'text-oss-muted'
}

function signalDotColor(signal: string): string {
  if (signal === 'BULLISH') return 'bg-emerald-400'
  if (signal === 'BEARISH') return 'bg-rose-400'
  return 'bg-amber-400'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function EMABox({
  label,
  value,
  price,
}: {
  label: string
  value: number | null
  price: number
}) {
  const above = value != null && price > value
  const below = value != null && price < value
  return (
    <div
      className={clsx(
        'rounded-lg px-3 py-2 border text-center',
        above && 'bg-emerald-500/8 border-emerald-500/20',
        below && 'bg-rose-500/8 border-rose-500/20',
        !above && !below && 'bg-white/3 border-white/6'
      )}
    >
      <div className="text-[10px] text-oss-muted uppercase tracking-wider mb-0.5">{label}</div>
      <div
        className={clsx(
          'font-mono text-sm font-medium',
          above && 'text-emerald-400',
          below && 'text-rose-400',
          !above && !below && 'text-oss-text'
        )}
      >
        {value != null ? fmt(value) : '—'}
      </div>
    </div>
  )
}

function IndicatorTile({
  label,
  value,
  signal,
  icon: Icon,
  suffix,
}: {
  label: string
  value: string
  signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  icon: React.ElementType
  suffix?: string
}) {
  return (
    <div className="rounded-lg bg-white/3 border border-white/6 px-3 py-2">
      <div className="flex items-center gap-1.5 mb-1">
        <div className={clsx('h-2 w-2 rounded-full', signalDotColor(signal))} />
        <span className="text-[10px] text-oss-muted uppercase tracking-wider">{label}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5 text-oss-muted" />
        <span className="font-mono text-sm font-medium text-oss-text">
          {value}
          {suffix && <span className="text-oss-muted text-xs ml-0.5">{suffix}</span>}
        </span>
      </div>
    </div>
  )
}

function RangeBar({
  low,
  high,
  current,
}: {
  low: number
  high: number
  current: number
}) {
  if (high <= low) return null
  const pct = Math.max(0, Math.min(100, ((current - low) / (high - low)) * 100))

  return (
    <div className="mt-1.5">
      <div className="flex justify-between text-[10px] text-oss-muted mb-1">
        <span>${fmt(low)}</span>
        <span>52W Range</span>
        <span>${fmt(high)}</span>
      </div>
      <div className="relative h-1.5 rounded-full bg-white/10">
        <div
          className="absolute h-1.5 rounded-full bg-gradient-to-r from-rose-500/60 via-amber-500/60 to-emerald-500/60"
          style={{ width: '100%' }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 h-3 w-1 rounded-full bg-white shadow-sm"
          style={{ left: `${pct}%`, marginLeft: '-2px' }}
        />
      </div>
    </div>
  )
}

function TapeReadBar({
  bullishPct,
  adx,
}: {
  bullishPct: number
  adx: number | null
}) {
  const bearishPct = 100 - bullishPct

  return (
    <div className="mt-4 pt-4 border-t border-white/6">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-oss-muted uppercase tracking-wider">Tape Read Consensus</span>
        {adx != null && adx >= 25 && (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/20">
            STRONG TREND
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-xs font-mono font-medium text-emerald-400 w-10 text-right">
          {bullishPct.toFixed(0)}%
        </span>
        <div className="flex-1 h-2.5 rounded-full bg-rose-500/30 overflow-hidden">
          <div
            className="h-full rounded-full bg-emerald-500/70 transition-all duration-500"
            style={{ width: `${bullishPct}%` }}
          />
        </div>
        <span className="text-xs font-mono font-medium text-rose-400 w-10">
          {bearishPct.toFixed(0)}%
        </span>
      </div>
    </div>
  )
}

function SignalBreakdown({ signals }: { signals: TapeSignal[] }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-[11px] text-oss-muted hover:text-oss-text transition-colors"
      >
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        Signal breakdown
      </button>
      {open && (
        <div className="mt-2 space-y-1.5">
          {signals.map((s) => (
            <div key={s.name} className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <div className={clsx('h-1.5 w-1.5 rounded-full', signalDotColor(s.signal))} />
                <span className="text-oss-muted">{s.name}</span>
              </div>
              <span className="text-oss-text font-mono text-[11px]">{s.reading}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// RSI helpers
// ---------------------------------------------------------------------------

function rsiSignal(rsi: number | null): 'BULLISH' | 'BEARISH' | 'NEUTRAL' {
  if (rsi == null) return 'NEUTRAL'
  if (rsi >= 50 && rsi <= 70) return 'BULLISH'
  if (rsi < 40) return 'BEARISH'
  return 'NEUTRAL'
}

function rsiLabel(rsi: number | null): string {
  if (rsi == null) return '—'
  if (rsi >= 70) return 'Overbought'
  if (rsi <= 30) return 'Oversold'
  if (rsi >= 50) return 'Bullish'
  return 'Weak'
}

// ---------------------------------------------------------------------------
// ADX helpers
// ---------------------------------------------------------------------------

function adxSignal(data: StockTechnicalsResponse): 'BULLISH' | 'BEARISH' | 'NEUTRAL' {
  if (data.adx_14 == null || data.adx_14 < 25) return 'NEUTRAL'
  if (data.plus_di != null && data.minus_di != null && data.plus_di > data.minus_di) return 'BULLISH'
  if (data.plus_di != null && data.minus_di != null && data.minus_di > data.plus_di) return 'BEARISH'
  return 'NEUTRAL'
}

function adxLabel(adx: number | null): string {
  if (adx == null) return '—'
  if (adx >= 40) return 'Very Strong'
  if (adx >= 25) return 'Trending'
  if (adx >= 20) return 'Emerging'
  return 'Weak'
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface UnderlyingStockDetailsProps {
  ticker: string
}

export default function UnderlyingStockDetails({ ticker }: UnderlyingStockDetailsProps) {
  const { data, isLoading, error } = useStockTechnicals(ticker)

  if (isLoading) {
    return (
      <div className="h-56 animate-pulse rounded-xl border border-oss-border bg-oss-surface" />
    )
  }

  if (error || !data) return null

  const d = data

  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-oss-text">Underlying Stock Details</h3>
          {d.sector && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/5 text-oss-muted border border-white/8">
              {d.sector}
            </span>
          )}
        </div>
        <span
          className={clsx(
            'text-xs font-semibold px-2.5 py-1 rounded-md border',
            verdictColor(d.tape_verdict)
          )}
        >
          {verdictLabel(d.tape_verdict)}
        </span>
      </div>

      {/* Top Row: Profile + Price Context */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-4">
        {/* Left: Company Profile */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-oss-muted">Market Cap</span>
            <span className="font-mono text-sm text-oss-text">{fmtLarge(d.market_cap)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-oss-muted">vs 52W High</span>
            <span
              className={clsx('font-mono text-sm', changeColor(d.pct_from_52w_high))}
            >
              {d.pct_from_52w_high != null ? `${fmt(d.pct_from_52w_high, 1)}%` : '—'}
            </span>
          </div>
          {d.homepage_url && (
            <a
              href={d.homepage_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-oss-muted hover:text-oss-accent transition-colors"
            >
              <ExternalLink className="h-3 w-3" />
              Website
            </a>
          )}
        </div>

        {/* Right: Price Context */}
        <div>
          <div className="flex items-baseline gap-3 mb-2">
            <span className="font-mono text-2xl font-bold text-oss-text">${fmt(d.price)}</span>
            <span className={clsx('font-mono text-sm font-medium', changeColor(d.change_pct))}>
              {d.change_dollar != null && d.change_dollar >= 0 ? '+' : ''}
              {d.change_dollar != null ? `$${fmt(d.change_dollar)}` : ''}
              {d.change_pct != null && (
                <span className="ml-1">
                  ({d.change_pct >= 0 ? '+' : ''}{fmt(d.change_pct, 1)}%)
                </span>
              )}
            </span>
          </div>
          {d.high_52w != null && d.low_52w != null && (
            <RangeBar low={d.low_52w} high={d.high_52w} current={d.price} />
          )}
          <div className="flex items-center gap-4 mt-2">
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-oss-muted">Vol</span>
              <span className="font-mono text-xs text-oss-text">{fmtVolume(d.volume)}</span>
            </div>
            {d.relative_volume != null && (
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-oss-muted">RVol</span>
                <span
                  className={clsx(
                    'font-mono text-xs font-medium',
                    d.relative_volume > 1.5 ? 'text-emerald-400' :
                    d.relative_volume < 0.7 ? 'text-rose-400' : 'text-oss-text'
                  )}
                >
                  {d.relative_volume.toFixed(1)}x
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Bottom Row: EMA Alignment + Momentum Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Left: EMA Alignment */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] text-oss-muted uppercase tracking-wider">EMA Alignment</span>
            <span
              className={clsx(
                'text-[10px] font-medium px-1.5 py-0.5 rounded',
                d.ema_alignment === 'BULLISH_STACK' && 'bg-emerald-500/10 text-emerald-400',
                d.ema_alignment === 'BEARISH_STACK' && 'bg-rose-500/10 text-rose-400',
                d.ema_alignment === 'ABOVE_ALL' && 'bg-emerald-500/8 text-emerald-300',
                d.ema_alignment === 'BELOW_ALL' && 'bg-rose-500/8 text-rose-300',
                d.ema_alignment === 'MIXED' && 'bg-white/5 text-oss-muted',
              )}
            >
              {d.ema_alignment?.replace('_', ' ') ?? '—'}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-1.5">
            <EMABox label="EMA 9" value={d.ema_9} price={d.price} />
            <EMABox label="EMA 21" value={d.ema_21} price={d.price} />
            <EMABox label="EMA 50" value={d.ema_50} price={d.price} />
            <EMABox label="EMA 200" value={d.ema_200} price={d.price} />
          </div>
        </div>

        {/* Right: Momentum Grid */}
        <div>
          <span className="text-[10px] text-oss-muted uppercase tracking-wider block mb-2">
            Momentum
          </span>
          <div className="grid grid-cols-2 gap-1.5">
            <IndicatorTile
              label="RSI (14)"
              value={d.rsi_14 != null ? fmt(d.rsi_14, 1) : '—'}
              signal={rsiSignal(d.rsi_14)}
              icon={Activity}
              suffix={d.rsi_14 != null ? ` ${rsiLabel(d.rsi_14)}` : undefined}
            />
            <IndicatorTile
              label="ADX (14)"
              value={d.adx_14 != null ? fmt(d.adx_14, 1) : '—'}
              signal={adxSignal(d)}
              icon={Target}
              suffix={d.adx_14 != null ? ` ${adxLabel(d.adx_14)}` : undefined}
            />
            <IndicatorTile
              label="MACD Hist"
              value={d.macd_histogram != null ? (d.macd_histogram >= 0 ? '+' : '') + fmt(d.macd_histogram, 3) : '—'}
              signal={
                d.macd_histogram != null
                  ? d.macd_histogram > 0 ? 'BULLISH' : d.macd_histogram < 0 ? 'BEARISH' : 'NEUTRAL'
                  : 'NEUTRAL'
              }
              icon={BarChart3}
            />
            <IndicatorTile
              label="OBV Trend"
              value={d.obv_trend ?? '—'}
              signal={
                d.obv_trend === 'RISING' ? 'BULLISH' : d.obv_trend === 'FALLING' ? 'BEARISH' : 'NEUTRAL'
              }
              icon={d.obv_trend === 'RISING' ? TrendingUp : d.obv_trend === 'FALLING' ? TrendingDown : Minus}
            />
          </div>
        </div>
      </div>

      {/* Tape Read Summary Bar */}
      <TapeReadBar bullishPct={d.tape_bullish_pct} adx={d.adx_14} />
      <SignalBreakdown signals={d.tape_signals} />
    </div>
  )
}
