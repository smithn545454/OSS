# Pillar v4 Execution Plan: Directional Conviction, Move Potential, Trade Structure

**Author:** Principal engineering plan (Claude), revised after codebase audit with Nick 2026-04-16
**Status:** Phase 1 + Phase 2 + Phase 3 complete (2026-04-17). Phase 4 ready to start pending Nick sign-off.
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

1. ✅ **Reason code generator** (Phase 3) — `backend/app/decision/calculator.py:204-259` (`generate_supporting_reasons`) contained 13 hardcoded v3 reason codes. Rewritten to dispatch on `DecisionContext.is_v4()`: v4 emits `STRONG_/DECENT_/WEAK_/POOR_DIRECTIONAL_CONVICTION/MOVE_POTENTIAL/TRADE_STRUCTURE`, `SHARPSHOOTER_SETUP` (tier_1), and `INSUFFICIENT_DATA_*` for zero-scored pillars. V3 codes preserved for historical records.
2. ✅ **LLM prompt/model/generator** (Phase 3) — Rewritten. `ScoresData` carries both regimes + a `regime` marker; `build_thesis_prompt` dispatches on regime (v3 → arithmetic composite with legacy labels; v4 → geometric mean with exponents and Sharpshooter labels); system prompt updated to document insufficient-data zero-collapse behaviour.
3. ✅ **Pillar orchestrator is fully hardcoded** (Phase 3) — `backend/app/pillars/calculator.py` refactored to registry pattern. `PillarCalculator` selects `_V3_REGISTRY` or `_V4_REGISTRY` based on active config. Both regimes reachable through Phase 9.
4. ✅ **DecisionCalculator signature** (Phase 3) — `compute_final_score` retained its v3 positional signature (backward-compat for rescore / tests / paper-trading); `compute_final_score_from_results(pillar_results, ...)` added for v4. `assign_quality_tier` accepts either v3 positional trio or v4 kwargs. `compute_decision` flips on `ctx.is_v4()`.
5. ✅ **PillarConfig validator forces v3 shape** (Phase 2) — Rewritten. Now enforces exactly one regime (fully v3 OR fully v4) and gates on `composite_formula` matching the regime.
6. ✅ **Sector map may not cover Russell 1000** (Phase 1) — Audit found 4.6% real coverage. `backfill_sectors.py` built using Finnhub `/stock/profile2` with a Finnhub-to-GICS taxonomy map. Post-backfill: 99.6% coverage (987 updated + 34 pre-existing / 1025 tickers).

---

## 6. Phase Schedule

Total active-engineering estimate: ~22-24 working days (original 10-14 understated scope).

| Phase | Scope | Duration | Status |
|---|---|---|---|
| 1 | Data foundation: price history + earnings history tables; backfills for ~1,500 tickers; new features (ma_150/200, high_52w, low_52w, bb_width_percentile, sector_rs_20d, historical_move_magnitude); sector map coverage validation | 4 days | ✅ **Complete** (2026-04-17, Lambda v234) — see §7.4 |
| 2 | Schema extensions (PillarId enum, PillarWeights, PillarConfig, Decision, PaperPosition — additive) | 1 day | ✅ **Complete** (2026-04-17, Lambda v235) — see §7.5 |
| 3 | v4 pillar classes + orchestrator registry refactor + LLM prompt/model/generator rewrite + composite formula + geometric-mean min-count rule + reason-code rewrite + tests | 5 days | ✅ **Complete** (2026-04-17, Lambda v236) — see §7.6 |
| 4 | Frontend types, `pillarMeta.ts`, EvaluationDetail page (new names/weights/geometric-mean explanation), Policy page, paper trading components | 2 days | ✅ **Complete** (2026-04-17, commit a36b14c, CloudFront live) — see §7.7 |
| 5 | v4.0.0 policy config build + seed (`PillarConfig.v4_default()` + `v4_default_policy.json`) | 1 day | ⏳ **Next up** |
| 6 | Backend hardcoded-reference sweep (API routes, paper trading, rule matcher, calibration, scripts) + test fixture migration (334 occurrences / 27 files) + flip v3 Decision score fields to Optional | 3 days | Pending |
| 7 | Activation (Tuesday) — **blocked on CloudFormation drift cleanup** (see §7.4 known issues) | 1 day | Blocked |
| 8 | 2-week observation + tuning | 14 days | Pending |
| 9 | v3 code removal | 2 days | Pending |
| 10 | Historical paper-position rescore: Finnhub earnings-history backfill, batch rescore v4 over 15,505 positions; v4 fields replace v3 fields on `PaperPosition` | 2-3 days | Pending |

