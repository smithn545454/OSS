"""Lambda-callable entry point for v5 weekly calibration.

This module is imported by the Lambda handler (``app.main``) to run the
weekly archetype rate-lookup computation and persist results to
``CalibrationRatesTable``. It is deliberately kept narrow: the CLI
counterpart at ``backend/scripts/v5_weekly_discovery.py`` still owns the
richer markdown/JSON reporting flow; this module is what production
actually calls on an EventBridge schedule.

Design notes:
- The position loader uses the sync boto3 client (matches the script's
  existing pattern) and runs inside ``asyncio.to_thread`` so the Lambda
  event loop isn't blocked.
- Closed paper positions already carry ``hr_archetype_matched`` and
  ``p_archetype_matched`` as written by the decision pipeline, so rate
  estimation reduces to filter + Wilson CI per archetype — no FVT
  enrichment needed at this tier.
- On first run (before the weekly job has ever fired) ``get_latest()``
  returns ``None`` in DecisionStage, which preserves the current seed
  fallback path.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import boto3
import httpx
from boto3.dynamodb.conditions import Key

from app.calibration.archetype_rates import (
    estimate_archetype_rates,
    filter_positions_for_archetype,
)
from app.core.schemas import ArchetypeConfig

logger = logging.getLogger(__name__)

DEFAULT_POLICY_API = (
    "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com"
)


def _env_table_prefix() -> str:
    return os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")


def _env_aws_region() -> str:
    return os.environ.get("AWS_REGION", "us-west-1")


def _env_policy_api() -> str:
    return os.environ.get("POLICY_API", DEFAULT_POLICY_API)


def _load_closed_positions(since: datetime) -> list[dict[str, Any]]:
    """Load closed paper positions with entry_date >= since.

    Sync boto3 call; run via ``asyncio.to_thread`` from async callers.
    """
    dyn = boto3.resource("dynamodb", region_name=_env_aws_region())
    table = dyn.Table(f"{_env_table_prefix()}-paper-positions")

    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq("POS#CLOSED"),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    since_iso = since.isoformat()
    rows: list[dict[str, Any]] = []
    for p in items:
        entry_date = str(p.get("entry_date") or "")
        if entry_date < since_iso:
            continue
        mfe = _as_float(p.get("max_favorable_excursion"))
        pnl = _as_float(p.get("current_pnl_pct"))
        if mfe is None or pnl is None:
            continue
        rows.append({
            "entry_date": entry_date,
            "hr_archetype_matched": str(p.get("hr_archetype_matched") or ""),
            "p_archetype_matched": str(p.get("p_archetype_matched") or ""),
            # Legacy v4.1.0 field — still present on older rows.
            "archetype_matched": str(p.get("archetype_matched") or ""),
            "scanner_source": str(p.get("scanner_source") or "UNKNOWN"),
            "max_favorable_excursion": mfe,
            "current_pnl_pct": pnl,
        })
    return rows


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_active_library() -> tuple[
    Optional[ArchetypeConfig], Optional[ArchetypeConfig]
]:
    """Fetch the active policy's archetype libraries over HTTP.

    Uses the public API endpoint instead of the PolicyTable directly so
    the rate lookup matches exactly what's running live (same policy row
    decision stage loaded). Network call kept sync to parallel the rest
    of the loader.
    """
    try:
        resp = httpx.get(f"{_env_policy_api()}/api/policies/active", timeout=30.0)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("weekly_runner: failed to fetch active policy: %s", exc)
        return None, None
    cfg = resp.json().get("config", {})
    hr = None
    p = None
    hr_data = cfg.get("v5_hr_archetypes")
    p_data = cfg.get("v5_p_archetypes")
    if hr_data:
        try:
            hr = ArchetypeConfig.model_validate(hr_data)
        except Exception as exc:
            logger.warning("weekly_runner: v5_hr_archetypes parse failed: %s", exc)
    if p_data:
        try:
            p = ArchetypeConfig.model_validate(p_data)
        except Exception as exc:
            logger.warning("weekly_runner: v5_p_archetypes parse failed: %s", exc)
    return hr, p


def compute_rate_lookups(
    positions: list[dict[str, Any]],
    hr_library: Optional[ArchetypeConfig],
    p_library: Optional[ArchetypeConfig],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Compute per-archetype HR and P rate lookups from closed positions.

    Returns two DynamoDB-friendly dicts keyed by archetype_id:
    - hr_rates[aid] = {point, lower, upper, n_trades}
    - p_rates[aid]  = {win_point, win_lower, win_upper, mean_pnl_pct, n_trades}

    Archetypes with zero matched positions are included with n_trades=0 so
    the decision stage can skip them cleanly without doing missing-key
    bookkeeping.
    """
    hr_rates: dict[str, dict[str, Any]] = {}
    if hr_library is not None:
        for arch in hr_library.archetypes:
            aid = arch.archetype_id
            matched = filter_positions_for_archetype(
                positions, aid, archetype_field="hr_archetype_matched",
            )
            if not matched:
                hr_rates[aid] = {
                    "point": 0.0, "lower": 0.0, "upper": 0.0, "n_trades": 0,
                }
                continue
            est = estimate_archetype_rates(matched, archetype_id=aid)
            hr_rates[aid] = {
                "point": est.hr200.point,
                "lower": est.hr200.lower,
                "upper": est.hr200.upper,
                "n_trades": est.hr200.n_raw,
            }

    p_rates: dict[str, dict[str, Any]] = {}
    if p_library is not None:
        for arch in p_library.archetypes:
            aid = arch.archetype_id
            matched = filter_positions_for_archetype(
                positions, aid, archetype_field="p_archetype_matched",
            )
            if not matched:
                p_rates[aid] = {
                    "win_point": 0.0, "win_lower": 0.0, "win_upper": 0.0,
                    "mean_pnl_pct": 0.0, "n_trades": 0,
                }
                continue
            est = estimate_archetype_rates(matched, archetype_id=aid)
            p_rates[aid] = {
                "win_point": est.win_rate.point,
                "win_lower": est.win_rate.lower,
                "win_upper": est.win_rate.upper,
                "mean_pnl_pct": est.mean_pnl_pct,
                "n_trades": est.win_rate.n_raw,
            }

    return hr_rates, p_rates


