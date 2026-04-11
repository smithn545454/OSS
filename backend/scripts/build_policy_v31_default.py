#!/usr/bin/env python3
"""Build Policy v3.1.0 from the Phase 3 fitted output and (optionally) seed it.

Reads `policy_v31_fitted.json` produced by `fit_pillars_v31.py`, wraps it
in the full `PolicyConfig` shape (with all the non-pillar sections inheriting
from the active production policy v3.0.0), and writes a final
`policy_v31_default.json` ready to either:

- Inspect locally
- Be POSTed to `/api/policies` (but the API auto-increments versions so this
  would mint v3.0.1, not v3.1.0)
- Be seeded directly into `oss-dev-policies` via `PolicyTable.put()` with
  `version="v3.1.0"` and `is_active=False`

Use `--seed` to perform the actual DynamoDB write. Without it the script is
read-only and just produces the JSON.

Usage:
    python3 backend/scripts/build_policy_v31_default.py [--seed]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add backend root for app imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schemas import Policy, PolicyConfig  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
FITTED_POLICY_PATH = OUTPUT_DIR / "policy_v31_fitted.json"
V30_DEFAULT_PATH = OUTPUT_DIR / "policy_v3_default.json"
V31_OUTPUT_PATH = OUTPUT_DIR / "policy_v31_default.json"

V31_VERSION = "v3.1.0"
V31_CHANGELOG_ENTRY = (
    "Policy v3.1.0 — Setup Pocket pillar rearchitecture. "
    "Setup Pocket promoted from 17%→40% composite weight to make pocket-fit "
    "(DTE, entry price, scanner source, |delta|, convergence) the dominant "
    "ranking signal. Premium Leverage and Underlying Behavior reduced to 25% "
    "and 35% respectively, retaining within-pocket quality filtering. "
    "Breakpoints fit on 70/30 train/holdout split (cut date 2026-03-12) using "
    "drift-aware methodology: terciles instead of quintiles, exponential "
    "recency weighting (30-day half-life), and per-feature generalization "
    "gate (|train_r - holdout_r| ≤ 0.05) which removes drift-prone subscores "
    "before they enter the policy. See "
    "scripts/output/pillar_v31_design_report.md for fitting details."
)


def build_policy_config(fitted: dict[str, Any]) -> PolicyConfig:
    """Build a full PolicyConfig with v3.1.0 pillars and v3.0.0 non-pillar sections.

    Loads the active v3.0.0 default policy JSON for non-pillar sections so
    we don't accidentally drop or change unrelated config.
    """
    # Use schema defaults for everything except pillars (PolicyConfig defaults
    # are sourced from policy_v3_default.json via _load_default_pillar_configs).
    base_config = PolicyConfig().model_dump()

    # Replace ONLY the pillars section with the fitted v3.1.0 layout.
    fitted_pillars = fitted["config"]["pillars"]
    base_config["pillars"] = fitted_pillars

    # Validate end-to-end (this also enforces weight sums)
    return PolicyConfig.model_validate(base_config)


def write_json_payload(policy_config: PolicyConfig, fit_metadata: dict[str, Any]) -> None:
    payload = {
        "config": policy_config.model_dump(mode="json"),
        "version": V31_VERSION,
        "created_by": "fit_pillars_v31/build_policy_v31_default.py",
        "changelog_entry": V31_CHANGELOG_ENTRY,
        "fit_metadata": fit_metadata,
    }
    V31_OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    log.info(f"Wrote {V31_OUTPUT_PATH}")


async def seed_to_dynamodb(policy_config: PolicyConfig) -> Policy:
    """Write Policy v3.1.0 directly to oss-dev-policies as inactive."""
    from app.db.tables import PolicyTable

    policy_hash = Policy.compute_hash(policy_config)
    policy = Policy(
        version=V31_VERSION,
        policy_hash=policy_hash,
        config=policy_config,
        created_by="fit_pillars_v31",
        is_active=False,
        changelog=[],
    )
    await PolicyTable.put(policy)
    log.info(f"Seeded {V31_VERSION} to DynamoDB (is_active=False)")
    return policy


def print_summary(policy_config: PolicyConfig) -> None:
    pillars = policy_config.pillars
    weights = pillars.weights
    print()
    print(f"Policy {V31_VERSION} pillar weights:")
    print(f"  premium_leverage   : {weights.premium_leverage:.2f}")
    print(f"  underlying_behavior: {weights.underlying_behavior:.2f}")
    print(f"  setup_quality      : {weights.setup_quality:.2f}  (Setup Pocket)")
    total = weights.premium_leverage + weights.underlying_behavior + weights.setup_quality
    print(f"  total              : {total:.4f}")
    print()
    for key in ("premium_leverage", "underlying_behavior", "setup_quality"):
        p = getattr(pillars, key)
        print(f"{p.display_name} ({p.pillar_id}):")
        for s in p.numeric_subscores:
            print(
                f"  NUM {s.weight:.4f}  {s.display_name} ({s.feature_field})  "
                f"{len(s.breakpoints)} breakpoints  [{s.source_tier}]"
            )
        for s in p.categorical_subscores:
            print(
                f"  CAT {s.weight:.4f}  {s.display_name} ({s.feature_field})  "
                f"{len(s.category_scores)} categories"
            )
        within = sum(s.weight for s in p.numeric_subscores) + sum(
            s.weight for s in p.categorical_subscores
        )
        print(f"  ----  total within-pillar weight: {within:.4f}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Policy v3.1.0 from fitted pillars")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Write Policy v3.1.0 directly to oss-dev-policies (inactive). "
             "Without this flag, only the JSON file is produced.",
    )
    args = parser.parse_args()

    if not FITTED_POLICY_PATH.exists():
        log.error(f"Fitted policy not found at {FITTED_POLICY_PATH}; run fit_pillars_v31.py first")
        return 1

    fitted = json.loads(FITTED_POLICY_PATH.read_text())
    fit_metadata = fitted.get("fit_metadata", {})

    log.info("Building PolicyConfig with v3.1.0 pillars + v3.0.0 baselines...")
    try:
        policy_config = build_policy_config(fitted)
    except Exception as exc:
        log.error(f"PolicyConfig validation failed: {exc}")
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json_payload(policy_config, fit_metadata)
    print_summary(policy_config)

    if args.seed:
        log.info("Seeding to DynamoDB...")
        log.info(
            f"  AWS_REGION={os.environ.get('AWS_REGION', '(default)')} "
            f"DYNAMODB_TABLE_PREFIX={os.environ.get('DYNAMODB_TABLE_PREFIX', '(default)')}"
        )
        asyncio.run(seed_to_dynamodb(policy_config))

    return 0


if __name__ == "__main__":
    sys.exit(main())