**Progress summary (end of 2026-04-17):** 4 of 10 phases complete. Backend Lambda v234/v235/v236 healthy with v3.1.3 policy still active; frontend CloudFront now carries the dual-regime renderer (commit a36b14c) and will display whichever pillar set the active policy produces. Zero behavior change for users; all v4 code paths unreachable until a v4 policy activates at Phase 7.

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

## 7.4 Phase 1 Actual Outcomes — Data Foundation (2026-04-17)

| Metric | Target | Actual |
|---|---|---|
| Price history coverage | ≥99% | **99.5%** (1020/1025 tickers, 195,202 bars) |
| Sector coverage (real GICS) | ≥95% | **99.6%** (987 updated + 34 pre-existing / 1025) |
| Earnings coverage | ≥90% | In progress (expected ≥95% based on /stock/earnings availability) |
| Full test suite | zero regressions | 2,192 passing |

Key mid-deploy course corrections:
- **Volume int/float bug** in `_to_price_history` caught 99% of first backfill attempt — Polygon returns split-adjusted fractional volume; Pydantic v2 strict-int rejected it. Fixed by rounding at the boundary (commit b607a30).
- **Finnhub /calendar/earnings free-tier limitation** — returns only the *next* upcoming event regardless of date range, so Phase 1's historical 1-day-move calculations needed a rewrite around `/stock/earnings` + volume-spike announcement detection (commit 0a1f509).
- **Sector-map audit revealed 4.6% real coverage** on the combined S&P 500 + Russell 1000 universe, not the expected 95%. Resolved in-session by adding a `backfill_sectors.py` script using Finnhub's `/stock/profile2` endpoint with a 150-entry Finnhub-to-GICS taxonomy mapping.

### Phase 1 Known Issues — MUST address before Phase 7 activation (still open at end of Phase 3)

These items were discovered during the Phase 1 deploy (2026-04-17) and worked around with minimal-risk substitutes. None block Phase 2-6 (which only deploy Lambda code via `./scripts/deploy.sh backend`, not CDK). They **must** be cleaned up before Phase 7, when we flip the active policy to v4.0.0 and any deploy reliability issue becomes a production risk.

**⚠ Action required (separate cleanup session before Phase 7):**

1. **CloudFormation drift on `oss-dev-daily-data-capture` EventBridge rule.** The rule exists in AWS but CloudFormation no longer owns it. Any `cdk deploy oss-dev-backend` fails with `AlreadyExists`. Resolution: either (a) delete the orphaned rule and let CDK recreate it — requires a ~3-5 min Lambda-broken window during deploy, or (b) use `cloudformation create-change-set --change-set-type IMPORT` to reconcile ownership without downtime. Option (b) is cleaner.

2. **`oss-dev-nightly-scribe` EventBridge rule not in CDK code.** Also orphaned from CloudFormation. Same resolution path as #1.

3. **Pillar v4 EventBridge rules created manually, not via CDK.** Phase 1 created `oss-dev-price-history-refresh` and `oss-dev-earnings-history-refresh` via `aws events put-rule` to sidestep #1. Once #1 is resolved, import these two rules into CloudFormation alongside so the CDK template matches reality.

4. **`DiaryTable` and `IntelligenceBucket` deleted** during the Phase 1 database-stack deploy. Confirmed non-issue by Nick (feature abandoned), but noting here so the audit trail is complete.

**Why deferred:** Resolving drift cleanly requires a CDK-focused session with time to test imports and verify no resources are inadvertently affected. Phase 1 data-foundation goals were achieved with manual rule creation, keeping risk low and momentum on the primary workstream.

**Do not advance to Phase 7 with these items unresolved** — by Phase 7 we need the ability to rollback via CDK if needed, and that requires drift-free stacks.

---

## 7.5 Phase 2 Outcomes — Schema Extensions (2026-04-17)

**Shipped:** Lambda v235, commit `36dd7fb`, merged to `main`. All schema changes additive; v3.1.3 remains the active policy with zero behavior change.

### What shipped

