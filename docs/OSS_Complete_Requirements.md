# OSS
## Complete Requirements Specification

**Document Version:** 2.0.0  
**Last Updated:** January 29, 2026  
**Classification:** Internal Engineering Specification  

---

# Table of Contents

1. Executive Summary
2. System Purpose & Philosophy
3. Non-Negotiable Principles
4. Success Criteria & Metrics
5. Scope Definition
6. Data Sources
7. Configuration & Policy System
8. Canonical Data Schemas
9. Pipeline Architecture Overview
10. Stage 1: Opportunity Discovery (Scanners)
11. Stage 2: Underlying Quality Filters
12. Stage 3: Contract Selection
13. Stage 4: Feature Computation
14. Stage 5: Pillar Scoring
15. Stage 6: Hard Gates
16. Stage 7: Decision Logic
17. Stage 8: Paper Trading & Performance Tracking
18. Observability & Telemetry
19. User Interface Requirements
20. Weekly Calibration Process
21. LLM Integration (Trade Thesis)
22. Implementation Checklist
23. Glossary
24. Appendix A: Reason Codes Reference
25. Appendix B: Default Configuration Values
26. Appendix C: Example Calculations

---

# 1. Executive Summary

## 1.1 What is OSS?

OSS (Option Scanner System) is a **cost-controlled, deterministic, fully observable** system that identifies **single-leg long options trades** (buying calls or puts) with a higher probability of profitability.

## 1.2 What OSS Does

1. **Discovers** ticker-level opportunities through four specialized scanners
2. **Selects** multiple candidate contracts per ticker across DTE buckets
3. **Scores** each contract using three pillar agents (Directional, Volatility, Structure)
4. **Gates** contracts through configurable hard filters that reject structurally unworkable trades
5. **Decides** with a deterministic verdict: APPROVE / WATCH / REJECT
6. **Explains** every decision with numeric evidence and reason codes
7. **Tracks** approved and watched trades via paper trading for continuous improvement

## 1.3 What OSS Does NOT Do

- Place real trades (recommendations only)
- Use machine learning for decisions (deterministic rules only)
- Support multi-leg strategies (single-leg only in v2)
- Auto-execute threshold changes (human approval required)

## 1.4 Design Philosophy

The core philosophy is **"explainability over optimization."** A trader must be able to look at any recommendation and understand exactly why it was made. Every threshold, weight, and decision point is:

- Configurable via UI (no code changes)
- Versioned for audit trail
- Visible with measured value vs. threshold

---

# 2. System Purpose & Philosophy

## 2.1 The Problem We're Solving

Most retail options traders lose money because they:

1. Buy options that require unrealistic price moves to profit
2. Overpay for implied volatility relative to realized movement
3. Trade illiquid contracts with wide bid-ask spreads
4. Enter positions without directional edge
5. Cannot explain why they entered a trade (gut feeling)

OSS addresses each of these systematically.

## 2.2 Core Value Proposition

OSS provides **structured, repeatable trade identification** by:

- Quantifying the required move vs. expected move (Move Sufficiency)
- Measuring IV vs. RV to detect overpriced options
- Enforcing liquidity minimums to ensure executable trades
- Scoring directional conviction using multiple technical indicators
- Documenting every decision for post-trade analysis

## 2.3 The Deterministic Commitment

**Same inputs + same policy = same outputs. Always.**

This means:
- No randomness in any calculation
- No LLM involvement in scoring or decisions
- No hidden state that affects outcomes
- Complete reproducibility for backtesting

---

# 3. Non-Negotiable Principles

These principles are **immutable** and override any other requirement:

| # | Principle | Implication |
|---|-----------|-------------|
| 1 | **Single-leg long options only** | No spreads, no combos, no short positions |
| 2 | **Deterministic decisions** | Same inputs + same policy → identical outputs |
| 3 | **No LLM in decision logic** | LLM used only for post-decision trade thesis on APPROVEs |
| 4 | **Hard gates dominate** | Any failed gate → REJECT, regardless of scores |
| 5 | **Everything is explainable** | Every score emits reason codes and measured values |
| 6 | **Config over code** | All thresholds editable in UI, no code deploys for tuning |
| 7 | **Evaluate both sides** | Always evaluate CALL and PUT for each opportunity |
| 8 | **Paper trade before real trade** | No real money features until paper tracking proves value |

---

# 4. Success Criteria & Metrics

## 4.1 Explainability Success Criteria

For any evaluated contract, a user MUST be able to see:

| Requirement | Description |
|-------------|-------------|
| Decision | Exact verdict (APPROVE/WATCH/REJECT) |
| Gate Results | All gates passed/failed with measured vs. threshold values |
| Pillar Scores | Each pillar score (0-100) with top contributors |
| Feature Values | Raw feature values used in scoring |
| Policy Reference | Policy version and effective config snapshot |
| Reason Codes | Primary and supporting reason codes |

## 4.2 Stage-Level Visibility

For each pipeline run and date:

| Metric | Description |
|--------|-------------|
| Counts In/Out | How many items entered and exited each stage |
| Drop-off Reasons | Pareto chart of why items were filtered |
| Breakdown Views | By scanner type, ticker, DTE bucket, option side |
| Timing | Processing time per stage |

## 4.3 Safe Iteration Criteria

Changing a threshold must:

1. Be possible through the Config UI (no code changes)
2. Create a new policy version automatically
3. Enable counterfactual analysis ("what would have changed?")
4. Be reversible (can restore previous policy)

## 4.4 Cost Discipline Targets

| Metric | Target |
|--------|--------|
| LLM calls per day | Only on APPROVE verdicts (typically < 20) |
| API calls per evaluation | Minimize Polygon calls via batching |
| Max evaluations per day | Configurable ceiling (default: 10000) |
| Monthly compute budget | Configurable ceiling |

## 4.5 Trading Performance Targets (Paper Trading)

| Metric | Target (Paper Trading Phase) |
|--------|------------------------------|
| APPROVE win rate | > 55% |
| APPROVE average return | > 25% |
| WATCH → would-have-been-APPROVE rate | Track for threshold tuning |
| REJECT false negative rate | < 10% of sampled REJECTs would have been winners |

---

# 5. Scope Definition

## 5.1 In Scope for v2

| Category | Items |
|----------|-------|
| **Markets** | US equity options (via Polygon) |
| **Position Types** | Long calls, long puts (single contract) |
| **Scanners** | Unusual Volume, Breakout/Breakdown, Compression→Expansion, Cheap Options |
| **Evaluation Sides** | Both CALL and PUT for every opportunity |
| **Contract Selection** | Multiple contracts per ticker (Top K per DTE bucket per side) |
| **Scoring** | Three pillar agents (Directional, Volatility, Structure) |
| **Filtering** | Configurable hard gates |
| **Decision Output** | APPROVE / WATCH / REJECT with full explanation |
| **Tracking** | Paper trading for APPROVE + WATCH; shadow tracking for sample REJECTs |
| **Observability** | Full pipeline telemetry, funnel dashboards |
| **Configuration** | UI-based policy management with versioning |

## 5.2 Out of Scope for v2

| Category | Reason |
|----------|--------|
| Automated execution | Risk management; manual trading first |
| Multi-leg strategies | Complexity; single-leg mastery first |
| Social sentiment / news | Adds noise; minimal catalysts only |
| Automatic threshold tuning | Human judgment required; suggest-only in v2 |
| Options on futures/indices | Focus on equities first |
| Pre-market / after-hours | Liquidity concerns |

