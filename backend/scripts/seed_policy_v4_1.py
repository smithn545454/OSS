#!/usr/bin/env python3
"""Seed Policy v4.0.1 as an INACTIVE draft in DynamoDB.

Reads `scripts/output/v4_1_policy.json` (from `build_policy_v4_1.py`)
and writes a `Policy` row for v4.0.1.

Usage (from backend/):
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev PYTHONPATH=. \\
    python3 scripts/seed_policy_v4_1.py

Pass --overwrite to replace an existing v4.0.1 row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schemas import Policy, PolicyChangelog, PolicyConfig  # noqa: E402
from app.db.tables import PolicyTable  # noqa: E402

SEED_PATH = Path(__file__).parent / "output" / "v4_1_policy.json"
TARGET_VERSION = "v4.0.1"


def _load_payload() -> dict:
    if not SEED_PATH.exists():
        print(
            f"ERROR: seed file {SEED_PATH} not found.\n"
            "Run `python3 scripts/build_policy_v4_1.py` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(SEED_PATH) as f:
        return json.load(f)


async def _main(overwrite: bool) -> None:
    payload = _load_payload()
    config = PolicyConfig.model_validate(payload["config"])

    if not config.pillars.is_v4():
        print("ERROR: seeded config is not in v4 regime.", file=sys.stderr)
        sys.exit(1)

    if not config.pillars.scanner_weights:
        print("ERROR: v4.0.1 must carry scanner_weights overrides.", file=sys.stderr)
        sys.exit(1)

    existing = await PolicyTable.get(TARGET_VERSION)
    if existing is not None and not overwrite:
        print(
            f"Policy {TARGET_VERSION} already exists "
            f"(is_active={existing.is_active}). Pass --overwrite to replace.",
            file=sys.stderr,
        )
        sys.exit(2)

    policy_hash = Policy.compute_hash(config)
    changelog_note = payload.get("changelog_entry", "Seed of Policy v4.0.1.")
    changelog = [
        PolicyChangelog(
            field_path="pillars.scanner_weights",
            old_value=None,
            new_value=f"v4.0.1: {changelog_note}",
            changed_at=datetime.now(timezone.utc).isoformat(),
            changed_by=payload.get("created_by", "scripts/seed_policy_v4_1.py"),
        )
    ]

    policy = Policy(
        version=TARGET_VERSION,
        policy_hash=policy_hash,
        config=config,
        created_by=payload.get("created_by", "scripts/seed_policy_v4_1.py"),
        is_active=False,
        changelog=changelog,
    )

    await PolicyTable.put(policy)
    action = "Overwrote" if existing is not None else "Created"
    print(
        f"{action} policy version {TARGET_VERSION} (inactive). "
        f"policy_hash={policy_hash[:12]}..."
    )
    print(f"Scanner weights: {len(config.pillars.scanner_weights)} overrides")
    print(f"Verify via: curl .../api/policies/{TARGET_VERSION}")
    print("Activate (when ready): POST .../api/policies/v4.0.1/activate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(overwrite=args.overwrite))


if __name__ == "__main__":
    main()
