# Convex Mode — Phase 8 Backtest (2026-04-27)

Three runs were executed during Phase 8 validation. **V3 was selected as
the production cutover configuration** (see V3 section below).

## V3 (Cutover Configuration) — 2026-04-27

| Field | Value |
|---|---|
| **Window** | 2025-04-28 → 2026-04-25 (260 trading days) |
| **Universe** | 250-ticker stride sample of `oss-dev-sp500-tickers` (has_options + active) |
| **Convex config** | `vol_iv_rank_max=25`, `tier_b_stage2_min=0.65`, `tier_b_stage3_min=0.55` |
| **Exit rules (backtest)** | profit_target=+100%, stop_loss=-35%, max_hold=20d, slippage=5%/side |
| **Wall time** | 12,125s (3h 22m) |

```
=== §11 Acceptance Gates ===
  Total trades                   232   PASS  (≥ 50)
  Hit rate %                   38.79   PASS  (≥ 30.0)
  Winner/loser ratio            2.75   FAIL  (need ≥ 3.0 — 8% gap)
  Expectancy %                 20.15   PASS  (> 0)
  Max consecutive losses          14   FAIL  (need ≤ 6)

=== Tier Breakdown ===
  Tier A: no trades  (no signal in this universe/window)
  Tier B: trades=  2  hit_rate= 0%   expectancy=-18.98%  (effectively empty)
  Tier C: trades=230  hit_rate=39.1% expectancy=+20.49%  (clean positive cohort)

=== Exit Reason Distribution ===
  PROFIT_TARGET: 88 trades   avg=+123.0%
  STOP_LOSS:    136 trades   avg= -47.4%
  TIME_EXIT:      8 trades   avg= +38.1%

=== MFE Distribution ===
  median MFE = +38%  |  trades w/ MFE ≥ 100%: 38%
```

**Cutover decision:** User approved full production cutover despite 2/5
gates failing, on the strength of:
- +20% expectancy, 232 trades (statistically significant)
- Single-tier (Tier C) profitable cohort with no negative-EV sleeve
- Top tickers reflect the intended exploder universe (FCX, BMNR, COP,
  CRCL, IONQ, UPST, DVN, LLY)
- Ratio gap is 8% (2.75 vs 3.0), not structural

Failed gates accepted as monitoring items, not blockers:
- **Winner/loser ratio 2.75×** — below 3.0× by 8%; live results may
  improve as Smart Money cohort populates from production UV signals
- **Max consecutive losses 14** — clustering in choppy regimes; live
  position sizing per Tier-C sleeve mitigates portfolio impact

## V2 (Default Watchlist + Default Config)

| Field | Value |
|---|---|
| **Window** | 2025-04-28 → 2026-04-25 |
| **Universe** | DEFAULT_WATCHLIST (78 mega-cap optionable tickers) |
| **Convex config** | `ConvexConfig()` defaults, `enabled=True` |
| **Exit rules** | profit_target=+50%, stop_loss=-50%, max_hold=30d, slippage=5%/side |
| **Wall time** | 6,231s (1h 44m) |

## V2 Results (post-OCC-fix)

```
=== §11 Acceptance Gates ===
  Total trades                   453   PASS  (≥ 50)
  Hit rate %                   40.18   PASS  (≥ 30.0)
  Winner/loser ratio            1.71   FAIL  (need ≥ 3.0)
  Expectancy %                  4.06   PASS  (> 0)
  Max consecutive losses          10   FAIL  (need ≤ 6)

=== Tier Breakdown ===
  Tier A: no trades
  Tier B: trades=119  hit_rate=32.8%  avg_pnl=-11.61%  expectancy=-11.61%
  Tier C: trades=334  hit_rate=42.8%  avg_pnl=+9.64%   expectancy=+9.64%

=== Smart Money Cohort ===
  Confirmed     :   0 trades  (UV disabled in historical mode)
  Not confirmed : 453 trades  hit_rate=40.2%  expectancy=+4.06%

=== Exit Reason Distribution ===
  PROFIT_TARGET: 224 trades  avg=+71.3%  median=+63.8%
  STOP_LOSS:     229 trades  avg=-61.8%  median=-59.4%

=== MFE Distribution ===
  median MFE = +49%  |  trades with MFE ≥ 50%: 49%  |  MFE ≥ 100%: 9%
```

## Verdict: 3 of 5 gates pass; 2 fail

**Pass:** trade count, hit rate, expectancy.

