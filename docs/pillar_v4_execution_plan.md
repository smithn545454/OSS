# Pillar v4 Execution Plan: Directional Conviction, Move Potential, Trade Structure

**Author:** Principal engineering plan (Claude), revised after codebase audit with Nick 2026-04-16
**Status:** Phase 1 in execution
**Trade universe:** S&P 500 **+ Russell 1000** (~1,500 tickers per scan)
**Constraint:** ZERO tolerance for disruption. Frontend must not lose functionality. No shadow mode.

---

## 1. Purpose

Replace the current three pillars (`PREMIUM_LEVERAGE`, `UNDERLYING_BEHAVIOR`, `SETUP_QUALITY`) with three new pillars (`DIRECTIONAL_CONVICTION`, `MOVE_POTENTIAL`, `TRADE_STRUCTURE`) using an immediate cutover pattern that preserves all historical data, keeps the frontend functional throughout, and uses the same migration-shim pattern the codebase already has for v2→v3.

Sharpshooter thesis: hunt 200%+ MFE trades where direction, magnitude, and structure align simultaneously. Russell 1000 scan should produce ~2-8 Sharpshooter-tier opportunities per day, ~8-25 High Conviction, most rejected.

---

## 2. Governing Principle — Clean Cutover to v4

Per Nick: "default all scoring in line with this new approach; any old weights/scoring approaches from the previous regime should be removed."

Two movements:

**Movement A (Phases 1-7):** Additive build, flip active policy. V4 becomes the only active scoring regime at Phase 7 activation. V3 code remains during observation window — unreachable but intact for emergency reactivation.

**Movement B (Phase 9, after 2 weeks stability):** Remove v3 code entirely. V3 pillar classes, v3 policy defaults, v3 weight editors — all deleted. After Phase 9, rollback is via Lambda version rollback only.

**Movement C (Phase 10):** Historical rescore. Replace v3 scores with v4 on all historical paper positions.

**Permanent retention even after Phase 9/10:**
- `PREMIUM_LEVERAGE`/`UNDERLYING_BEHAVIOR`/`SETUP_QUALITY` enum values — deserializer safety for any stored Decision / PillarScore records.
- Frontend `pillarMeta` v3 entries marked `legacy: true` — in case any unmigrated records surface.

---

## 3. Resolved Design Decisions (from review session)

| ID | Decision |
|---|---|
| Q1 | Pillar score = 0 when <3 of 5 (or 6) subscores available. **0 is the "insufficient data, don't trade" flag.** Score = max(1, computed) when ≥3 subscores present. |
| Q2 | Floor at 1 (not 0) when data is sufficient — prevents unintended zero-collapse in geometric mean. |
| Q3 | `historical_move_magnitude` = 1-day post-earnings move (close-to-close, announcement day to next trading day). |
| Q4 | Trade universe = both S&P 500 and Russell 1000. Preparing for ~1,500 tickers. |
| Q5 | Historical paper positions will be rescored against v4 — scope is new Phase 10 (after v4 stability proven). |
| Q6 | Tests updated as part of the migration work (334 occurrences across 27 test files). |
| Q7 | LLM prompt/model/generator rewritten to match v4 pillars and thesis approach. |
| Q8 | Finnhub historical earnings backfill (existing `/calendar/earnings` endpoint supports date ranges). |
| Q9 | Historical rescore replaces v3 fields on `PaperPosition`. Historical `Decision`/`PillarScore` records retain v3 (read-only). |
| Q10 | Rescore runs as one-shot batch at Phase 10 kickoff. |

---

## 4. Target Pillar Architecture

```
ConvictionScore = DirectionalConviction^0.40 × MovePotential^0.35 × TradeStructure^0.25
```

Each pillar is 0-100. Composite is 0-100.

**DIRECTIONAL_CONVICTION** (40% exponent) — 6 subscores:
- Stage 2 Trend Template (Minervini 7-criteria): 30%
- Relative Strength 20d vs SPY (percentile rank): 20%
- ADX × ±DI Directional Agreement: 15%
- Proximity to Breakout Pivot: 15%
- Volume/OBV Confirmation: 10%
- Sector Relative Strength: 10%

**MOVE_POTENTIAL** (35% exponent) — 5 subscores:
- Move Trigger (catalyst in DTE window OR technical breakout): 35%
- Historical Post-Event Move Magnitude (1-day post-earnings, last 4 quarters): 20%
- IV/RV Ratio: 15%
- Volatility Regime (BB Width percentile): 15%
- Expected vs Required Move: 15%

