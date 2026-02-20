/**
 * OCC option ticker parser.
 * Handles format: O:AAPL250321C00175000
 */

export interface ParsedOCC {
  ticker: string
  expiry: string
  type: 'CALL' | 'PUT'
  strike: number
}

/**
 * Parse an OCC option ticker into its components.
 * Format: O:AAPL250321C00175000
 *   - O: prefix (optional)
 *   - AAPL: underlying ticker (variable length)
 *   - 250321: expiration YYMMDD
 *   - C/P: call or put
 *   - 00175000: strike price * 1000
 */
export function parseOCC(occ: string): ParsedOCC | null {
  try {
    let raw = occ
    if (raw.startsWith('O:')) {
      raw = raw.slice(2)
    }

    // Find where date+type+strike begins by scanning from end
    // Strike is 8 digits, type is 1 char (C/P), date is 6 digits = 15 chars from end
    if (raw.length < 16) return null

    const strikeStr = raw.slice(-8)
    const typeChar = raw.slice(-9, -8)
    const dateStr = raw.slice(-15, -9)
    const ticker = raw.slice(0, -15)

    if (!ticker || !/^\d{6}$/.test(dateStr) || !['C', 'P'].includes(typeChar)) {
      return null
    }

    const year = 2000 + parseInt(dateStr.slice(0, 2), 10)
    const month = dateStr.slice(2, 4)
    const day = dateStr.slice(4, 6)
    const expiry = `${year}-${month}-${day}`

    const strike = parseInt(strikeStr, 10) / 1000

    return {
      ticker,
      expiry,
      type: typeChar === 'C' ? 'CALL' : 'PUT',
      strike,
    }
  } catch {
    return null
  }
}
