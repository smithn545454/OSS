# Convex Mode — Pre-Flight Downstream Impact Report

**Status:** DRAFT — awaiting Nick's review and explicit go-ahead before Phase 1 begins.

This report is the deliverable specified in [Section 3 of the source plan](../../../../Downloads/convex-mode-implementation-plan.md). It covers all 15 investigation areas, documents current OSS behavior with concrete file references, identifies blockers that need explicit resolution, and proposes handling for each area.

Architectural decisions already confirmed with Nick:
- Convex runs as a **parallel pipeline** alongside the existing 8-stage scanner pipeline.
- Output uses a new `Verdict.CONVEX_APPROVE` enum value (distinct from `APPROVE`).
- Tier sizing: A=50%, B=35%, C=25% of standard.
- Smart Money Confirmation: visibility-only at launch.
- FDA PDUFA: manual curation v1.
- Historical IV backfill (12 months) is a critical-path Phase 0.5.

**Top-level blockers** (must be resolved before Phase 4-5 can ship):

1. `IVHistory` schema does not store **25Δ put/call IV** (skew) or **multi-tenor IV** (front-month vs 60-day). Stage 3 cannot run without these. Schema extension + 12-month backfill required.
2. **Distance-to-significant-level** (52wk high, 6mo high, multi-touch resistance) is not currently computed. Stage 2B requires it.
3. **Sector classification** is on `StockSummary` but not persistently keyed for fast sector-cap enforcement at universe build time. Stage 1 needs a clean source.

Everything else fits within the existing architecture without major refactor.

---

## 3.1 Scanner Orchestration Framework

### Current state

- **Base interface:** `BaseScanner` ([backend/app/scanners/base.py:81](../backend/app/scanners/base.py)) — abstract `scan_ticker(ticker, context) → ScanResult`, plus `scan_batch()` with semaphore concurrency control.
- **Active scanners** ([backend/app/scanners/orchestrator.py:137](../backend/app/scanners/orchestrator.py)):
  ```python
  self._scanners: list[BaseScanner] = [
      BreakoutScanner(),
      CompressionScanner(),
      CheapOptionsScanner(),
  ]
  ```
- **UV is a separate Lambda pipeline** ([backend/lambdas/unusual_volume/](../backend/lambdas/unusual_volume/), [infrastructure/cdk/stacks/unusual_volume_stack.py](../infrastructure/cdk/stacks/unusual_volume_stack.py)) — fan-out via SNS, scheduled separately.
- **Backtest enable mechanism:** `scanners_enabled: Optional[list[str]]` parameter on `ScannerOrchestrator.__init__` ([orchestrator.py:116](../backend/app/scanners/orchestrator.py)) gates which scanners run. Production currently does not pass this list.
- **v5 active scanners:** `PolicyConfig.v5_active_scanners: list[str]` ([core/schemas.py:1569](../backend/app/core/schemas.py)) controls which scanners use v5 verdict logic. Not the same as enable/disable — it's verdict-path selection.

### Pause mechanism (proposed)

- Add `enabled: bool = True` to each scanner sub-config (`UnusualVolumeConfig`, `BreakoutConfig`, `CompressionConfig`, `CheapOptionsConfig`).
- `ScannerOrchestrator.__init__` reads `policy_config.scanner.{name}.enabled` when constructing `self._scanners`. Skip disabled.
- For the separate **UV Lambda**, disable its EventBridge rule via CDK ([unusual_volume_stack.py:362](../infrastructure/cdk/stacks/unusual_volume_stack.py)). UV detection logic gets lifted into a library function (`app/convex/uv_detector.py`) used by Convex Stage 2C.
- This is **policy-driven** (live-reloadable, no code deploy) and reuses the existing pattern from backtest mode.

### Convex pipeline shape

Convex does **not** conform to `BaseScanner` — its four stages are not per-ticker scans. Convex is a separate pipeline (`app/convex/pipeline.py`) invoked by its own EventBridge rule. It writes Decisions into `EvaluationTable` directly, sharing the schema but bypassing `ScannerOrchestrator`.

### Downstream consumers of paused scanners

Verified by grep: no production code outside `ScannerOrchestrator` constructs scanner instances directly. Pausing via the `enabled` flag is structurally clean. Tests reference scanners by class name; tests stay green.

### Status

- ✅ Pause mechanism design clean and reversible.
- ✅ Convex pipeline shape compatible with existing infrastructure.

---

## 3.2 Scoring and Conviction Infrastructure

### Current state

- **Pillar scoring** ([app/pillars/](../backend/app/pillars/)) is shared across all scanners; runs at Stage 5.
- **v5 dual-conviction** ([app/v5/pipeline.py](../backend/app/v5/pipeline.py)): `compute_v5_envelope()` produces HR (0-20) and P (0-100) conviction. Active when `v5_active=True` AND `scanner_source in v5_active_scanners`.
- **Verdict assignment** ([app/decision/calculator.py](../backend/app/decision/calculator.py)): gates → anti-archetype → v5 OR v4 score-based verdict.
- **APPROVE threshold** lives in `DecisionConfig` ([core/schemas.py](../backend/app/core/schemas.py)); v5 thresholds in `PolicyConfig.v5_hr_threshold` / `v5_p_threshold`.
- **Conviction score** is frontend-only ([frontend/src/lib/convictionScore.ts](../frontend/src/lib/convictionScore.ts)) — a freshness-decayed wrapper around `decision.final_score`.
- **`Decision` schema** ([core/schemas.py:392](../backend/app/core/schemas.py)) has 6 pillar score fields (3 v3 + 3 v4) + 17 v5 fields. All optional.

