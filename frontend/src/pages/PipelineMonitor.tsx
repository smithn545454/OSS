/**
 * Pipeline Monitor Page
 *
 * Per oss-pipeline-monitor-requirements.md, this page provides:
 * 1. Operational Monitoring: Visual confirmation that data flows correctly
 * 2. Diagnostic Investigation: Deep inspection of where contracts are filtered
 */

import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { usePageTitle } from '@/hooks/usePageTitle'

import { usePipelineMonitorRuns, usePipelineAggregate, usePipelineRunDetail } from '@/hooks/useApi'
import type {
  TimeRangeOption,
  PipelineMonitorScannerType,
  PipelineRunListItem,
  PipelineMonitorData,
  Verdict,
  DTEBucket,
  OptionType,
} from '@/lib/types'

import { PageHeader } from '@/components/pipeline/PageHeader'
import { FilterBar } from '@/components/pipeline/FilterBar'
import { EvaluationFilters } from '@/components/pipeline/EvaluationFilters'
import { RecentRunsSidebar } from '@/components/pipeline/RecentRunsSidebar'
import { PipelineVisualization } from '@/components/pipeline/PipelineVisualization'

// Local storage keys
const STORAGE_KEY_TIME_RANGE = 'oss-pipeline-time-range'
const STORAGE_KEY_SCANNER = 'oss-pipeline-scanner'
const STORAGE_KEY_VERDICT = 'oss-pipeline-verdict'
const STORAGE_KEY_DTE = 'oss-pipeline-dte'
const STORAGE_KEY_SIDE = 'oss-pipeline-side'
const STORAGE_KEY_CUSTOM_RANGE = 'oss-pipeline-custom-range'

// Default filter values
const DEFAULT_TIME_RANGE: TimeRangeOption = 'today'
const DEFAULT_SCANNER: PipelineMonitorScannerType = 'all'

/**
 * Load persisted filter from localStorage
 */
function loadFromStorage<T>(key: string, defaultValue: T): T {
  try {
    const stored = localStorage.getItem(key)
    if (stored) {
      return JSON.parse(stored) as T
    }
  } catch {
    // Ignore parse errors
  }
  return defaultValue
}

/**
 * Save filter to localStorage
 */
function saveToStorage<T>(key: string, value: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Ignore storage errors
  }
}

/**
 * Check if any stage or gate has an anomaly
 */
function hasAnyAnomaly(data: PipelineMonitorData | undefined): boolean {
  if (!data) return false
  return data.stages.some(stage => stage.status === 'anomaly')
}