**TRADE_STRUCTURE** (25% exponent) — 5 subscores:
- Delta Sweet Spot (peak at 0.30): 25%
- Gamma/Theta Ratio: 25%
- DTE Sweet Spot (catalyst-aware): 20%
- IV Rank (low is better, monotonic): 20%
- Strike Proximity to Pivot: 10%

### Hard Gates (Pre-Score Disqualification)
- Spread % > 10% → REJECT
- Open Interest < 100 → REJECT
- Daily Volume < 50 → REJECT
- Stage 2/4 trend template fails (< 3/7 criteria for direction) → REJECT
- Greeks coherence failure → REJECT (unchanged)

### Grand Slam Tier Gates (for Composite 90+)
- All three pillars ≥ 80/75/70 respectively
- |Delta| ∈ [0.20, 0.50]
- DTE ≤ 60 unless catalyst > 45 days away
- Move Trigger subscore ≥ 70
- IV Rank ≤ 60

Any failure caps composite at 89.

### Quality Tier Thresholds (Recalibrated for ~1,500-ticker universe)

| Tier | Composite Threshold | Expected Daily Flow |
|------|---------------------|---------------------|
| TIER_1 (Sharpshooter) | ≥ 92 + gates | 2-8 |
| TIER_2 (High Conviction) | 82-91 | 8-25 |
| TIER_3 (Tradeable) | 72-81 | 25-80 |
| WATCH | 62-71 | 40-150 |
| REJECT | < 62 or gate fail | majority |

---

## 5. Critical Audit Findings — Items Beyond Original Plan

The codebase audit surfaced six material items not in the original plan. All are folded into the phase schedule below.

1. **Reason code generator** — `backend/app/decision/calculator.py:204-259` (`generate_supporting_reasons`) contains 13 hardcoded v3 reason codes (`STRONG_PREMIUM_LEVERAGE`, `WEAK_SETUP_QUALITY`, etc.). Needs v4 equivalent + display shim for historical records.
2. **LLM prompt/model/generator** — `backend/app/llm/prompt.py:191-193`, `models.py:54-56, 131-133`, `generator.py:166-168` hardcode v3 pillar labels. Full rewrite, not a patch.
3. **Pillar orchestrator is fully hardcoded** — `backend/app/pillars/calculator.py` has four functions that all explicitly reference v3 pillars. Refactor to registry pattern.
4. **DecisionCalculator signature** — `compute_final_score()`, `assign_quality_tier()`, `compute_decision()` all take positional `premium_leverage, underlying_behavior, setup_quality` args. API reshape required.
5. **PillarConfig validator forces v3 shape** — `schemas.py:982-998` asserts all three v3 pillars exist with correct pillar_id. Validator must be rewritten to allow v3-OR-v4.
6. **Sector map may not cover Russell 1000** — `SP500TickerTable.get_sector_map()` returns tickers with non-empty `sector` field. Phase 1 includes a coverage audit + backfill if needed.

---

## 6. Phase Schedule

Total active-engineering estimate: ~22-24 working days (original 10-14 understated scope).

| Phase | Scope | Duration |
|---|---|---|
| 1 | Data foundation: price history + earnings history tables; backfills for ~1,500 tickers; new features (ma_150/200, high_52w, low_52w, bb_width_percentile, sector_rs_20d, historical_move_magnitude); sector map coverage validation | 4 days |
| 2 | Schema extensions (PillarId enum, PillarWeights, PillarConfig, Decision, PaperPosition — additive) | 1 day |
| 3 | v4 pillar classes + orchestrator registry refactor + LLM prompt/model/generator rewrite + composite formula + geometric-mean min-count rule + reason-code rewrite + tests | 5 days |
| 4 | Frontend types, `pillarMeta.ts`, EvaluationDetail page (new names/weights/geometric-mean explanation), Policy page, paper trading components | 2 days |
| 5 | v4.0.0 policy config build + seed | 1 day |
| 6 | Backend hardcoded-reference sweep (API routes, paper trading, rule matcher, calibration, scripts) + test fixture migration (334 occurrences / 27 files) | 3 days |
| 7 | Activation (Tuesday) | 1 day |
| 8 | 2-week observation + tuning | 14 days |
| 9 | v3 code removal | 2 days |
| 10 | Historical paper-position rescore: Finnhub earnings-history backfill, batch rescore v4 over 15,505 positions; v4 fields replace v3 fields on `PaperPosition` | 2-3 days |