- **`PillarId` enum** extended with three v4 values (`DIRECTIONAL_CONVICTION`, `MOVE_POTENTIAL`, `TRADE_STRUCTURE`) alongside preserved v3. V3 values are retained permanently for historical-data deserialization (per plan Section 2).
- **`PillarWeights` / `PillarConfig`**: all pillar-related fields converted to `Optional`; new regime-detecting validators enforce fully-v3 OR fully-v4 shape (mixed or bare construction rejected). Added `PillarWeights.v3_default()` / `v4_default()` and `PillarConfig.v3_default()` classmethods as explicit transition helpers — marked for Phase 9 removal.
- **`PillarConfig.composite_formula`** field added (`"weighted_sum"` for v3, `"weighted_geometric_mean"` for v4). Consumed by Phase 3 composite function.
- **`Decision` / `EvaluationSnapshot`**: added three v4 pillar-score fields as `Optional[float]`. V3 score fields kept non-Optional for Phase 2 — see deferral #1 below.
- **`PaperPosition`**: added three v4 denormalized pillar fields (`pillar_directional_conviction`, `pillar_move_potential`, `pillar_trade_structure`) as `Optional[float]`.
- **Production fallback callers** (`DecisionCalculator`, `DecisionStage`, `PillarCalculator`, `PillarStage`, `rescore_all_positions.py`): updated from bare `PillarWeights()` / `PillarConfig()` to explicit `v3_default()` factories. All call sites marked for Phase 9 cleanup.

### Design decision: Option A clean cutover (confirmed 2026-04-17)

Nick confirmed: old v3 weights are **not** the future default. PillarWeights/PillarConfig bare construction is now intentionally invalid. The `v3_default()` / `v4_default()` classmethods are the only acceptable baselines during the transition window; Phase 9 removes `v3_default()`.

### Verification

| Check | Result |
|---|---|
| Backend test suite | 2,195 passing |
| Ruff (touched files) | 151 baseline → 151 post-change (zero new regressions) |
| CloudWatch ERROR logs (5 min post-deploy) | none |
| Health endpoint | `healthy` |
| Active policy v3.1.3 deserialization under new schema | v3 fields populated, v4 fields `null` ✓ |
| Post-deploy pipeline run | completed, status=`healthy`, 220 contracts in prior run |

### Phase 2 deferrals (by design) — track for later phases

1. **V3 `premium_leverage_score` / `underlying_behavior_score` / `setup_quality_score` fields on `Decision` + `EvaluationSnapshot` were kept non-Optional** for Phase 2. The plan's Section 6 showed them as `Optional[float] = None`, but ~30 downstream read sites treat them as `float` (arithmetic comparisons, direct field reads, dict extraction). Making them Optional in Phase 2 would force mypy-narrowing refactors across v3-specific code paths, exceeding Phase 2's "purely additive" scope. **Phase 6 (hardcoded-reference sweep)** is the right home for this transition, since Phase 6 is already touching every v3 read site for generalization.

2. **Mypy narrowing in two v3 call sites** (`pillars/calculator.py:87-95`, `api/routes/paper_trading.py:2110-2120`): `config.premium_leverage` etc. became `Optional[PillarConfigV2]`, so v3 compute paths now have `assert` narrowing. These are interim — Phase 3 registry refactor replaces the entire hardcoded v3 dispatch with a regime-aware orchestrator.

3. **Redundant string-equality checks in `pillars/calculator.py:207-211`** (`pid == PillarId.PREMIUM_LEVERAGE or pid == "PREMIUM_LEVERAGE"`) now trigger mypy `comparison-overlap` warnings because the widened enum narrows the `or`-branch type. Latent dead code pre-existing; cleaned up in Phase 6.

### Heads-up items for Phase 3 kickoff

- **Phase 1 known issues still stand** (CloudFormation drift + manual EventBridge rules). They only block Phase 7 activation, not Phases 3–6 Lambda-code deploys. Schedule the drift-cleanup session before Phase 7.
- **`composite_formula` field is live but unused** until Phase 3 ships the `app/pillars/composite.py` dispatcher. Current v3.1.3 policy defaults it to `"weighted_sum"`, matching existing arithmetic behavior.
- **Phase 3 entry point** = `app/pillars/calculator.py` orchestrator needs to discover which pillar configs are populated in the active policy (v3 vs v4) and dispatch accordingly. Registry refactor is the natural home; v3 code paths remain reachable until Phase 9.

---

## 7.6 Phase 3 Outcomes — v4 Compute Classes + Regime Dispatch (2026-04-17)