async def run_weekly_discovery(
    discovery_weeks: int = 8,
    persist_rates: bool = True,
) -> dict[str, Any]:
    """Weekly entrypoint: compute rate lookups and persist to DynamoDB.

    Args:
        discovery_weeks: Sliding window of closed positions to draw from.
        persist_rates: If True, writes the rate lookups to
            ``CalibrationRatesTable`` (both LATEST and a VERSION row).
            Set False for dry runs / local inspection.

    Returns:
        Summary dict with rate counts, policy version, and persistence
        status. Safe to include in a Lambda response payload.
    """
    now = datetime.now(timezone.utc)
    discovery_since = now - timedelta(weeks=discovery_weeks)

    hr_library, p_library = await asyncio.to_thread(_fetch_active_library)
    positions = await asyncio.to_thread(_load_closed_positions, discovery_since)

    hr_rates, p_rates = compute_rate_lookups(positions, hr_library, p_library)

    logger.info(
        "weekly_runner: computed rates — positions=%d hr_archetypes=%d "
        "p_archetypes=%d",
        len(positions), len(hr_rates), len(p_rates),
    )

    summary: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "window_start": discovery_since.isoformat(),
        "window_end": now.isoformat(),
        "n_positions": len(positions),
        "hr_rates_count": len(hr_rates),
        "p_rates_count": len(p_rates),
        "hr_rates": hr_rates,
        "p_rates": p_rates,
        "persisted": False,
    }

    if persist_rates:
        from app.db.tables import CalibrationRatesTable, PolicyTable

        active_policy = await PolicyTable.get_active()
        policy_version = active_policy.version if active_policy else None

        await CalibrationRatesTable.save_latest(
            hr_rates=hr_rates,
            p_rates=p_rates,
            window_start=summary["window_start"],
            window_end=summary["window_end"],
            policy_version=policy_version,
        )
        await CalibrationRatesTable.save_version(
            hr_rates=hr_rates,
            p_rates=p_rates,
            window_start=summary["window_start"],
            window_end=summary["window_end"],
            policy_version=policy_version,
        )
        summary["persisted"] = True
        summary["policy_version"] = policy_version

    return summary
