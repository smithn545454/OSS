export function fmtUsd(amount: number, opts: { sign?: boolean } = {}): string {
  const sign = opts.sign && amount > 0 ? '+' : ''
  const abs = Math.abs(amount)
  const s = abs.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return `${sign}${amount < 0 ? '-' : ''}$${s}`
}

export function fmtPct(value: number, opts: { sign?: boolean } = {}): string {
  const sign = opts.sign && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export function pnlColor(value: number): string {
  if (value > 0) return 'text-oss-approve'
  if (value < 0) return 'text-oss-reject'
  return 'text-oss-muted'
}
