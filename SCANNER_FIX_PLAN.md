# Scanner Fix Plan: Restoring 3 Non-Functional Scanners

**Date:** 2026-02-16
**Baseline commit:** `e273a81` (main, CI green)
**Theme:** DON'T BREAK THE PIPELINE THAT'S ALREADY WORKING

---

## Current State Summary

| Scanner | Status | Root Cause | Confidence in Fix |
|---------|--------|------------|-------------------|
| **Breakout** | Working | N/A — producing all current APPROVEs | N/A |
| **Cheap Options** | 100% error rate | API returns empty; exact cause narrowed to 2 possibilities | **85%** |
| **Compression** | Barely triggers | Thresholds too strict; 66 triggers across 1,583 runs | **80%** |
| **Unusual Volume** | Running but 100% filtered | `underlying_price=0` from Polygon Basic tier; handoff rejects all | **85%** |

**Production data (last 7 days):**
- 1,000 pipeline runs, 54,167 evaluations, 864 APPROVEs (100% from Breakout/Breakdown)
- Cheap Options: 0 triggers, ~129,000 errors (every ticker, every run)
- Compression: 66 triggers total (< 0.05% trigger rate)
- Unusual Volume: 201,729 candidates found by UV Lambda, **0 handed off** (all filtered as `PRICE_TOO_LOW`)

---

## Verified Facts (from direct investigation)

### Polygon API Tier (RESOLVED — was Open Question #1)

Direct API testing on 2026-02-16 confirmed:

| Capability | Available on Our Tier? |
|---|---|
| `/v3/snapshot/options/{ticker}` endpoint | **YES** — returns data |
| Server-side DTE filters (`expiration_date.gte/lte`) | **YES** — filters work correctly |
| Server-side strike filters (`strike_price.gte/lte`) | **YES** — filters work correctly |
| `implied_volatility` field | **YES** — present on ~72% of contracts |
| `greeks` (delta, gamma, theta, vega) | **YES** — present on ~72% of contracts |
| `day` data (OHLCV, vwap) | **YES** — present for contracts that traded |
| `open_interest` | **YES** — present |
| `last_quote` (bid/ask) | **NO** — empty on 100% of contracts |
| `underlying_asset.price` | **NO** — empty |

**Key result:** AAPL with the exact scanner filters (30-45 DTE, ATM ±10%) returned **54 contracts** with IV data. The endpoint works. The tier is sufficient for Cheap Options scanning (which only needs IV, not bid/ask).

### UV Pipeline (RESOLVED — was Open Questions #2, #3, #4)