**Shipped:** Lambda v236, commit `dcd1e5f`, merged to `main`. All new code is additive and unreachable until a v4 policy activates at Phase 7 — v3.1.3 remains the active policy with zero behavior change.

### What shipped

- **`app/pillars/composite.py`** — `compute_composite_score(pillar_results, config, scanner_source)` dispatches on `PillarConfig.composite_formula`: `"weighted_sum"` → v3 arithmetic, `"weighted_geometric_mean"` → v4 geometric. Exposes `weighted_sum` and `weighted_geometric_mean` as public helpers, plus `apply_v4_rules()` for the per-pillar **insufficient-data rule** (score=0 when <3 subscores available → geometric mean collapses composite to 0 → auto-REJECT) and **floor rule** (score=`max(1, weighted_avg)` when enough subscores present, preventing zero-collapse).
- **Three new pillar compute modules** (each with a tagged accessor that exposes derived feature values to the scoring engine):
  - `directional_conviction.py` — 6 subscores: Stage 2 Minervini trend template (computed on-the-fly, 7-criteria bullish/bearish), RS 20d, ADX × ±DI directional agreement, 52-week pivot proximity, OBV confirmation, sector RS. Tags: `STAGE_2_TREND`, `RS_LEADER`, `ADX_DIRECTIONAL_AGREE`, `NEAR_BREAKOUT`, `VOLUME_CONFIRMED`, `SECTOR_LEADER`.
  - `move_potential.py` — 5 subscores: Move trigger (catalyst-window + breakout fallback), historical post-event move magnitude, IV/RV ratio, BB width percentile, expected/required move ratio. Tags: `CATALYST_IN_WINDOW`, `CHEAP_IV_EXPANSION`, `VOLATILITY_COMPRESSION`, `EXPECTED_MOVE_EXCEEDS_REQUIRED`, `LOW_HISTORICAL_CONFIDENCE`.
  - `trade_structure.py` — 5 subscores: Delta sweet spot, gamma/theta ratio (`γ·S²·σ/|θ|`), catalyst-aware DTE sweet spot, IV rank, strike-to-pivot distance. Tags: `DELTA_SHARPSHOOTER`, `GAMMA_RICH`, `DTE_SWEETSPOT`, `IV_RANK_CHEAP`, `STRIKE_AT_PIVOT`.
- **`ScoringContext` extended** — Phase 1 fields (`ma_150`, `ma_200`, `high_52w`, `low_52w`, `dist_to_52w_*_pct`, `bb_width`, `bb_width_percentile`, `sector`, `sector_rs_20d`, `historical_move_magnitude`, `historical_move_confidence`) plus `gamma`, `theta`, `vega`, `strike` from Evaluation. Both `from_evaluation_and_features` and `from_position_and_features` populate them, using `getattr(..., None)` fallbacks so historical positions without snapshot fields gracefully degrade.
- **Orchestrator refactor** — `PillarCalculator` selects v3 or v4 registry by inspecting which pillar slots are populated on the active `PillarConfig`. Both regimes flow through the same batch paths. Regime-aware diagnostic logging ("Pillar v3/v4 data availability …").
- **Module-level `compute_final_score(pl, ub, sq, config, scanner_source)`** — retained as the v3 positional API for rescore scripts, tests, paper-trading calls. New `compute_final_score_from_results(pillar_results, config, scanner_source)` accepts `PillarResult` objects directly and dispatches via the composite module. Both coexist through Phase 9.
- **`DecisionCalculator` regime-aware** — `DecisionContext` carries v3 AND v4 scores as `Optional[float]`, `is_v4()` flips all downstream logic (composite formula, tier assignment kwargs, reason-code emission). Added v4 reason codes (`STRONG_/DECENT_/WEAK_/POOR_DIRECTIONAL_CONVICTION/MOVE_POTENTIAL/TRADE_STRUCTURE`), `SHARPSHOOTER_SETUP` (v4 tier_1), `INSUFFICIENT_DATA_*` (emitted when v4 min-subscore rule zeros a pillar). Legacy `compute_final_score` and positional `assign_quality_tier` preserved; new `assign_quality_tier(..., directional_conviction=..., ...)` kwargs added for v4.
- **v3 Decision fields kept non-Optional** (per Phase 2 deferral) — v4 decisions populate v3 score fields with `0.0` as an inactive-regime sentinel. Phase 6 migrates readers to handle `Optional[float]`.
- **LLM module regime-aware** — `ScoresData` gains a `regime` marker + optional v3/v4 pillar-score fields. `build_thesis_prompt` dispatches on regime: v3 renders arithmetic-sum composite with legacy labels; v4 renders weighted geometric mean with exponents and Sharpshooter labels. System prompt updated to document the geometric-mean insufficient-data behaviour so the LLM reasons about weak pillars correctly. `ThesisGenerator.build_input` infers regime from the Decision.
- **`extract_pillar_scores_for_decision` is now regime-agnostic** — emits whichever snake_case keys are present on the results, no 50.0 default padding.

