# v5 Archetype Catalog

**As of:** 2026-04-20 v4.1.1 policy (12 HR + 10 P + 3 anti)

The catalog source of truth lives in code:

- `backend/app/v5/hr_archetypes.py::default_v5_hr_archetypes()` — 12 HR patterns
- `backend/app/v5/p_archetypes.py::default_v5_p_archetypes()` — 10 P patterns
- `backend/app/archetypes/defaults.py::default_anti_archetypes()` — 3 anti-archetypes (v4.1.0-era, still active)

This doc is a snapshot + commentary. Rates will drift — always pull live
values via `backend/scripts/seed_v5_archetype_rates.py` for current numbers.

## HR archetypes (12) — hunting ≥200% MFE grand slams

### Carried forward from v4.1.0 (6)

| ID | Conditions | Historical n | HR200 Wilson lower |
|---|---|---|---|
| **UV_LOTTERY_CALL** | UV × DTE 14-21 × \|delta\|<0.25 × CALL | 136 | **14.02%** |
| UV_REVERSAL_PUT | UV × PUT × TS≥75 × RS_AGAINST | 192 | 6.84% |
| UV_STRUCTURAL | UV × DTE 14-21 × TS≥75 | 369 | 3.32% |
| CHEAP_COMPRESSION | CHEAP × ADX<20 × score 65-78 × ATR 4-6% | 93 | 3.69% |
| CHEAP_VOL_REVERSAL | CHEAP × ATR≥6% × RS_AGAINST × IVRV 1.0-1.3 | 50 | 3.15% |
| CHEAP_ULTRA_CALL | CHEAP × DTE<14 × CALL × IVP<30 | 33 | 3.14% |

### New in v5.0.0 (6, discovered on 18,567-trade dataset)

| ID | Conditions | Historical n | HR200 Wilson lower |
|---|---|---|---|
| **UV_LOTTERY_DC_MID** | UV × DTE 14-21 × \|delta\|<0.25 × DC 40-60 | 41 | **15.69%** (strongest new) |
| UV_LOTTERY_IVP_LO | UV × DTE 14-21 × \|delta\|<0.25 × IVP<30 | 84 | 13.04% |
| UV_LOTTERY_IVRV_CHEAP | UV × DTE 14-21 × \|delta\|<0.25 × IVRV<1.0 | 116 | 11.45% |
| CHEAP_ULTRA_MP_HIGH | CHEAP × DTE<14 × MP 60-75 × CALL | 38 | 4.17% |
| CHEAP_SHORT_FAIR_CONTRARIAN | CHEAP × DTE 14-21 × IVRV 1.0-1.3 × RS_AGAINST | 48 | 3.29% |
| CHEAP_ULTRA_TS_MID | CHEAP × DTE<14 × TS 60-75 × CALL | 65 | 2.42% |

**How HR conviction maps to score:** `100 × Wilson_lower × fit × regime`.
Perfect match on UV_LOTTERY_DC_MID at neutral regime: 100 × 0.1569 × 1.0 × 1.0 ≈ **15.7** (TIER_1 Sharpshooter).
Same archetype at bullish-calm regime (×1.3): 100 × 0.1569 × 1.0 × 1.3 ≈ **20.4** → clamps to ~20.

## P archetypes (10) — hunting consistent profitable trades

### Whole-scanner grinders

| ID | Conditions | n | Win Wilson lower | Mean P&L |
|---|---|---|---|---|
| **BREAKDOWN_GRINDER** | scanner=BREAKDOWN | 232 | 59.63% | +29.57% |
| **REVALIDATION_QUALITY** | scanner=REVALIDATION | 112 | 60.59% | +47.52% |

### REVALIDATION refinements

> REVALIDATION is a synthetic re-evaluation pass, not a primary scanner —
> each matched trade is a recent APPROVE being re-scored against current
> prices and Greeks. `scanner_metrics.originating_scanner` on the
> opportunity records the real upstream scanner. UI label is
> "Re-evaluation".

| ID | Conditions | n | Win Wilson lower | Mean P&L |
|---|---|---|---|---|
| REVALIDATION_LOW_MP | REVAL × MP<40 | 41 | 71.56% | +71.11% |
| REVALIDATION_IVP_LO_CALL | REVAL × IVP<30 × CALL | 52 | 65.97% | +83.10% |

### UV profit-refinements

| ID | Conditions | n | Win Wilson lower | Mean P&L |
|---|---|---|---|---|
| UV_VOLATILE_COMPRESSION | UV × ADX<20 × ATR≥6% | 516 | 64.87% | +51.43% |
| UV_VOLATILE_CALL | UV × ATR≥6% × CALL | 694 | 59.17% | +38.62% |
| UV_DEEP_OTM_VOLATILE | UV × \|delta\|<0.25 × ATR≥6% | 230 | 55.75% | +55.46% |