---

# 6. Data Sources

## 6.1 Primary Source: Polygon.io

Polygon is the **single source of truth** for all market data.

### 6.1.1 Options Data from Polygon

| Data Type | Endpoint | Fields Used |
|-----------|----------|-------------|
| Options Chain | `/v3/snapshot/options/{underlyingAsset}` | All contracts for a ticker |
| Contract Snapshot | `/v3/snapshot/options/{underlyingAsset}/{optionsTicker}` | Bid, ask, mid, IV, Greeks, OI, volume |
| Option OHLCV | `/v2/aggs/ticker/{optionsTicker}/range/1/day` | Historical option prices |
| Greeks | From snapshot | Delta, gamma, theta, vega |

### 6.1.2 Underlying Data from Polygon

| Data Type | Endpoint | Fields Used |
|-----------|----------|-------------|
| Daily OHLCV | `/v2/aggs/ticker/{ticker}/range/1/day` | Open, high, low, close, volume |
| Previous Close | `/v2/aggs/ticker/{ticker}/prev` | Quick current price reference |

### 6.1.3 Corporate Actions (Limited)

| Data Type | Source | Usage |
|-----------|--------|-------|
| Earnings Date | External calendar or derived | Catalyst timing only |
| SEC Filings | EDGAR (metadata only) | Filing type and date |

## 6.2 Data Quality Requirements

| Requirement | Implementation |
|-------------|----------------|
| **Staleness Check** | Reject snapshots older than market open time on evaluation day |
| **Completeness Check** | Require all Greek values present (non-null) |
| **Coherence Check** | Validate Greeks are mathematically consistent (see GATE_GREEKS_COHERENCE) |
| **Missing Bars** | Flag underlyings with >2 missing bars in 30-day lookback |

---

# 7. Configuration & Policy System

## 7.1 Policy Definition

A **policy** is a complete set of all configurable parameters that determine system behavior. Every evaluation is tagged with the policy version used.

### 7.1.1 Policy Structure

```
Policy
├── version (semantic: v2.0.0)
├── created_at (UTC timestamp)
├── created_by (user identifier)
├── scanner_config
│   ├── unusual_volume_thresholds
│   ├── breakout_thresholds
│   ├── compression_thresholds
│   └── cheap_options_thresholds
├── underlying_filter_config
├── contract_selection_config
├── feature_config
├── pillar_config
├── gate_config
├── decision_config
└── tracking_config
```

## 7.2 Policy Versioning Rules

| Rule | Description |
|------|-------------|
| Immutability | Once created, a policy version is never modified |
| Semantic Versioning | MAJOR.MINOR.PATCH (2.0.0, 2.0.1, 2.1.0) |
| Auto-increment | Saving changes creates new version automatically |
| Changelog | Every change records: field, old value, new value, user, timestamp |
| Active Policy | Only one policy is "active" at a time |

## 7.3 Policy Storage Requirements

Every evaluation record MUST store:

| Field | Description |
|-------|-------------|
| `policy_version` | The version string (e.g., "v2.0.0") |
| `policy_hash` | SHA-256 hash of full policy JSON |
| `policy_snapshot_id` | Foreign key to archived policy snapshot |

---

# 8. Canonical Data Schemas

## 8.1 Opportunity (Stage 1 Output)

```typescript
interface Opportunity {
  opportunity_id: string;           // UUID v4
  underlying_ticker: string;        // e.g., "AAPL"
  timestamp_utc: string;            // ISO 8601
  scanner_triggers: ScannerTrigger[];
  direction_hint: "CALL" | "PUT" | "NONE";
  priority_score: number;           // 0-100
  created_at: string;
}

interface ScannerTrigger {
  scanner_type: "UNUSUAL_VOLUME" | "BREAKOUT" | "BREAKDOWN" | 
                "COMPRESSION_EXPANSION" | "CHEAP_OPTIONS";
  reason_codes: string[];
  metrics: Record<string, number>;
  triggered_at: string;
}
```

## 8.2 Evaluation (One Per Contract)

```typescript
interface Evaluation {
  evaluation_id: string;
  opportunity_id: string;
  underlying_ticker: string;
  option_ticker: string;
  option_type: "CALL" | "PUT";
  expiration_date: string;
  dte: number;
  strike: number;
  underlying_price: number;
  moneyness_pct: number;
  bid: number;
  ask: number;
  mid: number;
  spread_abs: number;
  spread_pct: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  open_interest: number;
  volume: number;
  oi_5d_change_pct: number | null;
  breakeven_price: number;
  required_move_pct: number;
  expected_move_pct: number;
  feasibility_ratio: number;
  time_adjusted_feasibility: number;
  dte_bucket: "A" | "B" | "C" | "D";
  rank_score: number;
  policy_version: string;
  evaluated_at: string;
}
```

## 8.3 PillarScore

```typescript
interface PillarScore {
  evaluation_id: string;
  pillar_id: "DIRECTIONAL" | "VOLATILITY" | "STRUCTURE";
  score: number;  // 0-100
  contributors: PillarContributor[];
  tags: string[];
}

interface PillarContributor {
  feature_name: string;
  subscore: number;
  weight: number;
  weighted_contribution: number;
  raw_value: number;
  distance_from_neutral: number;
}
```

## 8.4 GateResult

```typescript
interface GateResult {
  evaluation_id: string;
  gate_id: string;
  enabled: boolean;
  passed: boolean;
  measured_value: number;
  threshold_value: number;
  operator: "gte" | "lte" | "between" | "equals";
  units: string;
  reason_code: string;
  notes: string | null;
}
```

## 8.5 Decision

```typescript
interface Decision {
  evaluation_id: string;
  verdict: "APPROVE" | "WATCH" | "REJECT";
  quality_tier: "TIER_1" | "TIER_2" | "TIER_3" | null;
  final_score: number;
  directional_score: number;
  volatility_score: number;
  structure_score: number;
  primary_reason_code: string;
  supporting_reason_codes: string[];
  failed_gates: string[];
  concentration_warnings: string[];
  policy_version: string;
  decided_at: string;
}
```

## 8.6 PaperPosition

```typescript
interface PaperPosition {
  position_id: string;
  evaluation_id: string;
  option_ticker: string;
  entry_price: number;
  entry_date: string;
  quantity: number;
  verdict_at_entry: "APPROVE" | "WATCH";
  quality_tier_at_entry: string | null;
  exit_price: number | null;
  exit_date: string | null;
  exit_reason: "PROFIT_TARGET" | "STOP_LOSS" | "TIME_EXIT" | 
               "EXPIRATION" | "MANUAL" | null;
  current_price: number;
  current_pnl_pct: number;
  max_favorable_excursion: number;
  max_adverse_excursion: number;
  days_held: number;
  status: "OPEN" | "CLOSED";
  last_updated: string;
}
```

---

# 9. Pipeline Architecture Overview

## 9.1 Stage Flow

```
Stage 1: Opportunity Discovery (Scanners)
    ↓
Stage 2: Underlying Quality Filters
    ↓
Stage 3: Contract Selection
    ↓
Stage 4: Feature Computation
    ↓
Stage 5: Pillar Scoring
    ↓
Stage 6: Hard Gates
    ↓
Stage 7: Decision Logic
    ↓
Stage 8: Paper Trading & Tracking
```

