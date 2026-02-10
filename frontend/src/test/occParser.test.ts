import { describe, it, expect } from 'vitest'
import { parseOCC } from '../lib/occParser'

describe('parseOCC', () => {
  // -----------------------------------------------------------------------
  // Standard CALL parsing
  // -----------------------------------------------------------------------
  it('parses a standard CALL option ticker', () => {
    const result = parseOCC('O:AAPL250321C00175000')
    expect(result).toEqual({
      ticker: 'AAPL',
      expiry: '2025-03-21',
      type: 'CALL',
      strike: 175,
    })
  })

  // -----------------------------------------------------------------------
  // Standard PUT parsing
  // -----------------------------------------------------------------------
  it('parses a standard PUT option ticker', () => {
    const result = parseOCC('O:TSLA250418P00250000')
    expect(result).toEqual({
      ticker: 'TSLA',
      expiry: '2025-04-18',
      type: 'PUT',
      strike: 250,
    })
  })

  // -----------------------------------------------------------------------
  // Short ticker (1 character)
  // -----------------------------------------------------------------------
  it('parses a short (1-char) ticker with fractional strike', () => {
    const result = parseOCC('O:F250321C00012500')
    expect(result).toEqual({
      ticker: 'F',
      expiry: '2025-03-21',
      type: 'CALL',
      strike: 12.5,
    })
  })

  // -----------------------------------------------------------------------
  // Long ticker (4 characters)
  // -----------------------------------------------------------------------
  it('parses a long (4-char) ticker with large strike', () => {
    const result = parseOCC('O:NVDA250620C00900000')
    expect(result).toEqual({
      ticker: 'NVDA',
      expiry: '2025-06-20',
      type: 'CALL',
      strike: 900,
    })
  })

  // -----------------------------------------------------------------------
  // Null / undefined / empty input
  // -----------------------------------------------------------------------
  it('returns null for null input', () => {
    const result = parseOCC(null as unknown as string)
    expect(result).toBeNull()
  })

  it('returns null for undefined input', () => {
    const result = parseOCC(undefined as unknown as string)
    expect(result).toBeNull()
  })

  it('returns null for empty string input', () => {
    const result = parseOCC('')
    expect(result).toBeNull()
  })

  // -----------------------------------------------------------------------
  // Malformed input
  // -----------------------------------------------------------------------
  it('returns null for a malformed ticker (too short)', () => {
    const result = parseOCC('O:A')
    expect(result).toBeNull()
  })

  it('returns null for a malformed ticker (no type character)', () => {
    const result = parseOCC('O:AAPL250321X00175000')
    expect(result).toBeNull()
  })

  it('returns null for random garbage text', () => {
    const result = parseOCC('not-an-option-ticker')
    expect(result).toBeNull()
  })

  it('returns null for a ticker with non-numeric date portion', () => {
    const result = parseOCC('O:AAPLabcdefC00175000')
    expect(result).toBeNull()
  })

  // -----------------------------------------------------------------------
  // Decimal strike handling
  // -----------------------------------------------------------------------
  it('correctly handles decimal strikes (strike / 1000)', () => {
    const result = parseOCC('O:SPY250321C00585500')
    expect(result).toEqual({
      ticker: 'SPY',
      expiry: '2025-03-21',
      type: 'CALL',
      strike: 585.5,
    })
  })

  // -----------------------------------------------------------------------
  // Edge cases: without O: prefix
  // -----------------------------------------------------------------------
  it('parses a ticker without the O: prefix', () => {
    const result = parseOCC('AAPL250321C00175000')
    expect(result).toEqual({
      ticker: 'AAPL',
      expiry: '2025-03-21',
      type: 'CALL',
      strike: 175,
    })
  })
})
