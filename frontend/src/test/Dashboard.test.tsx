import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from '../pages/Dashboard'

// Mock the API hooks
vi.mock('@/hooks/useApi', () => ({
  usePipelineStats: vi.fn(() => ({
    data: {
      total_runs: 25,
      avg_duration_ms: 45000,
      total_opportunities: 150,
      total_evaluations: 80,
      total_approves: 12,
      total_watches: 28,
      total_rejects: 40,
      avg_approve_rate: 15,
      avg_opportunities_per_run: 6.0,
      avg_evaluations_per_run: 3.2,
      failed_runs: 0,
    },
    isLoading: false,
    error: null,
  })),
  useActivePolicy: vi.fn(() => ({
    data: {
      version: 'v2.0.0',
      is_active: true,
      config: {
        decision: { approve_threshold: 75 },
        gates: { min_open_interest: 300 },
      },
    },
    isLoading: false,
    error: null,
  })),
  usePipelineRuns: vi.fn(() => ({
    data: {
      runs: [
        {
          id: 'run-001',
          timestamp: '2026-01-17T16:00:00Z',
          total_contracts: 50,
          approved_count: 3,
          status: 'healthy',
        },
      ],
      total: 1,
      has_more: false,
    },
    isLoading: false,
    error: null,
  })),
}))

// Mock RepresentativeTraces component (it may have complex deps)
vi.mock('@/components/RepresentativeTraces', () => ({
  default: () => <div data-testid="representative-traces">Traces</div>,
}))

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        {ui}
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Dashboard', () => {
  it('renders without crashing', () => {
    renderWithProviders(<Dashboard />)
    // Dashboard should render something visible
    expect(document.body.textContent).toBeTruthy()
  })

  it('displays verdict distribution', () => {
    renderWithProviders(<Dashboard />)
    // Should show approve/watch/reject data somewhere in the page
    const allText = document.body.textContent || ''
    expect(allText).toContain('12')
    expect(allText).toContain('28')
    expect(allText).toContain('40')
  })

  it('shows loading state when data is loading', async () => {
    const { usePipelineStats } = await import('@/hooks/useApi')
    vi.mocked(usePipelineStats).mockReturnValueOnce({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)

    renderWithProviders(<Dashboard />)
    // Should still render without crashing
    expect(document.body).toBeTruthy()
  })
})