### Convex coexistence

Convex emits `Decision` with:
- `verdict = CONVEX_APPROVE` (new enum value)
- `final_score = 0.0` (Convex doesn't compute a composite score; field is required by schema, set to a neutral sentinel)
- All pillar fields = `None` (Convex doesn't compute pillars)
- All v5 fields = `None` (Convex doesn't compute conviction)
- New fields (Phase 1 schema migration):
  - `convex_tier: Optional[Literal["A", "B", "C"]]`
  - `convex_stages: Optional[ConvexStagesPayload]`
  - `smart_money_confirmation: Optional[bool]`
  - `convex_strength_composite: Optional[float]` (within-tier ranking only, never displayed)

### Decision.final_score handling

Two options:
- **Option A** (recommended): keep `final_score: float` required, set to `0.0` for Convex decisions. UI checks `verdict == "CONVEX_APPROVE"` and renders Convex stages instead of score bar.
- **Option B**: relax to `Optional[float]`. Cleaner conceptually but requires updates to many readers that assume `final_score` is non-null.

Option A wins because it's lower blast radius. The Convex UI explicitly suppresses score-bar rendering.

### Status

- ✅ Decision schema cleanly extensible with `Optional` fields.
- ✅ v4/v5 paths untouched; Convex flows through a parallel verdict-derivation function.

---

## 3.3 Database Schema

### Current state

Production tables (PK/SK pattern, single-table-style) defined in [infrastructure/cdk/stacks/database_stack.py](../infrastructure/cdk/stacks/database_stack.py) and [backend/app/db/tables.py](../backend/app/db/tables.py):

| Table | PK | SK | GSIs | Notes |
|---|---|---|---|---|
| `evaluations` | `EVAL#{ticker}` | `{timestamp}#{eval_id}` | GSI1: `VERDICT#{verdict}`; GSI2: `DATE#{date}` | Decision nested |
| `pipeline-runs` | `RUN` | `{started_at}#{run_id}` | — | |
| `stage-events` | `RUN#{run_id}` | `{started_at}#{stage}` | — | |
| `paper-positions` | `POS#{status}` | `{entry_date}#{position_id}` | GSI1, GSI2 | Catch-all (also alert config, alert log, real-trade config) |
| `iv-history` | `TICKER#{symbol}` | `DATE#{date}` | — | **ATM only, single tenor** |
| `oi-history` | `option_ticker` | `DATE#{date}` | — | Per-contract (per-strike-per-expiry); good for Convex |
| `price-history` | `TICKER#{symbol}` | `DATE#{date}` | — | 280-day TTL, OHLCV only |
| `earnings-cache` | `ticker` | — | — | Next earnings date |
| `earnings-history` | `TICKER#{symbol}` | `EARNINGS#{date}` | GSI1 | Past earnings + 1-day reaction |
| `real-trades` | `TRADE#{status}` | `{entry_date}#{trade_id}` | GSI1, GSI2 | Full eval snapshot embedded |
| `stock-summaries` | `TICKER#{symbol}` | `DATE#{date}` | — | AI-generated context |
| `backtest-runs` / `backtest-trades` / `backtest-pending-trades` / `backtest-insights` | `RUN#{run_id}` | varies | varies | Backtest harness |

### IV history gap (BLOCKER)

Current `IVHistory` schema ([core/schemas.py:1650](../backend/app/core/schemas.py)):
```python
class IVHistory(OSSBaseModel):
    ticker: str
    date: str
    atm_iv: float
    atm_call_iv: Optional[float] = None
    atm_put_iv: Optional[float] = None
    rv20: Optional[float] = None
    iv_rv_ratio: Optional[float] = None
    recorded_at: str = ...
```

**Missing for Convex Stage 3:**
- 60-day (or 30-day) IV at a second tenor for term-structure shape
- 25Δ put IV and 25Δ call IV for skew positioning

**Fix:** extend schema with:
```python
iv_30d: Optional[float] = None  # already approximately atm_iv but explicit
iv_60d: Optional[float] = None
iv_25d_put: Optional[float] = None
iv_25d_call: Optional[float] = None
```
Backfill via Polygon flat files. Phase 0.5.

### New tables required

- `convex-universe-snapshots` — PK=`UNIVERSE`, SK=`{snapshot_date}`, attrs: `tickers: list[ConvexUniverseEntry]` (each entry includes `tail_event_count_252d`, `hv_regime_ratio`, `historical_max_30d_move_pct`, `sector`, `market_cap`).
- `convex-stage-events` — PK=`RUN#{run_id}`, SK=`{ticker}#STAGE#{1-4}`, attrs: full stage payload (gate result, strength, explanation). Used by the failed-candidates debug page and the Evaluation Detail walkthrough.
- `catalyst-calendar` — PK=`TICKER#{symbol}`, SK=`EVENT#{date}#{type}`, attrs: `event_type`, `confirmed`, `source`, `metadata`. Stores earnings + FDA + macro + investor day. Earnings denormalized from `earnings-cache` for fast lookup.

### Migration strategy

- All new tables created via CDK DatabaseStack. **No destructive changes** to existing tables.
- The `IVHistory` schema extension is additive (new Optional fields). Existing records pass validation. Re-backfill writes the new columns over time.
- CDK DatabaseStack deploy is safe per [CLAUDE.md](../CLAUDE.md) guidance: `cdk deploy oss-dev-database` only, never `oss-dev-backend`.

### Status

- ⚠️ IV history schema extension is required and on the critical path.
- ✅ Three new tables additive, reversible.
- ✅ No existing-table destructive changes.

---

## 3.4 Evaluation Detail Page (UI)

### Current state

- File: [frontend/src/pages/EvaluationDetail.tsx](../frontend/src/pages/EvaluationDetail.tsx) — large component (~30+ subcomponents) rendering verdict, quality tier, contract Greeks, gate results, pillar breakdown, archetype match, v5 conviction panel, AI thesis, stock summary.
- Components consumed: `ConvictionPanelV5`, `ArchetypeMatchCard`, `AITradeThesis`, `AIStockSummary`, `TradeContextSection`, `UnderlyingStockDetails`, plus inline `ScoreBar` / `VerdictBadge` / `QualityTierBadge`.
- Backend payload from `GET /api/evaluations/detail/{ticker}/{evaluation_id}` ([api/routes/evaluations.py](../backend/app/api/routes/evaluations.py)): Evaluation + Decision (nested) + PillarScore list + GateResult list + FeatureValue list + thesis + stock summary + scanner metrics.

### Convex extension

- Conditional render at component root: `if (decision.verdict === 'CONVEX_APPROVE')` → render `ConvexEvaluationDetail` view; else → existing pillar/v5 view. The two regimes do not co-render.
- New components (under `frontend/src/components/convex/`):
  - `ConvexHeader` — tier badge, direction, contract one-liner, Smart Money badge, timestamp
  - `ConvexStage1Panel` — "Why this stock can move"
  - `ConvexStage2Panel` — "What's the catalyst": timeline + compression dashboard + UV panel + sympathy
  - `ConvexStage3Panel` — "Why options are cheap": IV gauges + term structure + skew chart
  - `ConvexStage4Panel` — "Why this specific contract": selected contract + alternatives table
  - `ConvexFinalSummary` — tier + sizing recommendation + invalidation conditions
- Stage panels collapsible; default expanded for Tier A, collapsed for Tier C.
- "Why didn't ticker XYZ make it?" link → new `/convex/failed-candidates/:date` page reads from `convex-stage-events` table.

### Backend payload

- Extend `GET /api/evaluations/detail/...` to include `convex_stages` block when present, OR add dedicated `GET /api/evaluations/convex/{ticker}/{eval_id}`.
- Recommended: extend existing endpoint. Frontend already loads via `useEvaluationDetail` hook ([frontend/src/hooks/useApi.ts](../frontend/src/hooks/useApi.ts)); a new field is a non-breaking addition.

### Routes

- Route `/evaluation/:ticker/:evaluationId` already handles arbitrary evaluations. No new route needed.

### Status

- ✅ Frontend can be extended cleanly. Conditional render gives a clean visual separation.
- ✅ Backend endpoint extension is non-breaking.

---

## 3.5 Alerting and Notifications

### Current state

- **Slack** ([backend/app/services/slack.py](../backend/app/services/slack.py)) is the sole notification channel.
- Alert config stored in `paper-positions` table (PK=`ALERT_CONFIG`, SK=`CURRENT`). Default `verdicts: ["APPROVE"]` ([slack.py:62, 414, 444](../backend/app/services/slack.py)).
- Trigger: post-decision in the worker; `should_send_alert()` checks HR conviction floor (default 10), P conviction floor (default 70), Tier 1 bypass (HR ≥ 14), per-contract / per-ticker cooldowns, daily cap, quiet hours.
- Webhook channels list in alert config; can route to multiple Slack channels by name.
- Email/push not currently integrated.

### Convex consumers

- **Recommended:** add a separate Slack webhook channel `#convex-approvals`. Add `CONVEX_APPROVE` to the default `verdicts` allow-list, OR keep `APPROVE` and `CONVEX_APPROVE` as independent alert toggles.
- Alert thresholds: HR/P conviction floors do not apply to Convex (no convictions computed). New Convex-specific thresholds: `convex_tier_alert_floor: Literal["A", "B", "C"]` (default `"B"` — alert on Tier A or B; suppress C).
- Cooldowns and quiet hours apply identically.

### "No signal" silence risk

- After pausing UV / Cheap Options / Compression: existing alert config will still fire on legacy `APPROVE` from BREAKOUT scanner. Nick will not interpret quiet evenings as outage so long as the Convex channel exists and produces signals.
- Mitigation: the Convex pipeline emits a daily "ran successfully, N candidates evaluated, M Tier A/B/C" summary message regardless of approvals.

### Status

- ✅ Alert routing is config-driven; CONVEX_APPROVE channel adds cleanly.
- ⚠️ Need explicit per-tier alert thresholds (Phase 6 config decision).

---

## 3.6 Dashboards, Reports, KPIs

### Current state

- **Calibration page** ([frontend/src/pages/Calibration.tsx](../frontend/src/pages/Calibration.tsx)) shows weekly summary: positions closed, win rate, avg return, gate effectiveness. No per-scanner breakdown in current UI.
- **Pipeline Monitor** ([frontend/src/pages/PipelineMonitor.tsx](../frontend/src/pages/PipelineMonitor.tsx)) visualizes pipeline runs by stage; filter by scanner type, verdict, DTE, option side.
- **MyTrades / Real Trades** ([frontend/src/pages/MyTrades.tsx](../frontend/src/pages/MyTrades.tsx), [TradeDetail.tsx](../frontend/src/pages/TradeDetail.tsx)) — manually tracked real trades with full eval snapshots; recent CSV export ([api/routes/real_trades.py](../backend/app/api/routes/real_trades.py)) includes scanner source.
- **Backtest insights** ([backtest-insights table](../infrastructure/cdk/stacks/database_stack.py)) — per-run aggregates.
- **Reporter** ([backend/app/calibration/reporter.py:133](../backend/app/calibration/reporter.py)): `EvaluationTable.list_by_verdict("APPROVE", limit=500)` — generates calibration reports. Will silently exclude Convex unless explicitly extended.

### Convex consumers

- Calibration reporter must aggregate `APPROVE` and `CONVEX_APPROVE` separately (not summed). Extend `reporter.py` to emit two parallel report sections.
- Pipeline Monitor needs Convex pipeline runs visible — add `pipeline_type: Literal["scanner", "convex", "uv"]` to PipelineRun and filter UI.
- Real Trades CSV export already snapshots full Decision, including new convex fields. Will work without changes once `convex_tier`, `convex_stages` are on Decision.
- KPI dashboards (none currently exist) — defer to a fast-follow.

### Quiet behavior

- All existing reports filter on `verdict == "APPROVE"` literal — they will continue to work with paused scanners producing zero `APPROVE`s, just with smaller numerators. They will NOT include Convex without explicit code changes (which is correct — we want them separated).

### Status

- ✅ Existing reports continue to function with paused scanners.
- ⚠️ Calibration reporter needs explicit Convex aggregation (Phase 1).
- ⚠️ Pipeline Monitor needs `pipeline_type` discrimination (Phase 1).

---

## 3.7 Trading Journal

### Current state

- **No dedicated journal table.** The journal is reconstructed from `PaperPosition` + `RealTrade` records.
- `PaperPosition` ([core/schemas.py](../backend/app/core/schemas.py)) denormalizes: `verdict_at_entry`, `quality_tier_at_entry`, `scanner_source`, `scanner_list`, `convergence_count`, `conviction_score`, all 6 pillar scores, `dte_at_entry`, entry Greeks, `entry_underlying_price`, `entry_moneyness_pct`, `entry_spread_pct`, `entry_open_interest`, `entry_volume`.
- `RealTrade` ([backend/app/db/tables.py:481](../backend/app/db/tables.py)) embeds full `EvaluationSnapshot` at trade time.
- CSV export ([backend/app/api/routes/real_trades.py:240](../backend/app/api/routes/real_trades.py)) serializes pillar/v5 fields as `oss_pillars_json`.
- **Missing journal columns** the Convex doc references: Market Regime, Sector Context, Vol Environment, Decision Quality. None currently captured at position entry.

### Convex consumers

- Add `convex_tier_at_entry`, `convex_stage_strengths` (denormalized at entry), `smart_money_confirmation_at_entry` to `PaperPosition` and `RealTrade.snapshot`.
- For the journal columns the doc lists (Market Regime, Sector Context, Vol Environment): defer to a post-cutover enhancement. Stage 3 captures vol environment implicitly via `convex_stages.stage_3.metrics`. Sector is on `StockSummary`. Market regime is in v5 envelopes.
- CSV export auto-picks up new fields once `Decision` schema is updated.

### Auto-population

- `paper_trading/position_manager.py:79` (`create_position_from_evaluation`) is the auto-population point. Extend to copy `decision.convex_tier`, `decision.smart_money_confirmation`, etc. onto `PaperPosition`.

### Status

- ✅ Journal extensions are denormalization-only; no new tables.
- ⏸ Market Regime / Sector / Vol Environment columns: deferred (out of scope for v1).

---

## 3.8 Backtest Infrastructure

### Current state

- Harness in [backend/app/backtest/](../backend/app/backtest/): coordinator, evaluate_worker, resolve_worker, finalize_worker, equity_curve, metrics.
- Phase 1 (eval) writes to `backtest-pending-trades`; Phase 2 (resolve) writes to `backtest-trades`.
- Lambda handlers in [backend/app/main.py](../backend/app/main.py) for each phase.
- Parameterizable: `BacktestRunConfig` accepts policy version, gate overrides, scanner filter, date range, position sizing.
- Historical IV: backfilled Dec 11 2025 – Mar 9 2026 (~92 trading days). Per CLAUDE.md.

### Convex backtest

- Per Nick's decision: **backfill 12 months of IV history** (Apr 2025 – Apr 2026) including 25Δ skew and 60-day tenor, then run a year-long Convex backtest.
- New `BacktestRunConfig.pipeline_type: Literal["scanner", "convex"]` switches between v4/v5 pipeline and Convex pipeline at backtest time.
- Convex backtest writes to `backtest-trades` with `scanner_source = "CONVEX"` (or `pipeline_type` field) for clean segmentation.
- Validation criteria from doc Section 11: hit rate ≥30%, avg winner ≥3× avg loser, expectancy positive after slippage, Tier A > Tier C, Smart Money confirmed > non-confirmed.

### Data backfill plan (Phase 0.5)

- Polygon Advanced plan provides historical options snapshots → flat files in S3 (per CLAUDE.md, the Mar 2026 backfill already pulled from S3 parquet).
- Reuse `scripts/backfill_iv_history_dynamodb.py` (pattern documented in CLAUDE.md "Pipeline Audit Fixes Mar 11").
- For 25Δ skew: Polygon snapshot endpoint has Greeks per contract; identify the 25Δ put and 25Δ call per ticker per day, compute skew at backfill time.
- Validation: data-completeness audit — every ticker × trading day must have ATM IV, 60-day IV, 25Δ put IV, 25Δ call IV, or be flagged "data_missing" (excluded from backtest sample).

### Status

- ⚠️ Backfill is critical-path Phase 0.5. Schema extension required first.
- ✅ Backtest harness already parameterizable; adding `pipeline_type` is additive.

---

## 3.9 Position Sizing and Risk

### Current state

- **Position sizing is fixed at quantity=1 contract** ([backend/app/paper_trading/position_manager.py:161](../backend/app/paper_trading/position_manager.py)). No tier-based sizing exists.
- No portfolio-level risk constraints (max delta, max vega, max theta) currently enforced.
- Standard sizing (real trades) is decided manually by Nick at trade time.

### Convex sizing

- Add `position_sizing_recommendation: Optional[str]` (e.g., `"50% of standard"`) to `Decision` schema. Embedded for Convex; null for legacy verdicts.
- Computed in Stage 6 (Tier Assignment) from a config map: `convex_sizing_pct = {"A": 0.50, "B": 0.35, "C": 0.25}` on `PolicyConfig.convex` block.
- Surfaced on Evaluation Detail page (`ConvexFinalSummary`) and in Slack alert payload.
- **No automated paper trading sizing change at v1** — the recommendation is informational. Position auto-creation continues at quantity=1; Nick reads the recommendation when manually entering real trades.
- Fast-follow: tier-aware paper trading sizing requires extending `PaperPosition.quantity` to be a float (or expressing as `notional_pct`); defer.

### Portfolio risk

- No max-vega / max-theta constraints currently. Doc calls these out as Section 3.9 line items but they are not blockers — Nick manages portfolio risk manually. Defer.

### Status

- ✅ Sizing recommendation slots cleanly into Decision schema.
- ⏸ Automated tier-based paper sizing deferred.

---

## 3.10 Data Feeds and External Dependencies

### Current state — what's ingested

| Data | Source | Table | Status |
|---|---|---|---|
| Daily OHLCV | Polygon | `price-history` | ✅ 252+ days |
| Options chain (Greeks, IV, OI, vol) | Polygon Advanced | (not stored; live fetch per evaluation) | ✅ |
| Earnings calendar | Finnhub | `earnings-cache` (next), `earnings-history` (past) | ✅ |
| ATM IV history | Derived from chain | `iv-history` | ✅ but ATM only |
| OI history | Polygon | `oi-history` (per contract) | ✅ |
| Stock context | LLM-generated | `stock-summaries` | ✅ |

### Required for Convex — gaps

| Need | Status | Plan |
|---|---|---|
| **25Δ put/call IV (skew)** | ❌ MISSING | Extend `iv-history` schema; Phase 0.5 backfill |
| **60-day tenor IV (term structure)** | ❌ MISSING | Same as above |
| **Bollinger Band Width historicals** | ❌ MISSING | Compute from `price-history`; new pre-Stage-2 ingest step |
| **ATR(14)/ATR(60) historicals** | ❌ MISSING | Compute from `price-history`; same |
| **Volume contraction (20d/90d ratio)** | ❌ MISSING (compute trivially) | Compute from `price-history` |
| **Distance-to-significant-level** | ❌ MISSING | New module: 52wk high, 6mo high, multi-touch resistance detection |
| **FDA PDUFA calendar** | ❌ MISSING | Manual seed v1 (per Nick's decision) |
| **Macro calendar (FOMC, CPI, NFP)** | ❌ MISSING | Manual seed v1 |
| **Sector classification** | ⚠ PARTIAL | On `StockSummary` (LLM-derived); need authoritative source for universe construction |
| **Market cap** | ⚠ NOT PERSISTED | Polygon ticker details fetched live; need persistent column on universe snapshot |
| **Per-strike OI** | ✅ exists | `oi-history` per `option_ticker` |
| **Sympathy detection (peer-in-sector)** | ❌ MISSING | New module on top of `earnings-history` + sector classification |

### Freshness requirements

- **Stage 2** needs intraday-fresh catalyst data: earnings cache refreshes daily ([CDK schedule 12:00 UTC](../infrastructure/cdk/stacks/backend_stack.py)). Acceptable for daily pipeline.
- **Stage 3** needs end-of-day IV surface: `iv-history` records written daily by capture job (22:00 UTC). Convex pipeline runs after this — fine.
- **Stage 4** needs current chain quotes: live Polygon fetch at evaluation time. Same pattern as existing pipeline.

### Vendor cost

- Polygon Advanced is already on the plan (per CLAUDE.md "Polygon API Key Upgrade Mar 12 2026"). No new vendor required for IV/skew/term-structure data.
- FDA PDUFA: deferred per Nick. Fast-follow vendor evaluation.

### Status

- ⚠️ Six data computations needed (25Δ skew, 60-day IV, BBW, ATR ratio, volume contraction, distance-to-level). All derivable from existing feeds.
- ⚠️ Manual catalyst seeding required v1.
- ✅ No new vendor onboarding required.

---

## 3.11 Scheduling and Pipeline Orchestration

### Current state

- **Daily scanner pipeline:** EventBridge rule, every 10 min, weekdays 13:00-21:00 UTC ([backend_stack.py:224](../infrastructure/cdk/stacks/backend_stack.py)). Coordinator/worker fan-out.
- **UV pipeline:** EventBridge rule, every 15 min weekdays ([unusual_volume_stack.py:362](../infrastructure/cdk/stacks/unusual_volume_stack.py)).
- **Calibration:** Monday 07:00 UTC weekly.
- **Paper trading update:** 21:15 UTC weekdays (post-close).
- **Earnings refresh:** 12:00 UTC weekdays.
- **Price history refresh:** 05:00 UTC Tue–Sat.
- **Data capture (IV/OHLCV ingest):** 22:00 UTC weekdays.

### New schedules for Convex

- **Convex daily pipeline:** new EventBridge rule, weekdays 22:30 UTC (after data capture settles, before pre-market alerts). Single invocation per day, not fan-out (Convex universe is small enough — ~300 names — to process in one Lambda run within 5min budget).
- **Kinetic universe refresh:** new EventBridge rule, monthly, 1st of month 02:00 UTC. Reads 18 months of `price-history`, writes to `convex-universe-snapshots`.
- **PDUFA / macro seed refresh:** manual via API endpoint (Nick updates as needed). No cron.

### Failure handling

- Convex pipeline graceful degradation: if Stage 3 fails for a ticker (missing skew data), mark candidate as `"vol_data_unavailable"` and exclude from advancing — do not halt the whole pipeline.
- Universe construction failure: pipeline reads the most recent successful snapshot. Stale snapshot warning if snapshot >40 days old (vs 30-day refresh cadence).

### Run ID and telemetry

- Convex pipeline writes to `pipeline-runs` and `stage-events` tables with `pipeline_type: "convex"` so the existing Pipeline Monitor sidebar surfaces it alongside scanner runs.

### Status

- ✅ Two new EventBridge rules; CDK additions only.
- ✅ Failure handling fits within existing run-status conventions.

---

## 3.12 Configuration Management

### Current state

- **Master config:** `PolicyConfig` ([core/schemas.py:1549](../backend/app/core/schemas.py)) with nested sub-configs (scanner, gates, pillars, decision, etc.).
- **Persistence:** `policies` table; `PolicyTable.set_active(version)` ([backend/app/core/policy.py:99](../backend/app/core/policy.py)) hot-swaps the active config without code deploy.
- **API:** `/api/policies/active`, `/api/policies/{version}/activate` ([backend/app/api/routes/policies.py](../backend/app/api/routes/policies.py)).
- **UI:** `/policy-config` page in frontend renders editable sub-configs.
- **All thresholds config-driven**, no hardcoded magic numbers in production scanner code.

### Convex config block

Add `ConvexConfig` to `PolicyConfig`:

```python
class ConvexConfig(OSSBaseModel):
    enabled: bool = False  # Master kill switch (Phase 1 default False)
    # Stage 1 — Kinetic Universe
    universe_min_options_volume: int = 5000
    universe_min_market_cap: float = 1_000_000_000
    universe_max_sector_pct: float = 0.25
    universe_min_tail_events_252d: int = 8
    universe_hv_regime_min: float = 0.7
    universe_hv_regime_max: float = 1.5
    universe_max_atm_spread_pct: float = 5.0
    # Stage 2 — Catalyst
    catalyst_compression_signals_required: int = 2
    catalyst_compression_bbw_percentile_max: int = 20
    catalyst_compression_atr_ratio_max: float = 0.75
    catalyst_uv_volume_multiplier: float = 4.0
    catalyst_event_window_min_days: int = 5
    catalyst_event_window_max_days: int = 30
    # Stage 3 — Vol Mispricing
    vol_iv_rank_max: int = 40
    vol_iv_percentile_max: int = 35
    vol_iv_hv_ratio_max: float = 1.10
    # Stage 4 — Contract Selection
    contract_delta_min: float = 0.25
    contract_delta_max: float = 0.35
    contract_dte_min: int = 30
    contract_dte_max: int = 60
    contract_dte_post_event_buffer: int = 14
    contract_max_spread_pct: float = 8.0
    contract_min_open_interest: int = 500
    # Tier thresholds
    tier_a_stage2_strength_min: float = 0.75
    tier_a_stage3_composite_min: float = 0.70
    tier_b_stage2_strength_min: float = 0.50
    tier_b_stage3_composite_min: float = 0.40
    # Sizing
    sizing_tier_a_pct: float = 0.50
    sizing_tier_b_pct: float = 0.35
    sizing_tier_c_pct: float = 0.25
    # Smart Money
    smart_money_promotes_tier: bool = False  # visibility-only at launch
```

All values config-driven, hot-reloadable via policy activation.

### Scanner enable flags

Add `enabled: bool = True` to each existing scanner sub-config (`UnusualVolumeConfig`, `BreakoutConfig`, `CompressionConfig`, `CheapOptionsConfig`).

### Status

- ✅ Config structure idiomatic with existing pattern.
- ✅ All Convex thresholds tunable post-cutover without redeploy.

---

## 3.13 Logging and Observability

### Current state

- **StageEvent table:** records per-stage telemetry (items_in/out, drop_reasons, processing_time_ms, metadata) per pipeline run.
- **PipelineRun table:** run-level metadata (status, started_at, current_stage, totals).
- **StageMapper** ([backend/app/observability/stage_mapper.py](../backend/app/observability/stage_mapper.py)) maps internal pipeline events to display gates for the Pipeline Monitor UI.
- **CloudWatch Lambda logs:** ERROR-level filtering documented in CLAUDE.md deploy protocol.
- **TraceSampler** ([backend/app/observability/trace_sampler.py:231](../backend/app/observability/trace_sampler.py)) samples APPROVE traces for deep inspection.

### Convex logging

- Each Convex stage emits a `StageEvent` per ticker with fields:
  - `items_in / items_out / items_dropped`
  - `drop_reasons: dict[str, int]` keyed by gate-fail reason (e.g., `"FAIL_LIQUIDITY"`, `"FAIL_KINETIC"`, `"FAIL_HV_REGIME"`)
  - `metadata`: stage-specific (e.g., Stage 2: `{date_known_advancers: N, compression_advancers: N, uv_advancers: N, sympathy_advancers: N}`)
- **Per-ticker decision log** to `convex-stage-events` table: every ticker that entered Stage 2 has a row per stage explaining why it advanced or failed. This is the source for the "Why didn't ticker XYZ make it?" debug page.
- **Daily summary log** at end of pipeline: universe size, Stage 2/3/4 advancer counts, final tier distribution. Posted to a dedicated Slack channel (`#convex-pipeline-status`) and to CloudWatch.
- **TraceSampler extension:** sample CONVEX_APPROVE traces (Tier A always; Tier B at 50%; Tier C at 10%).

### Performance metrics

- Per-stage timing logged via `processing_time_ms` on StageEvent.
- Convex pipeline budget: <5min total (Lambda timeout). Universe of ~300, Stage 2 advancers ~30, Stage 3 advancers ~15, Stage 4 final ~5-10.

### Status

- ✅ Existing observability infrastructure absorbs Convex telemetry without modification.
- ✅ Per-ticker decision rationale captured in `convex-stage-events` for debug.

---

## 3.14 Testing

### Current state

- **Backend:** pytest with `asyncio_mode = "auto"`, moto for DynamoDB mocking. 60% coverage threshold.
- **Frontend:** Vitest + React Testing Library + jsdom.
- Existing patterns: `conftest.py` sets `DYNAMODB_TABLE_PREFIX=oss-test`; `moto_dynamodb` fixture creates fresh tables per test.

### Convex test plan

- **Unit tests** (`backend/tests/convex/`):
  - `test_stage1_universe.py` — fixture-based universe construction; assert known kinetic / dead names handled correctly.
  - `test_stage2_catalyst.py` — fixtures per detection system (date-known, compression, UV, sympathy); deterministic strength calculations.
  - `test_stage3_volatility.py` — IV surface fixtures; direction inference cases (bullish, bearish, ambiguous/straddle).
  - `test_stage4_contract.py` — contract selection across catalyst types; alternatives correctly rejected; liquidity-fail handling.
  - `test_tier_assignment.py` — tier mapping deterministic on fixture stage outputs.
  - `test_uv_detector.py` — lifted UV logic produces same results as existing UV scanner on a fixture day.
- **Integration test** — full Convex pipeline against a fixture day; expected APPROVE list.
- **Regression test** — existing scanners produce identical outputs after the orchestrator `enabled` flag refactor.
- **UI tests** — Evaluation Detail rendering for Tier A/B/C fixtures.
- **Backtest validation** — Phase 8 acceptance criteria (Section 11 of source doc).

### Coverage target

- New Convex modules must hit 60% coverage minimum (project standard).
- Stage logic should target 80%+ given the pre-deployment stakes.

### Status

- ✅ Testing infrastructure ready; new test files additive.

---

## 3.15 Documentation

### Current state

- [CLAUDE.md](../CLAUDE.md) — operational + architectural reference, kept current.
- [docs/](../docs/) — design plans (`pillar_v4_execution_plan.md`, `v5_architecture.md`, `archetypes_catalog.md`, etc.).
- [baselines/](../baselines/) — known-good code+policy snapshots with restore docs.

### Convex docs to produce

- `docs/convex_mode_architecture.md` — system overview (companion to v5_architecture.md): pipeline, schemas, gates-and-tiers philosophy, why no composite scoring.
- `docs/convex_runbook.md` — what to do when each stage fails, how to interpret daily summary, common tuning levers.
- `docs/convex_data_glossary.md` — defines IV Rank, IV Percentile, BBW percentile, ATR ratio, term structure, skew, etc. (Hover tooltips in UI source from this.)
- Update [CLAUDE.md](../CLAUDE.md) — add Convex Mode section under Architecture; document the parallel pipeline; document the verdict enum extension.
- Baseline tag at cutover: `pipeline-stable-convex-v1.0-YYYY-MM-DD` per existing convention.
- Changelog entry for cutover deployment.

### Status

- ✅ Documentation pattern established; new docs additive.

---

## Summary — Blockers and Critical Path

### Hard blockers (must resolve before Phase 4-5)

1. **`IVHistory` schema extension** for 25Δ skew + multi-tenor IV. Schema migration + 12-month backfill (Phase 0.5).
2. **Distance-to-significant-level computation** module (52wk / 6mo / multi-touch). New code in Phase 3 (Stage 2).
3. **Sector classification + market cap persistence** for universe construction. Need authoritative source per ticker on `convex-universe-snapshots`.

### Soft blockers (must resolve before cutover)

4. **Verdict enum blast radius audited.** Every code path filtering `verdict == "APPROVE"` (40+ sites) is benign for Convex (excludes by default, which is correct), but explicit decisions needed for:
   - `paper_trading/stage.py:226` — auto-enroll positions on APPROVE/WATCH. Should auto-enroll on CONVEX_APPROVE? (Recommend yes for Tier A and B; no for C — Tier C is "warrants extra scrutiny.")
   - `services/quote_refresh.py:57` — refreshes APPROVE-eval quotes. Should also refresh CONVEX_APPROVE.
   - `decision/concentration.py` — concentration warnings. Convex needs independent concentration logic (different sizing).
   - `calibration/reporter.py:133` — calibration aggregates only APPROVE. Add parallel CONVEX_APPROVE aggregation.
   - `observability/trace_sampler.py:231,249` — sampling. Add CONVEX_APPROVE sampling.

5. **Calibration reporter Convex aggregation** — Phase 1 work.

6. **Pipeline Monitor `pipeline_type` discrimination** — Phase 1 work.

### Non-blockers (deferred to fast-follow)

- FDA PDUFA automated feed (manual v1).
- Tier-based paper trading auto-sizing (informational v1).
- Smart Money Confirmation tier promotion (visibility-only v1).
- Market Regime / Sector Context / Vol Environment journal columns.
- KPI dashboards.

---

## Items Where Current Architecture Cannot Accommodate Without Larger Refactor

**None.** Every required change is additive — new tables, new schema fields (all Optional), new enum value, new pipeline module, new EventBridge rules, new UI components. No existing code requires deletion or rewrite.

The closest thing to a refactor is the `enabled` flag on existing scanner sub-configs and the orchestrator's check on it — but that is a 5-line change.

---

## Approval Request

Before Phase 1 (Foundation) begins, I'd like Nick's explicit confirmation on:

1. ✅ Architectural decisions previously made still stand (parallel pipeline, CONVEX_APPROVE enum, manual PDUFA v1, 50/35/25 sizing, visibility-only Smart Money, 12-month IV backfill).
2. ☐ The five "soft blockers" handling proposals above (paper trading auto-enroll Tier A/B but not C; quote_refresh include Convex; concentration logic split; calibration parallel reports; trace sampling extension).
3. ☐ Convex pipeline runs at 22:30 UTC daily (after data capture, before pre-market). Acceptable timing?
4. ☐ Convex pipeline emits a daily summary message regardless of approvals — to mitigate "no signal" silence after pausing UV/Cheap/Compression. Acceptable?
5. ☐ Documentation deliverables (architecture doc, runbook, data glossary) acceptable scope.

Once approved, the Phase 0.5 + Phase 1 work begins in parallel.