export default function PipelineMonitor() {
  usePageTitle('Pipeline')
  const [searchParams, setSearchParams] = useSearchParams()

  // Initialize custom date range from URL params, then localStorage
  const [customDateRange, setCustomDateRange] = useState<{ start: string; end: string } | null>(() => {
    const urlStart = searchParams.get('start')
    const urlEnd = searchParams.get('end')
    if (urlStart && urlEnd) return { start: urlStart, end: urlEnd }
    return loadFromStorage(STORAGE_KEY_CUSTOM_RANGE, null)
  })

  // Initialize state from URL params, then localStorage, then defaults
  const [timeRange, setTimeRange] = useState<TimeRangeOption>(() => {
    const urlTime = searchParams.get('time') as TimeRangeOption | null
    if (urlTime) return urlTime
    const stored = loadFromStorage(STORAGE_KEY_TIME_RANGE, DEFAULT_TIME_RANGE)
    // If stored is 'custom' but no custom range exists, fall back to 'today'
    if (stored === 'custom' && !customDateRange) return DEFAULT_TIME_RANGE
    return stored
  })

  const [scannerFilter, setScannerFilter] = useState<PipelineMonitorScannerType>(() => {
    const urlScanner = searchParams.get('scanner') as PipelineMonitorScannerType | null
    if (urlScanner) return urlScanner
    return loadFromStorage(STORAGE_KEY_SCANNER, DEFAULT_SCANNER)
  })

  const [selectedRunId, setSelectedRunId] = useState<string | null>(() => {
    return searchParams.get('run')
  })

  const [verdictFilters, setVerdictFilters] = useState<Verdict[]>(() => {
    return loadFromStorage(STORAGE_KEY_VERDICT, [])
  })

  const [dteBucketFilters, setDteBucketFilters] = useState<DTEBucket[]>(() => {
    return loadFromStorage(STORAGE_KEY_DTE, [])
  })

  const [optionSideFilters, setOptionSideFilters] = useState<OptionType[]>(() => {
    return loadFromStorage(STORAGE_KEY_SIDE, [])
  })

  const [expandedGates, setExpandedGates] = useState<Set<string>>(new Set())
  const [expandedOverlaps, setExpandedOverlaps] = useState<Set<string>>(new Set())

  // Determine API params for custom range
  const customStart = timeRange === 'custom' ? customDateRange?.start : undefined
  const customEnd = timeRange === 'custom' ? customDateRange?.end : undefined

  // Fetch runs list
  const { data: runsData, isLoading: runsLoading } = usePipelineMonitorRuns({
    time: timeRange,
    scanner: scannerFilter,
    limit: 50,
    start: customStart,
    end: customEnd,
  })

  // Fetch aggregate data (when no run selected)
  const { data: aggregateData, isLoading: aggregateLoading } = usePipelineAggregate({
    time: timeRange,
    scanner: scannerFilter,
    start: customStart,
    end: customEnd,
    verdict: verdictFilters,
    dte_bucket: dteBucketFilters,
    option_side: optionSideFilters,
    enabled: !selectedRunId,
  })

  // Fetch run detail (when run selected)
  const { data: runDetailData, isLoading: runDetailLoading } = usePipelineRunDetail(
    selectedRunId,
    {
      verdict: verdictFilters,
      dte_bucket: dteBucketFilters,
      option_side: optionSideFilters,
    }
  )

  // Determine which data to display
  // aggregateData and runDetailData are already destructured from useQuery's { data } property
  const pipelineData: PipelineMonitorData | undefined = selectedRunId
    ? runDetailData
    : aggregateData
  const isLoading = runsLoading || (selectedRunId ? runDetailLoading : aggregateLoading)

  // Sync state to URL and localStorage
  useEffect(() => {
    const params = new URLSearchParams()
    if (timeRange !== DEFAULT_TIME_RANGE) params.set('time', timeRange)
    if (scannerFilter !== DEFAULT_SCANNER) params.set('scanner', scannerFilter)
    if (selectedRunId) params.set('run', selectedRunId)
    if (timeRange === 'custom' && customDateRange) {
      params.set('start', customDateRange.start)
      params.set('end', customDateRange.end)
    }

    setSearchParams(params, { replace: true })
  }, [timeRange, scannerFilter, selectedRunId, customDateRange, setSearchParams])

  useEffect(() => {
    saveToStorage(STORAGE_KEY_TIME_RANGE, timeRange)
    saveToStorage(STORAGE_KEY_SCANNER, scannerFilter)
    saveToStorage(STORAGE_KEY_VERDICT, verdictFilters)
    saveToStorage(STORAGE_KEY_DTE, dteBucketFilters)
    saveToStorage(STORAGE_KEY_SIDE, optionSideFilters)
    saveToStorage(STORAGE_KEY_CUSTOM_RANGE, customDateRange)
  }, [timeRange, scannerFilter, verdictFilters, dteBucketFilters, optionSideFilters, customDateRange])

  // Handlers
  const handleTimeRangeChange = useCallback((value: TimeRangeOption) => {
    setTimeRange(value)
    setSelectedRunId(null) // Deselect run when changing time range
  }, [])

  const handleCustomDateChange = useCallback((range: { start: string; end: string } | null) => {
    setCustomDateRange(range)
    if (range) {
      setTimeRange('custom')
      setSelectedRunId(null)
    }
  }, [])

  const handleScannerChange = useCallback((value: PipelineMonitorScannerType) => {
    setScannerFilter(value)
    setSelectedRunId(null) // Deselect run when changing scanner
  }, [])

  const handleRunSelect = useCallback((run: PipelineRunListItem) => {
    setSelectedRunId(prev => prev === run.id ? null : run.id)
    setExpandedGates(new Set()) // Reset expanded state
    setExpandedOverlaps(new Set())
  }, [])

  const handleVerdictToggle = useCallback((verdict: Verdict) => {
    setVerdictFilters(prev =>
      prev.includes(verdict)
        ? prev.filter(v => v !== verdict)
        : [...prev, verdict]
    )
  }, [])

  const handleDteBucketToggle = useCallback((bucket: DTEBucket) => {
    setDteBucketFilters(prev =>
      prev.includes(bucket)
        ? prev.filter(b => b !== bucket)
        : [...prev, bucket]
    )
  }, [])

  const handleOptionSideToggle = useCallback((side: OptionType) => {
    setOptionSideFilters(prev =>
      prev.includes(side)
        ? prev.filter(s => s !== side)
        : [...prev, side]
    )
  }, [])

  const handleGateToggle = useCallback((gateId: string) => {
    setExpandedGates(prev => {
      const next = new Set(prev)
      if (next.has(gateId)) {
        next.delete(gateId)
      } else {
        next.add(gateId)
      }
      return next
    })
  }, [])

  const handleOverlapToggle = useCallback((gateId: string) => {
    setExpandedOverlaps(prev => {
      const next = new Set(prev)
      if (next.has(gateId)) {
        next.delete(gateId)
      } else {
        next.add(gateId)
      }
      return next
    })
  }, [])

  const handleClearFilters = useCallback(() => {
    setVerdictFilters([])
    setDteBucketFilters([])
    setOptionSideFilters([])
  }, [])

  // Determine system status
  const hasAnomaly = hasAnyAnomaly(pipelineData)

  return (
    <div className="pipeline-monitor min-h-screen">
      {/* Page Header with Status Indicator */}
      <PageHeader hasAnomaly={hasAnomaly} />

      {/* Time & Scanner Filter Bar */}
      <FilterBar
        timeRange={timeRange}
        scannerFilter={scannerFilter}
        onTimeRangeChange={handleTimeRangeChange}
        onScannerChange={handleScannerChange}
        customDateRange={customDateRange}
        onCustomDateChange={handleCustomDateChange}
      />

      {/* Evaluation Filters */}
      <EvaluationFilters
        verdictFilters={verdictFilters}
        dteBucketFilters={dteBucketFilters}
        optionSideFilters={optionSideFilters}
        onVerdictToggle={handleVerdictToggle}
        onDteBucketToggle={handleDteBucketToggle}
        onOptionSideToggle={handleOptionSideToggle}
        onClearAll={handleClearFilters}
      />

      {/* Main Content: Sidebar + Visualization */}
      <div className="flex gap-6 mt-6">
        {/* Recent Runs Sidebar */}
        <RecentRunsSidebar
          runs={runsData?.runs || []}
          selectedRunId={selectedRunId}
          onSelectRun={handleRunSelect}
          isLoading={runsLoading}
        />

        {/* Pipeline Visualization Panel */}
        <div className="flex-1">
          <PipelineVisualization
            data={pipelineData}
            isLoading={isLoading}
            expandedGates={expandedGates}
            expandedOverlaps={expandedOverlaps}
            onGateToggle={handleGateToggle}
            onOverlapToggle={handleOverlapToggle}
          />
        </div>
      </div>
    </div>
  )
}
