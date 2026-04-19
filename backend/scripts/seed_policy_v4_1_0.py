#!/usr/bin/env python3
"""Seed Policy v4.1.0 as an INACTIVE draft in DynamoDB.

Reads `scripts/output/v4_1_0_policy.json` (from `build_policy_v4_1_0.py`)
and writes a `Policy` row for v4.1.0.

Usage (from backend/):
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev PYTHONPATH=. \\
    python3 scripts/seed_policy_v4_1_0.py

Pass --overwrite to replace an existing v4.1.0 row.
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

SEED_PATH = Path(__file__).parent / "output" / "v4_1_0_policy.json"
TARGET_VERSION = "v4.1.0"


def _load_payload() -> dict:
    if not SEED_PATH.exists():
        print(
            f"ERROR: seed file {SEED_PATH} not found.\n"
            "Run `python3 scripts/build_policy_v4_1_0.py` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(SEED_PATH) as f:
        return json.load(f)


async def _main(overwrite: bool) -> None:
    payload = _load_payload()
    config = PolicyConfig.model_validate(payload["config"])

    # Validation: v4.1.0 invariants.
    if not config.pillars.is_v4():
        print("ERROR: seeded config is not in v4 regime.", file=sys.stderr)
        sys.exit(1)
    if config.pillars.composite_formula != "weighted_max":
        print(
            f"ERROR: v4.1.0 must use composite_formula='weighted_max', "
            f"got '{config.pillars.composite_formula}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if config.archetypes is None or len(config.archetypes.archetypes) < 6:
        print(
            "ERROR: v4.1.0 must carry at least 6 archetypes.", file=sys.stderr
        )
        sys.exit(1)
    if (
        config.anti_archetypes is None
        or len(config.anti_archetypes.anti_archetypes) < 3
    ):
        print(
            "ERROR: v4.1.0 must carry at least 3 anti-archetypes.",
            file=sys.stderr,
        )
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
    changelog_note = payload.get("changelog_entry", "Seed of Policy v4.1.0.")
    changelog = [
        PolicyChangelog(
            field_path="archetypes",
            old_value=None,
            new_value=f"v4.1.0: {changelog_note}",
            changed_at=datetime.now(timezone.utc).isoformat(),
            changed_by=payload.get(
                "created_by", "scripts/seed_policy_v4_1_0.py"
            ),
        )
    ]

    policy = Policy(
        version=TARGET_VERSION,
        policy_hash=policy_hash,
        config=config,
        created_by=payload.get("created_by", "scripts/seed_policy_v4_1_0.py"),
        is_active=False,
        changelog=changelog,
    )

    await PolicyTable.put(policy)
    action = "Overwrote" if existing is not None else "Created"
    print(
        f"{action} policy version {TARGET_VERSION} (inactive). "
        f"policy_hash={policy_hash[:12]}..."
    )
    print(f"Composite formula: {config.pillars.composite_formula}")
    print(f"Archetypes:        {len(config.archetypes.archetypes)}")
    print(
        f"Anti-archetypes:   {len(config.anti_archetypes.anti_archetypes)}"
    )
    print(f"Verify via: curl .../api/policies/{TARGET_VERSION}")
    print("Activate (when ready): POST .../api/policies/v4.1.0/activate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(overwrite=args.overwrite))


if __name__ == "__main__":
    main()
