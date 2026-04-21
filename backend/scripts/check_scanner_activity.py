#!/usr/bin/env python3
"""Flag scanners that appear in policy.v5_active_scanners but have produced
zero triggers in the last N days.

Reads `oss-dev-opportunities` directly and groups by scanner_type inside
each opportunity's scanner_triggers list. Compares against the active
policy's v5_active_scanners allowlist.

REVALIDATION is excluded from the check — it's synthetic and only fires
when recent APPROVEs exist, so low counts there are not a scanner health
signal.

Usage:
  AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 backend/scripts/check_scanner_activity.py [--days 30]

Exits 0 when every active scanner produced at least one trigger in the
window; exits 1 when one or more produced zero (so this script is also
safe to wire into a weekly cron for alerting).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import httpx
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")
POLICY_API = os.environ.get(
    "POLICY_API",
    "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com",
)

EXCLUDED_FROM_CHECK = {"REVALIDATION"}


def _active_scanners_from_policy() -> list[str]:
    resp = httpx.get(f"{POLICY_API}/api/policies/active", timeout=30.0)
    resp.raise_for_status()
    cfg = resp.json().get("config", {})
    active = cfg.get("v5_active_scanners") or []
    return [str(s) for s in active]


def _count_triggers(days: int) -> Counter:
    dyn = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dyn.Table(f"{TABLE_PREFIX}-opportunities")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts: Counter = Counter()

    # Opportunities are partitioned by day-bucket strings we don't know up
    # front, so scan and filter. Fine for a once-weekly ad-hoc lint.
    kwargs: dict = {
        "ProjectionExpression": "scanner_triggers, created_at",
        "FilterExpression": Key("created_at").gte(since),
    }
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            for trig in item.get("scanner_triggers", []):
                st = trig.get("scanner_type")
                if st:
                    counts[str(st)] += 1
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=30,
        help="Lookback window in days (default 30)",
    )
    args = parser.parse_args()

    try:
        active = _active_scanners_from_policy()
    except Exception as exc:
        print(f"ERROR: failed to load active policy: {exc}", file=sys.stderr)
        return 2

    if not active:
        print("Active policy has no v5_active_scanners — nothing to check.")
        return 0

    print(f"Active scanners per policy: {active}")
    print(f"Counting triggers over the last {args.days} days...")

    counts = _count_triggers(args.days)

    any_silent = False
    for scanner in active:
        if scanner in EXCLUDED_FROM_CHECK:
            continue
        n = counts.get(scanner, 0)
        status = "OK" if n > 0 else "SILENT"
        marker = "✓" if n > 0 else "✗"
        print(f"  {marker} {scanner}: {n} triggers ({status})")
        if n == 0:
            any_silent = True

    # Also note any non-active scanners that DID produce triggers, so
    # operators can see scanners leaking past the allowlist.
    leakers = [
        (s, n) for s, n in counts.items()
        if s not in active and s not in EXCLUDED_FROM_CHECK
    ]
    if leakers:
        print("\nNon-active scanners producing triggers (audit D1):")
        for s, n in sorted(leakers, key=lambda x: -x[1]):
            print(f"  ! {s}: {n} triggers (not in v5_active_scanners)")

    return 1 if any_silent else 0


if __name__ == "__main__":
    sys.exit(main())
