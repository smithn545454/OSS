"""v4.1.0 default archetypes + anti-archetypes.

Derived from ``home_run_archetypes_findings.md`` (2026-04-18) on
18,567 paper trades. See the plan doc §2 for the source tables.
"""

from __future__ import annotations

from app.core.schemas import (
    AntiArchetypeConfig,
    AntiArchetypeDefinition,
    ArchetypeCondition,
    ArchetypeConfig,
    ArchetypeDefinition,
)


def default_archetypes() -> ArchetypeConfig:
    return ArchetypeConfig(
        archetypes=[
            # A — UV Lottery Call — 20.2% HR200 (18.7× baseline)
            ArchetypeDefinition(
                archetype_id="UV_LOTTERY_CALL",
                display_name="UV Lottery Call",
                description=(
                    "Unusual-volume flagged stock with short-dated (DTE 14-21), "
                    "far-OTM (|delta|<0.25) call. Tight gamma profile turns "
                    "small underlying moves into outsized % gains — classic "
                    "asymmetric lottery ticket."
                ),
                historical_n=114,
                historical_hr200_rate=0.2018,
                historical_win_rate=0.658,
                historical_mean_pnl_pct=84.36,
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_uv",
                        display_name="Scanner = UNUSUAL_VOLUME",
                        feature_field="scanner_source",
                        eq="UNUSUAL_VOLUME",
                    ),
                    ArchetypeCondition(
                        condition_id="dte_14_21",
                        display_name="DTE in [14, 21]",
                        feature_field="dte",
                        between=[14.0, 21.0],
                        feather=3.0,
                    ),
                    ArchetypeCondition(
                        condition_id="low_delta",
                        display_name="|delta| < 0.25",
                        feature_field="abs_delta",
                        lte=0.25,
                        feather=0.05,
                    ),
                    ArchetypeCondition(
                        condition_id="option_call",
                        display_name="Option type = CALL",
                        feature_field="option_type",
                        eq="CALL",
                    ),
                ],
            ),
            # B — UV Structural Explosion — 9.5% HR200
            ArchetypeDefinition(
                archetype_id="UV_STRUCTURAL",
                display_name="UV Structural Explosion",
                description=(
                    "Unusual-volume stock with short-dated option + excellent "
                    "Trade Structure pillar (TS≥75). Contract microstructure "
                    "is clean — low friction lets moves translate to wins."
                ),
                historical_n=274,
                historical_hr200_rate=0.0949,
                historical_win_rate=0.599,
                historical_mean_pnl_pct=50.32,
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_uv",
                        display_name="Scanner = UNUSUAL_VOLUME",
                        feature_field="scanner_source",
                        eq="UNUSUAL_VOLUME",
                    ),
                    ArchetypeCondition(
                        condition_id="dte_14_21",
                        display_name="DTE in [14, 21]",
                        feature_field="dte",
                        between=[14.0, 21.0],
                        feather=3.0,
                    ),
                    ArchetypeCondition(
                        condition_id="ts_high",
                        display_name="TS pillar ≥ 75",
                        feature_field="ts_score",
                        gte=75.0,
                        feather=5.0,
                    ),
                ],
            ),
            # C — UV Reversal Put — 10.5% HR200
            ArchetypeDefinition(
                archetype_id="UV_REVERSAL_PUT",
                display_name="UV Reversal Put",
                description=(
                    "Unusual-volume PUT on a stock that's been rallying "
                    "(RS>0 → contrarian for a put). Captures institutional "
                    "hedging/reversal flow that UV identifies."
                ),
                historical_n=220,
                historical_hr200_rate=0.1045,
                historical_win_rate=0.545,
                historical_mean_pnl_pct=44.19,
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_uv",
                        display_name="Scanner = UNUSUAL_VOLUME",
                        feature_field="scanner_source",
                        eq="UNUSUAL_VOLUME",
                    ),
                    ArchetypeCondition(
                        condition_id="option_put",
                        display_name="Option type = PUT",
                        feature_field="option_type",
                        eq="PUT",
                    ),
                    ArchetypeCondition(
                        condition_id="ts_high",
                        display_name="TS pillar ≥ 75",
                        feature_field="ts_score",
                        gte=75.0,
                        feather=5.0,
                    ),
                    ArchetypeCondition(
                        condition_id="rs_contrarian",
                        display_name="RS direction opposes option (contrarian)",
                        feature_field="rs_contrarian",
                        eq=1.0,
                    ),
                ],
            ),
            # D — Cheap Options Compression Breakout — 7.5% HR200
            ArchetypeDefinition(
                archetype_id="CHEAP_COMPRESSION",
                display_name="Cheap Options Compression Breakout",
                description=(
                    "Cheap-options entry on a coiled (low-ADX) underlying with "
                    "mid-range ATR and elevated move-potential pillar — the "
                    "spring about to uncoil."
                ),
                historical_n=93,
                historical_hr200_rate=0.0753,
                historical_win_rate=0.484,
                historical_mean_pnl_pct=26.99,
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_cheap",
                        display_name="Scanner = CHEAP_OPTIONS",
                        feature_field="scanner_source",
                        eq="CHEAP_OPTIONS",
                    ),
                    ArchetypeCondition(
                        condition_id="adx_low",
                        display_name="ADX < 20",
                        feature_field="adx_14",
                        lte=20.0,
                        feather=3.0,
                    ),
                    ArchetypeCondition(
                        condition_id="atr_mid_high",
                        display_name="ATR% in [4.0, 6.0]",
                        feature_field="atr14_pct",
                        between=[4.0, 6.0],
                        feather=1.0,
                    ),
                    ArchetypeCondition(
                        condition_id="mp_high",
                        display_name="MP pillar ≥ 60",
                        feature_field="mp_score",
                        gte=60.0,
                        feather=5.0,
                    ),
                ],
            ),
            # E — Cheap Options Volatile Reversal — 8.0% HR200
            ArchetypeDefinition(
                archetype_id="CHEAP_VOL_REVERSAL",
                display_name="Cheap Options Volatile Reversal",
                description=(
                    "High-ATR underlying where options are fairly-priced and "
                    "relative strength is against the option's direction — "
                    "catches sharp reversals."
                ),
                historical_n=50,
                historical_hr200_rate=0.0800,
                historical_win_rate=0.740,
                historical_mean_pnl_pct=51.74,
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_cheap",
                        display_name="Scanner = CHEAP_OPTIONS",
                        feature_field="scanner_source",
                        eq="CHEAP_OPTIONS",
                    ),
                    ArchetypeCondition(
                        condition_id="atr_high",
                        display_name="ATR% ≥ 6.0",
                        feature_field="atr14_pct",
                        gte=6.0,
                        feather=1.0,
                    ),
                    ArchetypeCondition(
                        condition_id="ivrv_fair",
                        display_name="IV/RV in [1.0, 1.3] (fairly priced)",
                        feature_field="iv_rv_ratio",
                        between=[1.0, 1.3],
                        feather=0.1,
                    ),
                    ArchetypeCondition(
                        condition_id="rs_contrarian",
                        display_name="RS direction opposes option (contrarian)",
                        feature_field="rs_contrarian",
                        eq=1.0,
                    ),
                ],
            ),
            # F — Cheap Options Ultra-Short Call — 10.8% HR200 (small n, raise threshold)
            ArchetypeDefinition(
                archetype_id="CHEAP_ULTRA_CALL",
                display_name="Cheap Options Ultra-Short Call",
                description=(
                    "Ultra-short-dated (DTE<14) CALL on a low-IV underlying "
                    "flagged by the cheap-options scanner. Huge historical "
                    "hit rate (87%) but tiny sample — treat with caution."
                ),
                historical_n=37,
                historical_hr200_rate=0.1081,
                historical_win_rate=0.865,
                historical_mean_pnl_pct=79.82,
                min_fit_to_match=80.0,
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_cheap",
                        display_name="Scanner = CHEAP_OPTIONS",
                        feature_field="scanner_source",
                        eq="CHEAP_OPTIONS",
                    ),
                    ArchetypeCondition(
                        condition_id="dte_ultra",
                        display_name="DTE < 14",
                        feature_field="dte",
                        lte=14.0,
                        feather=2.0,
                    ),
                    ArchetypeCondition(
                        condition_id="option_call",
                        display_name="Option type = CALL",
                        feature_field="option_type",
                        eq="CALL",
                    ),
                    ArchetypeCondition(
                        condition_id="ivp_low",
                        display_name="IV percentile < 30",
                        feature_field="iv_percentile",
                        lte=30.0,
                        feather=5.0,
                    ),
                ],
            ),
        ]
    )


