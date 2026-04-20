#!/usr/bin/env python3
"""Build + optionally activate a v5.0.0 policy from the current active policy.

Pulls the active policy (expected: v4.1.0), clones its config, and flips the
v5 activation fields:
  * v5_active=True
  * v5_active_scanners=["UNUSUAL_VOLUME", "CHEAP_OPTIONS", "BREAKDOWN", "REVALIDATION"]
  * v5_hr_archetypes = default_v5_hr_archetypes()
  * v5_p_archetypes  = default_v5_p_archetypes()
  * v5_calibration   = V5CalibrationConfig() (defaults)
  * v5_gbm_enabled   = True
  * v5_gbm_hr_weight = 0.5   (HR GBM holdout AUC 0.687 — useful)
  * v5_gbm_p_weight  = 0.0   (P GBM holdout AUC 0.501 — random; disable)
  * v5_hr_threshold  = 7.0
  * v5_p_threshold   = 50.0

Thresholds are starting points. Phase 8+ will retune.

BREAKOUT and COMPRESSION_EXPANSION stay on v4.1.0 (no positive v5 archetypes
for them — Phase 10 auto-discovery may surface one).

Usage:
  # Preview (default — generates JSON to /tmp/v5_policy.json, no server side-effects):
  python3 scripts/build_and_activate_v5_policy.py

  # Create the policy version (POSTs but does not activate):
  python3 scripts/build_and_activate_v5_policy.py --create

  # Full activation (POST + activate):
  python3 scripts/build_and_activate_v5_policy.py --create --activate

API host is read from the POLICY_API env var, defaults to the dev API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schemas import PolicyConfig, V5CalibrationConfig
from app.v5.hr_archetypes import default_v5_hr_archetypes
from app.v5.p_archetypes import default_v5_p_archetypes

DEFAULT_API = "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com"
API = os.environ.get("POLICY_API", DEFAULT_API)


def fetch_active_policy() -> dict:
    """Pull the currently active policy as raw JSON."""
    r = httpx.get(f"{API}/api/policies/active", timeout=30.0)
    r.raise_for_status()
    return r.json()


def build_v5_config(base_config_dict: dict) -> PolicyConfig:
    """Return a PolicyConfig cloned from base with v5 activation fields set."""
    # Round-trip through PolicyConfig for validation
    base_config = PolicyConfig.model_validate(base_config_dict)

    # Clone by re-validating a model_dump
    cfg_dict = base_config.model_dump()

    # v5 activation overrides
    cfg_dict["v5_active"] = True
    cfg_dict["v5_active_scanners"] = [
        "UNUSUAL_VOLUME",
        "CHEAP_OPTIONS",
        "BREAKDOWN",
        "REVALIDATION",
    ]
    cfg_dict["v5_calibration"] = V5CalibrationConfig().model_dump()
    cfg_dict["v5_hr_archetypes"] = default_v5_hr_archetypes().model_dump()
    cfg_dict["v5_p_archetypes"] = default_v5_p_archetypes().model_dump()
    cfg_dict["v5_gbm_enabled"] = True
    cfg_dict["v5_gbm_hr_weight"] = 0.5   # HR GBM: AUC 0.687 — useful
    cfg_dict["v5_gbm_p_weight"] = 0.0    # P GBM: AUC 0.501 — disable until Phase 7+ retrain
    cfg_dict["v5_hr_threshold"] = 7.0
    cfg_dict["v5_p_threshold"] = 50.0

    return PolicyConfig.model_validate(cfg_dict)


def create_policy(config: PolicyConfig, user: str) -> dict:
    """POST new policy to the API. Returns the created policy response."""
    payload = {
        "config": config.model_dump(mode="json"),
        "created_by": user,
    }
    r = httpx.post(f"{API}/api/policies", json=payload, timeout=60.0)
    if r.status_code != 200:
        print(f"Create failed: {r.status_code} {r.text[:500]}")
        r.raise_for_status()
    return r.json()


def activate_policy(version: str) -> dict:
    r = httpx.post(f"{API}/api/policies/{version}/activate", timeout=30.0)
    if r.status_code != 200:
        print(f"Activate failed: {r.status_code} {r.text[:500]}")
        r.raise_for_status()
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true",
                        help="POST the built config to /api/policies")
    parser.add_argument("--activate", action="store_true",
                        help="POST /api/policies/{version}/activate after creating")
    parser.add_argument("--out", default="/tmp/v5_policy.json")
    parser.add_argument("--user", default="claude-phase-7")
    args = parser.parse_args()

    print(f"API host: {API}")
    print(f"Fetching active policy...")
    active = fetch_active_policy()
    print(f"  base: version={active['version']} hash={active['policy_hash'][:16]}")

    cfg = build_v5_config(active["config"])
    print(f"\nv5 config built:")
    print(f"  v5_active: {cfg.v5_active}")
    print(f"  v5_active_scanners: {cfg.v5_active_scanners}")
    print(f"  v5_hr_archetypes: {len(cfg.v5_hr_archetypes.archetypes)} archetypes")
    print(f"  v5_p_archetypes:  {len(cfg.v5_p_archetypes.archetypes)} archetypes")
    print(f"  v5_gbm_enabled: {cfg.v5_gbm_enabled}")
    print(f"  v5_gbm_hr_weight: {cfg.v5_gbm_hr_weight}")
    print(f"  v5_gbm_p_weight:  {cfg.v5_gbm_p_weight} (P GBM disabled; AUC 0.50 noise)")
    print(f"  v5_hr_threshold:  {cfg.v5_hr_threshold}")
    print(f"  v5_p_threshold:   {cfg.v5_p_threshold}")

    # Save JSON preview
    with open(args.out, "w") as fp:
        json.dump(cfg.model_dump(mode="json"), fp, indent=2, default=str)
    print(f"\nSaved preview to {args.out}")

    if not args.create:
        print("\n(Dry run — pass --create to POST)")
        return 0

    print("\nPOSTing to /api/policies...")
    created = create_policy(cfg, args.user)
    new_version = created["policy"]["version"]
    new_hash = created["policy"]["policy_hash"][:16]
    print(f"  created version={new_version} hash={new_hash}")

    if not args.activate:
        print("\n(Created but not activated — pass --activate to flip.)")
        print(f"To activate: curl -X POST {API}/api/policies/{new_version}/activate")
        return 0

    print(f"\nActivating {new_version}...")
    activated = activate_policy(new_version)
    print(f"  response: {activated.get('message', activated)}")
    print("\nVerifying active policy...")
    now_active = fetch_active_policy()
    print(f"  active: version={now_active['version']} hash={now_active['policy_hash'][:16]}")
    print(f"  v5_active: {now_active['config'].get('v5_active')}")
    print(f"  v5_active_scanners: {now_active['config'].get('v5_active_scanners')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
