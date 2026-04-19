#!/usr/bin/env python3
"""Drive the v4.1.0 paper-trade rescore to completion.

Calls ``POST /api/paper-trading/rescore-v4.1.0`` in a loop until
``remaining == 0``. The endpoint is idempotent and resumable: each
position gets ``scoring_version = "v4.1.0"`` written once, so re-running
the script (or the endpoint) skips done rows.

Usage:
    # Dry run against local API (prints deltas, writes nothing)
    python scripts/rescore_paper_trades_v41.py --base-url http://localhost:8001 --dry-run

    # Real run against production API
    python scripts/rescore_paper_trades_v41.py \\
        --base-url https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com

    # Open positions only, smaller batch
    python scripts/rescore_paper_trades_v41.py \\
        --status open --batch-size 100
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def call_rescore(
    base_url: str,
    batch_size: int,
    status_filter: str,
    dry_run: bool,
) -> dict:
    qs = urllib.parse.urlencode(
        {
            "batch_size": batch_size,
            "status_filter": status_filter,
            "dry_run": str(dry_run).lower(),
        }
    )
    url = f"{base_url.rstrip('/')}/api/paper-trading/rescore-v4.1.0?{qs}"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True, help="API base URL (no trailing slash)")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--status", default="all", choices=["all", "open", "closed"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-batches", type=int, default=200, help="Safety cap")
    args = ap.parse_args()

    total_rescored = 0
    total_errors = 0
    total_skipped = 0
    batch_num = 0

    while batch_num < args.max_batches:
        batch_num += 1
        try:
            result = call_rescore(
                args.base_url, args.batch_size, args.status, args.dry_run
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code}: {body}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Request failed on batch {batch_num}: {e}", file=sys.stderr)
            return 1

        rescored = result.get("rescored", 0)
        errors = result.get("errors", 0)
        skipped = result.get("skipped", 0)
        remaining = result.get("remaining", 0)
        total_needing = result.get("total_needing_rescore", 0)

        total_rescored += rescored
        total_errors += errors
        total_skipped += skipped

        print(
            f"Batch {batch_num}: rescored={rescored} "
            f"errors={errors} skipped={skipped} "
            f"remaining={remaining} (of {total_needing})"
        )
        for s in result.get("sample_deltas", [])[:3]:
            print(
                f"  {s.get('ticker')} [{s.get('position_id', '')[:8]}] "
                f"tier: {s.get('old_tier')} -> {s.get('new_tier')} "
                f"archetype={s.get('archetype_matched')} "
                f"score={s.get('archetype_match_score')} "
                f"anti={s.get('anti_archetype_triggered')}"
            )

        if rescored == 0 and remaining == 0:
            break
        if rescored == 0 and errors == 0:
            print("No progress made; stopping to avoid infinite loop.", file=sys.stderr)
            break
        time.sleep(0.5)

    print(
        f"\nDone. Total rescored={total_rescored} "
        f"errors={total_errors} skipped={total_skipped} "
        f"batches={batch_num} dry_run={args.dry_run}"
    )
    return 0 if total_errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
