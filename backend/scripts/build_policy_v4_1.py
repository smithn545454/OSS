#!/usr/bin/env python3
"""Build Policy v4.0.1 — per-scanner pillar weight overrides.

Changes vs v4.0.0:
- ``scanner_weights`` populated with empirically-informed overrides per
  scanner rather than neutral (= global) weights.

Rationale (from diagnose_v4_weakness.py output on closed paper trades):
- BREAKOUT: DC has Pearson +0.47 vs P&L → boost DC weight.
- BREAKDOWN: identical trend-follow logic (with the Phase B direction
  flip) → same boost for PUTs.
- UNUSUAL_VOLUME: catalyst-driven; DC near-noise (−0.04 Pearson) → lean MP+TS.
- CHEAP_OPTIONS: premium-arbitrage; DC anti-predictive (−0.08) → lean MP+TS.
- COMPRESSION_EXPANSION: volatility-expansion play → lean MP.
- REVALIDATION: catalyst revalidation → lean MP.

Global (unknown-scanner) weights unchanged from v4.0.0.

Usage:
    python3 scripts/build_policy_v4_1.py           # writes JSON
    python3 scripts/build_policy_v4_1.py --print   # only prints summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schemas import (  # noqa: E402
    PillarConfig,
    PillarWeights,
    PolicyConfig,
)
from scripts.build_policy_v4_default import (  # noqa: E402
    _directional_conviction_pillar,
    _move_potential_pillar,
    _trade_structure_pillar,
    _v4_gate_config,
)

OUTPUT_DIR = Path(__file__).parent / "output"
POLICY_PATH = OUTPUT_DIR / "v4_1_policy.json"

VERSION = "v4.0.1"

# Per-scanner pillar weight overrides. Each triple (DC, MP, TS) must sum to 1.0.
SCANNER_WEIGHT_OVERRIDES: dict[str, tuple[float, float, float]] = {
    # Trend-following scanners — DC dominates (DC Pearson +0.47 on BREAKOUT).
    # BREAKDOWN gets identical weights; Phase B rs_20d/sector_rs_20d direction
    # flip makes DC symmetric across CALL/PUT.
    "BREAKOUT":              (0.50, 0.30, 0.20),
    "BREAKDOWN":             (0.50, 0.30, 0.20),

    # Catalyst-driven scanners — MP (catalyst + volatility) dominates; DC
    # carries ~1/3 as much weight because trend signal is ~noise here.
    "UNUSUAL_VOLUME":        (0.20, 0.50, 0.30),
    "REVALIDATION":          (0.25, 0.50, 0.25),

    # Volatility-structure scanners — MP + TS share the load; DC is least
    # informative for premium-arbitrage and compression-expansion entries.
    "CHEAP_OPTIONS":         (0.20, 0.40, 0.40),
    "COMPRESSION_EXPANSION": (0.30, 0.50, 0.20),
}


def _weights_for(dc: float, mp: float, ts: float) -> PillarWeights:
    """Build a v4-shaped PillarWeights (v3 fields explicitly None)."""
    total = dc + mp + ts
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Scanner weights must sum to 1.0 (got {total})")
    return PillarWeights(
        premium_leverage=None,
        underlying_behavior=None,
        setup_quality=None,
        directional_conviction=dc,
        move_potential=mp,
        trade_structure=ts,
    )


def build_v4_1_pillar_config() -> PillarConfig:
    """Return the v4.0.1 PillarConfig with scanner_weights populated."""
    global_weights = PillarWeights.v4_default()  # unchanged from v4.0.0

    scanner_weights: dict[str, PillarWeights] = {
        scanner: _weights_for(dc, mp, ts)
        for scanner, (dc, mp, ts) in SCANNER_WEIGHT_OVERRIDES.items()
    }

    return PillarConfig(
        weights=global_weights,
        scanner_weights=scanner_weights,
        composite_formula="weighted_geometric_mean",
        directional_conviction=_directional_conviction_pillar(),
        move_potential=_move_potential_pillar(),
        trade_structure=_trade_structure_pillar(),
    )


def build_v4_1_policy_config() -> PolicyConfig:
    return PolicyConfig(
        pillars=build_v4_1_pillar_config(),
        gates=_v4_gate_config(),
    )


def _serialize(config: PolicyConfig) -> dict[str, Any]:
    return {
        "config": config.model_dump(mode="json"),
        "created_by": "scripts/build_policy_v4_1.py",
        "version": VERSION,
        "changelog_entry": (
            "Policy v4.0.1 — per-scanner pillar weight overrides. "
            "BREAKOUT/BREAKDOWN lean DC (0.50); UNUSUAL_VOLUME/REVALIDATION "
            "lean MP (0.50); CHEAP_OPTIONS/COMPRESSION_EXPANSION lean MP+TS. "
            "Global weights unchanged. Motivated by diagnose_v4_weakness.py "
            "per-scanner correlation analysis showing DC's predictiveness "
            "varies from +0.47 (BREAKOUT) to −0.16 (BREAKDOWN pre-direction-"
            "flip) to −0.08 (CHEAP_OPTIONS). Combined with Phase B direction-"
            "aware DC (rs_20d + sector_rs_20d flip for PUTs) to give BREAKDOWN "
            "its full DC reward."
        ),
    }


def _print_summary(config: PolicyConfig) -> None:
    p = config.pillars
    g = p.weights
    print(f"\nPolicy version: {VERSION}")
    print(f"Composite formula: {p.composite_formula}\n")
    print("Global (default) pillar weights:")
    print(f"  directional_conviction: {g.directional_conviction}")
    print(f"  move_potential:         {g.move_potential}")
    print(f"  trade_structure:        {g.trade_structure}")

    print(f"\nPer-scanner weight overrides ({len(p.scanner_weights or {})}):")
    print(f"  {'Scanner':<25} {'DC':>6} {'MP':>6} {'TS':>6}")
    print(f"  {'-'*25} {'----':>6} {'----':>6} {'----':>6}")
    for scanner in sorted((p.scanner_weights or {}).keys()):
        w = p.scanner_weights[scanner]
        print(f"  {scanner:<25} {w.directional_conviction:>6.2f} "
              f"{w.move_potential:>6.2f} {w.trade_structure:>6.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", help="Print summary only")
    args = parser.parse_args()

    config = build_v4_1_policy_config()
    _print_summary(config)

    if args.print:
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = _serialize(config)
    POLICY_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {POLICY_PATH}")


if __name__ == "__main__":
    main()