---

## 7. Phase 1 Detailed Plan — Data Foundation

### 1.A Price History Infrastructure

1. **CDK: create `oss-dev-price-history` table**
   - PK=STRING (`TICKER#{symbol}`), SK=STRING (`DATE#{YYYY-MM-DD}`)
   - TTL attribute: `ttl` (280 days from record creation)
   - Deploy via `cdk deploy oss-dev-database` only

2. **`app/db/tables.py`: `PriceHistoryTable` operations** (follow `IVHistoryTable` pattern)
   - `put_bar(ticker, date, open, high, low, close, volume, vwap)`
   - `get_bars(ticker, lookback_days) -> list[DailyBar]`
   - `batch_put_bars(bars)`

3. **`app/services/price_history.py`: `PriceHistoryService`**
   - `get_bars(ticker, lookback_days=252) -> list[DailyBar]` — reads from DynamoDB, falls back to Polygon cache-miss
   - `refresh_daily()` — fetches yesterday's bar for all Russell 1000 + S&P 500 tickers (one grouped-daily call)
   - Write-through caching on Polygon fallback

4. **`backend/scripts/backfill_price_history.py`**
   - For each ticker in combined universe: fetch 280 daily bars via Polygon `/v2/aggs/ticker/{ticker}/range/1/day`
   - Batch-write to DynamoDB (25 bars per write)
   - Expected: ~1,500 API calls, Polygon Advanced Options plan covers this easily

5. **EventBridge: daily refresh hook**
   - Runs at 5am UTC (post-market close)
   - Invokes `PriceHistoryService.refresh_daily()`

