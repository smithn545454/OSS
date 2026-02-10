/**
 * PerformanceAnalytics — Tabbed analytics dashboard.
 *
 * Tabs: Overview | By Tier | By Exit
 */

import type {
  PaperTradingMetricsResponse,
  PaperTradingTiersResponse,
  PaperTradingExitsResponse,
} from '@/lib/types'
import { CheckCircle2, XCircle } from 'lucide-react'

type Tab = 'overview' | 'tiers' | 'exits'

interface PerformanceAnalyticsProps {
  metrics: PaperTradingMetricsResponse | undefined
  tiers: PaperTradingTiersResponse | undefined
  exits: PaperTradingExitsResponse | undefined
  activeTab: Tab
  onTabChange: (tab: Tab) => void
  isLoading: boolean
}

function MetricCard({
  label,
  value,
  color,
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div
      style={{
        padding: '16px',
        background: 'var(--bg-tertiary)',
        borderRadius: 'var(--radius-md)',
      }}
    >
      <div
        style={{
          fontSize: '11px',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          marginBottom: '4px',
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: '20px',
          fontWeight: 700,
          color: color || 'var(--text-primary)',
        }}
      >
        {value}
      </div>
    </div>
  )
}

function WinRateGauge({ winRate }: { winRate: number }) {
  const size = 120
  const strokeWidth = 10
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const target = 55
  const fillPct = Math.min(winRate / 100, 1)
  const targetPct = target / 100

  const color =
    winRate >= 55
      ? 'var(--color-success-text)'
      : winRate >= 45
        ? '#eab308'
        : 'var(--color-error-text)'

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '8px',
      }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--bg-tertiary)"
          strokeWidth={strokeWidth}
        />
        {/* Fill arc */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={`${fillPct * circumference} ${circumference}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        {/* Target marker (dashed) */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--text-muted)"
          strokeWidth={2}
          strokeDasharray={`2 ${circumference - 2}`}
          strokeDashoffset={-targetPct * circumference}
          opacity={0.5}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        {/* Center text */}
        <text
          x={size / 2}
          y={size / 2 - 4}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fontSize: '24px',
            fontWeight: 700,
            fill: color,
          }}
        >
          {winRate.toFixed(0)}%
        </text>
        <text
          x={size / 2}
          y={size / 2 + 16}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fontSize: '10px',
            fill: 'var(--text-muted)',
          }}
        >
          Win Rate
        </text>
      </svg>
      <div
        style={{
          fontSize: '11px',
          color: 'var(--text-muted)',
        }}
      >
        Target: {target}%
      </div>
    </div>
  )
}

function OverviewTab({
  metrics,
}: {
  metrics: PaperTradingMetricsResponse
}) {
  const m = metrics.metrics
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div
        style={{
          display: 'flex',
          gap: '24px',
          alignItems: 'flex-start',
        }}
      >
        <WinRateGauge winRate={m.win_rate} />
        <div
          style={{
            flex: 1,
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '12px',
          }}
        >
          <MetricCard
            label="Win Rate"
            value={`${m.win_rate.toFixed(1)}%`}
            color={
              m.win_rate >= 55
                ? 'var(--color-success-text)'
                : undefined
            }
          />
          <MetricCard
            label="Loss Rate"
            value={`${m.loss_rate.toFixed(1)}%`}
          />
          <MetricCard
            label="Avg Win"
            value={`+${m.avg_win_pct.toFixed(1)}%`}
            color="var(--color-success-text)"
          />
          <MetricCard
            label="Avg Loss"
            value={`${m.avg_loss_pct.toFixed(1)}%`}
            color="var(--color-error-text)"
          />
          <MetricCard
            label="Expectancy"
            value={`${m.expectancy >= 0 ? '+' : ''}${m.expectancy.toFixed(2)}%`}
            color={
              m.expectancy >= 0
                ? 'var(--color-success-text)'
                : 'var(--color-error-text)'
            }
          />
          <MetricCard
            label="Profit Factor"
            value={
              m.profit_factor != null
                ? m.profit_factor.toFixed(2)
                : 'N/A'
            }
            color={
              (m.profit_factor ?? 0) > 1
                ? 'var(--color-success-text)'
                : undefined
            }
          />
        </div>
      </div>

      {/* APPROVE vs WATCH */}
      <div>
        <h4
          style={{
            fontSize: '13px',
            fontWeight: 600,
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            marginBottom: '12px',
          }}
        >
          Verdict Comparison
        </h4>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '12px',
          }}
        >
          <VerdictCard
            label="APPROVE"
            data={m.approve_performance}
            badgeColor="rgba(34, 197, 94, 0.15)"
            textColor="var(--color-success-text)"
          />
          <VerdictCard
            label="WATCH"
            data={m.watch_performance}
            badgeColor="rgba(234, 179, 8, 0.15)"
            textColor="#eab308"
          />
        </div>
      </div>
    </div>
  )
}

function VerdictCard({
  label,
  data,
  badgeColor,
  textColor,
}: {
  label: string
  data: Record<string, number>
  badgeColor: string
  textColor: string
}) {
  return (
    <div
      style={{
        padding: '16px',
        background: 'var(--bg-tertiary)',
        borderRadius: 'var(--radius-md)',
      }}
    >
      <span
        style={{
          fontSize: '12px',
          fontWeight: 600,
          padding: '2px 8px',
          borderRadius: '9999px',
          background: badgeColor,
          color: textColor,
        }}
      >
        {label}
      </span>
      <div
        style={{
          marginTop: '12px',
          display: 'flex',
          gap: '24px',
          fontSize: '13px',
        }}
      >
        <div>
          <span style={{ color: 'var(--text-muted)' }}>Win Rate: </span>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {(data.win_rate ?? 0).toFixed(1)}%
          </span>
        </div>
        <div>
          <span style={{ color: 'var(--text-muted)' }}>Avg Return: </span>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {(data.avg_return ?? 0).toFixed(1)}%
          </span>
        </div>
        <div>
          <span style={{ color: 'var(--text-muted)' }}>Count: </span>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {data.count ?? 0}
          </span>
        </div>
      </div>
    </div>
  )
}

function TiersTab({ tiers }: { tiers: PaperTradingTiersResponse }) {
  const tierOrder = ['TIER_1', 'TIER_2', 'TIER_3']
  const tierData = tierOrder
    .filter((t) => t in tiers.tier_comparison)
    .map((t) => ({
      name: t,
      ...tiers.tier_comparison[t],
    }))

  // Check hierarchy: TIER_1 > TIER_2 > TIER_3 in win_rate
  const hierarchyValid =
    tierData.length >= 2 &&
    tierData.every(
      (t, i) =>
        i === 0 ||
        (tierData[i - 1].closed > 0 && t.closed > 0
          ? tierData[i - 1].win_rate >= t.win_rate
          : true)
    )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          fontSize: '13px',
        }}
      >
        {hierarchyValid ? (
          <>
            <CheckCircle2
              style={{
                width: 16,
                height: 16,
                color: 'var(--color-success-text)',
              }}
            />
            <span style={{ color: 'var(--color-success-text)' }}>
              Tier hierarchy validated — TIER_1 &gt; TIER_2 &gt; TIER_3
            </span>
          </>
        ) : (
          <>
            <XCircle
              style={{
                width: 16,
                height: 16,
                color: 'var(--color-error-text)',
              }}
            />
            <span style={{ color: 'var(--color-error-text)' }}>
              Tier hierarchy not met — tiers are not properly ordered
            </span>
          </>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '12px',
        }}
      >
        {tierData.map((tier) => (
          <div
            key={tier.name}
            style={{
              padding: '20px',
              background: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                fontSize: '14px',
                fontWeight: 700,
                color: 'var(--text-primary)',
                marginBottom: '16px',
              }}
            >
              {tier.name.replace('_', ' ')}
            </div>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                fontSize: '13px',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                }}
              >
                <span style={{ color: 'var(--text-muted)' }}>Win Rate</span>
                <span
                  style={{
                    fontWeight: 600,
                    color:
                      tier.win_rate >= 55
                        ? 'var(--color-success-text)'
                        : 'var(--text-primary)',
                  }}
                >
                  {tier.win_rate.toFixed(1)}%
                </span>
              </div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                }}
              >
                <span style={{ color: 'var(--text-muted)' }}>Avg Return</span>
                <span
                  style={{
                    fontWeight: 600,
                    color:
                      tier.avg_return >= 0
                        ? 'var(--color-success-text)'
                        : 'var(--color-error-text)',
                  }}
                >
                  {tier.avg_return >= 0 ? '+' : ''}
                  {tier.avg_return.toFixed(1)}%
                </span>
              </div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                }}
              >
                <span style={{ color: 'var(--text-muted)' }}>
                  Positions
                </span>
                <span style={{ color: 'var(--text-primary)' }}>
                  {tier.total}
                </span>
              </div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                }}
              >
                <span style={{ color: 'var(--text-muted)' }}>W/L</span>
                <span style={{ color: 'var(--text-primary)' }}>
                  {tier.wins}/{tier.losses}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ExitsTab({ exits }: { exits: PaperTradingExitsResponse }) {
  const exitEntries = Object.entries(exits.exit_analysis)
  const maxCount = Math.max(...exitEntries.map(([, d]) => d.count), 1)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Exit distribution bars */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {exitEntries.map(([reason, data]) => {
          const barWidth = (data.count / maxCount) * 100
          const isPositive = data.avg_return >= 0

          return (
            <div key={reason}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  marginBottom: '4px',
                  fontSize: '13px',
                }}
              >
                <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                  {reason.replace(/_/g, ' ')}
                </span>
                <span style={{ color: 'var(--text-muted)' }}>
                  {data.count} trades · avg{' '}
                  <span
                    style={{
                      color: isPositive
                        ? 'var(--color-success-text)'
                        : 'var(--color-error-text)',
                    }}
                  >
                    {isPositive ? '+' : ''}
                    {data.avg_return.toFixed(1)}%
                  </span>
                </span>
              </div>
              <div
                style={{
                  height: '8px',
                  background: 'var(--bg-tertiary)',
                  borderRadius: '4px',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${barWidth}%`,
                    background: isPositive
                      ? 'var(--color-success-text)'
                      : 'var(--color-error-text)',
                    borderRadius: '4px',
                    opacity: 0.7,
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>

      {/* Exit insights */}
      {exits.insights.length > 0 && (
        <div>
          <h4
            style={{
              fontSize: '13px',
              fontWeight: 600,
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginBottom: '8px',
            }}
          >
            Insights
          </h4>
          <ul
            style={{
              margin: 0,
              paddingLeft: '20px',
              fontSize: '13px',
              color: 'var(--text-secondary)',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
            }}
          >
            {exits.insights.map((insight, i) => (
              <li key={i}>{insight}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function PerformanceAnalytics({
  metrics,
  tiers,
  exits,
  activeTab,
  onTabChange,
  isLoading,
}: PerformanceAnalyticsProps) {
  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'tiers', label: 'By Tier' },
    { id: 'exits', label: 'By Exit' },
  ]

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        padding: '24px',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '20px',
        }}
      >
        <h3
          style={{
            fontSize: '16px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            margin: 0,
          }}
        >
          Performance Analytics
        </h3>

        {/* Tab switches */}
        <div
          style={{
            display: 'flex',
            gap: '2px',
            background: 'var(--bg-tertiary)',
            padding: '2px',
            borderRadius: 'var(--radius-sm)',
          }}
        >
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              style={{
                padding: '6px 14px',
                fontSize: '12px',
                fontWeight: 500,
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                cursor: 'pointer',
                background:
                  activeTab === tab.id
                    ? 'var(--bg-secondary)'
                    : 'transparent',
                color:
                  activeTab === tab.id
                    ? 'var(--text-primary)'
                    : 'var(--text-muted)',
                boxShadow:
                  activeTab === tab.id
                    ? '0 1px 2px rgba(0,0,0,0.1)'
                    : 'none',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div
          style={{
            height: '200px',
            background: 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-md)',
            animation: 'pulse 2s ease-in-out infinite',
          }}
        />
      ) : (
        <>
          {activeTab === 'overview' && metrics && (
            <OverviewTab metrics={metrics} />
          )}
          {activeTab === 'tiers' && tiers && <TiersTab tiers={tiers} />}
          {activeTab === 'exits' && exits && <ExitsTab exits={exits} />}
          {!metrics && !tiers && !exits && (
            <div
              style={{
                padding: '40px',
                textAlign: 'center',
                color: 'var(--text-muted)',
                fontSize: '14px',
              }}
            >
              No performance data available yet.
            </div>
          )}
        </>
      )}
    </div>
  )
}