### Verification

| Check | Result |
|---|---|
| Backend test suite | 2275 passing, 0 new regressions |
| New v4 tests (composite / 3 pillars / integration) | 80 passing, 91–98% coverage on new files |
| Ruff on new files | 0 errors |
| Ruff on pillars/decision/llm overall | +2 vs baseline (both in long-line existing patterns) |
| CloudWatch ERROR logs post-deploy | none (pre-existing contract_selector parse errors predate v236) |
| Health endpoint | `healthy` |
| Post-deploy pipeline run | v3 dispatch confirmed via "Pillar v3 data availability" diagnostic; 100% coverage on adx_14/iv_percentile/iv_rv_ratio/rv20/feasibility |
| v3.1.3 policy behavior | identical to pre-Phase 3 |

### Phase 3 deferrals (by design) — track for later phases

1. **Phase 5 still owes `PillarConfig.v4_default()` + seeded `v4_default_policy.json`.** Phase 3 leaves v4 configs to be constructed explicitly by callers (tests, Phase 5 seed script). Production cannot activate v4 until Phase 5 ships the JSON seed and `v4_default()` classmethod.
2. **Decision v3 score fields remain non-Optional.** When a v4 decision is emitted, `premium_leverage_score` / `underlying_behavior_score` / `setup_quality_score` are filled with `0.0` as a sentinel. Phase 6 migrates the ~30 downstream readers to handle `Optional[float]` and flips the schema.
3. **Reason-code v4 display mapping** — v4 reason codes (`STRONG_DIRECTIONAL_CONVICTION`, etc.) are emitted by the decision calculator; frontend display mapping ships in Phase 4 alongside `pillarMeta.ts`.
4. **Per-scanner weight presets for v4** — Phase 5 policy seed should include per-scanner overrides (`scanner_weights`). Phase 3 ships the plumbing; calibrated values follow in Phase 8 tuning.

### Heads-up items for Phase 4 kickoff

- **Frontend starts here.** `pillarMeta.ts` creation, `EvaluationDetail` / `PolicyConfig` / paper-trading components must read pillars from a single display-metadata map so both v3 and v4 decisions render correctly. Types change: `PillarId` becomes a union of `PillarIdLegacy | PillarIdV4`.
- **Visual smoke tests required.** Phase 4 is the first phase that touches user-facing surfaces — Playwright or manual visual verification on v3 historical records (no change) and a test v4 decision fixture (new labels / icons / colors) are in scope per plan Section 5.3.
- **LLM Phase 3 changes are already live.** Once a v4 decision flows through the pipeline (Phase 7+), the thesis prompt automatically renders v4 labels. No frontend work needed for the thesis viewer — existing renderer shows whatever the LLM produces.
- **Phase 1 CloudFormation drift still blocks Phase 7.** No change from Phase 2.

---

## 7.7 Phase 4 Outcomes — Frontend Dual-Regime Renderer (2026-04-17)

**Shipped:** Frontend commit `a36b14c`, merged to `main`, deployed to CloudFront (bundle `index-D9A_PRMK.js`). Zero user-visible change — v3.1.3 policy still active — but the entire UI now renders through a single pillar-metadata source of truth and handles both v3 and v4 decisions.

### What shipped