6. **`app/features/underlying.py`: new fields on `UnderlyingFeatures`**
   - `ma_150`, `ma_200` — SMA 150 and 200
   - `high_52w`, `low_52w` — 252-bar max/min close
   - `bb_width_percentile` — 20-day BB Width percentile-ranked against past 252 days
   - Caller passes 252-day bar window (vs today's ~60-day window)

### 1.B Earnings Infrastructure

**Decision:** keep existing `oss-dev-earnings-cache` for next-earnings fast lookup (no change). Add new `oss-dev-earnings-history` for historical events (past 4 quarters + historical move magnitude data).

1. **CDK: create `oss-dev-earnings-history` table**
   - PK=STRING (`TICKER#{symbol}`), SK=STRING (`EARNINGS#{YYYY-MM-DD}`)
   - GSI1: `GSI1PK=EARNINGS_DATE#{YYYY-MM-DD}`, `GSI1SK={ticker}` — date-range queries across tickers
   - Fields: `ticker`, `earnings_date`, `fiscal_period`, `eps_estimate`, `eps_actual`, `revenue_estimate`, `time_of_day`, `pre_earnings_close`, `post_earnings_close`, `one_day_move_pct`, `last_updated`
   - No TTL (historical records should persist)

2. **`app/db/tables.py`: `EarningsHistoryTable` operations**
   - `put_event(ticker, earnings_date, event_data)`
   - `get_recent_events(ticker, n=4) -> list[EarningsEvent]` — most recent N events
   - `get_next_event(ticker) -> Optional[EarningsEvent]` — first event where date >= today
   - `batch_put_events(events)`

3. **`app/services/earnings_calendar.py`: `EarningsCalendarService`**
   - Wraps Finnhub + DynamoDB
   - `get_historical_move_magnitude(ticker) -> Optional[float]` — avg absolute 1-day post-event return over last 4 events
   - `get_next_earnings(ticker) -> Optional[date]` — delegates to existing `EarningsCacheService` for hot path
   - `refresh_historical(ticker)` — fetches last 4 earnings from Finnhub, joins with price history to compute 1-day move, writes to `oss-dev-earnings-history`

4. **`backend/scripts/backfill_earnings_history.py`**
   - For each ticker: fetch last 4 earnings via Finnhub `/calendar/earnings?from=X&to=Y` (X = 18 months ago)
   - For each event: compute 1-day post-announcement move using `PriceHistoryService`
   - Write to `oss-dev-earnings-history`
   - Expected: ~1,500 Finnhub calls, ~25 minutes at 60 req/min rate limit

5. **EventBridge: daily earnings refresh hook**
   - Runs at 4am UTC
   - Refreshes any ticker whose next_earnings has passed (recomputes their latest 1-day move and appends new event)

### 1.C Feature Integration

1. **Sector map audit + backfill (CRITICAL PRECONDITION)**
   - Query count of Russell 1000 tickers with non-empty `sector` field
   - If <95% coverage, backfill via Finnhub profile endpoint or hardcoded sector map
   - Extend `SP500TickerTable.get_sector_map()` to accept optional `universe` filter

2. **`app/features/relative_strength.py`: `compute_sector_relative_strength`**
   - Sector → ETF map hardcoded: XLK, XLF, XLV, XLE, XLI, XLP, XLY, XLU, XLB, XLRE, XLC
   - Computes sector ETF 20-day return minus SPY 20-day return
   - Returns the ticker's sector RS

3. **`app/features/catalyst.py`: historical_move_magnitude integration**
   - `CatalystDataService.get_historical_move_magnitude(ticker)` — already defined contract; implementation pulls from `EarningsHistoryTable`
   - Flag `historical_move_confidence` = count of events used (0, 2, 3, 4)

4. **`app/features/models.py`: extend `FeatureSet` with new fields**
   - `ma_150`, `ma_200`, `high_52w`, `low_52w`, `bb_width_percentile`
   - `sector_rs_20d`, `sector` (string classification)
   - `historical_move_magnitude`, `historical_move_confidence`

### Phase 1 Acceptance Criteria

- [ ] `oss-dev-price-history` table created; backfill achieves ≥99% coverage on Russell 1000 + S&P 500
- [ ] Daily price history refresh runs without errors (verified via CloudWatch after one day)
- [ ] `oss-dev-earnings-history` table created; backfill achieves ≥90% coverage
- [ ] Daily earnings refresh runs without errors
- [ ] Feature computation returns non-null `ma_200`, `high_52w` for ≥99% of tickers
- [ ] Feature computation returns non-null `sector_rs_20d` for ≥95% of tickers
- [ ] Feature computation returns non-null `historical_move_magnitude` for ≥85% of tickers
- [ ] `pytest tests/ --tb=short -q` passes with zero regressions
- [ ] `ruff check app/` clean
- [ ] `mypy app/` clean

### Phase 1 Deployment Sequence

1. `cdk deploy oss-dev-database` — creates new tables (SAFE, never run backend stack)
2. `./scripts/deploy.sh backend` — deploys services + feature integration
3. Run backfill scripts:
   - `python scripts/backfill_price_history.py`
   - `python scripts/backfill_earnings_history.py`
4. Validate coverage via queries
5. Merge to `main`, tag `phase-1-data-foundation-YYYY-MM-DD`

### Phase 1 Rollback

Features are additive. If broken:
1. Disable service calls in `catalyst.py` / `relative_strength.py` (returns None, graceful degradation via weight redistribution)
2. Lambda rollback: `./scripts/deploy.sh rollback`
3. Tables can stay — no data dependency yet

### Phase 1 Known Issues — MUST address before Phase 7 activation

These items were discovered during the Phase 1 deploy (2026-04-17) and worked around with minimal-risk substitutes. None block Phase 2-6 (which only deploy Lambda code via `./scripts/deploy.sh backend`, not CDK). They **must** be cleaned up before Phase 7, when we flip the active policy to v4.0.0 and any deploy reliability issue becomes a production risk.

**⚠ Action required (separate cleanup session before Phase 7):**

1. **CloudFormation drift on `oss-dev-daily-data-capture` EventBridge rule.** The rule exists in AWS but CloudFormation no longer owns it. Any `cdk deploy oss-dev-backend` fails with `AlreadyExists`. Resolution: either (a) delete the orphaned rule and let CDK recreate it — requires a ~3-5 min Lambda-broken window during deploy, or (b) use `cloudformation create-change-set --change-set-type IMPORT` to reconcile ownership without downtime. Option (b) is cleaner.

2. **`oss-dev-nightly-scribe` EventBridge rule not in CDK code.** Also orphaned from CloudFormation. Same resolution path as #1.

3. **Pillar v4 EventBridge rules created manually, not via CDK.** Phase 1 created `oss-dev-price-history-refresh` and `oss-dev-earnings-history-refresh` via `aws events put-rule` to sidestep #1. Once #1 is resolved, import these two rules into CloudFormation alongside so the CDK template matches reality.

4. **`DiaryTable` and `IntelligenceBucket` deleted** during the Phase 1 database-stack deploy. Confirmed non-issue by Nick (feature abandoned), but noting here so the audit trail is complete.

**Why deferred:** Resolving drift cleanly requires a CDK-focused session with time to test imports and verify no resources are inadvertently affected. Phase 1 data-foundation goals were achieved with manual rule creation, keeping risk low and momentum on the primary workstream.

**Do not advance to Phase 7 with these items unresolved** — by Phase 7 we need the ability to rollback via CDK if needed, and that requires drift-free stacks.

---

## 8. Key Sub-Rules (apply throughout)

1. **Geometric mean insufficient-data rule:** a pillar with <3 available subscores returns score = 0. Composite then evaluates to 0 → auto-REJECT.
2. **Geometric mean floor:** a pillar with ≥3 subscores returns `max(1, weighted_sum)`. Floor prevents one weak pillar from zero-collapsing composite.
3. **Policy-version gating:** all v3 code stays until Phase 9. All v3 data stays forever (denormalized fields on `PaperPosition`, Decision/PillarScore enum values).
4. **Never `cdk deploy oss-dev-backend`:** breaks Lambda with raw unpackaged code. Use `./scripts/deploy.sh backend` always.
5. **Deploy per logical change:** never bundle multiple phases into a single deploy.
6. **Verify every deploy:** CloudWatch, Pipeline Monitor, health endpoint checks required per CLAUDE.md.
7. **Merge to main after every deploy:** `main` must stay in sync with production Lambda.

---

## 9. Open Items for Future Phases

- Phase 3 will need to resolve how the LLM prompt documents the geometric-mean composite ("why your score is X means Y"). Design during Phase 3.
- Phase 7 activation target day: Tuesday ~10-14 business days from Phase 1 start. Actual date TBD.
- Phase 10 rescore job: should it use the time-of-entry feature values (stored in `FeatureValueTable`) or recompute from today's data? **Decision: use stored features where available, recompute where missing.**

---

## 10. Quick Reference — Current State of Key Files (verified 2026-04-16)

| File | Lines | Finding |
|---|---|---|
| `backend/app/core/schemas.py` | 62-67 | PillarId enum with 3 v3 values |
| `backend/app/core/schemas.py` | 728-752 | PillarWeights with v3.1.0 defaults (0.25/0.35/0.40) |
| `backend/app/core/schemas.py` | 949-999 | PillarConfig validator — forces v3 shape |
| `backend/app/core/schemas.py` | 437-523 | PaperPosition with v3 denormalized fields |
| `backend/app/pillars/calculator.py` | 86-96 | Orchestrator — hardcoded v3 dispatch |
| `backend/app/pillars/calculator.py` | 188-207 | get_pillar_scores_dict — hardcoded v3 |
| `backend/app/pillars/calculator.py` | 210-243 | compute_final_score — hardcoded v3 weights |
| `backend/app/decision/calculator.py` | 128-150 | Composite = weighted arithmetic sum |
| `backend/app/decision/calculator.py` | 204-259 | 13 hardcoded v3 reason codes |
| `backend/app/llm/prompt.py` | 191-193 | LLM prompt with v3 labels |
| `backend/app/llm/models.py` | 54-56, 131-133 | LLM ScoreInput with v3 fields |
| `backend/app/llm/generator.py` | 166-168 | LLM generator pulls v3 fields from Decision |
| `backend/app/db/tables.py` | 1866-1967 | SP500TickerTable — sector/universe support |
| `backend/app/features/underlying.py` | — | Needs ma_150, ma_200, high_52w, low_52w, bb_width_percentile |
| `infrastructure/cdk/stacks/database_stack.py` | 282-292 | Existing earnings-cache table (PK=ticker only) |
| Frontend `lib/types.ts` | 19, 157-160, 195-197, 419-423, 456-461 | Confirmed v3 pillar references |
| Frontend `pages/EvaluationDetail.tsx` | 363-367, 411 | pillarConfig map + conditional on PREMIUM_LEVERAGE |
| Frontend `pages/PolicyConfig.tsx` | 946 | Hardcoded pillar-key array |

---

## 11. Execution Protocol

Follow `CLAUDE.md` deployment protocol (mandatory):

1. Pre-deploy: tests + lint + type-check must pass
2. Deploy via `./scripts/deploy.sh backend` (never `cdk deploy oss-dev-backend`)
3. Verify: CloudWatch errors, Pipeline Monitor stages, health endpoint
4. Merge to `main` after every successful deploy
5. Tag milestone at end of each phase

---

**End of Plan. Phase 1 in execution.**
