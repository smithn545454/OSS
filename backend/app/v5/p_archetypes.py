"""v5 P-conviction archetype library — 10 patterns hunting consistent profit.

Complement to the HR archetype library (``app.v5.hr_archetypes``). Where
HR archetypes hunt ≥200% MFE outliers, P archetypes hunt reliable
profitability — high win rate × positive mean P&L — including the
"grinder" scanners (BREAKDOWN, REVALIDATION) that systematically score
0 on HR conviction but deliver real money.

Selected from discovery against 18,567 closed paper positions (2026-04-19)
using the filter: n ≥ 30, mean_pnl > +5%, win-rate Wilson lower ≥ 1.3×
scanner baseline. Ranked by ``Wilson_lower(win) × normalized_pnl × log(n)``
and pruned for coverage + diversity across scanners.

Coverage: these 10 patterns collectively match ~20% of the closed-position
dataset, with average per-pattern Wilson lower win rate of 70%+ and mean
P&L of +45%. That's the grinder cohort the HR library misses.
"""

from __future__ import annotations

from app.core.schemas import (
    ArchetypeCondition,
    ArchetypeConfig,
    ArchetypeDefinition,
)


def default_v5_p_archetypes() -> ArchetypeConfig:
    """Return the canonical 10-archetype P-conviction library."""
    return ArchetypeConfig(archetypes=[
        # ====================================================================
        # SECTION A — Whole-scanner grinder archetypes (simplest, highest
        # coverage). Each matches ANY trade from its scanner.
        # ====================================================================

        # 1. BREAKDOWN_GRINDER — the reliable bear grinder. Zero HR200 but
        # 66% win rate, +30% mean. Everything the HR library can't score.
        ArchetypeDefinition(
            archetype_id="BREAKDOWN_GRINDER",
            display_name="Breakdown Grinder",
            description=(
                "The Breakdown scanner as a whole. Zero HR200 historically "
                "but 66% win rate and +30% mean P&L — a reliable small-winner "
                "pattern that HR conviction systematically ignores. Identifies "
                "bearish trades on names that are breaking below support."
            ),
            historical_n=232,
            historical_hr200_rate=0.0,
            historical_win_rate=0.659,
            historical_mean_pnl_pct=29.57,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_breakdown",
                    display_name="Scanner = BREAKDOWN",
                    feature_field="scanner_source",
                    eq="BREAKDOWN",
                ),
            ],
        ),

        # 2. REVALIDATION_QUALITY — the highest-quality scanner by P conviction.
        # 70% win, +47.5% mean, 28% HR100 on small n=112. Pure signal density.
        ArchetypeDefinition(
            archetype_id="REVALIDATION_QUALITY",
            display_name="Revalidation Quality",
            description=(
                "The Revalidation scanner as a whole. 70% win rate, +47.5% "
                "mean P&L on n=112 — the cleanest quality signal in the "
                "dataset. HR200 rate 1.79% (above baseline 1.08%) but not "
                "the main edge — the edge is win-rate consistency."
            ),
            historical_n=112,
            historical_hr200_rate=0.0179,
            historical_win_rate=0.696,
            historical_mean_pnl_pct=47.52,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_revalidation",
                    display_name="Scanner = REVALIDATION",
                    feature_field="scanner_source",
                    eq="REVALIDATION",
                ),
            ],
        ),

        # ====================================================================
        # SECTION B — REVALIDATION refinements (small n but extraordinary
        # win rates and mean P&L)
        # ====================================================================

        # 3. REVALIDATION_LOW_MP — the "low move potential is signal" pattern.
        # REVAL + MP<40 → 86% win, +71% mean on n=41. Counter-intuitive: weak
        # MP pillar in REVAL context correlates with mean-reversion success.
        ArchetypeDefinition(
            archetype_id="REVALIDATION_LOW_MP",
            display_name="Revalidation Low-MP",
            description=(
                "Revalidation trades where MP pillar is <40. Counter-"
                "intuitively signals strong REVAL setups — low MP aligns "
                "with mean-reversion thesis (don't need a big move). "
                "86% win rate, +71% mean P&L on n=41."
            ),
            historical_n=41,
            historical_hr200_rate=0.0,
            historical_win_rate=0.862,
            historical_mean_pnl_pct=71.10,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_revalidation",
                    display_name="Scanner = REVALIDATION",
                    feature_field="scanner_source",
                    eq="REVALIDATION",
                ),
                ArchetypeCondition(
                    condition_id="mp_low",
                    display_name="MP pillar < 40",
                    feature_field="mp_score",
                    lte=40.0,
                    feather=5.0,
                ),
            ],
        ),

        # 4. REVALIDATION_IVP_LO_CALL — REVAL + low-IV call. 79% win, +83% mean.
        # The CALL variant of the REVAL quality play with IV expansion room.
        ArchetypeDefinition(
            archetype_id="REVALIDATION_IVP_LO_CALL",
            display_name="Revalidation Low-IV Call",
            description=(
                "Revalidation CALL on low-IV-percentile underlying. "
                "79% win rate, +83% mean P&L on n=52. Captures IV expansion "
                "on top of directional edge when REVAL signal fires."
            ),
            historical_n=52,
            historical_hr200_rate=0.0385,
            historical_win_rate=0.795,
            historical_mean_pnl_pct=83.10,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_revalidation",
                    display_name="Scanner = REVALIDATION",
                    feature_field="scanner_source",
                    eq="REVALIDATION",
                ),
                ArchetypeCondition(
                    condition_id="ivp_lo",
                    display_name="IV percentile < 30",
                    feature_field="iv_percentile",
                    lte=30.0,
                    feather=5.0,
                ),
                ArchetypeCondition(
                    condition_id="option_call",
                    display_name="Option type = CALL",
                    feature_field="option_type",
                    eq="CALL",
                ),
            ],
        ),

        # ====================================================================
        # SECTION C — UV profit-refinements (subsets of UV with reliable profit
        # signal — large enough cohorts to generalize)
        # ====================================================================

        # 5. UV_VOLATILE_COMPRESSION — UV + compressed ADX + volatile ATR. The
        # "UV on names coiled but volatile" setup. n=516 — biggest P cohort.
        ArchetypeDefinition(
            archetype_id="UV_VOLATILE_COMPRESSION",
            display_name="UV Volatile Compression",
            description=(
                "UV flagged + ADX < 20 (no clear trend) + ATR >= 6% "
                "(volatile baseline). 65% win rate, +51% mean P&L on n=516 "
                "— a large, reliable grinder cohort. Also catches 22 HR200 "
                "winners for ~4.3% HR rate alongside consistent profit."
            ),
            historical_n=516,
            historical_hr200_rate=0.0426,
            historical_win_rate=0.690,
            historical_mean_pnl_pct=51.43,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="adx_low",
                    display_name="ADX(14) < 20",
                    feature_field="adx_14",
                    lte=20.0,
                    feather=2.0,
                ),
                ArchetypeCondition(
                    condition_id="atr_volatile",
                    display_name="ATR% >= 6",
                    feature_field="atr14_pct",
                    gte=6.0,
                    feather=1.0,
                ),
            ],
        ),

        # 6. UV_VOLATILE_CALL — UV + high-ATR CALL. Big cohort (n=694),
        # solid 59% win rate, +39% mean. The "UV grinder on volatile CALLs."
        ArchetypeDefinition(
            archetype_id="UV_VOLATILE_CALL",
            display_name="UV Volatile Call",
            description=(
                "UV flagged + ATR >= 6% + CALL. Largest UV grinder cohort "
                "(n=694). 59% win rate, +39% mean P&L, 26 HR200 winners. "
                "The catch-all for 'UV + volatile + calls' that aren't "
                "captured by the tighter HR lottery archetypes."
            ),
            historical_n=694,
            historical_hr200_rate=0.0375,
            historical_win_rate=0.628,
            historical_mean_pnl_pct=38.62,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="atr_volatile",
                    display_name="ATR% >= 6",
                    feature_field="atr14_pct",
                    gte=6.0,
                    feather=1.0,
                ),
                ArchetypeCondition(
                    condition_id="option_call",
                    display_name="Option type = CALL",
                    feature_field="option_type",
                    eq="CALL",
                ),
            ],
        ),

        # 7. UV_DEEP_OTM_VOLATILE — UV + far-OTM + volatile underlying.
        # n=230 with 58% win, +55% mean. The bridge between UV_LOTTERY_CALL
        # (HR-focused) and consistent-winner territory.
        ArchetypeDefinition(
            archetype_id="UV_DEEP_OTM_VOLATILE",
            display_name="UV Deep-OTM Volatile",
            description=(
                "UV flagged + |delta| < 0.25 + ATR% >= 6. n=230 with 58% "
                "win rate, +55% mean P&L, 18 HR200 (7.8% HR rate). The "
                "broader deep-OTM UV cohort not captured by UV_LOTTERY_CALL "
                "(which adds DTE 14-21 and CALL constraints)."
            ),
            historical_n=230,
            historical_hr200_rate=0.0783,
            historical_win_rate=0.620,
            historical_mean_pnl_pct=55.46,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="deep_otm",
                    display_name="|delta| < 0.25",
                    feature_field="abs_delta",
                    lte=0.25,
                    feather=0.05,
                ),
                ArchetypeCondition(
                    condition_id="atr_volatile",
                    display_name="ATR% >= 6",
                    feature_field="atr14_pct",
                    gte=6.0,
                    feather=1.0,
                ),
            ],
        ),

        # ====================================================================
        # SECTION D — CHEAP profit-refinements (high-win-rate cohorts that
        # don't map to HR archetypes)
        # ====================================================================

        # 8. CHEAP_CONTRARIAN_CHEAP_VOL — CHEAP + IVRV<1.0 + MP<40 + RS_WITH.
        # Counter-intuitive: low-MP trades WITH the trend on cheap vol. 81% win.
        ArchetypeDefinition(
            archetype_id="CHEAP_CONTRARIAN_CHEAP_VOL",
            display_name="Cheap Contrarian on Cheap Vol",
            description=(
                "CHEAP_OPTIONS + IV/RV < 1.0 (vol is cheap) + MP < 40 + "
                "RS with the trend. 81% win rate, +50% mean P&L on n=109. "
                "Counter-intuitive: trades where move-potential pillar "
                "judges the setup weak but price trend agrees — cheap-vol "
                "premium-decay plays that resolve fast."
            ),
            historical_n=109,
            historical_hr200_rate=0.0,
            historical_win_rate=0.807,
            historical_mean_pnl_pct=50.22,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="ivrv_cheap",
                    display_name="IV/RV < 1.0",
                    feature_field="iv_rv_ratio",
                    lte=1.0,
                    feather=0.1,
                ),
                ArchetypeCondition(
                    condition_id="mp_low",
                    display_name="MP pillar < 40",
                    feature_field="mp_score",
                    lte=40.0,
                    feather=5.0,
                ),
                # RS_WITH is the non-contrarian case; rs_contrarian resolves
                # to 0 when RS agrees with direction. Require 0.
                ArchetypeCondition(
                    condition_id="rs_with",
                    display_name="RS with-direction (not contrarian)",
                    feature_field="rs_contrarian",
                    lte=0.0,
                ),
            ],
        ),

        # 9. CHEAP_VOLATILE_CALL — CHEAP + ADX<20 + ATR>=6% + CALL. n=140,
        # 71% win, +66% mean, 7 HR200. Strong dual-axis pattern.
        ArchetypeDefinition(
            archetype_id="CHEAP_VOLATILE_CALL",
            display_name="Cheap Volatile Call",
            description=(
                "CHEAP_OPTIONS + ADX < 20 + ATR% >= 6 + CALL. n=140 with "
                "71% win rate, +66% mean P&L, 7 HR200 winners (5% HR rate). "
                "Strong on both profitability and home-run potential — "
                "represents the 'cheap call on a compressed-but-volatile' "
                "name setup."
            ),
            historical_n=140,
            historical_hr200_rate=0.0500,
            historical_win_rate=0.714,
            historical_mean_pnl_pct=66.48,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="adx_low",
                    display_name="ADX(14) < 20",
                    feature_field="adx_14",
                    lte=20.0,
                    feather=2.0,
                ),
                ArchetypeCondition(
                    condition_id="atr_volatile",
                    display_name="ATR% >= 6",
                    feature_field="atr14_pct",
                    gte=6.0,
                    feather=1.0,
                ),
                ArchetypeCondition(
                    condition_id="option_call",
                    display_name="Option type = CALL",
                    feature_field="option_type",
                    eq="CALL",
                ),
            ],
        ),

        # ====================================================================
        # SECTION E — BREAKOUT refinement (provisional; extraordinary stats
        # but small n pattern — monitor realized rate closely)
        # ====================================================================

        # 10. BREAKOUT_CLEAN_ATR_MIDHI — BREAKOUT + IVP<30 + ATR 4-6%.
        # 97% win rate on n=127 — extraordinary. Worth codifying but flagged
        # as provisional: watch for simulator-exit-rule artifacts.
        # Note: ATR 4-6% is the ATR_HI bucket; values above 6% (ATR_VOL) show
        # much weaker BREAKOUT performance in the data (see n=3 seed mismatch
        # if this archetype used gte=6.0).
        ArchetypeDefinition(
            archetype_id="BREAKOUT_CLEAN_ATR_MIDHI",
            display_name="Breakout Clean-IV Mid-High ATR",
            description=(
                "BREAKOUT + IV percentile < 30 + ATR% in [4, 6]. "
                "Extraordinary 97% win rate, +65% mean P&L on n=127 in "
                "discovery. Flagged as provisional: the uniformity of exit "
                "P&L across subfilters suggests these trades hit profit "
                "targets consistently due to moderate-but-real volatility. "
                "Retire if rolling realized win rate drops below 80% for "
                "4+ consecutive weeks."
            ),
            historical_n=127,
            historical_hr200_rate=0.0157,
            historical_win_rate=1.000,
            historical_mean_pnl_pct=64.92,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_breakout",
                    display_name="Scanner = BREAKOUT",
                    feature_field="scanner_source",
                    eq="BREAKOUT",
                ),
                ArchetypeCondition(
                    condition_id="ivp_lo",
                    display_name="IV percentile < 30",
                    feature_field="iv_percentile",
                    lte=30.0,
                    feather=5.0,
                ),
                ArchetypeCondition(
                    condition_id="atr_midhi",
                    display_name="ATR% in [4, 6]",
                    feature_field="atr14_pct",
                    between=[4.0, 6.0],
                    feather=1.0,
                ),
            ],
        ),
    ])


# Convenience: P-archetype IDs ordered by P-conviction strength (descending)
P_ARCHETYPE_IDS_BY_STRENGTH: list[str] = [
    "BREAKOUT_CLEAN_ATR_MIDHI",        # 97% win (provisional)
    "REVALIDATION_LOW_MP",             # 86% win
    "REVALIDATION_IVP_LO_CALL",        # 79% win
    "CHEAP_CONTRARIAN_CHEAP_VOL",      # 81% win
    "CHEAP_VOLATILE_CALL",             # 71% win
    "UV_VOLATILE_COMPRESSION",         # 65% win (large n)
    "UV_DEEP_OTM_VOLATILE",            # 58% win
    "UV_VOLATILE_CALL",                # 59% win (largest n)
    "REVALIDATION_QUALITY",            # 70% win (whole scanner)
    "BREAKDOWN_GRINDER",               # 66% win (whole scanner)
]
