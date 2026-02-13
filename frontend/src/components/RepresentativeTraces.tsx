/**
 * Representative Traces Panel - Section 18.3
 * 
 * Displays sampled edge-case evaluations for observability:
 * - Common Gate Failures
 * - Highest REJECT Scores (near-misses)
 * - Lowest APPROVE Scores (borderline)
 * - TIER_1 Approvals (high-confidence)
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { 
  Shield, 
  TrendingDown, 
  TrendingUp, 
  Star,
  ChevronRight,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Loader2
} from 'lucide-react'
import { useRepresentativeTraces } from '@/hooks/useApi'
import type { TraceSample, GateFailureSample } from '@/lib/types'
import clsx from 'clsx'

type TabId = 'gate_failures' | 'high_rejects' | 'low_approves' | 'tier_1'

interface TabConfig {
  id: TabId
  label: string
  icon: React.ReactNode
  description: string
  color: string
}

const TABS: TabConfig[] = [
  {
    id: 'gate_failures',
    label: 'Gate Failures',
    icon: <Shield className="h-4 w-4" />,
    description: 'Most common rejection points',
    color: 'text-oss-reject',
  },
  {
    id: 'high_rejects',
    label: 'Near-Miss Rejects',
    icon: <TrendingUp className="h-4 w-4" />,
    description: 'Highest scores that were rejected',
    color: 'text-amber-400',
  },
  {
    id: 'low_approves',
    label: 'Borderline Approves',
    icon: <TrendingDown className="h-4 w-4" />,
    description: 'Lowest scores that were approved',
    color: 'text-oss-watch',
  },
  {
    id: 'tier_1',
    label: 'TIER-1 Approvals',
    icon: <Star className="h-4 w-4" />,
    description: 'High-confidence trades',
    color: 'text-oss-approve',
  },
]

function TraceSampleCard({ sample }: { sample: TraceSample }) {
  const isCall = sample.option_type === 'CALL'
  
  return (
    <Link
      to={`/evaluation/${sample.ticker}/${sample.evaluation_id}`}
      className="group block rounded-lg bg-oss-bg p-3 transition-colors hover:bg-oss-bg/80"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={clsx(
            'flex h-8 w-8 items-center justify-center rounded-lg text-xs font-bold',
            sample.verdict === 'APPROVE' && 'bg-oss-approve/10 text-oss-approve',
            sample.verdict === 'WATCH' && 'bg-oss-watch/10 text-oss-watch',
            sample.verdict === 'REJECT' && 'bg-oss-reject/10 text-oss-reject'
          )}>
            {sample.final_score.toFixed(0)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-medium text-oss-text">{sample.ticker}</span>
              <span className={clsx(
                'rounded px-1.5 py-0.5 text-xs font-medium',
                isCall ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
              )}>
                {sample.option_type}
              </span>
              {sample.quality_tier && (
                <span className="rounded bg-oss-accent/10 px-1.5 py-0.5 text-xs text-oss-accent">
                  {sample.quality_tier.replace('_', ' ')}
                </span>
              )}
            </div>
            <p className="text-xs text-oss-muted">
              ${sample.strike} · {sample.dte} DTE · Bucket {sample.dte_bucket}
            </p>
          </div>
        </div>
        <ChevronRight className="h-4 w-4 text-oss-muted opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
      {sample.failed_gates.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {sample.failed_gates.slice(0, 3).map((gate) => (
            <span
              key={gate}
              className="rounded bg-oss-reject/10 px-1.5 py-0.5 text-xs text-oss-reject"
            >
              {gate.replace(/^GATE_/, '').replace(/_/g, ' ')}
            </span>
          ))}
          {sample.failed_gates.length > 3 && (
            <span className="text-xs text-oss-muted">
              +{sample.failed_gates.length - 3} more
            </span>
          )}
        </div>
      )}
    </Link>
  )
}

function GateFailureCard({ failure }: { failure: GateFailureSample }) {
  const [expanded, setExpanded] = useState(false)
  
  return (
    <div className="rounded-lg bg-oss-bg">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between p-3 text-left transition-colors hover:bg-oss-bg/80"
      >
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-oss-reject/10">
            <XCircle className="h-4 w-4 text-oss-reject" />
          </div>
          <div>
            <p className="text-sm font-medium text-oss-text">
              {failure.gate_id.replace(/^GATE_/, '').replace(/_/g, ' ')}
            </p>
            <p className="text-xs text-oss-muted">
              {failure.failure_count} failure{failure.failure_count !== 1 ? 's' : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-oss-reject/10 px-2 py-0.5 text-xs font-mono text-oss-reject">
            {failure.failure_count}
          </span>
          <ChevronRight className={clsx(
            'h-4 w-4 text-oss-muted transition-transform',
            expanded && 'rotate-90'
          )} />
        </div>
      </button>
      
      {expanded && failure.sample_evaluations.length > 0 && (
        <div className="border-t border-oss-border px-3 pb-3 pt-2">
          <p className="mb-2 text-xs text-oss-muted">Sample evaluations:</p>
          <div className="space-y-2">
            {failure.sample_evaluations.map((sample) => (
              <TraceSampleCard key={sample.evaluation_id} sample={sample} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function EmptyState({ tab }: { tab: TabConfig }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <div className="rounded-full bg-oss-bg p-3 text-oss-muted">
        {tab.icon}
      </div>
      <p className="mt-3 text-sm text-oss-muted">
        No {tab.label.toLowerCase()} found
      </p>
      <p className="mt-1 text-xs text-oss-muted/70">
        Run pipeline evaluations to see data here
      </p>
    </div>
  )
}

export default function RepresentativeTraces() {
  const [activeTab, setActiveTab] = useState<TabId>('gate_failures')
  const { data, isLoading, error } = useRepresentativeTraces()
  
  if (error) {
    return (
      <div className="rounded-xl border border-oss-border bg-oss-surface p-6">
        <div className="flex items-center gap-2 text-oss-reject">
          <AlertTriangle className="h-5 w-5" />
          <span className="text-sm font-medium">Failed to load traces</span>
        </div>
      </div>
    )
  }
  
  const activeTabConfig = TABS.find(t => t.id === activeTab)!
  
  const getContent = () => {
    if (isLoading) {
      return (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-oss-accent" />
        </div>
      )
    }
    
    if (!data) {
      return <EmptyState tab={activeTabConfig} />
    }
    
    switch (activeTab) {
      case 'gate_failures':
        if (data.common_gate_failures.length === 0) {
          return <EmptyState tab={activeTabConfig} />
        }
        return (
          <div className="space-y-2">
            {data.common_gate_failures.map((failure) => (
              <GateFailureCard key={failure.gate_id} failure={failure} />
            ))}
          </div>
        )
      
      case 'high_rejects':
        if (data.highest_reject_scores.length === 0) {
          return <EmptyState tab={activeTabConfig} />
        }
        return (
          <div className="space-y-2">
            {data.highest_reject_scores.map((sample) => (
              <TraceSampleCard key={sample.evaluation_id} sample={sample} />
            ))}
          </div>
        )
      
      case 'low_approves':
        if (data.lowest_approve_scores.length === 0) {
          return <EmptyState tab={activeTabConfig} />
        }
        return (
          <div className="space-y-2">
            {data.lowest_approve_scores.map((sample) => (
              <TraceSampleCard key={sample.evaluation_id} sample={sample} />
            ))}
          </div>
        )
      
      case 'tier_1':
        if (data.tier_1_approvals.length === 0) {
          return <EmptyState tab={activeTabConfig} />
        }
        return (
          <div className="space-y-2">
            {data.tier_1_approvals.map((sample) => (
              <TraceSampleCard key={sample.evaluation_id} sample={sample} />
            ))}
          </div>
        )
    }
  }
  
  return (
    <div className="rounded-xl border border-oss-border bg-oss-surface">
      {/* Header */}
      <div className="border-b border-oss-border p-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-oss-text">Representative Traces</h3>
            <p className="mt-1 text-xs text-oss-muted">
              Edge-case evaluations for debugging and analysis
            </p>
          </div>
          {data?.summary && (
            <div className="flex items-center gap-4 text-xs">
              <div className="flex items-center gap-1">
                <CheckCircle className="h-3 w-3 text-oss-approve" />
                <span className="text-oss-muted">{data.summary.total_tier_1} TIER-1</span>
              </div>
              <div className="flex items-center gap-1">
                <XCircle className="h-3 w-3 text-oss-reject" />
                <span className="text-oss-muted">{data.summary.unique_gates_failed} gates failed</span>
              </div>
            </div>
          )}
        </div>
      </div>
      
      {/* Tabs */}
      <div className="flex border-b border-oss-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={clsx(
              'flex flex-1 items-center justify-center gap-2 px-4 py-3 text-xs font-medium transition-colors',
              activeTab === tab.id
                ? `${tab.color} border-b-2 border-current bg-oss-bg/50`
                : 'text-oss-muted hover:text-oss-text'
            )}
          >
            {tab.icon}
            <span className="hidden sm:inline">{tab.label}</span>
          </button>
        ))}
      </div>
      
      {/* Tab Description */}
      <div className="border-b border-oss-border bg-oss-bg/30 px-4 py-2">
        <p className="text-xs text-oss-muted">{activeTabConfig.description}</p>
      </div>
      
      {/* Content */}
      <div className="max-h-[400px] overflow-y-auto p-4">
        {getContent()}
      </div>
    </div>
  )
}
