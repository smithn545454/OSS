/**
 * Tests for api.ts URL construction and useApi.ts query key factories.
 *
 * These tests verify:
 * - URL construction with proper encoding
 * - Query parameter assembly
 * - ApiError class behavior
 * - Query key factory correctness
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// API URL Construction Tests (mock fetch to capture the URL)
// ---------------------------------------------------------------------------

// We mock global fetch so we can inspect the URLs that api.ts constructs
let lastFetchUrl = ''
let lastFetchOptions: RequestInit = {}

beforeEach(() => {
  lastFetchUrl = ''
  lastFetchOptions = {}
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    lastFetchUrl = url
    lastFetchOptions = options ?? {}
    return {
      ok: true,
      json: async () => ({}),
    }
  }))
})

describe('api.ts URL construction', () => {
  it('listPolicies encodes limit param', async () => {
    const { listPolicies } = await import('../lib/api')
    await listPolicies(50)
    expect(lastFetchUrl).toBe('/api/policies?limit=50')
  })

  it('getPolicy encodes version with special chars', async () => {
    const { getPolicy } = await import('../lib/api')
    await getPolicy('v2.0.0')
    expect(lastFetchUrl).toBe('/api/policies/v2.0.0')
  })

  it('activatePolicy uses POST method', async () => {
    const { activatePolicy } = await import('../lib/api')
    await activatePolicy('v2.0.0')
    expect(lastFetchUrl).toContain('/api/policies/v2.0.0/activate')
    expect(lastFetchOptions.method).toBe('POST')
  })

  it('diffPolicies encodes both versions', async () => {
    const { diffPolicies } = await import('../lib/api')
    await diffPolicies('v1.0', 'v2.0')
    expect(lastFetchUrl).toBe('/api/policies/diff/v1.0/v2.0')
  })

  it('getPipelineStats includes days param', async () => {
    const { getPipelineStats } = await import('../lib/api')
    await getPipelineStats(14)
    expect(lastFetchUrl).toBe('/api/pipeline/stats?days=14')
  })

  it('completePipelineRun encodes runId and status', async () => {
    const { completePipelineRun } = await import('../lib/api')
    await completePipelineRun('run-123', 'failed')
    expect(lastFetchUrl).toContain('/api/pipeline/runs/run-123/complete?status=failed')
  })

  it('getEvaluationDetail encodes all path params', async () => {
    const { getEvaluationDetail } = await import('../lib/api')
    await getEvaluationDetail('AAPL', 'eval-001')
    expect(lastFetchUrl).toContain('/api/evaluations/detail/AAPL/eval-001')
  })

  it('listEvaluations builds filter params correctly', async () => {
    const { listEvaluations } = await import('../lib/api')
    await listEvaluations({ verdict: 'APPROVE', dte_bucket: 'B', option_type: 'CALL', limit: 25 })
    expect(lastFetchUrl).toContain('verdict=APPROVE')
    expect(lastFetchUrl).toContain('dte_bucket=B')
    expect(lastFetchUrl).toContain('option_type=CALL')
    expect(lastFetchUrl).toContain('limit=25')
  })

  it('listEvaluations omits undefined filters', async () => {
    const { listEvaluations } = await import('../lib/api')
    await listEvaluations({ verdict: 'REJECT' })
    expect(lastFetchUrl).toContain('verdict=REJECT')
    expect(lastFetchUrl).not.toContain('dte_bucket')
    expect(lastFetchUrl).not.toContain('option_type')
  })

  it('getCalibrationReport encodes reportId', async () => {
    const { getCalibrationReport } = await import('../lib/api')
    await getCalibrationReport('report-abc-123')
    expect(lastFetchUrl).toBe('/api/calibration/reports/report-abc-123')
  })

  it('getRepresentativeTraces builds all params', async () => {
    const { getRepresentativeTraces } = await import('../lib/api')
    await getRepresentativeTraces(5, 8, 12)
    expect(lastFetchUrl).toContain('gate_failure_limit=5')
    expect(lastFetchUrl).toContain('reject_sample_limit=8')
    expect(lastFetchUrl).toContain('approve_sample_limit=12')
  })

  it('getContractQuotes joins contract IDs with comma', async () => {
    const { getContractQuotes } = await import('../lib/api')
    await getContractQuotes(['O:AAPL250320C00185000', 'O:MSFT250320C00400000'])
    expect(lastFetchUrl).toContain('contracts=')
    // Should be URL-encoded comma-separated
    expect(decodeURIComponent(lastFetchUrl)).toContain('O:AAPL250320C00185000,O:MSFT250320C00400000')
  })

  it('getApproveEvaluations builds params from options', async () => {
    const { getApproveEvaluations } = await import('../lib/api')
    await getApproveEvaluations({
      excludeEarnings: true,
      earningsDays: 5,
      scanner: 'BREAKOUT',
      limit: 50,
    })
    expect(lastFetchUrl).toContain('exclude_earnings=true')
    expect(lastFetchUrl).toContain('earnings_days=5')
    expect(lastFetchUrl).toContain('scanner=BREAKOUT')
    expect(lastFetchUrl).toContain('limit=50')
  })

  it('getApproveEvaluations omits undefined options', async () => {
    const { getApproveEvaluations } = await import('../lib/api')
    await getApproveEvaluations({})
    expect(lastFetchUrl).toBe('/api/evaluations/approve?')
  })

  it('getWatchInsights includes since_days param', async () => {
    const { getWatchInsights } = await import('../lib/api')
    await getWatchInsights(14)
    expect(lastFetchUrl).toBe('/api/evaluations/watch/insights?since_days=14')
  })

  it('getPipelineMonitorRuns omits "all" scanner filter', async () => {
    const { getPipelineMonitorRuns } = await import('../lib/api')
    await getPipelineMonitorRuns({ scanner: 'all', time: 'today', limit: 10 })
    expect(lastFetchUrl).not.toContain('scanner=')
    expect(lastFetchUrl).toContain('time=today')
    expect(lastFetchUrl).toContain('limit=10')
  })

  it('getPipelineMonitorRuns includes non-all scanner', async () => {
    const { getPipelineMonitorRuns } = await import('../lib/api')
    await getPipelineMonitorRuns({ scanner: 'breakout' })
    expect(lastFetchUrl).toContain('scanner=BREAKOUT')
  })

  it('getPipelineAggregateData joins array filters with commas', async () => {
    const { getPipelineAggregateData } = await import('../lib/api')
    // Mock to intercept the nested response
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      lastFetchUrl = url
      return {
        ok: true,
        json: async () => ({ data: { stages: [], time_range: '', scanner_type: '', total_input: 0 } }),
      }
    }))
    await getPipelineAggregateData({
      verdict: ['APPROVE', 'WATCH'],
      dte_bucket: ['A', 'B'],
      option_side: ['CALL'],
    })
    expect(lastFetchUrl).toContain('verdict=APPROVE%2CWATCH')
    expect(lastFetchUrl).toContain('dte_bucket=A%2CB')
    expect(lastFetchUrl).toContain('option_side=CALL')
  })

  it('triggerUVScan uses POST', async () => {
    const { triggerUVScan } = await import('../lib/api')
    await triggerUVScan()
    expect(lastFetchUrl).toBe('/api/scanners/uv-scan-trigger')
    expect(lastFetchOptions.method).toBe('POST')
  })
})

// ---------------------------------------------------------------------------
// ApiError Tests
// ---------------------------------------------------------------------------

describe('ApiError', () => {
  it('constructs with status and body', async () => {
    const { ApiError } = await import('../lib/api')
    const err = new ApiError(404, 'Not Found', { detail: 'Missing' })
    expect(err.status).toBe(404)
    expect(err.statusText).toBe('Not Found')
    expect(err.body).toEqual({ detail: 'Missing' })
    expect(err.message).toContain('404')
    expect(err.name).toBe('ApiError')
  })

  it('is an instance of Error', async () => {
    const { ApiError } = await import('../lib/api')
    const err = new ApiError(500, 'Internal', null)
    expect(err).toBeInstanceOf(Error)
  })
})

// ---------------------------------------------------------------------------
// fetchApi Error Handling Tests
// ---------------------------------------------------------------------------

describe('fetchApi error handling', () => {
  it('throws ApiError on non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 422,
      statusText: 'Unprocessable',
      json: async () => ({ detail: 'validation error' }),
    })))

    const { getHealth, ApiError } = await import('../lib/api')
    await expect(getHealth()).rejects.toThrow(ApiError)
  })

  it('throws ApiError even when error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => { throw new Error('not json') },
    })))

    const { getHealth, ApiError } = await import('../lib/api')
    try {
      await getHealth()
      expect.unreachable('should have thrown')
    } catch (e: any) {
      expect(e).toBeInstanceOf(ApiError)
      expect(e.body).toBeNull()
    }
  })
})

// ---------------------------------------------------------------------------
// Query Key Factory Tests
// ---------------------------------------------------------------------------

describe('queryKeys', () => {
  it('health key is stable', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    expect(queryKeys.health).toEqual(['health'])
  })

  it('policy key includes version', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    expect(queryKeys.policy('v2.0.0')).toEqual(['policies', 'v2.0.0'])
  })

  it('policyDiff key includes both versions', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    expect(queryKeys.policyDiff('v1', 'v2')).toEqual(['policies', 'diff', 'v1', 'v2'])
  })

  it('pipelineStats key includes days', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    expect(queryKeys.pipelineStats(14)).toEqual(['pipeline', 'stats', 14])
  })

  it('evaluationDetail key includes all parts', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    expect(queryKeys.evaluationDetail('AAPL', 'eval-001')).toEqual([
      'evaluations', 'AAPL', 'eval-001', 'detail'
    ])
  })

  it('evaluations key includes filter object', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    const filters = { verdict: 'APPROVE' as const, limit: 50 }
    const key = queryKeys.evaluations(filters)
    expect(key).toEqual(['evaluations', 'list', filters])
  })

  it('pipelineMonitorRuns key includes options', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    const key = queryKeys.pipelineMonitorRuns({ time: 'today', scanner: 'breakout', limit: 20 })
    expect(key[0]).toBe('pipeline')
    expect(key[1]).toBe('monitor')
    expect(key[2]).toBe('runs')
    expect(key[3]).toEqual({ time: 'today', scanner: 'breakout', limit: 20 })
  })

  it('approveEvaluations key includes scoring options', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    const key = queryKeys.approveEvaluations({ excludeEarnings: true, scanner: 'BREAKOUT' })
    expect(key[0]).toBe('evaluations')
    expect(key[1]).toBe('approve')
  })

  it('contractQuotes key includes contract IDs', async () => {
    const { queryKeys } = await import('../hooks/useApi')
    const ids = ['O:AAPL', 'O:MSFT']
    expect(queryKeys.contractQuotes(ids)).toEqual(['market', 'quotes', ids])
  })
})
