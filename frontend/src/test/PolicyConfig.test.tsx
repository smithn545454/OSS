import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import PolicyConfig from '../pages/PolicyConfig'
import type { Policy, PolicyConfig as PolicyConfigType } from '@/lib/types'

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

const mockActivateMutate = vi.fn()
const mockCreateMutateAsync = vi.fn()

const defaultConfig: PolicyConfigType = {
  scanner: {
    unusual_volume: { volume_ratio_threshold: 2.5, oi_change_threshold_pct: 15 },
    breakout: { lookback_days: 20 },
    compression: { atr_period: 14, compression_multiplier: 0.75, break_pct: 2.5 },
    cheap_options: { iv_rv_ratio_max: 0.85, iv_percentile_max: 30 },
  },
  underlying_filter: {
    min_price: 10,
    min_avg_dollar_volume: 5_000_000,
    max_missing_bars: 3,
    exclude_earnings_within_days: 5,
  },
  contract_selection: {
    dte_buckets: {},
    delta_bands: {
      CALL: { min_delta: 0.20, max_delta: 0.75 },
      PUT: { min_delta: -0.75, max_delta: -0.20 },
    },
    top_k: 3,
    target_delta_call: 0.45,
    target_delta_put: -0.45,
    min_open_interest: 100,
    min_volume: 50,
    max_spread_pct: 10,
    min_mid_price: 0.5,
    rank_weight_liquidity: 0.40,
    rank_weight_delta: 0.35,
    rank_weight_spread: 0.25,
  },
  gates: {
    min_open_interest: 300,
    min_volume: 75,
    max_spread_pct: 8,
    dte_min: 7,
    dte_max: 90,
    move_sufficiency_max: 1.25,
    iv_percentile_max: 80,
    breakout_volume_min: 1.5,
    theta_burden_max: 3,
  },
  pillars: {
    weights: { directional: 0.4, volatility: 0.35, structure: 0.25 },
    directional: {
      trend_alignment_weight: 0.30,
      momentum_weight: 0.25,
      signal_confirmation_weight: 0.20,
      relative_strength_weight: 0.15,
      catalyst_weight: 0.10,
    },
    volatility: {
      iv_vs_rv_weight: 0.35,
      iv_percentile_weight: 0.25,
      iv_regime_weight: 0.20,
      theta_adjusted_edge_weight: 0.20,
    },
    structure: {
      spread_weight: 0.30,
      open_interest_weight: 0.25,
      volume_weight: 0.20,
      theta_burden_weight: 0.15,
      liquidity_trend_weight: 0.10,
    },
  },
  pillar_weights: { directional: 0.4, volatility: 0.35, structure: 0.25 },
  decision: {
    approve_threshold: 75,
    watch_threshold: 55,
    tier_1_min_score: 85,
    tier_1_min_pillar: 70,
    tier_1_max_spread: 5,
    tier_2_min_pillar: 50,
  },
  tracking: {
    profit_target_pct: 50,
    stop_loss_pct: 40,
    time_exit_dte: 5,
    shadow_sample_rate: 0.1,
    shadow_near_miss_threshold: 5,
  },
}

const mockActivePolicy: Policy = {
  version: 'v2.0.0',
  policy_hash: 'abc123',
  config: defaultConfig,
  created_at: '2026-01-15T10:00:00Z',
  created_by: 'admin',
  is_active: true,
  changelog: [],
}

const mockPolicies: Policy[] = [
  mockActivePolicy,
  {
    ...mockActivePolicy,
    version: 'v1.0.0',
    is_active: false,
    created_at: '2026-01-10T10:00:00Z',
    changelog: [
      {
        field_path: 'gates.min_open_interest',
        old_value: 200,
        new_value: 300,
        changed_at: '2026-01-15T10:00:00Z',
        changed_by: 'admin',
      },
    ],
  },
]

