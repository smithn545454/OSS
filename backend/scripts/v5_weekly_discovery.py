#!/usr/bin/env python3
"""Weekly v5 archetype discovery + drift detection runner.

What it does:
  1. Loads last 8 weeks of closed paper positions (for discovery)
  2. Loads last 4 weeks of closed positions (for drift check)
  3. Loads the active policy (to read current archetype library)
  4. Runs HR candidate discovery + P candidate discovery
  5. Runs drift check on every active HR + P archetype
  6. Emits a markdown summary + JSON artifacts to /tmp

Intended to run weekly (EventBridge cron Monday 07:00 UTC once that's wired)
or ad-hoc. Phase 8 ships compute-only — promotion/retirement proposals
land in the report and wait for explicit human approval.

Usage:
  AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 backend/scripts/v5_weekly_discovery.py \\
    --out-dir /tmp/v5_weekly
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
import httpx
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calibration.archetype_discovery import (
    CandidateArchetype,
    DriftAssessment,
    detect_drift,
    discover_candidates,
)
from app.core.schemas import ArchetypeConfig

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")
API = os.environ.get("POLICY_API", "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com")

OPT_TICKER_RE = re.compile(r"^(?:O:)?([A-Z]+)(\d{6})([CP])(\d+)$")

FVT_FEATURES = [
    "iv_percentile", "iv_rv_ratio", "rs_20d", "atr14_pct",
    "adx_14", "plus_di", "minus_di",
]


# ============================================================================
# Data loading
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


def _fetch_fvt(fvt: Any, eval_id: str) -> dict[str, float]:
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


def load_closed_positions(
    since: Optional[datetime] = None,
    enrich_fvt: bool = True,
) -> list[dict]:
    """Load all closed positions, optionally filtered by entry_date >= since."""
    print(f"Loading closed positions (since={since.isoformat() if since else 'all'})...")
    dyn = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions = dyn.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt = dyn.Table(f"{TABLE_PREFIX}-feature-values")

    items = _query_partition(positions, "POS#CLOSED")
    print(f"  raw items: {len(items)}")

    since_iso = since.isoformat() if since else ""
    rows: list[dict] = []
    for p in items:
        entry_date = str(p.get("entry_date") or "")
        if since and entry_date < since_iso:
            continue
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
        rows.append({
            "evaluation_id": str(p.get("evaluation_id") or ""),
            "entry_date": entry_date,
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
    print(f"  in-window: {len(rows)}")

    if enrich_fvt:
        eval_ids = list({r["evaluation_id"] for r in rows if r["evaluation_id"]})
        print(f"  fetching FVT for {len(eval_ids)} eval_ids...")
        enrich: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=32) as ex:
            futs = {ex.submit(_fetch_fvt, fvt, eid): eid for eid in eval_ids}
            done = 0
            for fut in as_completed(futs):
                enrich[futs[fut]] = fut.result()
                done += 1
                if done % 2000 == 0:
                    print(f"    {done}/{len(eval_ids)}")
        for r in rows:
            feats = enrich.get(r["evaluation_id"], {})
            for fname in FVT_FEATURES:
                if r.get(fname) is None and fname in feats:
                    r[fname] = feats[fname]
        print("  FVT complete")

    return rows


def fetch_active_library() -> tuple[Optional[ArchetypeConfig], Optional[ArchetypeConfig]]:
    """Pull active policy from the API and return (hr_archetypes, p_archetypes)."""
    r = httpx.get(f"{API}/api/policies/active", timeout=30.0)
    r.raise_for_status()
    cfg = r.json().get("config", {})
    hr = None
    p = None
    hr_data = cfg.get("v5_hr_archetypes")
    p_data = cfg.get("v5_p_archetypes")
    if hr_data:
        hr = ArchetypeConfig.model_validate(hr_data)
    if p_data:
        p = ArchetypeConfig.model_validate(p_data)
    return hr, p


# ============================================================================
# Report generation
# ============================================================================


def _fmt_conditions(conditions) -> str:
    return " × ".join(f"{f}={b}" for f, b in conditions)


def build_markdown_report(
    hr_candidates: list[CandidateArchetype],
    p_candidates: list[CandidateArchetype],
    hr_drift: list[DriftAssessment],
    p_drift: list[DriftAssessment],
    window_weeks: int,
    generated_at: datetime,
) -> str:
    lines: list[str] = []
    lines.append("# v5 Weekly Archetype Discovery Report")
    lines.append(f"\n**Generated:** {generated_at.isoformat()}")
    lines.append(f"**Discovery window:** last {window_weeks} weeks\n")

    # Summary
    promote_ready = sum(1 for c in hr_candidates[:10] if c.lift_lower >= 3.0)
    retire_candidates = [d for d in (hr_drift + p_drift) if d.drift_severity == "retire"]
    lines.append("## Summary\n")
    lines.append(
        f"- **HR candidates:** {len(hr_candidates)} found "
        f"(top {promote_ready} meet 3× lift)"
    )
    lines.append(f"- **P candidates:** {len(p_candidates)} found")
    lines.append(f"- **Retire signals:** {len(retire_candidates)} active archetypes drifting\n")

    # HR candidates
    lines.append("## HR candidates (top 15 by stability)\n")
    if not hr_candidates:
        lines.append("_No novel HR candidates this week._\n")
    else:
        lines.append(
            "| # | Scanner | Conditions | n | HR200 | Point | Lower | Lift | mean P&L |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(hr_candidates[:15], 1):
            lines.append(
                f"| {i} | {c.scanner} | {_fmt_conditions(c.conditions)} | {c.n} | "
                f"{c.hr200_count} | {c.point_rate * 100:.2f}% | "
                f"{c.lower_rate * 100:.2f}% | {c.lift_lower:.2f}× | "
                f"{c.mean_pnl_pct:+.2f}% |"
            )
        lines.append("")

    # P candidates
    lines.append("## P candidates (top 15 by stability)\n")
    if not p_candidates:
        lines.append("_No novel P candidates this week._\n")
    else:
        lines.append(
            "| # | Scanner | Conditions | n | Wins | Win% | Win-L | Lift | mean P&L |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(p_candidates[:15], 1):
            lines.append(
                f"| {i} | {c.scanner} | {_fmt_conditions(c.conditions)} | {c.n} | "
                f"{c.wins_count} | {c.point_rate * 100:.2f}% | "
                f"{c.lower_rate * 100:.2f}% | {c.lift_lower:.2f}× | "
                f"{c.mean_pnl_pct:+.2f}% |"
            )
        lines.append("")

    # Drift
    lines.append("## Drift assessment — active archetypes\n")
    lines.append(
        "| Archetype | Target | Hist n | Hist lower | Recent n | Recent pt | "
        "Severity | Note |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for d in hr_drift + p_drift:
        marker = {"ok": "✓", "watch": "⚠", "retire": "✗"}.get(d.drift_severity, d.drift_severity)
        lines.append(
            f"| {d.archetype_id} | {d.target} | {d.historical_n} | "
            f"{d.historical_lower * 100:.2f}% | {d.recent_n} | "
            f"{d.recent_point * 100:.2f}% | {marker} {d.drift_severity} | {d.note} |"
        )
    lines.append("")

    # Call to action
    lines.append("## Proposed actions\n")
    promote = [c for c in hr_candidates[:5] if c.lift_lower >= 3.0]
    if promote:
        lines.append("### Promote (generate draft policy)")
        for c in promote:
            lines.append(
                f"- `{c.scanner}_{'_'.join(b for _, b in c.conditions)}` "
                f"— {c.point_rate * 100:.1f}% {c.target} rate "
                f"(Wilson lower {c.lower_rate * 100:.1f}%, n={c.n})"
            )
        lines.append("")

    if retire_candidates:
        lines.append("### Retire (draft policy removes these)")
        for d in retire_candidates:
            lines.append(f"- `{d.archetype_id}` — {d.note}")
        lines.append("")

    if not promote and not retire_candidates:
        lines.append("_No changes proposed this week._\n")

    lines.append("---\n")
    lines.append(
        "Nothing in this report auto-applies. Promotion/retirement requires "
        "explicit click (see Phase 8 frontend additions, pending).\n"
    )
    return "\n".join(lines)


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="/tmp/v5_weekly")
    parser.add_argument("--discovery-weeks", type=int, default=8)
    parser.add_argument("--drift-weeks", type=int, default=4)
    parser.add_argument("--min-n", type=int, default=30)
    parser.add_argument("--min-lift", type=float, default=2.5)
    parser.add_argument("--skip-fvt", action="store_true",
                        help="Skip FVT enrichment (faster, fewer features)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    discovery_since = now - timedelta(weeks=args.discovery_weeks)
    drift_since = now - timedelta(weeks=args.drift_weeks)

    print("Fetching active archetype library from API...")
    hr_library, p_library = fetch_active_library()
    n_hr = len(hr_library.archetypes) if hr_library else 0
    n_p = len(p_library.archetypes) if p_library else 0
    print(f"  v5_hr_archetypes: {n_hr}")
    print(f"  v5_p_archetypes:  {n_p}")

    # Load positions (two windows)
    discovery_positions = load_closed_positions(
        since=discovery_since, enrich_fvt=not args.skip_fvt,
    )
    drift_positions = [p for p in discovery_positions if p["entry_date"] >= drift_since.isoformat()]
    print(f"  drift window: {len(drift_positions)}")

    # Discover HR candidates
    print("\nDiscovering HR candidates...")
    hr_candidates = discover_candidates(
        discovery_positions,
        existing_library=hr_library,
        target="hr200",
        min_n=args.min_n,
        min_lift_lower=args.min_lift,
    )
    print(f"  {len(hr_candidates)} novel HR candidates")

    # Discover P candidates
    print("\nDiscovering P candidates...")
    p_candidates = discover_candidates(
        discovery_positions,
        existing_library=p_library,
        target="profit",
        min_n=args.min_n,
        min_lift_lower=1.3,          # Lower lift threshold for profit
        min_mean_pnl=5.0,            # Require >5% mean P&L
    )
    print(f"  {len(p_candidates)} novel P candidates")

    # Drift assessment
    print("\nRunning drift assessment...")
    hr_drift: list[DriftAssessment] = []
    if hr_library:
        for a in hr_library.archetypes:
            hr_drift.append(detect_drift(a, drift_positions, target="hr200"))
    p_drift: list[DriftAssessment] = []
    if p_library:
        for a in p_library.archetypes:
            p_drift.append(detect_drift(a, drift_positions, target="profit"))

    ok = sum(1 for d in hr_drift + p_drift if d.drift_severity == "ok")
    watch = sum(1 for d in hr_drift + p_drift if d.drift_severity == "watch")
    retire = sum(1 for d in hr_drift + p_drift if d.drift_severity == "retire")
    print(f"  drift: {ok} ok, {watch} watch, {retire} retire")

    # Report
    md = build_markdown_report(
        hr_candidates=hr_candidates,
        p_candidates=p_candidates,
        hr_drift=hr_drift,
        p_drift=p_drift,
        window_weeks=args.discovery_weeks,
        generated_at=now,
    )
    md_path = out_dir / f"discovery_{now.strftime('%Y-%m-%d')}.md"
    md_path.write_text(md)
    print(f"\nMarkdown report: {md_path}")

    # JSON artifacts
    json_path = out_dir / f"discovery_{now.strftime('%Y-%m-%d')}.json"
    with open(json_path, "w") as fp:
        json.dump({
            "generated_at": now.isoformat(),
            "discovery_weeks": args.discovery_weeks,
            "drift_weeks": args.drift_weeks,
            "n_positions": len(discovery_positions),
            "hr_candidates": [
                {
                    "scanner": c.scanner,
                    "conditions": list(c.conditions),
                    "depth": c.depth,
                    "n": c.n,
                    "hr200_count": c.hr200_count,
                    "point": c.point_rate,
                    "lower": c.lower_rate,
                    "upper": c.upper_rate,
                    "lift_lower": c.lift_lower,
                    "mean_pnl_pct": c.mean_pnl_pct,
                    "stability": c.stability_score,
                }
                for c in hr_candidates
            ],
            "p_candidates": [
                {
                    "scanner": c.scanner,
                    "conditions": list(c.conditions),
                    "depth": c.depth,
                    "n": c.n,
                    "wins_count": c.wins_count,
                    "point": c.point_rate,
                    "lower": c.lower_rate,
                    "upper": c.upper_rate,
                    "lift_lower": c.lift_lower,
                    "mean_pnl_pct": c.mean_pnl_pct,
                    "stability": c.stability_score,
                }
                for c in p_candidates
            ],
            "drift_hr": [d.__dict__ for d in hr_drift],
            "drift_p": [d.__dict__ for d in p_drift],
        }, fp, indent=2, default=str)
    print(f"JSON artifacts:  {json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
