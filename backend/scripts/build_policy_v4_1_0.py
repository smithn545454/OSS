#!/usr/bin/env python3
"""Build Policy v4.1.0 — archetype-aware scoring.

Changes vs v4.0.1:
- ``composite_formula = "weighted_max"`` (up from weighted_geometric_mean).
- ``archetypes`` populated with six empirically-validated home-run
  archetypes from home_run_archetypes_findings.md (2026-04-18).
- ``anti_archetypes`` populated with three empirically-validated losing
  patterns that hard-REJECT on match.
- ADX subscore in Directional Conviction rebuilt to an inverted-U curve
  peaking at ADX=22 (code change — Phase 1; no policy knob).
- Per-scanner pillar weight overrides retained from v4.0.1.
- Archetype tier thresholds: archetype_match_score ≥ 80 → TIER_1,
  ≥ 70 → TIER_2 (DecisionConfig defaults).

Usage:
    python3 scripts/build_policy_v4_1_0.py           # writes JSON
    python3 scripts/build_policy_v4_1_0.py --print   # only prints summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.archetypes.defaults import (  # noqa: E402
    default_anti_archetypes,
    default_archetypes,
)
from app.core.schemas import (  # noqa: E402
    PillarConfig,
    PolicyConfig,
)
from scripts.build_policy_v4_1 import (  # noqa: E402
    build_v4_1_pillar_config as _v4_0_1_pillar_config,
)
from scripts.build_policy_v4_default import _v4_gate_config  # noqa: E402

OUTPUT_DIR = Path(__file__).parent / "output"
POLICY_PATH = OUTPUT_DIR / "v4_1_0_policy.json"

VERSION = "v4.1.0"


def build_v4_1_0_pillar_config() -> PillarConfig:
    """Pillar config identical to v4.0.1 except composite_formula."""
    base = _v4_0_1_pillar_config()
    return base.model_copy(update={"composite_formula": "weighted_max"})


def build_v4_1_0_policy_config() -> PolicyConfig:
    return PolicyConfig(
        pillars=build_v4_1_0_pillar_config(),
        gates=_v4_gate_config(),
        archetypes=default_archetypes(),
        anti_archetypes=default_anti_archetypes(),
    )


def _serialize(config: PolicyConfig) -> dict[str, Any]:
    return {
        "config": config.model_dump(mode="json"),
        "created_by": "scripts/build_policy_v4_1_0.py",
        "version": VERSION,
        "changelog_entry": (
            "Policy v4.1.0 — archetype-aware scoring. composite_formula "
            "switches from weighted_geometric_mean to weighted_max. Adds "
            "six home-run archetypes (secondary scoring axis) and three "
            "hard-REJECT anti-archetypes. Per-scanner pillar weights "
            "unchanged from v4.0.1. ADX subscore rebuilt in code to an "
            "inverted-U peaking at ADX=22. Motivated by "
            "home_run_archetypes_findings.md (2026-04-18): zero home runs "
            "score above 80 on v4.0.1, whereas three empirical clusters "
            "exceed 20× baseline HR200 rate and three negative clusters "
            "exhibit 0-0.1% HR200."
        ),
    }


def _print_summary(config: PolicyConfig) -> None:
    p = config.pillars
    print(f"\nPolicy version:    {VERSION}")
    print(f"Composite formula: {p.composite_formula}")

    arch = config.archetypes
    if arch:
        print(f"\nArchetypes ({len(arch.archetypes)}):")
        for a in arch.archetypes:
            print(
                f"  {a.archetype_id:<22} n={a.historical_n:<5} "
                f"HR200={a.historical_hr200_rate * 100:>5.1f}%  "
                f"win={a.historical_win_rate * 100:>4.0f}%  "
                f"conds={len(a.conditions)}"
            )

    aarch = config.anti_archetypes
    if aarch:
        print(f"\nAnti-archetypes ({len(aarch.anti_archetypes)}):")
        for aa in aarch.anti_archetypes:
            print(
                f"  {aa.anti_archetype_id:<22} n={aa.historical_n:<5} "
                f"win={aa.historical_win_rate * 100:>4.0f}%  "
                f"mean_pnl={aa.historical_mean_pnl_pct:>+6.1f}%"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", help="Print summary only")
    args = parser.parse_args()

    config = build_v4_1_0_policy_config()
    _print_summary(config)

    if args.print:
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = _serialize(config)
    POLICY_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {POLICY_PATH}")


if __name__ == "__main__":
    main()
