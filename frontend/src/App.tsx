import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Opportunities from './pages/Opportunities'
import PolicyConfig from './pages/PolicyConfig'
import PipelineMonitor from './pages/PipelineMonitor'
import EvaluationDetail from './pages/EvaluationDetail'
import Calibration from './pages/Calibration'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="opportunities" element={<Opportunities />} />
        <Route path="pipeline" element={<PipelineMonitor />} />
        <Route path="calibration" element={<Calibration />} />
        <Route path="config" element={<PolicyConfig />} />
        <Route path="evaluation/:ticker/:evaluationId" element={<EvaluationDetail />} />
      </Route>
    </Routes>
  )
}

export default App