def default_anti_archetypes() -> AntiArchetypeConfig:
    """Three empirically-validated losing patterns that REJECT on match."""
    return AntiArchetypeConfig(
        anti_archetypes=[
            AntiArchetypeDefinition(
                anti_archetype_id="BREAKOUT_MP_ELITE",
                display_name="BREAKOUT × MP Elite",
                description=(
                    "BREAKOUT scanner trigger combined with MP_score ≥ 75 — "
                    "MP double-counts the momentum already baked into the "
                    "BREAKOUT trigger. 321 historical trades, 0% win rate, "
                    "mean −57.5% P&L."
                ),
                historical_n=321,
                historical_win_rate=0.0,
                historical_mean_pnl_pct=-57.5,
                rejection_reason="ANTI_BREAKOUT_MP_ELITE",
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_breakout",
                        display_name="Scanner = BREAKOUT",
                        feature_field="scanner_source",
                        eq="BREAKOUT",
                    ),
                    ArchetypeCondition(
                        condition_id="mp_elite",
                        display_name="MP pillar ≥ 75",
                        feature_field="mp_score",
                        gte=75.0,
                    ),
                ],
            ),
            AntiArchetypeDefinition(
                anti_archetype_id="UV_LONG_DATED",
                display_name="UV × Long-Dated",
                description=(
                    "UV scanner on DTE ≥ 45 — UV signal decays by the time "
                    "these trades can move. 2,241 trades, 0.6% HR200, "
                    "mean −9.3% P&L."
                ),
                historical_n=2241,
                historical_win_rate=0.37,
                historical_mean_pnl_pct=-9.3,
                rejection_reason="ANTI_UV_LONG_DATED",
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_uv",
                        display_name="Scanner = UNUSUAL_VOLUME",
                        feature_field="scanner_source",
                        eq="UNUSUAL_VOLUME",
                    ),
                    ArchetypeCondition(
                        condition_id="dte_long",
                        display_name="DTE ≥ 45",
                        feature_field="dte",
                        gte=45.0,
                    ),
                ],
            ),
            AntiArchetypeDefinition(
                anti_archetype_id="CHEAP_DC_ELITE",
                display_name="CHEAP × DC Elite",
                description=(
                    "CHEAP_OPTIONS trigger combined with DC_score ≥ 75 — "
                    "double-counts direction already priced into the "
                    "cheap-options setup. 2,315 trades, 0.1% HR200, "
                    "mean −5.0% P&L."
                ),
                historical_n=2315,
                historical_win_rate=0.41,
                historical_mean_pnl_pct=-5.0,
                rejection_reason="ANTI_CHEAP_DC_ELITE",
                conditions=[
                    ArchetypeCondition(
                        condition_id="scanner_cheap",
                        display_name="Scanner = CHEAP_OPTIONS",
                        feature_field="scanner_source",
                        eq="CHEAP_OPTIONS",
                    ),
                    ArchetypeCondition(
                        condition_id="dc_elite",
                        display_name="DC pillar ≥ 75",
                        feature_field="dc_score",
                        gte=75.0,
                    ),
                ],
            ),
        ]
    )