vi.mock('@/hooks/useApi', () => ({
  usePolicies: vi.fn(() => ({
    data: { policies: mockPolicies, count: 2 },
    isLoading: false,
    error: null,
  })),
  useActivePolicy: vi.fn(() => ({
    data: mockActivePolicy,
    isLoading: false,
    error: null,
  })),
  useActivatePolicy: vi.fn(() => ({
    mutate: mockActivateMutate,
    isPending: false,
  })),
  useCreatePolicy: vi.fn(() => ({
    mutateAsync: mockCreateMutateAsync,
    isPending: false,
  })),
  usePolicyDiff: vi.fn(() => ({
    data: null,
    isLoading: false,
  })),
}))

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PolicyConfig />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PolicyConfig Page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCreateMutateAsync.mockResolvedValue({})
  })

  // --- Rendering ---

  it('renders page title', () => {
    renderPage()
    expect(screen.getByText('Policy Configuration')).toBeInTheDocument()
  })

  it('shows active policy version', () => {
    renderPage()
    const versionElements = screen.getAllByText('v2.0.0')
    expect(versionElements.length).toBeGreaterThanOrEqual(1)
  })

  it('renders policy version list with active badge', () => {
    renderPage()
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('shows inactive version with activate button', () => {
    renderPage()
    expect(screen.getByText('v1.0.0')).toBeInTheDocument()
    // The "Activate" button should be present for the inactive version
    expect(screen.getByText('Activate')).toBeInTheDocument()
  })

  it('renders gate config values in read mode', () => {
    renderPage()
    // The "Hard Gates" section is open by default
    expect(screen.getByText('Hard Gates')).toBeInTheDocument()
    // Gate values should be displayed
    const allText = document.body.textContent || ''
    expect(allText).toContain('300') // min_open_interest
    expect(allText).toContain('75')  // min_volume
  })

  // --- Loading State ---

  it('shows skeleton loading state', async () => {
    const { usePolicies, useActivePolicy } = await import('@/hooks/useApi')
    vi.mocked(usePolicies).mockReturnValueOnce({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)
    vi.mocked(useActivePolicy).mockReturnValueOnce({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)

    renderPage()
    // Should show loading skeletons (animate-pulse divs)
    const pulses = document.querySelectorAll('.animate-pulse')
    expect(pulses.length).toBeGreaterThan(0)
  })

  // --- Empty Policies ---

  it('shows empty state when no policies exist', async () => {
    const { usePolicies, useActivePolicy } = await import('@/hooks/useApi')
    vi.mocked(usePolicies).mockReturnValueOnce({
      data: { policies: [], count: 0 },
      isLoading: false,
      error: null,
    } as any)
    vi.mocked(useActivePolicy).mockReturnValueOnce({
      data: null,
      isLoading: false,
      error: null,
    } as any)

    renderPage()
    expect(screen.getByText('No policies found')).toBeInTheDocument()
    expect(screen.getByText('No active policy')).toBeInTheDocument()
  })

  // --- Edit Mode ---

  it('enters edit mode when Edit Configuration is clicked', () => {
    renderPage()
    const editButton = screen.getByText('Edit Configuration')
    fireEvent.click(editButton)

    // Should show editing controls
    expect(screen.getByText('Cancel')).toBeInTheDocument()
    expect(screen.getByText('Reset')).toBeInTheDocument()
    expect(screen.getByText(/Save as New Version/)).toBeInTheDocument()
  })

  it('cancels edit mode', () => {
    renderPage()
    fireEvent.click(screen.getByText('Edit Configuration'))
    expect(screen.getByText('Cancel')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Cancel'))
    // Should return to view mode
    expect(screen.getByText('Edit Configuration')).toBeInTheDocument()
  })

  it('shows unsaved changes notice when config is modified', () => {
    renderPage()
    fireEvent.click(screen.getByText('Edit Configuration'))

    // Find a number input and change its value
    const inputs = document.querySelectorAll('input[type="number"]')
    expect(inputs.length).toBeGreaterThan(0)
    fireEvent.change(inputs[0], { target: { value: '999' } })

    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument()
  })

  it('resets config back to original when Reset is clicked', () => {
    renderPage()
    fireEvent.click(screen.getByText('Edit Configuration'))

    // Change a value
    const inputs = document.querySelectorAll('input[type="number"]')
    fireEvent.change(inputs[0], { target: { value: '999' } })
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument()

    // Click Reset
    fireEvent.click(screen.getByText('Reset'))
    // The unsaved changes notice should disappear
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument()
  })

  // --- Activate ---

  it('calls activate mutation when Activate button is clicked', () => {
    renderPage()
    const activateBtn = screen.getByText('Activate')
    fireEvent.click(activateBtn)
    expect(mockActivateMutate).toHaveBeenCalledWith('v1.0.0')
  })

  // --- Compare ---

  it('opens compare modal when Compare is clicked', () => {
    renderPage()
    // There should be Compare buttons for each version
    const compareButtons = screen.getAllByText('Compare')
    // Click compare on the inactive version
    fireEvent.click(compareButtons[compareButtons.length - 1])
    // Should show the compare modal
    expect(screen.getByText('Compare Policies')).toBeInTheDocument()
  })

  // --- Section Collapse ---

  it('toggles section visibility on click', () => {
    renderPage()
    // "Underlying Filters" section should be collapsed by default (defaultOpen=false)
    const filterSection = screen.getByText('Underlying Filters')
    // Click to open it
    fireEvent.click(filterSection)
    // Should now show filter config values
    const allText = document.body.textContent || ''
    expect(allText).toContain('Minimum Price')
  })
})