## 9.2 Stage Summary

| Stage | Name | Input | Output | Purpose |
|-------|------|-------|--------|---------|
| 1 | Opportunity Discovery | Market data | Opportunities | Find tickers worth evaluating |
| 2 | Underlying Filters | Opportunities | Filtered Opportunities | Drop low-quality underlyings |
| 3 | Contract Selection | Filtered Opportunities | Evaluations | Select multiple contracts per ticker |
| 4 | Feature Computation | Evaluations | Features | Calculate all scoring inputs |
| 5 | Pillar Scoring | Features | PillarScores | Score Directional, Volatility, Structure |
| 6 | Hard Gates | Evaluations + Features | GateResults | Binary pass/fail checks |
| 7 | Decision Logic | PillarScores + GateResults | Decisions | Final verdict + quality tier |
| 8 | Paper Trading | Decisions | PaperPositions | Track performance |
# 10. Stage 1: Opportunity Discovery (Scanners)

## 10.1 Overview

Stage 1 runs four independent scanners that identify ticker-level opportunities. Each scanner operates on specific market data and produces trigger signals when conditions are met.

**Output:** Opportunity records (ticker-level, not contract-level)

## 10.2 Scanner 1: Unusual Options Volume

### Purpose
Detect abnormal options activity that may indicate informed trading or upcoming catalysts.

### Input Data
- Options chain volume and OI (aggregate by ticker)
- 20-day historical volume averages

### Trigger Conditions (configurable)

| Condition | Default | Operator | Description |
|-----------|---------|----------|-------------|
| `volume_ratio` | 2.0 | >= | Today's total option volume / 20-day average |
| `oi_change_pct` | 15% | >= | (Today OI - Prior Day OI) / Prior Day OI |

**Trigger Logic:** Fire if `volume_ratio >= threshold` OR `oi_change_pct >= threshold`

### Direction Hint Logic

| Condition | Direction Hint |
|-----------|----------------|
| `call_put_volume_ratio >= 1.3` | CALL |
| `call_put_volume_ratio <= 0.7` | PUT |
| Otherwise | NONE |

Where `call_put_volume_ratio = total_call_volume / total_put_volume`

### Metrics to Store

```typescript
{
  today_total_options_volume: number,
  avg_20d_options_volume: number,
  volume_ratio: number,
  today_oi: number,
  prior_day_oi: number,
  oi_change_pct: number,
  call_volume: number,
  put_volume: number,
  call_put_volume_ratio: number
}
```

### Reason Codes
- `UNUSUAL_VOL_VOLUME_RATIO_EXCEEDED`
- `UNUSUAL_VOL_OI_CHANGE_EXCEEDED`
- `UNUSUAL_VOL_BOTH_CONDITIONS`

---

## 10.3 Scanner 2: Breakout / Breakdown

### Purpose
Identify confirmed range breaks using daily close prices.

### Input Data
- Daily OHLCV bars for underlying (N+1 days minimum)

### Definitions (configurable)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `N` (lookback) | 20 | Number of prior trading days for range calculation |
| `confirmation_method` | CLOSE | Use close price for confirmation |

### Calculation Rules (Exact - No Ambiguity)

```
1. Fetch daily bars for at least N+1 trading days
2. Sort by timestamp ascending
3. Identify the most recent bar as "today"
4. range_bars = the N bars immediately preceding today (NOT including today)
5. prior_N_day_high = MAX(high) over range_bars
6. prior_N_day_low = MIN(low) over range_bars
7. Breakout triggered if: today_close > prior_N_day_high
8. Breakdown triggered if: today_close < prior_N_day_low
```

### Direction Hint Logic

| Trigger | Direction Hint |
|---------|----------------|
| Breakout | CALL |
| Breakdown | PUT |

### Metrics to Store

```typescript
{
  N: number,
  prior_N_day_high: number,
  prior_N_day_low: number,
  today_close: number,
  today_volume: number,
  avg_20d_volume: number,
  volume_ratio: number,
  triggered_side: "BREAKOUT" | "BREAKDOWN"
}
```

### Reason Codes
- `BREAKOUT_CLOSE_ABOVE_RANGE`
- `BREAKDOWN_CLOSE_BELOW_RANGE`

---

## 10.4 Scanner 3: Compression → Expansion

### Purpose
Detect volatility compression (coiled spring) followed by early expansion (breakout beginning).

### Input Data
- Daily OHLCV bars for underlying (30+ days)

### Parameters (configurable)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `atr_period` | 14 | ATR calculation period |
| `compression_lookback` | 20 | Days to find ATR floor |
| `compression_multiplier` | 1.10 | ATR must be within this × floor |
| `range_lookback` | 10 | Days for range break check |
| `break_pct` | 2% | Percentage beyond range for break |

### Calculation Rules (Exact)

```
1. Compute ATR(14) series for all available bars
2. atr_floor = MIN(ATR14) over the 20 bars prior to today (not including today)
3. compression_condition = ATR14_today <= atr_floor × 1.10
4. range_bars = the 10 bars prior to today (not including today)
5. prior_range_high = MAX(high) over range_bars
6. prior_range_low = MIN(low) over range_bars
7. break_up = today_close >= prior_range_high × 1.02
8. break_down = today_close <= prior_range_low × 0.98
9. TRIGGER if compression_condition AND (break_up OR break_down)
```

### ATR Calculation (for clarity)

```
True Range (TR) = MAX of:
  - current_high - current_low
  - ABS(current_high - previous_close)
  - ABS(current_low - previous_close)

ATR(14) = Simple Moving Average of TR over 14 periods
```

### Direction Hint Logic

| Break Direction | Direction Hint |
|-----------------|----------------|
| break_up | CALL |
| break_down | PUT |

### Metrics to Store

```typescript
{
  atr_period: number,
  atr_today: number,
  atr_floor: number,
  compression_multiplier: number,
  is_compressed: boolean,
  prior_range_high: number,
  prior_range_low: number,
  today_close: number,
  break_pct: number,
  triggered_direction: "UP" | "DOWN"
}
```

### Reason Codes
- `COMPRESSION_EXPANSION_UP`
- `COMPRESSION_EXPANSION_DOWN`

---

## 10.5 Scanner 4: Cheap Options (IV vs RV)

### Purpose
Find underlyings where options are reasonably priced relative to realized volatility.

### Input Data
- Options chain (for IV proxy)
- Daily underlying closes (for RV calculation)

### Parameters (configurable)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `rv_window` | 20 | Trading days for RV calculation |
| `atm_dte_target_min` | 30 | Min DTE for ATM IV proxy |
| `atm_dte_target_max` | 45 | Max DTE for ATM IV proxy |
| `iv_rv_ratio_max` | 1.10 | Trigger if IV/RV <= this |
| `iv_percentile_max` | 40 | Trigger if IV percentile <= this |

### Calculation Rules (Exact)

**Realized Volatility (RV20):**
```
1. Fetch 21 daily closes (to get 20 returns)
2. daily_returns[i] = LN(close[i] / close[i-1])
3. std_dev = STDEV(daily_returns)
4. RV20 = std_dev × SQRT(252)  // Annualize
```

