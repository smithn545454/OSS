"""v5 HR-conviction archetype library — 12 patterns hunting MFE ≥ 200%.

Composition:
  * 6 archetypes carried forward from v4.1.0 (UV_LOTTERY_CALL, UV_STRUCTURAL,
    UV_REVERSAL_PUT, CHEAP_COMPRESSION, CHEAP_VOL_REVERSAL, CHEAP_ULTRA_CALL)
  * 6 new archetypes discovered by ``backend/scripts/v5_historical_validation.py``
    on the 2026-04-19 closed-position snapshot (18,567 positions, baseline
    HR200 rate 1.08%). Each new archetype was selected for Wilson-lower-bound
    stability ≥ 2× scanner baseline, n ≥ 20, HR200 count ≥ 3.

The seed ``historical_*`` rates here are point-in-time snapshots from the
2026-04-19 analysis. Runtime conviction uses the rolling rate estimator
(:mod:`app.calibration.archetype_rates`) which recomputes Wilson bounds
from the live paper-position table — these seeds are provenance only.

Note on the dropped 13th archetype: the plan called for
UV_STRUCTURAL_HIGH_SCORE (UV × DTE 14-21 × TS≥75 × score 65-78). It was
dropped from Phase 2 because the matcher cannot resolve ``conv_score``
without computing the composite first — a chicken-and-egg situation. Add
in Phase 3+ once we expose a final-score feature on ScoringContext.
"""

from __future__ import annotations

from app.core.schemas import (
    ArchetypeCondition,
    ArchetypeConfig,
    ArchetypeDefinition,
)