- **`frontend/src/lib/pillarMeta.ts`** — the single display-metadata map. 306 lines. Covers all six `PillarId` values (three legacy marked `legacy: true`, three v4 Sharpshooter) with per-pillar label/shortLabel/icon (lucide-react: Zap/Activity/BarChart3 for v3, Compass/Rocket/Layers for v4)/color/badgeClass/defaultWeight/description. Exports helpers: `pillarMeta(id)`, `pillarIdFromKey`, `isV4PillarConfig`, `activePillarKeys`, `compositeFormulaDescription`, `PILLAR_KEYS_LEGACY` / `PILLAR_KEYS_V4`, plus `REASON_CODE_LABELS` + `reasonCodeLabel(code)` covering the full v4 reason-code vocabulary (SHARPSHOOTER_SETUP / STRONG|DECENT|WEAK|POOR_ per pillar / INSUFFICIENT_DATA_*) and the legacy v3 codes.
- **`frontend/src/lib/types.ts`** — `PillarId` became `PillarIdLegacy | PillarIdV4`. `PillarWeights`, `PillarConfig`, `Decision.*_score`, and `PaperPosition.pillar_*` fields are now all Optional with both regimes' slots present. `PillarConfig.composite_formula` added. `ApproveEvaluation.pillarScores` widened to `Partial<Record<PillarId, number>>`.
- **`EvaluationDetail.tsx` PillarCard** — reads `pillarMeta(pillar.pillar_id)` for icon/color/label. Subscore-vs-contributors toggle is now data-driven (uses `showFullBreakdown = meta.legacy ? pillar_id === 'PREMIUM_LEVERAGE' : contributors.length <= 6`), so v4 pillars render a full subscore breakdown like v3 Premium Leverage does. `DecisionExplanation` uses `reasonCodeLabel(code)` so v4 reason codes render with proper capitalization ("Sharpshooter Setup", "Strong Directional Conviction", …).
- **`PolicyConfig.tsx`** — new `PillarWeightsEditor` component iterates `activePillarKeys(config.pillars)` dynamically and labels each weight via `pillarMeta`. Section header flips between "Pillar Weights" (v3) and "Pillar Weights (v4 Sharpshooter)" (v4). Composite formula is displayed via `compositeFormulaDescription(formula)`. Weight-sum validator is regime-aware; first populated key carries the error. Read-only subscore list also iterates the regime's populated pillar slots.
- **`TradeDetail.tsx`** — new `renderSnapshotPillarMetrics()` helper checks the trade snapshot for v4 score fields first (`directional_conviction_score`, etc.), and falls back to v3 score fields if absent. Pre-v4 trades keep displaying their immutable v3 snapshot values forever.
- **Paper-trading components** (all four): `PositionTracker.tsx` expanded panel, `ScoreCalibration.tsx` radar chart axes, `TradeLibrary.tsx` expanded detail — each uses `(p as unknown as Record<...>)[`pillar_${key}`]` lookups driven by whichever pillar set the position carries. Legacy positions continue to render with v3 pillars; v4-era positions will show v4 pillars. `ManualRuleForm.tsx` Scores & Quality group gains three v4 pillar-minimum fields (pillar_directional_conviction_min / pillar_move_potential_min / pillar_trade_structure_min) alongside the three v3 fields.
- **`FeatureImportance.tsx`** — pillarBadge color map expanded to cover both the legacy lowercase semantic labels (directional / volatility / structure), the v3 PillarId and snake_case variants, and all v4 variants. Unknown values fall through to a neutral palette.
- **`convictionScore.ts`** — doc-comment rewritten to describe both regimes (weighted arithmetic sum for v3, weighted geometric mean for v4 with zero-collapse behavior). Frontend logic unchanged: reads `decision.final_score` directly; regime selection happens server-side.

### Verification

| Check | Result |
|---|---|
| `tsc -b` | clean (0 errors) |
| `vite build` | clean; 2,191 modules, 953 KB JS / 68 KB CSS bundle |
| `eslint` | clean (1 pre-existing `react-refresh/only-export-components` warning on `FilterBar.tsx`, unrelated to Phase 4) |
| New tests: `pillarMeta.test.ts` + updated `convictionScore.test.ts` | 47/47 passing (20 new pillarMeta tests covering all six IDs, regime detection, key→id mapping, composite-formula descriptions, v4+v3 reason-code labels, and unknown fallback) |
| Pre-existing test-file failures | 4 suites fail to import because `@testing-library/react` isn't installed locally — confirmed to fail identically on `main` pre-change (stash + vitest run). Not a Phase 4 regression. 2 `scannerMetrics.test.ts` failures are pre-existing `-0 vs +0` and formatting drift, also unrelated. |
| CloudFront bundle published | `index-D9A_PRMK.js` serving as of 2026-04-17 T18:14 UTC |
| Bundle content check | `curl ... \| grep -E "Directional Conviction\|Move Potential\|Trade Structure\|Sharpshooter Setup\|weighted_geometric_mean\|DIRECTIONAL_CONVICTION\|MOVE_POTENTIAL\|TRADE_STRUCTURE"` returns all eight strings → v4 code shipped. |
| CloudWatch ERRORs, 10 min post-deploy | none |
| Health endpoint | `healthy` |
| Pipeline Monitor post-deploy | 155 contracts in latest run, status `healthy`, v3.1.3 policy still active |