**IV Proxy:**
```
1. Filter chain to contracts with DTE between 30 and 45
2. For each side (CALL, PUT), find contract closest to ATM
3. ATM defined as: MIN(ABS(strike - underlying_price))
4. iv_proxy = AVERAGE(atm_call_iv, atm_put_iv)
```

### Trigger Conditions

Fire if: `iv_rv_ratio <= 1.10` OR `iv_percentile <= 40`

### Direction Hint
Always `NONE` (this scanner is volatility-based, not directional)

### Metrics to Store

```typescript
{
  rv20: number,
  iv_proxy: number,
  iv_rv_ratio: number,
  iv_percentile: number | null,
  atm_call_iv: number,
  atm_put_iv: number,
  atm_call_strike: number,
  atm_put_strike: number,
  atm_dte: number
}
```

### Reason Codes
- `CHEAP_OPTIONS_IV_RV_LOW`
- `CHEAP_OPTIONS_IV_PERCENTILE_LOW`
- `CHEAP_OPTIONS_BOTH_CONDITIONS`

---

## 10.6 Opportunity Merge Rules

When multiple scanners trigger for the same ticker on the same run day:

### Merge Logic

```
1. Create ONE Opportunity record per ticker
2. Aggregate all scanner_triggers[] from each scanner
3. Combine direction hints:
   - If all hints agree → use that direction
   - If hints conflict → use NONE
   - If some NONE + some directional → use the directional hint
4. Priority score calculation:
   base_priority = MAX(individual_scanner_priorities)
   bonus = 15 × (number_of_additional_scanners)
   priority_score = MIN(base_priority + bonus, 100)
```

### Scanner Base Priority Values

| Scanner | Base Priority |
|---------|---------------|
| Breakout/Breakdown + Volume Confirmation | 75 |
| Compression → Expansion | 70 |
| Unusual Volume (volume_ratio > 3.0) | 65 |
| Unusual Volume (volume_ratio 2.0-3.0) | 55 |
| Cheap Options | 50 |

---

# 11. Stage 2: Underlying Quality Filters

## 11.1 Overview

Stage 2 applies cheap filters to remove obviously unsuitable underlyings before the expensive contract selection phase.

**Input:** Opportunity list from Stage 1  
**Output:** Filtered Opportunity list

## 11.2 Filter Definitions

### Filter 1: Minimum Underlying Price

| Parameter | Default | Operator |
|-----------|---------|----------|
| `min_underlying_price` | $5.00 | >= |

**Logic:** `underlying_last_price >= $5.00`

**Rationale:** Sub-$5 stocks often have illiquid options and high relative spreads.

**Reason Code:** `FILTER_FAIL_UNDERLYING_PRICE`

---

### Filter 2: Minimum Average Dollar Volume

| Parameter | Default | Operator |
|-----------|---------|----------|
| `min_avg_dollar_volume_20d` | $20,000,000 | >= |

**Calculation:**
```
For each of the last 20 trading days:
  daily_dollar_volume[i] = close[i] × volume[i]
avg_dollar_volume = AVERAGE(daily_dollar_volume)
```

**Logic:** `avg_dollar_volume >= $20,000,000`

**Rationale:** Ensures institutional liquidity in the underlying.

**Reason Code:** `FILTER_FAIL_DOLLAR_VOLUME`

---

### Filter 3: Data Completeness

| Parameter | Default |
|-----------|---------|
| `max_missing_bars_30d` | 2 |

**Logic:** Count missing trading day bars in 30-day lookback. Fail if > 2 missing.

**Rationale:** Missing data indicates delistings, halts, or data quality issues.

**Reason Code:** `FILTER_FAIL_MISSING_BARS`

---

### Filter 4: Earnings Window (Optional, Default OFF)

| Parameter | Default | Operator |
|-----------|---------|----------|
| `exclude_earnings_within_days` | 0 (OFF) | > |

**Logic:** If parameter > 0, exclude if `days_to_earnings <= parameter`

**Rationale:** Some traders want to avoid binary earnings events.

**Reason Code:** `FILTER_FAIL_EARNINGS_WINDOW`

---

## 11.3 Telemetry Requirements

For each filter:
- `filter_name`
- `opportunities_in`
- `opportunities_passed`
- `opportunities_failed`
- `failure_reason_codes[]` with counts

---

# 12. Stage 3: Contract Selection

## 12.1 Overview

Stage 3 selects multiple contracts per ticker for full evaluation. 

**Key Requirement:** Evaluate MANY contracts per ticker, not just one.

**Input:** Filtered Opportunities from Stage 2  
**Output:** Evaluation records (one per selected contract)

## 12.2 DTE Bucket Definitions

| Bucket | Min DTE | Max DTE | Label |
|--------|---------|---------|-------|
| A | 7 | 21 | Short-term |
| B | 22 | 45 | Medium-term |
| C | 46 | 75 | Intermediate |
| D | 76 | 120 | Long-term |

## 12.3 Selection Pipeline (Per Ticker)

For each ticker, for each DTE bucket, for BOTH sides (CALL and PUT):

### Step 1: DTE Filter

Include contracts where: `bucket_min <= DTE <= bucket_max`

### Step 2: Delta Band Filter

| Side | Min Delta | Max Delta |
|------|-----------|-----------|
| CALL | 0.20 | 0.75 |
| PUT | -0.75 | -0.20 |

**Rationale for expanded range (0.20-0.75 vs original 0.25-0.55):** Deep ITM options (0.60-0.75 delta) can offer:
- Lower theta decay relative to premium
- Smaller moves needed for profitability
- Less IV sensitivity

### Step 3: Liquidity Baseline Filters

| Filter | Default | Operator |
|--------|---------|----------|
| Min Open Interest | 200 | >= |
| Min Daily Volume | 50 | >= |
| Max Spread Percent | 10% | <= |
| Min Mid Price | $0.20 | >= |

**Spread Percent Calculation:**
```
spread_pct = (ask - bid) / mid × 100
```

### Step 4: Moneyness Filter

| Side | Min Moneyness | Max Moneyness |
|------|---------------|---------------|
| CALL | -5% (5% ITM) | +15% OTM |
| PUT | -15% OTM | +5% (5% ITM) |

**Moneyness Calculation:**
```
For CALL: moneyness_pct = (strike - underlying_price) / underlying_price × 100
For PUT: moneyness_pct = (underlying_price - strike) / underlying_price × 100
```

### Step 5: Ranking and Selection

**Default K:** 3 per bucket per side

**Ranking Score (configurable weights):**

```
rank_score = (0.40 × liquidity_score) + 
             (0.35 × delta_closeness_score) + 
             (0.25 × spread_tightness_score)
```

**Liquidity Score (0-100):**
```
oi_component = MIN(LOG10(open_interest) / LOG10(10000), 1) × 50
vol_component = MIN(LOG10(volume + 1) / LOG10(1000), 1) × 50
liquidity_score = oi_component + vol_component
```

**Delta Closeness Score (0-100):**
```
target_delta = 0.45 (for CALL) or -0.45 (for PUT)
delta_distance = ABS(delta - target_delta)
max_distance = 0.30
delta_closeness_score = (1 - MIN(delta_distance / max_distance, 1)) × 100
```

**Spread Tightness Score (0-100):**
```
spread_tightness_score = (1 - MIN(spread_pct / 10, 1)) × 100
```

