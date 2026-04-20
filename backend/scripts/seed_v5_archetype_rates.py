#!/usr/bin/env python3
"""Seed Wilson-bound rate estimates for the v5 HR archetype library.

Pulls closed paper positions from ``oss-dev-paper-positions``, matches each
position against the v5 HR archetype library, and computes per-archetype
Wilson-bound HR200 / win-rate / mean P&L estimates. Writes results to
``baselines/<date>-v5-hr-archetype-rates.json`` for inspection and to
``/tmp/v5_hr_archetype_rates.json`` for downstream tooling.

Phase 2: outputs JSON only — the live rate-lookup dict gets built at
Lambda init time in Phase 3 by re-running the same logic against DynamoDB.

Usage:
  AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
  python3 backend/scripts/seed_v5_archetype_rates.py

Optional flags:
  --rolling-window N    Only consider the most recent N closed positions
  --ewma-half-life N    Apply EWMA decay with half-life N (oldest get less weight)
  --out PATH            Override JSON output path
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Make the backend app importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calibration.archetype_rates import (
    HR200_THRESHOLD,
    estimate_archetype_rates,
)
from app.v5.hr_archetypes import default_v5_hr_archetypes

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")
OPT_TICKER_RE = re.compile(r"^(?:O:)?([A-Z]+)(\d{6})([CP])(\d+)$")

FVT_FEATURES = [
    "iv_percentile", "iv_rv_ratio", "rs_20d", "atr14_pct", "adx_14",
    "plus_di", "minus_di",
]


# ============================================================================
# Helpers
# ============================================================================

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_option_type(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    m = OPT_TICKER_RE.match(t)
    if not m:
        return None
    return "CALL" if m.group(3) == "C" else "PUT"


def _query_partition(table: Any, pk: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _fetch_fvt_for_eval(fvt: Any, eval_id: str) -> dict[str, float]:
    try:
        resp = fvt.query(
            KeyConditionExpression=Key("PK").eq(f"EVAL#{eval_id}")
            & Key("SK").begins_with("FEATURE#"),
        )
    except Exception:
        return {}
    out: dict[str, float] = {}
    for item in resp.get("Items", []):
        name = str(item.get("SK", "")).replace("FEATURE#", "")
        if name in FVT_FEATURES:
            v = _f(item.get("value"))
            if v is not None:
                out[name] = v
    return out


def _rs_contrarian(option_type: Optional[str], rs: Optional[float]) -> int:
    """Return 1 if RS is contrarian to the option direction, 0 else."""
    if rs is None or option_type is None:
        return 0
    if option_type == "CALL":
        return 1 if rs < 0.95 else 0
    return 1 if rs > 1.05 else 0


def _matches_archetype(record: dict, archetype) -> bool:
    """Strict match (no feather): all conditions must hold for the seed.

    The runtime matcher applies feather-graded fit. The seed uses strict
    matching to compute the historical "in-cohort" HR200 rate — exactly
    the value the runtime Wilson lower bound estimates from forward data.
    """
    for cond in archetype.conditions:
        feature = cond.feature_field
        # Resolve feature value
        if feature == "abs_delta":
            d = record.get("delta")
            value = abs(d) if d is not None else None
        elif feature == "rs_contrarian":
            value = _rs_contrarian(record.get("option_type"), record.get("rs_20d"))
        elif feature == "ts_score":
            value = record.get("ts_score")
        elif feature == "mp_score":
            value = record.get("mp_score")
        elif feature == "dc_score":
            value = record.get("dc_score")
        else:
            value = record.get(feature)

        if value is None:
            return False

        # Apply condition (strict — no feather)
        if cond.eq is not None and value != cond.eq:
            return False
        if cond.in_values is not None and value not in cond.in_values:
            return False
        if cond.between is not None:
            lo, hi = cond.between[0], cond.between[1]
            if not (lo <= float(value) <= hi):
                return False
        if cond.lte is not None and float(value) > cond.lte:
            return False
        if cond.gte is not None and float(value) < cond.gte:
            return False
    return True


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rolling-window", type=int, default=None,
                        help="Restrict to most recent N closed positions per archetype")
    parser.add_argument("--ewma-half-life", type=int, default=None,
                        help="Apply EWMA decay with this half-life (in trade count)")
    parser.add_argument("--out", default="/tmp/v5_hr_archetype_rates.json",
                        help="Output JSON path")
    args = parser.parse_args()

    print(f"Loading closed paper positions from {TABLE_PREFIX}-paper-positions...")
    dyn = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions_table = dyn.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dyn.Table(f"{TABLE_PREFIX}-feature-values")

    items = _query_partition(positions_table, "POS#CLOSED")
    print(f"  raw items: {len(items)}")

    # Normalize records into the shape the matcher expects
    records: list[dict] = []
    for p in items:
        mfe = _f(p.get("max_favorable_excursion"))
        pnl = _f(p.get("current_pnl_pct"))
        if mfe is None or pnl is None:
            continue
        opt_type = str(p.get("option_type") or "").upper() or None
        if not opt_type:
            opt_type = _parse_option_type(p.get("option_ticker"))
        dte = _f(p.get("dte_at_entry"))
        if dte is None:
            try:
                exp = datetime.fromisoformat(str(p.get("expiration_date"))[:10])
                ent = datetime.fromisoformat(str(p.get("entry_date"))[:10])
                dte = (exp - ent).days
            except Exception:
                pass
        records.append({
            "evaluation_id": str(p.get("evaluation_id") or ""),
            "entry_date": str(p.get("entry_date") or ""),
            "scanner_source": str(p.get("scanner_source") or "UNKNOWN"),
            "option_type": opt_type,
            "dte": dte,
            "delta": _f(p.get("entry_delta")),
            "iv_percentile": _f(p.get("entry_iv_percentile")),
            "iv_rv_ratio": _f(p.get("entry_iv_rv_ratio")),
            "dc_score": _f(p.get("pillar_directional_conviction")),
            "mp_score": _f(p.get("pillar_move_potential")),
            "ts_score": _f(p.get("pillar_trade_structure")),
            "max_favorable_excursion": mfe,
            "current_pnl_pct": pnl,
        })
    print(f"  usable records: {len(records)}")

    # FVT enrichment for features not denormalized on positions
    eval_ids = list({r["evaluation_id"] for r in records if r["evaluation_id"]})
    print(f"  fetching FVT for {len(eval_ids)} eval_ids (parallel, 32 workers)...")
    enrich: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = {ex.submit(_fetch_fvt_for_eval, fvt_table, eid): eid for eid in eval_ids}
        done = 0
        for fut in as_completed(futs):
            enrich[futs[fut]] = fut.result()
            done += 1
            if done % 2000 == 0:
                print(f"    {done}/{len(eval_ids)}")
    print("  FVT complete")

    for r in records:
        feats = enrich.get(r["evaluation_id"], {})
        for fname in FVT_FEATURES:
            if r.get(fname) is None and fname in feats:
                r[fname] = feats[fname]

    # Sort oldest→newest so rolling_window/EWMA work correctly
    records.sort(key=lambda r: r.get("entry_date") or "")

    # ========================================================================
    # Per-archetype rate estimation
    # ========================================================================
    print()
    print("=" * 80)
    print("v5 HR Archetype Rate Estimates")
    print(f"  rolling_window: {args.rolling_window or 'all'}")
    print(f"  ewma_half_life: {args.ewma_half_life or 'none'}")
    print("=" * 80)

    library = default_v5_hr_archetypes()
    output: dict[str, dict] = {}

    header = (
        f"\n{'Archetype':<32} {'n':>5} {'HR200':>5} {'point':>7} "
        f"{'lower':>7} {'upper':>7} {'mean P&L':>9}"
    )
    print(header)
    print("-" * 90)

    for arch in library.archetypes:
        cohort = [r for r in records if _matches_archetype(r, arch)]
        rates = estimate_archetype_rates(
            cohort,
            archetype_id=arch.archetype_id,
            rolling_window_n=args.rolling_window,
            ewma_half_life_n=args.ewma_half_life,
        )
        output[arch.archetype_id] = {
            "archetype_id": arch.archetype_id,
            "n_raw": rates.hr200.n_raw,
            "n_effective": rates.hr200.n_effective,
            "hr200": {
                "point": round(rates.hr200.point, 4),
                "lower": round(rates.hr200.lower, 4),
                "upper": round(rates.hr200.upper, 4),
            },
            "win_rate": {
                "point": round(rates.win_rate.point, 4),
                "lower": round(rates.win_rate.lower, 4),
                "upper": round(rates.win_rate.upper, 4),
            },
            "mean_pnl_pct": round(rates.mean_pnl_pct, 2),
            "median_pnl_pct": round(rates.median_pnl_pct, 2),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "seed_n": int(arch.historical_n),
            "seed_hr200_rate": float(arch.historical_hr200_rate),
        }
        # Compute count of HR200 trades for display
        hr_count = sum(
            1 for r in cohort
            if (r.get("max_favorable_excursion") or 0) >= HR200_THRESHOLD
        )
        print(f"{arch.archetype_id:<32} {rates.hr200.n_raw:>5} {hr_count:>5} "
              f"{rates.hr200.point*100:>6.2f}% {rates.hr200.lower*100:>6.2f}% "
              f"{rates.hr200.upper*100:>6.2f}% {rates.mean_pnl_pct:>+8.2f}%")

    # Save output
    with open(args.out, "w") as fp:
        json.dump(output, fp, indent=2)
    print(f"\nWrote rate estimates to: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