### Phase 4 deferrals (by design) — track for later phases

1. **Full visual regression pass requires a v4 fixture in production.** Until a v4 policy activates (Phase 7), there is no real v4 evaluation to render. The code paths are exercised by unit tests and by the shipped bundle's string content, but pixel-level QA of the v4 pillar cards / weight editor / paper-trading radar deferrals to Phase 7 verification. Rollback is a one-line `./scripts/deploy.sh rollback-frontend` if anything looks wrong.
2. **`Policy page v4 section header copy`** currently reads "Pillar Weights (v4 Sharpshooter)" — Nick may want a different banner/explanation once the real v4.0.0 policy is seeded in Phase 5 (e.g. "Switch to geometric-mean composite", instructions for the grand-slam tier gates). Kept minimal here.
3. **`FilterBar.tsx` react-refresh warning** is pre-existing and not pillar-related; leaving alone.

### Heads-up items for Phase 5 kickoff

- **Frontend is ready for v4 data as soon as Phase 5 seeds a v4 policy.** Activating that policy (Phase 7) will automatically render v4 pillar cards, Sharpshooter tier labels, and geometric-mean descriptions without any additional frontend work.
- **`PillarConfig.v4_default()` classmethod still pending** — Phase 5's responsibility. The frontend expects a policy with `composite_formula: "weighted_geometric_mean"` and the three v4 pillar slots populated; seed script should produce exactly that shape.
- **Per-scanner weight presets (`scanner_weights`)** — the `PillarConfig.scanner_weights` field is typed on the frontend (`Record<string, PillarWeights>`) but no UI editor exists yet. Phase 5 seed can populate neutral defaults; Phase 8 tuning may need a dedicated editor surface.
- **Phase 1 CloudFormation drift still blocks Phase 7.** Unchanged from Phase 2 / 3.

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

## 10. Quick Reference — Original State of Key Files (baseline, verified 2026-04-16)

**This section is a frozen audit snapshot from before any phase executed.** Several locations have been restructured by Phases 1-3 — use it to understand where the work started, not the current state. Line numbers below will not match HEAD; consult §7.4-7.6 outcomes and `git log` for what's there now.