## 12.4 Selection Telemetry

For each ticker, persist:

```typescript
{
  underlying_ticker: string,
  contracts_in_chain: number,
  bucket_stats: {
    bucket: string,
    side: "CALL" | "PUT",
    contracts_in_dte_range: number,
    survived_delta_filter: number,
    survived_liquidity_filter: number,
    survived_moneyness_filter: number,
    selected_count: number,
    selected_contracts: {
      option_ticker: string,
      strike: number,
      dte: number,
      delta: number,
      rank_score: number
    }[]
  }[]
}
```
# 13. Stage 4: Feature Computation

## 13.1 Overview

Stage 4 computes all features needed for pillar scoring. Features are raw calculated values; scoring happens in Stage 5.

**Input:** Evaluation records from Stage 3  
**Output:** FeatureValue records attached to each Evaluation

## 13.2 Feature Categories

### Category A: Underlying Technical Features

| Feature | Calculation | Units |
|---------|-------------|-------|
| `close` | Current underlying close | dollars |
| `sma20` | 20-day Simple Moving Average of close | dollars |
| `sma50` | 50-day Simple Moving Average of close | dollars |
| `return_5d` | (close - close_5d_ago) / close_5d_ago × 100 | percent |
| `return_20d` | (close - close_20d_ago) / close_20d_ago × 100 | percent |
| `trend_aligned_bullish` | 1 if close > sma20 > sma50, else 0 | boolean |
| `trend_aligned_bearish` | 1 if close < sma20 < sma50, else 0 | boolean |
| `atr14` | 14-day Average True Range | dollars |
| `atr14_pct` | atr14 / close × 100 | percent |

### Category B: Relative Strength Features

| Feature | Calculation | Units |
|---------|-------------|-------|
| `spy_return_5d` | 5-day return of SPY | percent |
| `spy_return_20d` | 20-day return of SPY | percent |
| `rs_5d` | return_5d - spy_return_5d | percent |
| `rs_20d` | return_20d - spy_return_20d | percent |

### Category C: Volatility Features

| Feature | Calculation | Units |
|---------|-------------|-------|
| `rv20` | 20-day Realized Volatility (annualized) | decimal |
| `iv` | Contract Implied Volatility | decimal |
| `iv_rv_ratio` | iv / rv20 | ratio |
| `iv_percentile` | 252-day percentile rank of IV (if available) | percent |
| `iv_regime` | Classification (see below) | enum |

### Category D: Contract-Specific Features

| Feature | Calculation | Units |
|---------|-------------|-------|
| `mid` | (bid + ask) / 2 | dollars |
| `spread_pct` | (ask - bid) / mid × 100 | percent |
| `theta_pct` | ABS(theta) / mid × 100 | percent |
| `breakeven_price` | strike + mid (CALL) or strike - mid (PUT) | dollars |
| `required_move_pct` | ABS(breakeven_price - underlying_price) / underlying_price × 100 | percent |
| `expected_move_pct` | iv × SQRT(DTE / 365) × 100 | percent |
| `feasibility_ratio` | required_move_pct / expected_move_pct | ratio |
| `time_adjusted_feasibility` | required_move_pct / (expected_move_pct × SQRT(DTE / 30)) | ratio |
| `theta_adjusted_edge` | See calculation below | ratio |

### Category E: Liquidity Features

| Feature | Calculation | Units |
|---------|-------------|-------|
| `open_interest` | Contract OI | contracts |
| `volume` | Today's contract volume | contracts |
| `oi_5d_change_pct` | (OI_today - OI_5d_ago) / OI_5d_ago × 100 | percent |

### Category F: Catalyst Features

| Feature | Calculation | Units |
|---------|-------------|-------|
| `days_to_earnings` | Days until next earnings (null if unknown) | days |
| `recent_sec_filing` | 1 if 8-K/10-Q/10-K in last 10 trading days | boolean |

## 13.3 Special Feature Calculations

### Theta-Adjusted Edge Ratio

This feature answers: "Am I paying more in theta than I'm likely to make from delta gains?"

```
daily_expected_move = underlying_price × (iv / SQRT(252))
daily_theta_cost = ABS(theta)
daily_expected_gain = daily_expected_move × ABS(delta)

theta_adjusted_edge = daily_expected_gain / daily_theta_cost
```

**Interpretation:**
- > 1.0: Expected delta gains exceed theta costs
- < 1.0: Theta decay likely to outpace gains
- > 2.0: Strong edge

### IV Regime Classification

```python
def classify_iv_regime(iv_percentile, days_to_earnings, iv_10d_change):
    if days_to_earnings is not None and days_to_earnings <= 14:
        if iv_percentile < 50:
            return "IV_COMPRESSED_PRE_CATALYST"
        else:
            return "IV_ELEVATED_PRE_CATALYST"
    
    if iv_10d_change > 10:  # IV rose >10% in 10 days
        return "IV_TRENDING_UP"
    elif iv_10d_change < -10:
        return "IV_TRENDING_DOWN"
    
    if iv_percentile < 30:
        return "IV_LOW_REGIME"
    elif iv_percentile > 70:
        return "IV_HIGH_REGIME"
    
    return "IV_NEUTRAL_REGIME"
```

---

# 14. Stage 5: Pillar Scoring

## 14.1 Overview

Stage 5 converts features into three pillar scores (0-100). Pillars **never reject** - they only score.

**Input:** FeatureValue records  
**Output:** PillarScore records with contributors

## 14.2 Pillar 1: Directional Edge (0-100)

### Purpose
Measure how likely the underlying is to move in the option's direction within the DTE window.

### Direction Mapping Rule

| Contract Type | "Positive" Direction | "Negative" Direction |
|---------------|---------------------|---------------------|
| CALL | Bullish signals | Bearish signals |
| PUT | Bearish signals | Bullish signals |

### Subscores

**1. Trend Alignment Subscore (0-100)**

| Condition | Score (CALL) | Score (PUT) |
|-----------|--------------|-------------|
| Strong aligned trend (close > sma20 > sma50) | 90 | 10 |
| Partial alignment (close > sma20, sma20 ≤ sma50) | 65 | 35 |
| Neutral (close between SMAs) | 50 | 50 |
| Partial bearish (close < sma20, sma20 ≥ sma50) | 35 | 65 |
| Strong bearish trend (close < sma20 < sma50) | 10 | 90 |

**2. Momentum Subscore (0-100)**

DTE-adjusted momentum (use appropriate timeframe for DTE):

| DTE Bucket | Primary Momentum Feature | Weight |
|------------|-------------------------|--------|
| A (7-21) | return_5d | 70% return_5d, 30% return_20d |
| B (22-45) | return_5d + return_20d | 50% each |
| C (46-75) | return_20d | 30% return_5d, 70% return_20d |
| D (76-120) | return_20d | 20% return_5d, 80% return_20d |

**Momentum mapping (for the blended return):**

| Blended Return (CALL) | Score |
|----------------------|-------|
| ≥ +10% | 95 |
| +5% to +10% | Linear 75-95 |
| 0% to +5% | Linear 55-75 |
| -5% to 0% | Linear 35-55 |
| -10% to -5% | Linear 15-35 |
| ≤ -10% | 5 |

For PUT, invert the return sign before mapping.

