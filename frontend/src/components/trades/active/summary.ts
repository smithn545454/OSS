import type { LiveTrade, LiveTradesSummary } from '@/lib/types'

/**
 * Aggregate a list of live trades into the portfolio header.
 *
 * Computed client-side (rather than from a backend endpoint) because the
 * list endpoint already fetched every quote we'd need — a second call
 * would force a parallel Polygon refresh on a cold Lambda container and
 * hit the 30s API Gateway timeout.
 */
export function computeSummary(trades: LiveTrade[]): LiveTradesSummary {
  if (trades.length === 0) {
    return {
      open_count: 0,
      dollar_pnl_open_total: 0,
      premium_at_risk_total: 0,
      pnl_pct_weighted: 0,
      attention_count: 0,
      near_tp_count: 0,
      near_sl_count: 0,
      paper_closed_count: 0,
      last_updated: null,
      quote_sources: { intraday: 0, daily_batch: 0, snapshot: 0 },
    }
  }

  const dollar_pnl_open_total = trades.reduce((s, t) => s + t.dollar_pnl_open, 0)
  const premium_at_risk_total = trades.reduce((s, t) => s + t.premium_at_risk, 0)
  const near_tp_count = trades.filter((t) => t.attention_flag === 'near_tp').length
  const near_sl_count = trades.filter((t) => t.attention_flag === 'near_sl').length
  const paper_closed_count = trades.filter(
    (t) => t.paper_position_status === 'CLOSED'
  ).length

  const pnl_pct_weighted =
    premium_at_risk_total > 0
      ? (dollar_pnl_open_total / premium_at_risk_total) * 100
      : 0

  const last_updated = trades
    .map((t) => t.last_quote_at)
    .filter((x): x is string => !!x)
    .sort()
    .slice(-1)[0] ?? null

  const quote_sources = { intraday: 0, daily_batch: 0, snapshot: 0 }
  for (const t of trades) {
    quote_sources[t.quote_source] = (quote_sources[t.quote_source] ?? 0) + 1
  }

  return {
    open_count: trades.length,
    dollar_pnl_open_total: Math.round(dollar_pnl_open_total * 100) / 100,
    premium_at_risk_total: Math.round(premium_at_risk_total * 100) / 100,
    pnl_pct_weighted: Math.round(pnl_pct_weighted * 100) / 100,
    attention_count: near_tp_count + near_sl_count,
    near_tp_count,
    near_sl_count,
    paper_closed_count,
    last_updated,
    quote_sources,
  }
}