def default_v5_hr_archetypes() -> ArchetypeConfig:
    """Return the canonical 12-archetype HR-conviction library."""
    return ArchetypeConfig(archetypes=[
        # ====================================================================
        # SECTION A — Carried forward from v4.1.0 (rates re-measured 2026-04-19)
        # ====================================================================

        # 1. UV_LOTTERY_CALL — the workhorse. Wilson lower 14.02% (n=136).
        ArchetypeDefinition(
            archetype_id="UV_LOTTERY_CALL",
            display_name="UV Lottery Call",
            description=(
                "Unusual-volume flagged stock with short-dated (DTE 14-21), "
                "far-OTM (|delta|<0.25) call. Tight gamma profile turns "
                "small underlying moves into outsized % gains — the classic "
                "asymmetric lottery ticket."
            ),
            historical_n=136,
            historical_hr200_rate=0.1985,
            historical_win_rate=0.654,
            historical_mean_pnl_pct=82.00,
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

        # 2. UV_REVERSAL_PUT — contrarian put on a rallying name. WL 6.84% (n=192).
        ArchetypeDefinition(
            archetype_id="UV_REVERSAL_PUT",
            display_name="UV Reversal Put",
            description=(
                "Unusual-volume PUT on a stock that's been rallying "
                "(RS_AGAINST = contrarian for a put). Captures institutional "
                "hedging/reversal flow that UV identifies. Requires clean "
                "structure (TS≥75)."
            ),
            historical_n=192,
            historical_hr200_rate=0.1042,
            historical_win_rate=0.562,
            historical_mean_pnl_pct=45.31,
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
                    display_name="Relative strength contrarian (against position)",
                    feature_field="rs_contrarian",
                    gte=1.0,
                ),
            ],
        ),

        # 3. UV_STRUCTURAL — broad UV+TS pattern. Weakest of the carried-over
        # archetypes (WL 3.32%); kept for now, will retire if rolling rate stays low.
        ArchetypeDefinition(
            archetype_id="UV_STRUCTURAL",
            display_name="UV Structural Explosion",
            description=(
                "Unusual-volume stock with short-dated option + excellent "
                "Trade Structure pillar (TS≥75). Contract microstructure "
                "is clean — low friction lets moves translate to wins. "
                "Note: weakest of the UV archetypes; retire if rolling "
                "realized HR200 falls below 3% Wilson lower for 4+ weeks."
            ),
            historical_n=369,
            historical_hr200_rate=0.0515,
            historical_win_rate=0.442,
            historical_mean_pnl_pct=14.34,
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

        # 4. CHEAP_COMPRESSION — coiled spring on cheap options. WL 3.69% (n=93).
        ArchetypeDefinition(
            archetype_id="CHEAP_COMPRESSION",
            display_name="Cheap Compression Breakout",
            description=(
                "Cheap-options scanner + low ADX (price ranging/coiling) + "
                "high MP pillar (big move probable) + ATR 4-6% (volatile but "
                "not chaotic). The spring-about-to-uncoil setup."
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
                    display_name="ADX(14) < 20 (compression)",
                    feature_field="adx_14",
                    lte=20.0,
                    feather=2.0,
                ),
                ArchetypeCondition(
                    condition_id="mp_high",
                    display_name="MP pillar 60-75",
                    feature_field="mp_score",
                    between=[60.0, 75.0],
                    feather=5.0,
                ),
                ArchetypeCondition(
                    condition_id="atr_mid_high",
                    display_name="ATR% in [4, 6]",
                    feature_field="atr14_pct",
                    between=[4.0, 6.0],
                    feather=1.0,
                ),
            ],
        ),

        # 5. CHEAP_VOL_REVERSAL — fairly priced contrarian on volatile name. WL 3.15% (n=50).
        ArchetypeDefinition(
            archetype_id="CHEAP_VOL_REVERSAL",
            display_name="Cheap Vol Reversal",
            description=(
                "Cheap options on a high-ATR (volatile) underlying, "
                "contrarian relative strength, fairly-priced IV/RV (1.0-1.3). "
                "Catches sharp reversals on volatile names."
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
                    display_name="ATR% ≥ 6",
                    feature_field="atr14_pct",
                    gte=6.0,
                    feather=1.0,
                ),
                ArchetypeCondition(
                    condition_id="rs_contrarian",
                    display_name="RS contrarian",
                    feature_field="rs_contrarian",
                    gte=1.0,
                ),
                ArchetypeCondition(
                    condition_id="ivrv_fair",
                    display_name="IV/RV in [1.0, 1.3]",
                    feature_field="iv_rv_ratio",
                    between=[1.0, 1.3],
                    feather=0.1,
                ),
            ],
        ),

        # 6. CHEAP_ULTRA_CALL — short DTE + cheap call on low-IV name. WL 3.14% (n=33, small).
        ArchetypeDefinition(
            archetype_id="CHEAP_ULTRA_CALL",
            display_name="Cheap Ultra Call",
            description=(
                "Ultra-short DTE (<14) cheap call on low-IV underlying. "
                "Convexity when underlying moves into ITM territory. "
                "Sample is small (n=33) — Wilson lower is conservative."
            ),
            historical_n=33,
            historical_hr200_rate=0.0909,
            historical_win_rate=0.848,
            historical_mean_pnl_pct=76.13,
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

        # ====================================================================
        # SECTION B — Newly discovered (Wilson-lower ranked) — 2026-04-19
        # ====================================================================

        # 7. UV_LOTTERY_DC_MID — STRONGEST new pattern. WL 18.00% (n=36).
        # Subset of UV_LOTTERY_CALL with DC pillar in 40-60 band.
        ArchetypeDefinition(
            archetype_id="UV_LOTTERY_DC_MID",
            display_name="UV Lottery (DC Mid-Band)",
            description=(
                "UV lottery setup (DTE 14-21, |delta|<0.25) where DC pillar "
                "lands in 40-60 — neither weak nor elite. The 'sleeper' band "
                "where the underlying isn't obviously trending but UV flow "
                "signals smart-money positioning. Strongest discovered new "
                "pattern: 30.56% point HR200, Wilson lower 18.00%."
            ),
            historical_n=36,
            historical_hr200_rate=0.3056,
            historical_win_rate=0.639,
            historical_mean_pnl_pct=96.22,
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
                    condition_id="dc_mid",
                    display_name="DC pillar in [40, 60]",
                    feature_field="dc_score",
                    between=[40.0, 60.0],
                    feather=5.0,
                ),
            ],
        ),

        # 8. UV_LOTTERY_IVRV_CHEAP — UV lottery with under-priced IV. WL 13.05% (n=96).
        ArchetypeDefinition(
            archetype_id="UV_LOTTERY_IVRV_CHEAP",
            display_name="UV Lottery (IV/RV Cheap)",
            description=(
                "UV lottery setup with IV/RV < 1.0 — option is under-priced "
                "vs realized vol. Adds a vol-edge layer to the gamma play. "
                "Wilson lower 13.05% on n=96."
            ),
            historical_n=96,
            historical_hr200_rate=0.1979,
            historical_win_rate=0.615,
            historical_mean_pnl_pct=80.82,
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
                    condition_id="ivrv_cheap",
                    display_name="IV/RV < 1.0 (cheap vol)",
                    feature_field="iv_rv_ratio",
                    lte=1.0,
                    feather=0.1,
                ),
            ],
        ),

        # 9. UV_LOTTERY_IVP_LO — UV lottery with low IV percentile. WL 12.34% (n=76).
        ArchetypeDefinition(
            archetype_id="UV_LOTTERY_IVP_LO",
            display_name="UV Lottery (IV Pctl Low)",
            description=(
                "UV lottery setup with IV percentile < 30 — IV in the low end "
                "of its 252-day range, leaving room for IV expansion if the "
                "move triggers. Wilson lower 12.34% on n=76."
            ),
            historical_n=76,
            historical_hr200_rate=0.1974,
            historical_win_rate=0.671,
            historical_mean_pnl_pct=88.95,
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
                    condition_id="ivp_lo",
                    display_name="IV percentile < 30",
                    feature_field="iv_percentile",
                    lte=30.0,
                    feather=5.0,
                ),
            ],
        ),

        # 10. CHEAP_ULTRA_MP_HIGH — small but extraordinary. 95.7% win rate. WL 6.98% (n=23).
        ArchetypeDefinition(
            archetype_id="CHEAP_ULTRA_MP_HIGH",
            display_name="Cheap Ultra MP-High Call",
            description=(
                "Ultra-short DTE (<14) cheap call where MP pillar is 60-75. "
                "Small sample (n=23) but extraordinary 95.7% win rate, "
                "+97.5% mean P&L. Wilson lower 6.98% reflects sample-size "
                "humility — treat as provisional until n grows."
            ),
            historical_n=23,
            historical_hr200_rate=0.1739,
            historical_win_rate=0.957,
            historical_mean_pnl_pct=97.50,
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
                    condition_id="mp_high",
                    display_name="MP pillar 60-75",
                    feature_field="mp_score",
                    between=[60.0, 75.0],
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

        # 11. CHEAP_SHORT_FAIR_CONTRARIAN — fairly-priced contrarian short DTE. WL 4.67% (n=34).
        ArchetypeDefinition(
            archetype_id="CHEAP_SHORT_FAIR_CONTRARIAN",
            display_name="Cheap Short-DTE Fair Contrarian",
            description=(
                "Cheap-options scanner + DTE 14-21 + IV/RV in fair-value "
                "band (1.0-1.3) + RS contrarian. Captures short-dated mean "
                "reversion plays at fair vol. Wilson lower 4.67% on n=34."
            ),
            historical_n=34,
            historical_hr200_rate=0.1176,
            historical_win_rate=0.618,
            historical_mean_pnl_pct=55.60,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="dte_short",
                    display_name="DTE in [14, 21]",
                    feature_field="dte",
                    between=[14.0, 21.0],
                    feather=3.0,
                ),
                ArchetypeCondition(
                    condition_id="ivrv_fair",
                    display_name="IV/RV in [1.0, 1.3]",
                    feature_field="iv_rv_ratio",
                    between=[1.0, 1.3],
                    feather=0.1,
                ),
                ArchetypeCondition(
                    condition_id="rs_contrarian",
                    display_name="RS contrarian",
                    feature_field="rs_contrarian",
                    gte=1.0,
                ),
            ],
        ),

        # 12. CHEAP_ULTRA_TS_MID — ultra-short cheap call with mid-band TS. WL 4.29% (n=37).
        ArchetypeDefinition(
            archetype_id="CHEAP_ULTRA_TS_MID",
            display_name="Cheap Ultra TS-Mid Call",
            description=(
                "Ultra-short DTE (<14) cheap call where TS pillar is 60-75 "
                "(decent but not elite microstructure). 81.1% win rate, "
                "+71.2% mean P&L on n=37. Wilson lower 4.29%."
            ),
            historical_n=37,
            historical_hr200_rate=0.1081,
            historical_win_rate=0.811,
            historical_mean_pnl_pct=71.18,
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
                    condition_id="ts_mid",
                    display_name="TS pillar 60-75",
                    feature_field="ts_score",
                    between=[60.0, 75.0],
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
    ])


# Convenience: list of archetype IDs in canonical order (HR-rate descending)
HR_ARCHETYPE_IDS_BY_STRENGTH: list[str] = [
    "UV_LOTTERY_DC_MID",        # WL 18.00%
    "UV_LOTTERY_CALL",          # WL 14.02%
    "UV_LOTTERY_IVRV_CHEAP",    # WL 13.05%
    "UV_LOTTERY_IVP_LO",        # WL 12.34%
    "UV_REVERSAL_PUT",          # WL 6.84%
    "CHEAP_ULTRA_MP_HIGH",      # WL 6.98%
    "CHEAP_SHORT_FAIR_CONTRARIAN",  # WL 4.67%
    "CHEAP_ULTRA_TS_MID",       # WL 4.29%
    "CHEAP_COMPRESSION",        # WL 3.69%
    "UV_STRUCTURAL",            # WL 3.32%
    "CHEAP_VOL_REVERSAL",       # WL 3.15%
    "CHEAP_ULTRA_CALL",         # WL 3.14%
]
