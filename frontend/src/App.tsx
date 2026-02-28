import { Component, type ReactNode } from 'react'
import { Routes, Route, Navigate, Link } from 'react-router-dom'
import { XCircle, ArrowLeft } from 'lucide-react'
import Layout from './components/Layout'
import Opportunities from './pages/Opportunities'
import PolicyConfig from './pages/PolicyConfig'
import PipelineMonitor from './pages/PipelineMonitor'
import EvaluationDetail from './pages/EvaluationDetail'
import Calibration from './pages/Calibration'
import PaperTrading from './pages/PaperTrading'
import Backtesting from './pages/Backtesting'
import Opps from './pages/Opps'
import { useIsMobile } from './hooks/useIsMobile'

function ResponsiveOpportunities() {
  const isMobile = useIsMobile()
  return isMobile ? <Opps /> : <Opportunities />
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
        <Route path="opportunities" element={<ResponsiveOpportunities />} />
        <Route path="opps" element={<Navigate to="/opportunities" replace />} />
        <Route path="pipeline" element={<PipelineMonitor />} />
        <Route path="calibration" element={<Calibration />} />
        <Route path="paper-trading" element={<PaperTrading />} />
        <Route path="backtesting" element={<Backtesting />} />
        <Route path="config" element={<PolicyConfig />} />
        <Route
          path="evaluation/:ticker/:evaluationId"
          element={
            <EvaluationErrorBoundary>
              <EvaluationDetail />
            </EvaluationErrorBoundary>
          }
        />
      </Route>
    </Routes>
  )
}

export default App