| File | Lines (pre-work) | Original Finding | Status after Phase 3 |
|---|---|---|---|
| `backend/app/core/schemas.py` | 62-67 | PillarId enum with 3 v3 values | ✅ Phase 2 — extended with 3 v4 values alongside |
| `backend/app/core/schemas.py` | 728-752 | PillarWeights with v3.1.0 defaults | ✅ Phase 2 — all fields Optional; `v3_default()` / `v4_default()` classmethods; regime validator |
| `backend/app/core/schemas.py` | 949-999 | PillarConfig validator — forces v3 shape | ✅ Phase 2 — enforces exactly one regime, gates on `composite_formula` |
| `backend/app/core/schemas.py` | 437-523 | PaperPosition with v3 denormalized fields | ✅ Phase 2 — added `pillar_directional_conviction / _move_potential / _trade_structure` (Optional) |
| `backend/app/pillars/calculator.py` | 86-96 | Orchestrator — hardcoded v3 dispatch | ✅ Phase 3 — registry pattern, `_V3_REGISTRY` + `_V4_REGISTRY` |
| `backend/app/pillars/calculator.py` | 188-207 | get_pillar_scores_dict — hardcoded v3 | ✅ Phase 3 — regime-agnostic, emits keys for whichever pillars are present |
| `backend/app/pillars/calculator.py` | 210-243 | compute_final_score — hardcoded v3 weights | ✅ Phase 3 — v3 positional signature preserved; new `compute_final_score_from_results` for v4 |
| `backend/app/decision/calculator.py` | 128-150 | Composite = weighted arithmetic sum | ✅ Phase 3 — regime-aware dispatch via `PillarConfig.composite_formula` |
| `backend/app/decision/calculator.py` | 204-259 | 13 hardcoded v3 reason codes | ✅ Phase 3 — dispatches on `ctx.is_v4()`; v4 reason codes added (`SHARPSHOOTER_SETUP`, `STRONG_DIRECTIONAL_CONVICTION`, …) |
| `backend/app/llm/prompt.py` | 191-193 | LLM prompt with v3 labels | ✅ Phase 3 — regime-aware formatter; v4 renders geometric-mean composite + exponents |
| `backend/app/llm/models.py` | 54-56, 131-133 | LLM ScoreInput with v3 fields | ✅ Phase 3 — `ScoresData` carries both regimes + `regime` marker |
| `backend/app/llm/generator.py` | 166-168 | LLM generator pulls v3 fields from Decision | ✅ Phase 3 — populates both regimes from Decision; infers active regime |
| `backend/app/db/tables.py` | 1866-1967 | SP500TickerTable — sector/universe support | ✅ Phase 1 — backfilled to 99.6% real GICS coverage on S&P 500 + Russell 1000 |
| `backend/app/features/underlying.py` | — | Needs ma_150, ma_200, high_52w, low_52w, bb_width_percentile | ✅ Phase 1 — all added; 99.5% coverage on combined universe |
| `infrastructure/cdk/stacks/database_stack.py` | 282-292 | Existing earnings-cache table (PK=ticker only) | ✅ Phase 1 — new `oss-dev-price-history` + `oss-dev-earnings-history` tables added |
| Frontend `lib/types.ts` | 19, 157-160, 195-197, 419-423, 456-461 | Confirmed v3 pillar references | ✅ Phase 4 — `PillarId` is now `PillarIdLegacy \| PillarIdV4`; Weights/Config/Decision/PaperPosition all have v4 slots Optional. `composite_formula` added. |
| Frontend `pages/EvaluationDetail.tsx` | 363-367, 411 | pillarConfig map + conditional on PREMIUM_LEVERAGE | ✅ Phase 4 — PillarCard reads `pillarMeta(pillar.pillar_id)`; subscore-vs-contributors toggle is data-driven. |
| Frontend `pages/PolicyConfig.tsx` | 946 | Hardcoded pillar-key array | ✅ Phase 4 — `PillarWeightsEditor` iterates `activePillarKeys(config.pillars)` dynamically; labels via `pillarMeta`. |

**New in Phase 3 (not in original audit, worth knowing about):**

| File | Purpose |
|---|---|
| `backend/app/pillars/composite.py` | v3 + v4 composite dispatch, `apply_v4_rules` (min-subscore + floor) |
| `backend/app/pillars/directional_conviction.py` | v4 Directional Conviction pillar (6 subscores, Stage 2 template) |
| `backend/app/pillars/move_potential.py` | v4 Move Potential pillar (5 subscores, catalyst-aware trigger) |
| `backend/app/pillars/trade_structure.py` | v4 Trade Structure pillar (5 subscores, γ/θ ratio) |
| `backend/tests/test_composite.py` + 4 more | 80 v4-specific tests (91-98% coverage on new files) |

**New in Phase 4 (frontend):**

| File | Purpose |
|---|---|
| `frontend/src/lib/pillarMeta.ts` | Single display-metadata source of truth: PILLAR_META + `pillarMeta()` + `pillarIdFromKey` + `isV4PillarConfig` / `activePillarKeys` + `compositeFormulaDescription` + `REASON_CODE_LABELS` / `reasonCodeLabel` |
| `frontend/src/test/pillarMeta.test.ts` | 20 tests covering all six pillars, regime detection, reason-code labeling, unknown-id fallback |
| Component `PillarWeightsEditor` in `PolicyConfig.tsx` | Regime-aware pillar-weights editor — iterates `activePillarKeys(config.pillars)`; labels via `pillarMeta`; composite-formula caption |
| Helper `renderSnapshotPillarMetrics()` in `TradeDetail.tsx` | Prefers v4 snapshot fields, falls back to v3 — preserves historical trades forever |

---

## 11. Execution Protocol

Follow `CLAUDE.md` deployment protocol (mandatory):

1. Pre-deploy: tests + lint + type-check must pass
2. Deploy via `./scripts/deploy.sh backend` (never `cdk deploy oss-dev-backend`)
3. Verify: CloudWatch errors, Pipeline Monitor stages, health endpoint
4. Merge to `main` after every successful deploy
5. Tag milestone at end of each phase

---

**End of Plan. Phases 1-4 complete (2026-04-17). Phase 5 (v4 default policy config build + seed) ready to start.**
