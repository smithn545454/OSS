/**
 * Rule Breakdown Component
 * 
 * Per spec section 5.8: Individual rule pass/fail display within gates
 */

import { CheckCircle, XCircle } from 'lucide-react'
import clsx from 'clsx'
import type { DisplayRule } from '@/lib/types'

interface RuleBreakdownProps {
  rules: DisplayRule[]
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

export function RuleBreakdown({ rules }: RuleBreakdownProps) {
  return (
    <div>
      {/* Section Header */}
      <p 
        className="text-[10px] uppercase tracking-wide mb-2"
        style={{ color: 'var(--text-disabled)' }}
      >
        Rule-Level Breakdown
      </p>

      {/* Rules Container */}
      <div className="flex flex-col gap-1.5 mb-3">
        {rules.map((rule, index) => {
          const isCritical = rule.severity === 'critical'

          return (
            <div
              key={index}
              className={clsx(
                'rule-row',
                isCritical && 'critical'
              )}
            >
              {/* Rule Name */}
              <span 
                className="text-[11px] font-mono"
                style={{ color: 'var(--text-tertiary)' }}
              >
                {rule.name}
              </span>

              {/* Rule Metrics */}
              <div className="flex gap-3 text-[11px]">
                {/* Pass Count */}
                <span 
                  className="flex items-center gap-1"
                  style={{ color: 'var(--color-success-text)' }}
                >
                  <CheckCircle className="w-3 h-3" />
                  {formatNumber(rule.passed)}
                </span>

                {/* Fail Count */}
                <span 
                  className="flex items-center gap-1"
                  style={{ color: 'var(--color-error-text)' }}
                >
                  <XCircle className="w-3 h-3" />
                  {formatNumber(rule.failed)}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
