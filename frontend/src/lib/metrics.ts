/**
 * Shared metric calculations for θ-Adj EV and Return %.
 * Used by OpportunityCard and EvaluationDetail.
 */

export function calculateReturnPct(thetaAdjEV: number | null, premium: number | null): number | null {
  if (thetaAdjEV == null || premium == null) return null
  const contractCost = premium * 100
  if (contractCost <= 0) return null
  return (thetaAdjEV / contractCost) * 100
}

export function getReturnColor(returnPct: number | null): string {
  if (returnPct == null) return 'rgba(255, 255, 255, 0.3)'
  if (returnPct >= 8) return '#00E5CC'
  if (returnPct >= 3) return '#F59E0B'
  return '#EF4444'
}