**3. Signal Confirmation Subscore (0-100)**

| Scanner Trigger | Matches Contract Side | Score |
|-----------------|----------------------|-------|
| Breakout | CALL | 85 |
| Breakdown | PUT | 85 |
| Compression→Expansion Up | CALL | 75 |
| Compression→Expansion Down | PUT | 75 |
| Unusual Volume (matching direction hint) | Either | 65 |
| No matching signal | Either | 45 |
| Conflicting signal | Either | 25 |

**4. Relative Strength Subscore (0-100)**

| RS_20d (CALL) | Score |
|---------------|-------|
| ≥ +8% | 95 |
| +5% to +8% | Linear 80-95 |
| +2% to +5% | Linear 65-80 |
| -2% to +2% | Linear 45-65 |
| -5% to -2% | Linear 30-45 |
| ≤ -5% | 20 |

For PUT, invert the RS value before mapping.

**5. Catalyst Subscore (0-100)**

| Condition | Score |
|-----------|-------|
| Earnings within 7 days | 70 (high event risk/opportunity) |
| Earnings 8-14 days | 60 |
| Earnings 15-30 days | 55 |
| Recent SEC filing (10 days) | 60 |
| No catalyst | 50 |

### Directional Pillar Weights (configurable)

| Subscore | Default Weight |
|----------|----------------|
| Trend Alignment | 30% |
| Momentum | 25% |
| Signal Confirmation | 20% |
| Relative Strength | 15% |
| Catalyst | 10% |

### Score Calculation

```
directional_score = 
  (trend_subscore × 0.30) +
  (momentum_subscore × 0.25) +
  (signal_subscore × 0.20) +
  (rs_subscore × 0.15) +
  (catalyst_subscore × 0.10)
```

---

## 14.3 Pillar 2: Volatility Edge (0-100)

### Purpose
Determine if the option is fairly priced relative to realized volatility and market conditions.

### Subscores

**1. IV vs RV Subscore (0-100)**

| IV/RV Ratio | Score |
|-------------|-------|
| ≤ 0.90 | 95 (options cheap) |
| 0.90 - 1.00 | Linear 85-95 |
| 1.00 - 1.10 | Linear 70-85 |
| 1.10 - 1.25 | Linear 55-70 |
| 1.25 - 1.50 | Linear 35-55 |
| > 1.50 | 20 (options expensive) |

**2. IV Percentile Subscore (0-100)**

| IV Percentile | Score |
|---------------|-------|
| ≤ 20% | 95 |
| 20-30% | Linear 85-95 |
| 30-50% | Linear 65-85 |
| 50-70% | Linear 45-65 |
| 70-85% | Linear 30-45 |
| > 85% | 20 |

**3. IV Regime Subscore (0-100)**

| Regime | Score | Rationale |
|--------|-------|-----------|
| IV_LOW_REGIME | 80 | Favorable entry |
| IV_COMPRESSED_PRE_CATALYST | 75 | IV likely to expand |
| IV_NEUTRAL_REGIME | 60 | Neutral |
| IV_TRENDING_DOWN | 55 | Improving |
| IV_TRENDING_UP | 40 | Deteriorating |
| IV_HIGH_REGIME | 30 | Expensive |
| IV_ELEVATED_POST_CATALYST | 25 | IV crush risk |
| IV_ELEVATED_PRE_CATALYST | 35 | Already priced in |

**4. Theta-Adjusted Edge Subscore (0-100)**

| Theta-Adjusted Edge Ratio | Score |
|---------------------------|-------|
| ≥ 2.5 | 95 |
| 2.0 - 2.5 | Linear 80-95 |
| 1.5 - 2.0 | Linear 65-80 |
| 1.0 - 1.5 | Linear 50-65 |
| 0.75 - 1.0 | Linear 35-50 |
| < 0.75 | 20 |

### Volatility Pillar Weights (configurable)

| Subscore | Default Weight |
|----------|----------------|
| IV vs RV | 35% |
| IV Percentile | 25% |
| IV Regime | 20% |
| Theta-Adjusted Edge | 20% |

---

## 14.4 Pillar 3: Structure & Quality (0-100)

### Purpose
Assess tradability, liquidity, and execution quality.

### Subscores

**1. Spread Subscore (0-100)**

| Spread % | Score |
|----------|-------|
| ≤ 2% | 95 |
| 2-4% | Linear 80-95 |
| 4-6% | Linear 65-80 |
| 6-8% | Linear 50-65 |
| 8-10% | Linear 35-50 |
| > 10% | 20 |

**2. Open Interest Subscore (0-100)**

| Open Interest | Score |
|---------------|-------|
| ≥ 2000 | 95 |
| 1000-2000 | Linear 80-95 |
| 500-1000 | Linear 65-80 |
| 300-500 | Linear 50-65 |
| 200-300 | Linear 35-50 |
| < 200 | 20 |

**3. Volume Subscore (0-100)**

| Daily Volume | Score |
|--------------|-------|
| ≥ 500 | 90 |
| 300-500 | Linear 75-90 |
| 150-300 | Linear 60-75 |
| 75-150 | Linear 45-60 |
| 50-75 | Linear 35-45 |
| < 50 | 25 |

**4. Theta Burden Subscore (0-100)**

| Theta % per Day | Score |
|-----------------|-------|
| ≤ 0.5% | 90 |
| 0.5-1.0% | Linear 75-90 |
| 1.0-1.5% | Linear 60-75 |
| 1.5-2.0% | Linear 45-60 |
| 2.0-3.0% | Linear 30-45 |
| > 3.0% | 20 |

**5. Liquidity Trend Subscore (0-100)**

| OI 5-Day Change | Score |
|-----------------|-------|
| ≥ +10% | 85 (growing interest) |
| 0% to +10% | Linear 65-85 |
| -10% to 0% | Linear 50-65 |
| -20% to -10% | Linear 35-50 |
| < -20% | 25 (liquidity deteriorating) |

### Structure Pillar Weights (configurable)

| Subscore | Default Weight |
|----------|----------------|
| Spread | 30% |
| Open Interest | 25% |
| Volume | 20% |
| Theta Burden | 15% |
| Liquidity Trend | 10% |

---

## 14.5 Pillar Score Storage

Store PillarScore with top 3 contributors (by distance from neutral):

```typescript
{
  evaluation_id: "...",
  pillar_id: "DIRECTIONAL",
  score: 72,
  contributors: [
    {
      feature_name: "trend_alignment",
      subscore: 90,
      weight: 0.30,
      weighted_contribution: 27,
      raw_value: 1,
      distance_from_neutral: 40
    },
    {
      feature_name: "momentum_5d",
      subscore: 78,
      weight: 0.25,
      weighted_contribution: 19.5,
      raw_value: 7.2,
      distance_from_neutral: 28
    },
    {
      feature_name: "signal_confirmation",
      subscore: 85,
      weight: 0.20,
      weighted_contribution: 17,
      raw_value: "BREAKOUT",
      distance_from_neutral: 35
    }
  ],
  tags: ["STRONG_TREND", "BREAKOUT_CONFIRMED"]
}
```
# 15. Stage 6: Hard Gates

## 15.1 Overview

Hard gates are binary pass/fail checks. **Any failed enabled gate → REJECT**, regardless of scores.

## 15.2 Gate Definitions

