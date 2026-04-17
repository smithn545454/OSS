#!/usr/bin/env python3
"""Build default Policy v4.0.0 JSON — the v4 Sharpshooter regime seed.

Unlike v3, which was derived from a 15,505-position backtest analysis,
the v4 regime uses **design-reasoned breakpoints**: each subscore curve
is author-designed to match the Sharpshooter thesis (see Section 4.1 of
`docs/pillar_v4_execution_plan.md`). Breakpoints are intentionally coarse
in this seed — Phase 8 observation + tuning on live v4 pipeline output
calibrates them via the Policy page.

Three pillars, weighted geometric mean composite:
- DIRECTIONAL_CONVICTION (0.40 exponent) — 6 subscores
- MOVE_POTENTIAL (0.35 exponent) — 5 subscores
- TRADE_STRUCTURE (0.25 exponent) — 5 subscores

Output: backend/scripts/output/v4_default_policy.json

Use the companion script `seed_policy_v4.py` to write the resulting
config to DynamoDB as an inactive policy version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.schemas import (
    GateConfig,
    NumericSubscoreConfig,
    PillarConfig,
    PillarConfigV2,
    PillarId,
    PillarWeights,
    PolicyConfig,
    SubscoreBreakpoint,
)

OUTPUT_DIR = Path(__file__).parent / "output"
POLICY_PATH = OUTPUT_DIR / "v4_default_policy.json"

# Scanner keys for per-scanner weight overrides. These match the values
# that `PillarConfig.get_weights()` normalizes from Evaluation.scanner_source
# (uppercase with trailing "_SCANNER" stripped).
SCANNER_KEYS = ["BREAKOUT", "BREAKDOWN", "COMPRESSION_EXPANSION", "UNUSUAL_VOLUME", "CHEAP_OPTIONS"]


def _bp(value: float, score: float) -> SubscoreBreakpoint:
    return SubscoreBreakpoint(value=value, score=score)


def _num(
    subscore_id: str,
    display_name: str,
    feature_field: str,
    weight: float,
    breakpoints: list[SubscoreBreakpoint],
    source_tier: str = "tier1",
) -> NumericSubscoreConfig:
    return NumericSubscoreConfig(
        subscore_id=subscore_id,
        display_name=display_name,
        feature_field=feature_field,
        weight=weight,
        breakpoints=breakpoints,
        source_tier=source_tier,
    )


# ============================================================================
# Directional Conviction pillar (0.40 exponent; 6 subscores)
# ============================================================================


def _directional_conviction_pillar() -> PillarConfigV2:
    return PillarConfigV2(
        pillar_id=PillarId.DIRECTIONAL_CONVICTION,
        display_name="Directional Conviction",
        description=(
            "How confidently the underlying is trending in the option's direction. "
            "Dominant pillar (0.40 exponent) in the geometric-mean composite — a "
            "weak directional signal collapses the Sharpshooter score. Six subscores "
            "blend Stage 2 trend template, relative strength, ADX/DI agreement, "
            "breakout proximity, OBV confirmation, and sector RS."
        ),
        numeric_subscores=[
            _num(
                "stage_2_trend_score",
                "Stage 2 Trend Template",
                "stage_2_trend_score",
                0.30,
                [
                    _bp(0.0, 0.0),
                    _bp(28.0, 10.0),
                    _bp(57.0, 50.0),
                    _bp(85.0, 85.0),
                    _bp(100.0, 100.0),
                ],
            ),
            _num(
                "rs_20d",
                "Relative Strength 20d vs SPY",
                "rs_20d",
                0.20,
                [
                    _bp(-15.0, 0.0),
                    _bp(-5.0, 20.0),
                    _bp(0.0, 45.0),
                    _bp(3.0, 65.0),
                    _bp(7.0, 85.0),
                    _bp(15.0, 100.0),
                ],
            ),
            _num(
                "adx_directional_score",
                "ADX x Directional Agreement",
                "adx_directional_score",
                0.15,
                [
                    _bp(0.0, 0.0),
                    _bp(30.0, 25.0),
                    _bp(50.0, 55.0),
                    _bp(75.0, 85.0),
                    _bp(100.0, 100.0),
                ],
            ),
            _num(
                "breakout_proximity_pct",
                "Proximity to Breakout Pivot",
                "breakout_proximity_pct",
                0.15,
                # Signed % distance from 52w pivot (positive = post-breakout).
                # Peaks at just-broke-out (+1..+3%), penalizes far-below or
                # already-extended.
                [
                    _bp(-20.0, 10.0),
                    _bp(-8.0, 40.0),
                    _bp(-3.0, 70.0),
                    _bp(-1.0, 90.0),
                    _bp(2.0, 100.0),
                    _bp(6.0, 80.0),
                    _bp(12.0, 50.0),
                    _bp(25.0, 15.0),
                ],
            ),
            _num(
                "obv_confirmation_score",
                "Volume / OBV Confirmation",
                "obv_confirmation_score",
                0.10,
                [
                    _bp(0.0, 0.0),
                    _bp(10.0, 10.0),
                    _bp(50.0, 50.0),
                    _bp(90.0, 90.0),
                    _bp(100.0, 100.0),
                ],
            ),
            _num(
                "sector_rs_20d",
                "Sector Relative Strength 20d",
                "sector_rs_20d",
                0.10,
                [
                    _bp(-10.0, 5.0),
                    _bp(-3.0, 25.0),
                    _bp(0.0, 50.0),
                    _bp(2.0, 70.0),
                    _bp(5.0, 90.0),
                    _bp(10.0, 100.0),
                ],
            ),
        ],
    )


# ============================================================================
# Move Potential pillar (0.35 exponent; 5 subscores)
# ============================================================================


def _move_potential_pillar() -> PillarConfigV2:
    return PillarConfigV2(
        pillar_id=PillarId.MOVE_POTENTIAL,
        display_name="Move Potential",
        description=(
            "The probability and magnitude of a significant price move during the "
            "option's life. Middle exponent (0.35) in the composite. Blends catalyst "
            "presence, historical post-event magnitude, IV/RV ratio, volatility "
            "compression, and expected-vs-required move. Cheap IV with a compressed "
            "range just before a known catalyst scores highest."
        ),
        numeric_subscores=[
            _num(
                "move_trigger_score",
                "Move Trigger (catalyst / breakout)",
                "move_trigger_score",
                0.35,
                # `move_trigger_score` accessor returns a precomputed 0-100 value
                # (catalyst-in-window gives 60-90; breakout fallback 45-70). The
                # breakpoints lift the middle band since 55-70 represents "soft
                # trigger" (technical breakout without catalyst) — still tradeable.
                [
                    _bp(0.0, 0.0),
                    _bp(40.0, 30.0),
                    _bp(55.0, 55.0),
                    _bp(70.0, 75.0),
                    _bp(85.0, 92.0),
                    _bp(100.0, 100.0),
                ],
            ),
            _num(
                "historical_move_magnitude",
                "Historical Post-Event Move (1d avg, last 4Q)",
                "historical_move_magnitude",
                0.20,
                # Average absolute 1-day post-earnings return as a percentage.
                # Median stock moves ~3%; high-vol names 7-12%; meme/growth 15%+.
                [
                    _bp(0.0, 10.0),
                    _bp(1.5, 30.0),
                    _bp(3.0, 55.0),
                    _bp(5.0, 75.0),
                    _bp(8.0, 90.0),
                    _bp(15.0, 100.0),
                ],
            ),
            _num(
                "iv_rv_ratio",
                "IV / RV Ratio (cheap IV)",
                "iv_rv_ratio",
                0.15,
                # Ratio << 1 means IV is priced below realized — room for
                # expansion. Above 1.3 means the move is already paid for.
                [
                    _bp(0.5, 100.0),
                    _bp(0.75, 90.0),
                    _bp(0.9, 78.0),
                    _bp(1.0, 60.0),
                    _bp(1.15, 40.0),
                    _bp(1.3, 25.0),
                    _bp(2.0, 10.0),
                ],
                source_tier="tier2",
            ),
            _num(
                "bb_width_percentile",
                "Bollinger Band Width Percentile (compression)",
                "bb_width_percentile",
                0.15,
                # Low percentile = band tight = compressed range. Classic
                # compression-breakout setup; high percentile = already expanded.
                [
                    _bp(0.0, 95.0),
                    _bp(15.0, 90.0),
                    _bp(30.0, 70.0),
                    _bp(50.0, 45.0),
                    _bp(70.0, 25.0),
                    _bp(90.0, 15.0),
                    _bp(100.0, 10.0),
                ],
                source_tier="tier2",
            ),
            _num(
                "expected_vs_required",
                "Expected / Required Move (breakeven reach)",
                "expected_vs_required",
                0.15,
                # Ratio >= 1 means 1-sigma expected move reaches breakeven.
                # 1.3+ is Sharpshooter territory; below 0.7 almost never wins.
                [
                    _bp(0.3, 10.0),
                    _bp(0.7, 30.0),
                    _bp(1.0, 55.0),
                    _bp(1.3, 75.0),
                    _bp(1.7, 90.0),
                    _bp(2.5, 100.0),
                ],
                source_tier="tier2",
            ),
        ],
    )


# ============================================================================
# Trade Structure pillar (0.25 exponent; 5 subscores)
# ============================================================================


def _trade_structure_pillar() -> PillarConfigV2:
    return PillarConfigV2(
        pillar_id=PillarId.TRADE_STRUCTURE,
        display_name="Trade Structure",
        description=(
            "Contract-level quality: did we pick the right strike, DTE, and IV "
            "regime? Smallest exponent (0.25) in the composite but still critical — "
            "great direction and magnitude wrapped in a bad contract is still low-EV. "
            "Peaks at |delta| ~0.30, gamma-rich / theta-light, 28-35 DTE sweet spot, "
            "low IV rank, and strike at the pivot."
        ),
        numeric_subscores=[
            _num(
                "abs_delta",
                "|Entry Delta| (Sharpshooter peak at 0.30)",
                "abs_delta",
                0.25,
                # Non-monotonic: peak at 0.30, drops off toward 0 (lottery) and
                # 0.70+ (ITM / premium-heavy).
                [
                    _bp(0.05, 10.0),
                    _bp(0.15, 45.0),
                    _bp(0.22, 75.0),
                    _bp(0.30, 95.0),
                    _bp(0.40, 85.0),
                    _bp(0.50, 60.0),
                    _bp(0.70, 30.0),
                    _bp(0.90, 10.0),
                ],
                source_tier="tier2",
            ),
            _num(
                "gamma_theta_ratio",
                "Gamma / Theta Ratio (convexity vs decay)",
                "gamma_theta_ratio",
                0.25,
                # Raw formula: (gamma * close^2 * iv) / |theta|. Typical values
                # scale with underlying price; 30-60 is rich, 100+ is elite.
                [
                    _bp(0.0, 10.0),
                    _bp(5.0, 25.0),
                    _bp(15.0, 50.0),
                    _bp(30.0, 75.0),
                    _bp(60.0, 90.0),
                    _bp(120.0, 100.0),
                ],
                source_tier="tier2",
            ),
            _num(
                "dte_sweet_spot_score",
                "DTE Sweet Spot (catalyst-aware)",
                "dte_sweet_spot_score",
                0.20,
                # Accessor returns a precomputed 20-92 value; curve shapes it
                # to the 0-100 band, rewarding 70+ (catalyst-aligned or 28-35 DTE).
                [
                    _bp(0.0, 5.0),
                    _bp(25.0, 25.0),
                    _bp(45.0, 50.0),
                    _bp(65.0, 75.0),
                    _bp(82.0, 92.0),
                    _bp(95.0, 100.0),
                ],
            ),
            _num(
                "iv_percentile",
                "IV Rank (low is better)",
                "iv_percentile",
                0.20,
                # Monotonic: cheap IV (low percentile) is best — leaves room
                # for expansion and reduces vega paid upfront.
                [
                    _bp(0.0, 100.0),
                    _bp(20.0, 90.0),
                    _bp(40.0, 70.0),
                    _bp(60.0, 45.0),
                    _bp(80.0, 25.0),
                    _bp(100.0, 10.0),
                ],
                source_tier="tier2",
            ),
            _num(
                "strike_vs_pivot_pct",
                "Strike Proximity to Pivot",
                "strike_vs_pivot_pct",
                0.10,
                # Absolute % distance from the option-direction 52w pivot.
                # Peaks at 0 (strike at pivot), decays quickly.
                [
                    _bp(0.0, 100.0),
                    _bp(2.0, 85.0),
                    _bp(5.0, 60.0),
                    _bp(10.0, 35.0),
                    _bp(20.0, 15.0),
                    _bp(40.0, 5.0),
                ],
            ),
        ],
    )


# ============================================================================
# Gates (Section 4.2 — reuses existing GateConfig schema)
# ============================================================================


def _v4_gate_config() -> GateConfig:
    """Gate thresholds aligned with Sharpshooter thesis.

    The hard gates listed in Section 4.2 of the execution plan are already
    enforced by the production gate pipeline via the existing GateConfig
    fields. This seed keeps the defaults for everything not explicitly
    called out in the v4 spec so we inherit years of tuning on the bid-ask
    / OI / volume / greeks rails, and overlays the small adjustments the
    v4 thesis prescribes.
    """
    # Start from schema defaults, then layer v4 overrides.
    return GateConfig(
        max_spread_pct=10.0,  # Section 4.2: Spread % > 10% → REJECT
        min_open_interest=100,  # Section 4.2: OI < 100 → REJECT
        min_daily_volume=50,  # Section 4.2: Daily volume < 50 → REJECT
    )


# ============================================================================
# Full PillarConfig assembly
# ============================================================================


def build_v4_pillar_config() -> PillarConfig:
    """Return the v4 PillarConfig with weights, formula, and pillar slots populated."""
    global_weights = PillarWeights.v4_default()
    # Neutral scanner overrides: every scanner mirrors the global weights
    # initially. Phase 8 tuning edits them via the Policy page.
    scanner_weights: dict[str, PillarWeights] = {
        key: PillarWeights.v4_default() for key in SCANNER_KEYS
    }
    return PillarConfig(
        weights=global_weights,
        scanner_weights=scanner_weights,
        composite_formula="weighted_geometric_mean",
        directional_conviction=_directional_conviction_pillar(),
        move_potential=_move_potential_pillar(),
        trade_structure=_trade_structure_pillar(),
    )


def build_v4_policy_config() -> PolicyConfig:
    """Return the full PolicyConfig for the v4.0.0 seed."""
    return PolicyConfig(
        pillars=build_v4_pillar_config(),
        gates=_v4_gate_config(),
    )


def _serialize(config: PolicyConfig) -> dict[str, Any]:
    """Produce the JSON shape seed_policy_v4.py consumes."""
    return {
        "config": config.model_dump(mode="json"),
        "created_by": "scripts/build_policy_v4_default.py",
        "version": "v4.0.0",
        "changelog_entry": (
            "Initial seed of Policy v4.0.0 — the Sharpshooter regime. "
            "Three new pillars (Directional Conviction 0.40, Move Potential 0.35, "
            "Trade Structure 0.25) composed via weighted geometric mean. "
            "Design-reasoned breakpoints; Phase 8 observation + tuning will "
            "calibrate on live pipeline output. See "
            "docs/pillar_v4_execution_plan.md Section 4 for the thesis."
        ),
    }


def _print_summary(config: PolicyConfig) -> None:
    p = config.pillars
    weights = p.weights
    assert weights.directional_conviction is not None
    assert weights.move_potential is not None
    assert weights.trade_structure is not None

    print()
    print(f"Composite formula: {p.composite_formula}")
    print("Pillar exponents (geometric mean):")
    print(f"  directional_conviction: {weights.directional_conviction}")
    print(f"  move_potential:         {weights.move_potential}")
    print(f"  trade_structure:        {weights.trade_structure}")

    if p.scanner_weights:
        print(f"Per-scanner overrides: {len(p.scanner_weights)} scanners, "
              f"all seeded neutral (= global weights).")

    for pillar_attr in ("directional_conviction", "move_potential", "trade_structure"):
        pillar = getattr(p, pillar_attr)
        assert pillar is not None
        print()
        pid = pillar.pillar_id
        pid_str = pid.value if hasattr(pid, "value") else str(pid)
        print(f"{pillar.display_name} ({pid_str}):")
        for s in pillar.numeric_subscores:
            print(
                f"  {s.weight:.4f}  {s.display_name} ({s.feature_field})  "
                f"{len(s.breakpoints)} breakpoints  [{s.source_tier}]"
            )
        total = sum(s.weight for s in pillar.numeric_subscores) + sum(
            s.weight for s in pillar.categorical_subscores
        )
        print(f"  ----  total within-pillar weight: {total:.4f}")


def main() -> None:
    config = build_v4_policy_config()
    payload = _serialize(config)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(POLICY_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {POLICY_PATH}")

    _print_summary(config)


if __name__ == "__main__":
    main()