### CHEAP profit-refinements

| ID | Conditions | n | Win Wilson lower | Mean P&L |
|---|---|---|---|---|
| CHEAP_CONTRARIAN_CHEAP_VOL | CHEAP × IVRV<1.0 × MP<40 × RS_WITH | 109 | 72.34% | +50.22% |
| CHEAP_VOLATILE_CALL | CHEAP × ADX<20 × ATR≥6% × CALL | 140 | 63.45% | +66.48% |

### Provisional (watch closely)

| ID | Conditions | n | Win Wilson lower | Mean P&L |
|---|---|---|---|---|
| BREAKOUT_CLEAN_ATR_MIDHI | BREAKOUT × IVP<30 × ATR 4-6% | 127 | **97.06%** | +64.92% |

The 97% win rate is anomalous — flagged as provisional. The weekly discovery
script will watch this one. Retire criterion: rolling realized win rate below
80% for 4 consecutive weeks.

**How P conviction maps to score:** `100 × Wilson_lower(P_win) × normalize_pnl(mean_pnl) × fit × regime`,
where `normalize_pnl` maps [-50%, +50%] mean P&L into [0, 2]. Perfect match
on REVALIDATION_QUALITY at neutral regime:
100 × 0.6059 × 1.95 × 1.0 × 1.0 ≈ **118 → clamps to 100** (TIER_2 Quality).

## Anti-archetypes (3) — hard rejects

Still active from v4.1.0. Fire before scoring regardless of v5 state.

| ID | Conditions | n | Historical HR200 | Mean P&L | Win rate |
|---|---|---|---|---|---|
| BREAKOUT_MP_ELITE | BREAKOUT × MP ≥ 75 | 321 | **0.00%** | −57.52% | **0%** |
| UV_LONG_DATED | UV × DTE ≥ 45 | 2,241 | 0.62% | −9.25% | 37.4% |
| CHEAP_DC_ELITE | CHEAP × DC ≥ 75 | 2,315 | 0.09% | −4.96% | 41.4% |

The BREAKOUT×MP_ELITE result (321 trades, 0% win rate) is the single cleanest
empirical signal in the dataset — any matching trade is force-rejected.

## Scanner-to-archetype coverage

| Scanner | v5 active? | HR archetypes | P archetypes |
|---|---|---|---|
| UNUSUAL_VOLUME | ✓ | 9 (all UV_*) | 3 (UV_VOLATILE_*, UV_DEEP_OTM_VOLATILE) |
| CHEAP_OPTIONS | ✓ | 6 (all CHEAP_*) | 2 (CHEAP_CONTRARIAN_CHEAP_VOL, CHEAP_VOLATILE_CALL) |
| BREAKDOWN | ✓ | 0 | 1 (BREAKDOWN_GRINDER) |
| REVALIDATION | ✓ | 0 | 3 (REVAL_*) |
| BREAKOUT | **✗ (v4.1.0 fallback)** | 0 | 1 (BREAKOUT_CLEAN_ATR_MIDHI, provisional) |
| COMPRESSION_EXPANSION | **✗ (v4.1.0 fallback)** | 0 | 0 |

BREAKOUT trades with `MP_ELITE ≥ 75` will hit the anti-archetype and reject.
Other BREAKOUT trades go through the v4.1.0 composite path.

## Adding a new archetype

**Discovery path (recommended):**

1. Run weekly discovery: `backend/scripts/v5_weekly_discovery.py`
2. Review top candidates in the markdown report
3. If a candidate has Wilson lower ≥ 2.5× scanner baseline, n ≥ 30, clean
   P&L, and survives 2 weekly runs in a row → write it into
   `hr_archetypes.py` / `p_archetypes.py`
4. Build + activate a new policy:
   `backend/scripts/build_and_activate_v5_policy.py --create --activate`

**Manual path:**

Edit `hr_archetypes.py` or `p_archetypes.py` directly. Each archetype needs
feature conditions + historical_n + historical_hr200_rate + historical_win_rate
+ historical_mean_pnl_pct. Use `_default_strict_match` semantics (feature value
must fall in the condition range). Feather is handled at runtime by the
matcher; the seed rates don't use it.

## Retiring an archetype

1. Weekly discovery flags it with drift_severity="retire"
2. Confirm: realized rate has been below historical Wilson lower for 4+ weeks
3. Remove from `hr_archetypes.py` / `p_archetypes.py`
4. Build + activate a new policy

The removed archetype's ID stays in paper-position history (records reference
it by string). Don't rename, don't renumber — just delete from the
`default_v5_*_archetypes()` return list.