### GATE_MIN_OPEN_INTEREST
- **Threshold:** 300 contracts (>=)
- **Rationale:** Ensures sufficient liquidity
- **Reason Codes:** `GATE_PASS_MIN_OI` / `GATE_FAIL_MIN_OI`

### GATE_MIN_VOLUME
- **Threshold:** 75 contracts (>=)
- **Rationale:** Active trading indicates executable prices
- **Reason Codes:** `GATE_PASS_MIN_VOLUME` / `GATE_FAIL_MIN_VOLUME`

### GATE_MAX_SPREAD_PCT
- **Threshold:** 8% (<=)
- **Rationale:** Wide spreads create P&L drag (tightened from 12%)
- **Reason Codes:** `GATE_PASS_SPREAD` / `GATE_FAIL_SPREAD`

### GATE_DTE_RANGE
- **Range:** 7-120 days
- **Rationale:** <7 DTE = gamma risk; >120 DTE = capital efficiency
- **Reason Codes:** `GATE_PASS_DTE` / `GATE_FAIL_DTE_TOO_SHORT` / `GATE_FAIL_DTE_TOO_LONG`

### GATE_MOVE_SUFFICIENCY (Critical)
- **Threshold:** time_adjusted_feasibility <= 1.25
- **Calculation:**
```
breakeven_price = strike + mid (CALL) or strike - mid (PUT)
required_move_pct = ABS(breakeven - underlying) / underlying × 100
expected_move_pct = iv × SQRT(DTE / 365) × 100
time_adjusted_feasibility = required_move_pct / (expected_move_pct × SQRT(DTE / 30))
```
- **Rationale:** Ensures required move is achievable
- **Reason Codes:** `GATE_PASS_MOVE_SUFFICIENCY` / `GATE_FAIL_MOVE_SUFFICIENCY`

### GATE_IV_PERCENTILE_MAX
- **Threshold:** 85% (<=)
- **Rationale:** High IV = expensive options
- **Reason Codes:** `GATE_PASS_IV_PERCENTILE` / `GATE_FAIL_IV_PERCENTILE`

### GATE_BREAKOUT_VOLUME (Conditional)
- **Threshold:** 1.5× average volume (>=)
- **Applies When:** Scanner trigger includes BREAKOUT/BREAKDOWN
- **Rationale:** Low-volume breakouts often fail
- **Reason Codes:** `GATE_PASS_BREAKOUT_VOLUME` / `GATE_FAIL_BREAKOUT_VOLUME` / `GATE_SKIP_NOT_BREAKOUT`

### GATE_GREEKS_COHERENCE
- **Validates:** Delta within expected range for ATM, theta < 0, vega > 0, gamma > 0
- **Rationale:** Catch bad data
- **Reason Codes:** `GATE_PASS_GREEKS_COHERENCE` / `GATE_FAIL_GREEKS_*`

### GATE_THETA_BURDEN_MAX
- **Threshold:** 4% per day (<=)
- **Calculation:** ABS(theta) / mid × 100
- **Rationale:** High theta decay is problematic
- **Reason Codes:** `GATE_PASS_THETA_BURDEN` / `GATE_FAIL_THETA_BURDEN`

---

# 16. Stage 7: Decision Logic

## 16.1 Final Score Calculation

```
final_score = (0.35 × directional_score) + (0.35 × volatility_score) + (0.30 × structure_score)
```

## 16.2 Verdict Determination

**Step 1:** Check gates - any failure = REJECT with `REJECTED_BY_GATES`

**Step 2:** Apply score bands (if gates pass):
- final_score >= 75 → **APPROVE**
- 65 <= final_score < 75 → **WATCH**
- final_score < 65 → **REJECT**

## 16.3 Quality Tier Assignment (APPROVE only)

| Tier | Criteria |
|------|----------|
| **TIER_1** | score ≥ 85, all pillars ≥ 70, spread ≤ 5% |
| **TIER_2** | score ≥ 75, all pillars ≥ 55 |
| **TIER_3** | APPROVE but one pillar < 55 |

## 16.4 Concentration Warnings

- `WARN_CONCENTRATION_SAME_TICKER`: >3 contracts same underlying
- `WARN_CONCENTRATION_DIRECTIONAL`: >70% approvals same direction

---

# 17. Stage 8: Paper Trading

## 17.1 Position Entry
- **Trigger:** Verdict = APPROVE or WATCH
- **Quantity:** 1 contract
- **Entry Price:** Mid at evaluation time

## 17.2 Daily Updates
- Fetch current price
- Calculate P&L %
- Update MFE/MAE
- Check exit conditions

## 17.3 Exit Conditions (Priority Order)
1. **Profit Target:** +50%
2. **Stop Loss:** -50%
3. **Time Exit:** DTE <= 5
4. **Expiration**

## 17.4 Performance Metrics
- Win Rate, Average Win/Loss, Expectancy
- MFE/MAE analysis
- Exit type distribution

## 17.5 Shadow Tracking
Sample REJECTs to measure false negatives:
- Random 5% of REJECTs
- All near-miss REJECTs (score 60-65)
- All single-gate-failure REJECTs
# 18. Observability & Telemetry

## 18.1 Event Types to Persist

| Event Type | Retention |
|------------|-----------|
| PipelineRun | 90 days |
| StageEvent | 90 days |
| Opportunity | Permanent |
| Evaluation | Permanent |
| FeatureValue | 90 days |
| PillarScore | Permanent |
| GateResult | Permanent |
| Decision | Permanent |
| PaperPosition | Permanent |

## 18.2 Funnel Dashboard Requirements

For each stage display:
- Items received / passed / dropped
- Drop rate percentage
- Processing time
- Failure pareto by reason code
- Breakdown by scanner, DTE bucket, option side

## 18.3 Representative Trace Sampling

| Sample Type | Count |
|-------------|-------|
| Common Gate Failures | 10 |
| Highest REJECT Scores | 10 |
| Lowest APPROVE Scores | 10 |
| TIER_1 Approvals | All |

---

# 19. User Interface Requirements

## 19.1 Evaluation Detail Page

**Sections (in order):**

1. **Header:** Ticker, verdict badge, quality tier, timestamp, policy version
2. **Final Score Bar:** 0-100 with threshold markers at 65/75
3. **Contract Card:** Strike, expiration, DTE, price, spread, IV, Greeks, OI, volume
4. **Gate Results:** Red panel if failures, green if all passed
5. **Pillar Cards:** Three cards showing score and top 3 contributors each
6. **Decision Explanation:** Reason codes and score band
7. **AI Trade Thesis:** Only for APPROVE
8. **Paper Tracking Panel:** Entry, current P&L, MFE/MAE

## 19.2 Pipeline Monitor Page

- Stage funnel visualization
- Gate failure pareto chart
- Breakdown controls (date, scanner, DTE, side)

## 19.3 Config / Policy Page

- View/edit all thresholds
- Enable/disable gates
- Save as new version
- Compare versions
- Changelog

---

# 20. Weekly Calibration Process

## 20.1 Overview

Automated weekly analysis produces threshold suggestions. **No auto-apply** — human review required.

## 20.2 Analysis Types

1. **Gate Effectiveness:** rejection rate × false negative rate
2. **Threshold Sensitivity:** simulate ±10-20% adjustments
3. **Score Band Calibration:** compare win rates across bands

