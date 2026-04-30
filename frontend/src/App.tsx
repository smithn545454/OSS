import { Component, type ReactNode } from 'react'
import { Routes, Route, Navigate, Link } from 'react-router-dom'
import { XCircle, ArrowLeft } from 'lucide-react'
import Layout from './components/Layout'
import PolicyConfig from './pages/PolicyConfig'
import Intelligence from './pages/Intelligence'
import PaperTrading from './pages/PaperTrading'
import MyTrades from './pages/MyTrades'
import TradeDetail from './pages/TradeDetail'
import AlertsConfig from './pages/AlertsConfig'
import Backtesting from './pages/Backtesting'
import ConvexOpportunities from './pages/ConvexOpportunities'
import ConvexEvaluationDetail from './pages/ConvexEvaluationDetail'
import ConvexFailedCandidates from './pages/ConvexFailedCandidates'
import ConvexPipelineMonitor from './pages/ConvexPipelineMonitor'
import { usePageTitle } from './hooks/usePageTitle'

function ConvexOpportunitiesPage() {
  usePageTitle('Opportunities')
  return <ConvexOpportunities />
}

class EvaluationErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="space-y-8">
          <Link
            to="/opportunities"
            className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-oss-muted hover:bg-oss-surface hover:text-oss-text transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Opportunities
          </Link>
          <div className="rounded-xl border border-oss-reject/30 bg-oss-reject/5 p-6 text-center">
            <XCircle className="h-12 w-12 text-oss-reject mx-auto mb-4" />
            <p className="text-oss-text">Something went wrong rendering this evaluation</p>
            <p className="text-sm text-oss-muted mt-2">
              {this.state.error?.message || 'Unknown error'}
            </p>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/opportunities" replace />} />
        <Route path="dashboard" element={<Navigate to="/opportunities" replace />} />
        <Route path="opportunities" element={<ConvexOpportunitiesPage />} />
        <Route path="opps" element={<Navigate to="/opportunities" replace />} />
        <Route path="pipeline" element={<ConvexPipelineMonitor />} />
        <Route path="intelligence" element={<Intelligence />} />
        <Route path="paper-trading" element={<PaperTrading />} />
        <Route path="trades" element={<MyTrades />} />
        <Route path="trades/:tradeId" element={<TradeDetail />} />
        <Route path="alerts" element={<AlertsConfig />} />
        <Route path="backtesting" element={<Backtesting />} />
        <Route path="config" element={<PolicyConfig />} />
        <Route
          path="evaluation/:ticker/:evaluationId"
          element={
            <EvaluationErrorBoundary>
              <ConvexEvaluationDetail />
            </EvaluationErrorBoundary>
          }
        />
        {/* /convex aliases — kept one release for bookmarks; remove next pass. */}
        <Route path="convex" element={<Navigate to="/opportunities" replace />} />
        <Route
          path="convex/runs/:runId/failed"
          element={<ConvexFailedCandidates />}
        />
        <Route
          path="convex/:ticker/:evaluationId"
          element={
            <EvaluationErrorBoundary>
              <ConvexEvaluationDetail />
            </EvaluationErrorBoundary>
          }
        />
      </Route>
    </Routes>
  )
}

export default App
