import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FlaskConical } from 'lucide-react'
import clsx from 'clsx'
import DataStoreTab from '@/components/backtest/DataStoreTab'
import ReplayEngineTab from '@/components/backtest/ReplayEngineTab'

const TABS = [
  { id: 'data-store', label: 'Data Store' },
  { id: 'replay-engine', label: 'Replay Engine' },
  { id: 'results', label: 'Results' },
  { id: 'ai-advisor', label: 'AI Advisor' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function Backtesting() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab') as TabId | null
  const [activeTab, setActiveTab] = useState<TabId>(
    tabParam && TABS.some((t) => t.id === tabParam) ? tabParam : 'data-store'
  )

  const handleTabChange = (tabId: TabId) => {
    setActiveTab(tabId)
    setSearchParams({ tab: tabId })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-oss-text">Backtesting</h1>
          <span className="inline-flex items-center gap-1 rounded-md bg-oss-accent/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-oss-accent border border-oss-accent/20">
            <FlaskConical className="h-3 w-3" />
            Engine
          </span>
        </div>
      </div>
      <p className="text-sm text-oss-muted -mt-4">
        Replay the evaluation pipeline against historical data to prove statistical edge.
      </p>

      {/* Tab Navigation */}
      <div className="flex gap-1 border-b border-oss-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={clsx(
              'px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px',
              activeTab === tab.id
                ? 'border-oss-accent text-oss-accent'
                : 'border-transparent text-oss-muted hover:text-oss-text hover:border-oss-border'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'data-store' && <DataStoreTab />}

      {activeTab === 'replay-engine' && <ReplayEngineTab />}

      {activeTab === 'results' && (
        <div className="rounded-xl border border-oss-border bg-oss-surface p-12 text-center">
          <p className="text-oss-muted">Results Tracker — coming in Phase 3</p>
        </div>
      )}

      {activeTab === 'ai-advisor' && (
        <div className="rounded-xl border border-oss-border bg-oss-surface p-12 text-center">
          <p className="text-oss-muted">AI Strategy Advisor — coming in Phase 4</p>
        </div>
      )}
    </div>
  )
}
