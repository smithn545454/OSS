#!/usr/bin/env python3
"""Seed Policy v4.0.0 as an INACTIVE draft in DynamoDB.

Reads `scripts/output/v4_default_policy.json` (produced by
`build_policy_v4_default.py`) and writes a `Policy` record with:

- `version = "v4.0.0"` — explicit (the API's POST handler auto-increments
  to vX.Y.(Z+1), so it cannot produce this version on its own)
- `is_active = False` — activation happens in Phase 7
- `config` — the v4 PolicyConfig from the seed file
- `policy_hash` — SHA-256 over the config
- `changelog` — single entry describing the v4 regime

Usage (from the backend/ directory):

    AWS_REGION=us-west-1 \\
    DYNAMODB_TABLE_PREFIX=oss-dev \\
    PYTHONPATH=. \\
    python3 scripts/seed_policy_v4.py

Pass `--overwrite` to replace an existing v4.0.0 row (otherwise the script
aborts if v4.0.0 already exists — we do not want to silently clobber a row
that a human may have edited via the Policy page).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.schemas import Policy, PolicyChangelog, PolicyConfig
from app.db.tables import PolicyTable

SEED_PATH = Path(__file__).parent / "output" / "v4_default_policy.json"
TARGET_VERSION = "v4.0.0"


def _load_payload() -> dict:
    if not SEED_PATH.exists():
        print(
            f"ERROR: seed file {SEED_PATH} not found.\n"
            "Run `PYTHONPATH=. python3 scripts/build_policy_v4_default.py` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(SEED_PATH) as f:
        return json.load(f)


async def _main(overwrite: bool) -> None:
    payload = _load_payload()
    config = PolicyConfig.model_validate(payload["config"])

    if not config.pillars.is_v4():
        print(
            "ERROR: seeded config is not in v4 regime. Rebuild the seed file.",
            file=sys.stderr,
        )
        sys.exit(1)

    existing = await PolicyTable.get(TARGET_VERSION)
    if existing is not None and not overwrite:
        print(
            f"Policy {TARGET_VERSION} already exists in DynamoDB "
            f"(is_active={existing.is_active}). Pass --overwrite to replace it.",
            file=sys.stderr,
        )
        sys.exit(2)

    policy_hash = Policy.compute_hash(config)
    changelog_note = payload.get(
        "changelog_entry",
        "Initial seed of Policy v4.0.0.",
    )
    changelog = [
        PolicyChangelog(
            field_path="pillars",
            old_value=None,
            new_value=f"v4.0.0 (Sharpshooter regime): {changelog_note}",
            changed_at=datetime.now(timezone.utc).isoformat(),
            changed_by=payload.get("created_by", "scripts/seed_policy_v4.py"),
        )
    ]

    policy = Policy(
        version=TARGET_VERSION,
        policy_hash=policy_hash,
        config=config,
        created_by=payload.get("created_by", "scripts/seed_policy_v4.py"),
        is_active=False,
        changelog=changelog,
    )

    await PolicyTable.put(policy)
    action = "Overwrote" if existing is not None else "Created"
    print(
        f"{action} policy version {TARGET_VERSION} (inactive). "
        f"policy_hash={policy_hash[:12]}..."
    )
    print(f"Verify via: curl .../api/policies/{TARGET_VERSION}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing v4.0.0 row if one exists.",
    )
    args = parser.parse_args()
    asyncio.run(_main(overwrite=args.overwrite))


if __name__ == "__main__":
    main()