| Question | Answer |
|---|---|
| UV candidate freshness | Runs every ~15 minutes; latest scans are minutes old |
| UV candidate volume per scan | ~1,750 candidates per scan across 498 tickers |
| UV candidates data format | Full contract-level records with `underlying_ticker`, `strike`, `expiration_date`, `dte`, `today_volume`, `avg_volume_20d`, `volume_ratio`, `oi_change_pct`, `trigger_reasons`, `priority_score`, `iv`, `delta`, `gamma`, `theta`, `vega` |
| Why 0 handoffs | `underlying_price=0` (Polygon Basic doesn't include `underlying_asset.price`) → handoff filter rejects as `PRICE_TOO_LOW` |
| UV handoff creates evaluations? | Would create them if candidates passed filtering, but none ever have |
| UV runs Stages 3-8? | **NO** — UV pipeline only runs Stages 1-2. Stages 3-8 integration is needed separately |

---

## Scanner Deep Dives

### Scanner 1: Cheap Options (BROKEN — 100% failure rate)

**What it does:** Finds underlyings where options are cheap relative to realized volatility. Triggers when IV/RV ratio ≤ 1.10 OR IV percentile ≤ 40th percentile.

**How it works:**
1. Calculate 20-day Realized Volatility (RV20) from daily bars (cached in Phase 1)
2. Fetch options chain to calculate IV proxy (average of ATM call/put IV)
3. Compare IV/RV ratio and IV percentile against thresholds

**Where it fails:** Step 2. The orchestrator (`orchestrator.py:1060-1066`) calls `polygon.get_options_chain_minimal()` with 30-45 DTE and ATM ±10% strike filters. It returns empty for all 70 non-Breakout tickers, every run.

**What we now know:**
- The Polygon endpoint works (54 results for AAPL with identical filters)
- IV data is available on our tier
- The failure is NOT a tier limitation

**Remaining uncertainty — two possible causes:**

1. **Rate limiting (most likely):** Phase 1 makes ~60 grouped API calls. Then Phase 3 fires 70 snapshot calls through a semaphore of 20 (so up to 20 simultaneous). Polygon Basic tier may have a per-minute rate limit that Phase 1 exhausts, causing Phase 3 to get throttled. The `_rate_limited_request` method catches HTTP errors silently and returns `None`, which becomes `[]` (empty list). **There are no HTTP error logs in CloudWatch for this path** — but that's consistent with the error being swallowed.

2. **Ticker-specific data sparsity (less likely):** The 70 non-Breakout tickers may have fewer options in the narrow 30-45 DTE window. But all are S&P 500 constituents and should have standard monthly expirations.

**Investigation plan to resolve the remaining 15% uncertainty:**

Deploy a zero-risk diagnostic change: add logging to `get_options_chain_minimal()` that captures the HTTP response status code and result count BEFORE any error swallowing. This tells us in one pipeline run whether the API is returning 429 (rate limit), 200 with empty results, or something else.

```python
# In polygon.py get_options_chain_minimal(), before the existing response check:
logger.info(
    f"[MINIMAL] {underlying_ticker}: status={response.status_code if response else 'None'}, "
    f"results={len(data.get('results', []))} contracts"
)
```

This change:
- Touches only the Phase 3 code path (Breakout runs in Phase 2, completely separate)
- Is purely additive logging (no behavior change)
- Will definitively answer whether it's rate limiting vs. genuinely empty responses

**Fix approach (regardless of which cause):**
- Widen the server-side DTE range from 30-45 to **7-120 days** (captures more expirations, reduces chance of empty results)
- Remove server-side strike price filters (let the scanner filter ATM contracts programmatically)
- Apply the 30-45 DTE + ATM ±10% filtering programmatically in the scanner after the fetch
- If rate limiting is confirmed: add a per-request delay or reduce Phase 3 concurrency from 20 to 5

---

### Scanner 2: Compression (WORKING but rarely triggers)

**What it does:** Detects volatility compression (ATR near minimum) followed by a price break out of the recent range. Classic "coiled spring" pattern.

**How it works:**
1. Calculate ATR(14) series from daily bars
2. Find ATR floor = minimum ATR over prior 20 bars
3. Check compression: ATR today ≤ ATR floor × 1.10
4. Check break: close ≥ prior 10-day high × 1.02 OR close ≤ prior 10-day low × 0.98
5. Trigger requires BOTH compression AND break

**Confirmed facts:**
- Scanner code is correct and executes without errors
- Zero triggers in all recent runs (78 tickers per run, 100% non-trigger rate)
- No per-ticker diagnostic logging exists — we can't see how close tickers come to triggering

**Why it barely triggers:**
- `compression_multiplier=1.10` requires ATR within 10% of its 20-day minimum — mathematically very strict
- `break_pct=2.0` requires a 2% move beyond the 10-day range — significant for compressed (low-ATR) stocks
- Both conditions must be true on the same bar — inherently contradictory since compressed stocks don't make big moves
- This is an extremely rare pattern that may only trigger during specific market regimes

**Note:** The Compression scanner uses daily price bars ONLY. It has no DTE/options filtering. The 30-45 DTE range restriction is a Cheap Options scanner issue, not Compression.

**Remaining uncertainty (20%):**
We don't know which condition is the primary bottleneck (compression check vs. break check), or what threshold values would produce a reasonable trigger rate without generating noise.

**Investigation plan:**
Add near-miss diagnostic logging to the compression scanner:
```python
# After computing metrics, before returning non-triggered result:
compression_ratio = atr_today / compression_threshold  # <1.0 means compressed
break_proximity = min(
    (today_close - break_up_threshold) / break_up_threshold,
    (break_down_threshold - today_close) / break_down_threshold,
)
logger.info(
    f"Compression {ticker}: ratio={compression_ratio:.3f} "
    f"(need ≤1.0), break_prox={break_proximity:.3f} (need ≥0.0)"
)
```

One pipeline run gives us the distribution. Then we pick thresholds with data, not guesses. We can co-deploy this with the Cheap Options diagnostic logging in a single release.

**Fix approach:**
- Tune policy config values (no code changes needed for the actual fix):
  - `compression_multiplier`: 1.10 → value chosen from diagnostic data (likely 1.15-1.25)
  - `break_pct`: 2.0 → value chosen from diagnostic data (likely 1.0-1.5)
- If diagnostics show almost no tickers are even close to compression, consider whether `compression_lookback=20` should be increased (longer lookback = more likely to find a low ATR floor)

---

### Scanner 3: Unusual Volume (RUNNING but 100% filtered)

**What it does:** Detects unusual options trading activity — volume spikes, open interest changes, and combinations that suggest informed trading.

**Architecture:**
```
Publisher (EventBridge) → SNS → Worker (per ticker) → DynamoDB Streams → Handoff → Aggregator
```

Five Lambda functions: `oss-dev-uv-publisher`, `oss-dev-uv-worker`, `oss-dev-uv-handoff`, `oss-dev-uv-aggregator`, `oss-dev-uv-nightly-stats`.

**What's actually happening:**
1. Publisher runs every 15 minutes, sends 498 tickers
2. Worker fetches full options chain from Polygon, identifies ~1,750 UV candidates per scan
3. Worker writes candidates to `oss-dev-uv-candidates` table (201,729+ records)
4. **Worker writes `underlying_price=0`, `bid=0`, `ask=0` for every candidate** (because Polygon Basic tier returns empty `underlying_asset.price` and `last_quote`)
5. Handoff Lambda fires on DynamoDB Streams, checks `underlying_price < $5.00` → **0 < 5 → FILTERED as `PRICE_TOO_LOW`**
6. All 201,729+ candidates are filtered. Zero ever reach evaluation.

**The fix is clear and specific:**

The UV worker (`lambdas/unusual_volume/worker.py`) extracts prices from missing fields:
```python
"underlying_price": Decimal(str(underlying_asset.get("price", 0)))  # Always 0
"bid": Decimal(str(last_quote.get("bid", 0)))                       # Always 0
"ask": Decimal(str(last_quote.get("ask", 0)))                       # Always 0
```

Fix: Use `day.close` fallback (same pattern as Stage 3's existing fix in `contract_selector.py`):
```python
# underlying_price: use Polygon previous close (1 API call per ticker, cacheable)
# Or pass from publisher which can fetch via grouped daily endpoint

# bid/ask: fall back to day.close with 5% spread estimate
bid = last_quote.get("bid", 0)
ask = last_quote.get("ask", 0)
if bid == 0 and ask == 0:
    close = contract.get("day", {}).get("close", 0)
    if close > 0:
        bid = close * 0.975
        ask = close * 1.025
```

**Remaining uncertainty (15%) — the Stage 3-8 integration:**

After fixing the UV worker, candidates will pass handoff filtering and the UV pipeline will produce UV Opportunities. But per CLAUDE.md, the UV pipeline only runs Stages 1-2. These UV Opportunities need to flow through the main pipeline's Stages 3-8 to produce evaluations with verdicts.

Two architectural approaches:

**Option A: Main pipeline reads UV opportunities at start of run**
- At the beginning of `run_scan()`, query `oss-dev-uv-candidates` (or a new UV opportunities table) for recent UV-sourced opportunities
- Inject them into the main pipeline before Stage 2 (or after Stage 2, since UV handoff already does underlying filtering)
- They flow through Stages 3-8 alongside Breakout/Compression/Cheap Options opportunities

**Option B: Expand UV handoff to run Stages 3-8**
- Modify the UV handoff Lambda to call the main pipeline's Stages 3-8 directly
- This keeps the UV pipeline self-contained but duplicates pipeline logic

**Recommendation: Option A** — cleaner, avoids code duplication, ensures consistent scoring/gating. The main pipeline's Stage 1 merger already handles multiple scanner types. Adding UV as another source is architecturally consistent.

**Specific design for Option A:**
1. UV handoff creates `Opportunity` records with `scanner_type=UNUSUAL_VOLUME` in the main opportunities table
2. Main pipeline's `run_scan()` queries for recent UV opportunities (from the last 30 minutes, to match the pipeline's 15-minute cadence)
3. UV opportunities skip Stage 1 (already discovered) and Stage 2 (already filtered by UV handoff), entering at Stage 3 (Contract Selection)
4. From Stage 3 onward, UV opportunities are treated identically to Breakout/Compression/Cheap Options opportunities

This needs to be designed carefully to avoid:
- Double-processing UV opportunities across pipeline runs (need deduplication or a "claimed" flag)
- Timing issues between UV pipeline and main pipeline
- Overloading the main pipeline with too many UV candidates

---

## Secondary Issue: Directional Pillar Scoring Bias

Even after fixing all three scanners, there's a systematic scoring disadvantage for non-Breakout scanners in the Directional Pillar's Signal Confirmation subscore (`directional.py:152-237`):

| Scanner | Signal Confirmation Score |
|---------|--------------------------|
| Breakout + matching direction | 85 |
| Compression + matching direction | 75 |
| Unusual Volume + matching direction | 65 |
| Cheap Options (always) | 50 |

This contributes ~2.5 points of final score difference between Breakout and Cheap Options (through 20% subscore weight × 35% pillar weight). Not enough alone to prevent APPROVEs, but enough to tip borderline cases.

**Recommendation:** Address this AFTER fixing the three scanners. Once we have data from all four scanners flowing through the pipeline, we can observe whether the scoring bias meaningfully prevents non-Breakout APPROVEs and tune accordingly.

---

## Execution Plan

### Phase 0: Establish Baseline + Deploy Diagnostics ✅ COMPLETE
**Goal:** Tag rollback point and gather missing data with zero-risk diagnostic logging.

- **Step 0.1:** Tagged `pipeline-stable-2026-02-16` ✅
- **Step 0.2:** Deployed diagnostic logging (commit `2893d14`, Lambda v6) ✅
- **Step 0.3:** Diagnostic data not yet available (deployed Sunday evening, no weekday pipeline run yet). Proceeded to Phase 1 since the fix approach works regardless of root cause.

### Phase 1: Fix Cheap Options Scanner ✅ DEPLOYED — awaiting weekday verification
**Goal:** Get the Cheap Options scanner producing triggers and flowing through the full pipeline.

**Deployed:** commit `0fd5804`, Lambda v7, 2026-02-17

**What was changed (4 files):**
- `orchestrator.py`: Phase 3 concurrency 20→5, DTE range 30-45→7-90, removed server-side strike filter
- `polygon.py`: API result limit 250→1000
- `cheap_options.py`: DTE range 30-45→7-90 throughout, removed redundant chain filtering and fallback strike filter
- `utils.py`: `calculate_iv_proxy` defaults widened to 7-90 DTE

**Design decision:** User chose "widen to 7-90 everywhere" over fallback or per-expiration grouping. The full 7-90 DTE range flows from API fetch → cache → scanner → IV proxy calculation with no intermediate narrowing.

**Verification status:**
- CI: ✅ green
- Health endpoint: ✅ healthy
- CloudWatch errors: ✅ none
- Lambda deployed: ✅ version 7, commit confirmed
- **Pipeline run with market data: ⏳ pending (next weekday)**

**What to verify on Monday:**
- CloudWatch `[MINIMAL]` logs show `results=N` (not 0) for each ticker
- Stage 1 scanner stats show `cheap_options` triggers > 0
- No new errors from wider chain data
- Breakout scanner output unchanged

**Rollback:** `./scripts/deploy.sh rollback` → version 6 (diagnostic-only)

### Phase 2: Fix Compression Scanner Thresholds
**Goal:** Increase Compression trigger rate from ~0% to meaningful level.
**Risk:** VERY LOW — policy config changes only, no code changes required.

**Step 2.1: Choose threshold values** (informed by Phase 0 diagnostics)
- Analyze the compression ratio distribution from diagnostic logs
- Pick `compression_multiplier` that captures the tightest 5-10% of tickers (likely 1.15-1.25)
- Pick `break_pct` that captures meaningful breakouts (likely 1.0-1.5)

**Step 2.2: Update policy config**
- Update via Policy API or direct DynamoDB write
- No code deployment needed

**Step 2.3: Verify**
- Monitor next pipeline runs
- Confirm Compression triggers appear in Stage 1
- Confirm triggers flow through Stages 2-8 and produce evaluations

### Phase 3: Fix Unusual Volume Pipeline + Integration
**Goal:** Get UV candidates through handoff AND into the main pipeline's Stages 3-8.
**Risk:** MODERATE — two-part change: UV worker fix (low risk) + main pipeline integration (moderate risk).

**Step 3.1: Fix UV worker price data** (independent, deploy first)
- Add `day.close` fallback for `bid`/`ask` in UV worker
- Add `underlying_price` from Polygon grouped daily endpoint or previous close
- Deploy UV worker Lambda
- Verify: handoff filter passes candidates (check `HANDED_OFF` count > 0 in DynamoDB)

**Step 3.2: Design Stage 3-8 integration**
- UV handoff writes `Opportunity` records with `scanner_type=UNUSUAL_VOLUME` to the main opportunities table
- Main pipeline `run_scan()` queries for recent UV opportunities (within last 30 min)
- UV opportunities enter at Stage 3 (skip redundant Stage 1-2 since UV pipeline already did these)
- Add deduplication to prevent double-processing across pipeline runs

**Step 3.3: Implement and test**
- Write integration in `orchestrator.py`
- Write tests with mocked UV opportunity data
- Run full test suite to verify Breakout flow is unchanged

**Step 3.4: Deploy and verify**
- Full CLAUDE.md deployment protocol
- Verify: UV-sourced evaluations appear in Pipeline Monitor stages 3-8
- Verify: UV-sourced opportunities appear on the Opportunities page with "Unusual Volume" scanner badge
- Verify: Breakout evaluations are unchanged

### Phase 4 (Future): Scoring Fairness Review
**Deferred until:** All four scanners are producing evaluations through the full pipeline with at least one week of production data to analyze.

---

## Key Safety Guardrails

1. **Phase 0 is pure diagnostics.** Only adds log statements. Zero behavior change. Zero risk to Breakout.

2. **Phase 1 is isolated to Phase 3 of the orchestrator.** The code that runs Breakout and Compression (Phase 2) is completely separate from the code that runs Cheap Options (Phase 3). Changes to Phase 3 cannot affect Phase 2.

3. **Phase 2 is config-only.** No code changes. The Compression scanner logic is unchanged; it just evaluates against wider thresholds.

4. **Phase 3 has two sub-steps with independent rollback:**
   - Step 3.1 (UV worker fix) is a separate Lambda deployment. If it causes issues, roll back the UV worker Lambda independently. The main pipeline Lambda is untouched.
   - Step 3.3 (integration) modifies the main pipeline Lambda but only adds a new code path for UV opportunities. Breakout/Compression/Cheap Options paths are unchanged.

5. **Each phase is independently deployable and verifiable.** If Phase 1 succeeds but Phase 3 causes issues, roll back Phase 3 without losing Phase 1's fix.

6. **Rollback plan for each phase:**
   - Phase 0: `./scripts/deploy.sh rollback` (reverts logging; optional, logging is harmless)
   - Phase 1: `./scripts/deploy.sh rollback` (reverts to Phase 0 Lambda)
   - Phase 2: Revert policy thresholds to 1.10 / 2.0 via DynamoDB or Policy API
   - Phase 3 Step 3.1: Roll back UV worker Lambda independently
   - Phase 3 Step 3.3: `./scripts/deploy.sh rollback` (reverts main Lambda to Phase 1 version)

---

## Resolved Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Polygon subscription tier | Basic tier. Snapshot endpoint works. IV + Greeks available. No bid/ask or underlying_asset.price. |
| 2 | UV candidate freshness | Scans run every 15 min. Data is minutes old. |
| 3 | UV candidate volume | ~1,750 per scan (need to assess how many pass handoff after price fix) |
| 4 | Compression tuning target | TBD from Phase 0 diagnostic data (targeting 2-5% trigger rate) |
| 5 | Why Cheap Options returns 0 for all tickers | TBD from Phase 0 diagnostic logs (rate limiting vs. empty responses) |
| 6 | UV integration architecture | Option A: main pipeline reads UV opportunities, processes through Stages 3-8 |

---

## What "Done" Looks Like

After all phases complete, a pipeline run should show:

```
Stage 1 (Opportunity Discovery):
  Breakout:     ~8 triggers  (unchanged from today)
  Compression:  ~2-5 triggers  (up from 0)
  Cheap Options: ~5-15 triggers  (up from 0)
  Unusual Volume: ~10-30 candidates  (up from 0 in main pipeline)

Stage 2-8: All scanner types flowing through with evaluations
  producing APPROVE/WATCH/REJECT verdicts from all 4 scanners

Opportunities page: Mix of scanner badges (Breakout, Compression,
  Cheap Options, Unusual Volume) — not just Breakout
```