## 20.3 Report Format

```
WEEKLY CALIBRATION REPORT
Week of [date]

SUMMARY
• Positions closed: 47
• Win rate: 57% ✓
• Avg return: +28% ✓

GATE ANALYSIS
[Table of rejection rates and false negatives]

SUGGESTIONS
1. [Gate]: Consider adjustment [X → Y]
   - Estimated impact: [details]
   - Recommendation: REVIEW/NO CHANGE

[Approve/Reject buttons for each suggestion]
```

---

# 21. LLM Integration (Trade Thesis)

## 21.1 When LLM is Called

**ONLY when:** verdict == APPROVE (never for WATCH/REJECT)

## 21.2 Input Packet

```json
{
  "underlying": { "ticker", "price", "sma20", "sma50", "returns" },
  "contract": { "type", "strike", "expiration", "dte", "mid", "iv", "delta", "theta" },
  "scores": { "final", "directional", "volatility", "structure" },
  "pillar_contributors": { ... },
  "scanner_triggers": [...],
  "policy_version": "..."
}
```

## 21.3 Output Schema (Required)

```json
{
  "setup_summary": "string",
  "thesis": "string",
  "supporting_evidence": ["..."],
  "risks": ["..."],
  "invalidation_conditions": ["..."],
  "exit_plan": {
    "profit_target": "string",
    "stop_loss": "string",
    "time_exit": "string"
  }
}
```

## 21.4 Cost Control

- Max 50 LLM calls per day
- 1000 output token limit
- Skip if limit reached (mark as pending)

---

# 22. Implementation Checklist

## Phase 1: Core Infrastructure
- [ ] Database schemas
- [ ] Policy versioning
- [ ] Config UI (read-only)
- [ ] Pipeline orchestration

## Phase 2: Scanners
- [ ] Unusual Volume
- [ ] Breakout/Breakdown
- [ ] Compression→Expansion
- [ ] Cheap Options
- [ ] Opportunity merge

## Phase 3: Selection
- [ ] Underlying filters
- [ ] DTE buckets
- [ ] Contract selection
- [ ] Ranking algorithm

## Phase 4: Scoring
- [ ] Feature computation
- [ ] Directional pillar
- [ ] Volatility pillar
- [ ] Structure pillar

## Phase 5: Gates & Decision
- [ ] All gate implementations
- [ ] Decision logic
- [ ] Quality tiers
- [ ] Concentration warnings

## Phase 6: Tracking
- [ ] Paper position management
- [ ] Exit condition checking
- [ ] Performance metrics
- [ ] Shadow tracking

## Phase 7: UI
- [ ] Evaluation detail page
- [ ] Pipeline monitor
- [ ] Config page

## Phase 8: Calibration & LLM
- [ ] Weekly calibration job
- [ ] LLM thesis generation

---

# 23. Glossary

| Term | Definition |
|------|------------|
| ATM | At-the-money; strike equals underlying |
| ATR | Average True Range; volatility measure |
| Breakeven | Price for option to be profitable at expiration |
| Delta | Option price change per $1 underlying move |
| DTE | Days to expiration |
| Gamma | Delta change per $1 underlying move |
| Gate | Binary pass/fail filter |
| ITM | In-the-money; has intrinsic value |
| IV | Implied volatility |
| MAE | Max Adverse Excursion |
| MFE | Max Favorable Excursion |
| OI | Open interest |
| OTM | Out-of-the-money |
| Pillar | Scoring dimension |
| Policy | Complete parameter set |
| RV | Realized volatility |
| Theta | Daily value decay |
| Vega | Price change per 1% IV change |

---

# 24. Appendix A: Reason Codes

## Scanner Codes
- `UNUSUAL_VOL_VOLUME_RATIO_EXCEEDED`
- `UNUSUAL_VOL_OI_CHANGE_EXCEEDED`
- `BREAKOUT_CLOSE_ABOVE_RANGE`
- `BREAKDOWN_CLOSE_BELOW_RANGE`
- `COMPRESSION_EXPANSION_UP/DOWN`
- `CHEAP_OPTIONS_IV_RV_LOW`
- `CHEAP_OPTIONS_IV_PERCENTILE_LOW`

## Filter Codes
- `FILTER_FAIL_UNDERLYING_PRICE`
- `FILTER_FAIL_DOLLAR_VOLUME`
- `FILTER_FAIL_MISSING_BARS`
- `FILTER_FAIL_EARNINGS_WINDOW`

## Gate Codes
- `GATE_PASS_*` / `GATE_FAIL_*` for each gate

## Decision Codes
- `APPROVED_BY_SCORE`
- `WATCH_BY_SCORE`
- `REJECTED_BY_SCORE`
- `REJECTED_BY_GATES`

---

# 25. Appendix B: Default Configuration

```yaml
# SCANNERS
unusual_volume:
  volume_ratio_threshold: 2.0
  oi_change_threshold_pct: 15.0

breakout:
  lookback_days: 20

compression:
  atr_period: 14
  compression_multiplier: 1.10
  break_pct: 2.0

cheap_options:
  iv_rv_ratio_max: 1.10
  iv_percentile_max: 40

# FILTERS
underlying:
  min_price: 5.00
  min_avg_dollar_volume: 20000000
  max_missing_bars: 2

# SELECTION
dte_buckets:
  A: [7, 21]
  B: [22, 45]
  C: [46, 75]
  D: [76, 120]

delta_bands:
  call: [0.20, 0.75]
  put: [-0.75, -0.20]

selection:
  top_k: 3
  target_delta_call: 0.45
  target_delta_put: -0.45

# GATES
gates:
  min_open_interest: 300
  min_volume: 75
  max_spread_pct: 8.0
  dte_min: 7
  dte_max: 120
  move_sufficiency_max: 1.25
  iv_percentile_max: 85
  breakout_volume_min: 1.5
  theta_burden_max: 4.0

# SCORING
pillar_weights:
  directional: 0.35
  volatility: 0.35
  structure: 0.30

# DECISION
thresholds:
  approve: 75
  watch: 65

quality_tiers:
  tier_1_min_score: 85
  tier_1_min_pillar: 70
  tier_1_max_spread: 5.0
  tier_2_min_pillar: 55

# TRACKING
paper_trading:
  profit_target_pct: 50
  stop_loss_pct: 50
  time_exit_dte: 5
```

---

# 26. Appendix C: Example Calculations

## Example 1: Move Sufficiency Gate

**Inputs:**
- AAPL at $185.00
- $190 Call, 23 DTE, mid $2.45
- IV: 32.5%

**Calculation:**
```
breakeven = 190 + 2.45 = $192.45
required_move = |192.45 - 185| / 185 = 4.03%
expected_move = 0.325 × sqrt(23/365) = 8.16%
time_adjusted = 4.03 / (8.16 × sqrt(23/30)) = 0.56
```

**Result:** 0.56 <= 1.25 → **PASS**

## Example 2: Final Decision

**Inputs:**
- Directional: 78
- Volatility: 81
- Structure: 84
- All gates passed

**Calculation:**
```
final = (0.35 × 78) + (0.35 × 81) + (0.30 × 84) = 80.85
```

**Result:** 80.85 >= 75 → **APPROVE (TIER_2)**

---

# Document End

**Version:** 2.0.0  
**This document is the authoritative specification for OSS implementation.**