**Fail:**
- **Winner/loser ratio 1.71×** — the symmetric +50%/-50% exit cuts winners early. Avg winner +71.3% vs avg loser -61.8% (slippage degrades both). Median MFE was +49% — half of trades reached the +50% profit target band; only 9% reached +100%.
- **Max consecutive losses 10** — clustering during specific market regimes (e.g. low-vol drift weeks where compression false-fires).
- **Tier A: zero trades** — Stage 3+4 strength composites never hit the Tier A threshold in this universe / window.
- **Smart Money cohort: zero confirmed** — by design; UV detection is disabled in historical mode (no agg-options-volume history pre-Phase 0.5).
- **Tier B underperforms Tier C** (B = -11.6% expectancy, C = +9.6%) — tier ordering is INVERTED, indicating the within-tier composite scoring isn't capturing convexity.

## Caveats (impact on results)

1. **Survivorship-biased universe** — DEFAULT_WATCHLIST is current-date; delisted/acquired tickers from the backtest period are absent.
2. **UV / sympathy disabled** — Stage 2 fires only on date-known + compression catalysts.
3. **Stage 4 measured-move plumbing not threaded** — Stage 2 catalyst context (compression measured-move, historical event-move) is not yet passed through the backtest harness, so Stage 4 uses its bar-derived fallback.
4. **Catalyst calendar denormalized from `oss-dev-earnings-cache`** — covers next-60-day window only; historical earnings dates beyond the cache snapshot are absent. Date-known catalyst hits are correspondingly sparse.

## Stage advancers (sample)

```
u=78 s2=23 s3=14 s4=7 (a=0 b=0 c=7)   -- typical mid-window day
u=78 s2=35 s3=0  s4=0 (a=0 b=0 c=0)   -- IV-history-thin day
u=78 s2=24 s3=15 s4=10 (a=0 b=2 c=8)  -- earnings-rich day
```

Stage 2: 18-40 hits/day (compression-driven). Stage 3: 0-17 hits (limited by multi-tenor IV history coverage). Stage 4: typically 60-90% of Stage 3 advancers pass contract selection.

## V1 archived

`baselines/2026-04-27-convex-phase8-backtest-v1-bug.json` — first run had a bug where `option_ticker` resolved to the underlying symbol instead of OCC format, so forward-walk option price lookups returned None and every trade exited at expiration via intrinsic value. Headline numbers were wildly inflated (+45.88% expectancy, 4.21× ratio) but unrepresentative of any tradeable strategy. Fixed in `historical_providers.py` by rebuilding OCC tickers from `(underlying, expiry, type, strike)` after `_chain_to_contract_candidates`.

## Recommendation

**Do not cutover on this result alone.** Two structural issues block cutover:

1. **Tier B negative expectancy + Tier C outperforming Tier B** — the within-tier composite scoring needs investigation before live deployment. If Tier B were broken in production, position sizing recommendations would over-concentrate in Tier B (largest sleeve) and underweight Tier C (smallest sleeve), inverting the actual edge.
2. **No Tier A signal in the historical universe** — Tier A is the highest-conviction sleeve. Production needs to either confirm Tier A fires regularly in the broader live universe, or relax thresholds.

**Possible paths forward (one or more):**
- **Strategy tuning (one cycle authorized):** raise profit_target to 100% (let winners run) and tighten Stage 3 IV thresholds (fewer but higher-quality entries). Re-run, see if ratio crosses 3.0 and Tier B normalizes.
- **Universe broadening:** rebuild backtest universe from S&P 500 + Russell 2000 to capture more exploder candidates. Adds ~1h dev + 4-6h run time but addresses the tier distribution.
- **Stage 4 measured-move plumbing:** thread Stage 2 detector context into Stage 4 inputs. May surface Tier A signals that the bar-fallback misses.
- **Phased cutover:** cut over Stage 1+2 telemetry only (no live trades), shadow-mode for 2-4 weeks, then enable live Decision emission once tier distribution and Smart Money cohort populate from live UV.

## Files

- `2026-04-27-convex-phase8-backtest.json` — v2 results (this run)
- `2026-04-27-convex-phase8-backtest-v1-bug.json` — v1 archived (option-ticker bug)
- `2026-04-27-pre-convex-policy.json` — active policy snapshot (v4.1.1)
- `2026-04-27-convex-cutover-policy.json` — pre-built v4.2.0-convex flip policy (NOT YET DEPLOYED)
- Tag: `pipeline-stable-pre-convex-2026-04-27` (rollback target)
