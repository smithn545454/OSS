#!/usr/bin/env python3
"""Rescore all paper positions with Policy v3.0.0 pillars.

This script recomputes the three new pillar scores (Premium Leverage,
Underlying Behavior, Setup Quality) for every paper position and
migrates the denormalized fields on PaperPosition from the old v2
names (pillar_directional/volatility/structure) to the new v3 names
(pillar_premium_leverage/underlying_behavior/setup_quality).

Prerequisites:
- Phase 5 backfill should be run first so Tier 2 features (ADX, RSI,
  MACD, etc.) are available in FeatureValueTable for all positions.
- Policy v3.0.0 should already be seeded (or the seed JSON loaded).

Strategy:
1. Query all positions (open + closed).
2. Backup each position's old v2 scores to a JSON file for rollback.
3. For each position:
   - Load its FeatureSet from FeatureValueTable via
     `FeatureSet.from_feature_values()`.
   - Build a ScoringContext from the position + feature set.
   - Run the new PillarCalculator to get the 3 pillar scores.
   - Compute the new composite/conviction score.
   - Update the DynamoDB item: SET new fields, REMOVE old fields,
     SET rescored_at.
4. Print summary statistics comparing old vs new score distributions.

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/rescore_all_positions.py [--dry-run] [--limit N]

Runtime: ~8 minutes for 15,505 positions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Add backend to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schemas import PillarConfig  # noqa: E402
from app.features.models import FeatureSet  # noqa: E402
from app.pillars.calculator import PillarCalculator, compute_final_score  # noqa: E402
from app.pillars.models import ScoringContext  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
BACKUP_PATH_TEMPLATE = str(
    OUTPUT_DIR / "position_scores_backup_{timestamp}.json"
)


# ============================================================================
# Helpers
# ============================================================================


def decimal_to_python(obj: Any) -> Any:
    """Recursively convert Decimal → float/int."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: decimal_to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimal_to_python(i) for i in obj]
    return obj


def to_decimal(v: Any) -> Any:
    """Convert float → Decimal for DynamoDB writes."""
    if isinstance(v, float):
        return Decimal(str(v))
    return v


# ============================================================================
# FeatureValue → FeatureSet reconstruction
# ============================================================================


def load_feature_set_for_eval(
    fvt_table: Any,
    evaluation_id: str,
) -> Optional[FeatureSet]:
    """Query FeatureValueTable for all features of an evaluation and
    reconstruct a FeatureSet.
    """
    from app.core.schemas import FeatureValue

    try:
        resp = fvt_table.query(
            KeyConditionExpression=Key("PK").eq(f"EVAL#{evaluation_id}")
            & Key("SK").begins_with("FEATURE#"),
        )
    except Exception as e:
        log.debug(f"FVT query failed for {evaluation_id}: {e}")
        return None

    items = resp.get("Items", [])
    if not items:
        return None

    feature_values: list[FeatureValue] = []
    for item in items:
        name = str(item.get("SK", "")).replace("FEATURE#", "")
        value = decimal_to_python(item.get("value"))
        try:
            feature_values.append(
                FeatureValue(
                    evaluation_id=evaluation_id,
                    feature_name=name,
                    value=value,
                )
            )
        except Exception as e:
            log.debug(f"FeatureValue coercion failed for {name}: {e}")
            continue

    try:
        return FeatureSet.from_feature_values(evaluation_id, feature_values)
    except Exception as e:
        log.debug(f"FeatureSet reconstruction failed for {evaluation_id}: {e}")
        return None


# ============================================================================
# Score a single position
# ============================================================================


def build_scoring_context(
    position: dict[str, Any],
    feature_set: Optional[FeatureSet],
) -> ScoringContext:
    """Build a ScoringContext from a raw position dict and its FeatureSet."""
    # Convert position dict Decimals to floats first
    p = decimal_to_python(position)

    scanner_list = p.get("scanner_list") or []
    convergence_count = int(p.get("convergence_count") or 0)

    return ScoringContext(
        evaluation_id=str(p.get("evaluation_id", "")),
        underlying_ticker=str(p.get("underlying_ticker", "")),
        option_type=str(p.get("option_type") or "CALL"),
        dte_bucket=str(p.get("dte_bucket") or "B"),
        scanner_triggers=[str(s) for s in scanner_list],
        direction_hint="NONE",
        convergence_count=convergence_count,
        # Category A: Underlying
        close=(feature_set.close if feature_set else (p.get("entry_underlying_price") or 0.0)),
        sma20=feature_set.sma20 if feature_set else None,
        sma50=feature_set.sma50 if feature_set else None,
        atr14=feature_set.atr14 if feature_set else None,
        atr14_pct=(feature_set.atr14_pct if feature_set else (p.get("entry_atr14_pct"))),
        return_5d=feature_set.return_5d if feature_set else None,
        return_20d=feature_set.return_20d if feature_set else None,
        trend_aligned_bullish=(feature_set.trend_aligned_bullish if feature_set else False),
        trend_aligned_bearish=(feature_set.trend_aligned_bearish if feature_set else False),
        # Category B: RS
        rs_5d=feature_set.rs_5d if feature_set else None,
        rs_20d=(feature_set.rs_20d if feature_set else p.get("entry_rs_20d")),
        # Category C: Volatility
        rv20=(feature_set.rv20 if feature_set else p.get("entry_rv20")),
        iv=(feature_set.iv if feature_set else (p.get("entry_iv") or 0.0)),
        iv_rv_ratio=(feature_set.iv_rv_ratio if feature_set else p.get("entry_iv_rv_ratio")),
        iv_percentile=(feature_set.iv_percentile if feature_set else p.get("entry_iv_percentile")),
        iv_regime=(
            str(feature_set.iv_regime)
            if feature_set and feature_set.iv_regime
            else "IV_NEUTRAL_REGIME"
        ),
        # Category D: Contract
        mid=(feature_set.mid if feature_set else (p.get("entry_price") or 0.0)),
        spread_pct=(
            feature_set.spread_pct if feature_set else (p.get("entry_spread_pct") or 0.0)
        ),
        theta_pct=feature_set.theta_pct if feature_set else 0.0,
        theta_adjusted_edge=(
            feature_set.theta_adjusted_edge
            if feature_set
            else p.get("entry_theta_adjusted_edge")
        ),
        required_move_pct=(feature_set.required_move_pct if feature_set else 0.0),
        expected_move_pct=(feature_set.expected_move_pct if feature_set else 0.0),
        feasibility_ratio=(
            feature_set.feasibility_ratio
            if feature_set
            else (p.get("entry_feasibility_ratio") or 0.0)
        ),
        time_adjusted_feasibility=(
            feature_set.time_adjusted_feasibility if feature_set else 0.0
        ),
        delta=float(p.get("entry_delta") or 0.0),
        dte=int(p.get("dte_at_entry") or 0),
        # Category E: Liquidity
        open_interest=(
            feature_set.open_interest
            if feature_set
            else int(p.get("entry_open_interest") or 0)
        ),
        volume=(
            feature_set.volume if feature_set else int(p.get("entry_volume") or 0)
        ),
        oi_5d_change_pct=feature_set.oi_5d_change_pct if feature_set else None,
        # Category F: Catalyst
        days_to_earnings=(
            feature_set.days_to_earnings
            if feature_set
            else p.get("entry_days_to_earnings")
        ),
        recent_sec_filing=feature_set.recent_sec_filing if feature_set else False,
        # Category G: Technical indicators
        ema_9=feature_set.ema_9 if feature_set else None,
        ema_21=feature_set.ema_21 if feature_set else None,
        ema_50=feature_set.ema_50 if feature_set else None,
        ema_200=feature_set.ema_200 if feature_set else None,
        ema_alignment=feature_set.ema_alignment if feature_set else None,
        rsi_14=feature_set.rsi_14 if feature_set else None,
        macd_histogram=feature_set.macd_histogram if feature_set else None,
        adx_14=feature_set.adx_14 if feature_set else None,
        plus_di=feature_set.plus_di if feature_set else None,
        minus_di=feature_set.minus_di if feature_set else None,
        obv_trend=feature_set.obv_trend if feature_set else None,
    )


def score_one_position(
    calculator: PillarCalculator,
    pillar_config: PillarConfig,
    position: dict[str, Any],
    feature_set: Optional[FeatureSet],
) -> dict[str, float]:
    """Score a single position. Returns dict with new pillar + composite scores."""
    ctx = build_scoring_context(position, feature_set)
    results = calculator.compute_pillars(
        evaluation=None, feature_set=None, opportunity=None, context=ctx
    )
    scores: dict[str, float] = {}
    for r in results:
        pid = str(r.pillar_id.value) if hasattr(r.pillar_id, "value") else str(r.pillar_id)
        if pid == "PREMIUM_LEVERAGE":
            scores["pillar_premium_leverage"] = round(r.score, 2)
        elif pid == "UNDERLYING_BEHAVIOR":
            scores["pillar_underlying_behavior"] = round(r.score, 2)
        elif pid == "SETUP_QUALITY":
            scores["pillar_setup_quality"] = round(r.score, 2)
    p = decimal_to_python(position)
    scanner_list = p.get("scanner_list") or []
    scanner_source = p.get("scanner_source") or (scanner_list[0] if scanner_list else None)
    composite = compute_final_score(
        scores.get("pillar_premium_leverage", 50),
        scores.get("pillar_underlying_behavior", 50),
        scores.get("pillar_setup_quality", 50),
        pillar_config,
        scanner_source=scanner_source,
    )
    scores["conviction_score"] = round(composite, 2)
    return scores


# ============================================================================
# DynamoDB update (SET new fields, REMOVE old fields)
# ============================================================================


def update_position_scores(
    dynamodb_client: Any,
    table_name: str,
    pk: str,
    sk: str,
    new_scores: dict[str, float],
    rescored_at: str,
    dry_run: bool = False,
) -> None:
    """Update a position's scores in DynamoDB.

    Uses raw boto3 `update_item` with an update_expression that sets the
    new pillar fields and removes the old ones atomically.
    """
    if dry_run:
        return

    update_expr_set = [
        "#pl = :pl",
        "#ub = :ub",
        "#sq = :sq",
        "#cs = :cs",
        "#ra = :ra",
        "#lu = :lu",
    ]
    expr_attr_names = {
        "#pl": "pillar_premium_leverage",
        "#ub": "pillar_underlying_behavior",
        "#sq": "pillar_setup_quality",
        "#cs": "conviction_score",
        "#ra": "rescored_at",
        "#lu": "last_updated",
        # For removal
        "#pd": "pillar_directional",
        "#pv": "pillar_volatility",
        "#ps": "pillar_structure",
    }
    expr_attr_values = {
        ":pl": to_decimal(new_scores["pillar_premium_leverage"]),
        ":ub": to_decimal(new_scores["pillar_underlying_behavior"]),
        ":sq": to_decimal(new_scores["pillar_setup_quality"]),
        ":cs": to_decimal(new_scores["conviction_score"]),
        ":ra": rescored_at,
        ":lu": rescored_at,
    }

    update_expression = (
        "SET " + ", ".join(update_expr_set)
        + " REMOVE #pd, #pv, #ps"
    )

    dynamodb_client.update_item(
        TableName=table_name,
        Key={
            "PK": {"S": pk},
            "SK": {"S": sk},
        },
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues={
            k: ({"N": str(v)} if isinstance(v, (int, float, Decimal)) else {"S": str(v)})
            for k, v in expr_attr_values.items()
        },
    )


# ============================================================================
# Main flow
# ============================================================================


def query_all_positions(
    positions_table: Any, status_pk: str
) -> list[dict[str, Any]]:
    """Query all positions from one status partition (POS#OPEN or POS#CLOSED)."""
    results: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(status_pk)}
    while True:
        resp = positions_table.query(**kwargs)
        results.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return results


async def main_async(args: argparse.Namespace) -> None:
    log.info(f"Connecting to DynamoDB region={AWS_REGION} prefix={TABLE_PREFIX}")
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
    positions_table = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dynamodb.Table(f"{TABLE_PREFIX}-feature-values")
    positions_table_name = f"{TABLE_PREFIX}-paper-positions"

    # Load pillar config (uses v3.0.0 defaults from the seed JSON)
    log.info("Loading Policy v3.0.0 pillar config...")
    pillar_config = PillarConfig.v3_default()
    calculator = PillarCalculator(pillar_config)

    # Query all positions
    log.info("Querying closed positions...")
    closed_positions = query_all_positions(positions_table, "POS#CLOSED")
    log.info(f"  {len(closed_positions)} closed")

    log.info("Querying open positions...")
    open_positions = query_all_positions(positions_table, "POS#OPEN")
    log.info(f"  {len(open_positions)} open")

    all_positions = closed_positions + open_positions
    log.info(f"Total positions to rescore: {len(all_positions)}")

    if args.limit:
        all_positions = all_positions[: args.limit]
        log.info(f"Limited to {args.limit} positions")

    # Backup old scores first
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = BACKUP_PATH_TEMPLATE.format(timestamp=timestamp)

    log.info(f"Backing up old scores to {backup_path}...")
    backup_records: list[dict[str, Any]] = []
    for pos in all_positions:
        p = decimal_to_python(pos)
        backup_records.append(
            {
                "position_id": p.get("position_id"),
                "evaluation_id": p.get("evaluation_id"),
                "entry_date": p.get("entry_date"),
                "status": str(p.get("status")),
                "old_pillar_directional": p.get("pillar_directional"),
                "old_pillar_volatility": p.get("pillar_volatility"),
                "old_pillar_structure": p.get("pillar_structure"),
                "old_conviction_score": p.get("conviction_score"),
            }
        )
    with open(backup_path, "w") as f:
        json.dump(backup_records, f, indent=2)
    log.info(f"Backed up {len(backup_records)} positions")

    # Rescore loop
    rescore_timestamp = datetime.now(timezone.utc).isoformat()
    rescored = 0
    skipped_no_features = 0
    errors = 0
    old_scores: list[float] = []
    new_scores: list[float] = []
    start_time = time.time()

    for idx, pos in enumerate(all_positions):
        if idx % 500 == 0 and idx > 0:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed else 0
            eta = (len(all_positions) - idx) / rate if rate > 0 else 0
            log.info(
                f"  {idx}/{len(all_positions)} "
                f"(rescored={rescored}, skipped={skipped_no_features}, errors={errors}) "
                f"{rate:.1f}/s, ETA {eta/60:.1f} min"
            )

        try:
            p = decimal_to_python(pos)
            eval_id = str(p.get("evaluation_id", ""))
            if not eval_id:
                errors += 1
                continue

            # Load FeatureSet from FVT
            feature_set = load_feature_set_for_eval(fvt_table, eval_id)
            if feature_set is None:
                skipped_no_features += 1
                continue

            # Compute new scores
            scores = score_one_position(calculator, pillar_config, pos, feature_set)

            # Track distribution for summary
            old_conv = p.get("conviction_score")
            if old_conv is not None:
                try:
                    old_scores.append(float(old_conv))
                except (TypeError, ValueError):
                    pass
            new_scores.append(scores["conviction_score"])

            # Build DynamoDB keys
            status = str(p.get("status"))
            pk = f"POS#{status}"
            sk = f"{p.get('entry_date')}#{p.get('position_id')}"

            # Update (atomic SET new + REMOVE old)
            update_position_scores(
                dynamodb_client,
                positions_table_name,
                pk,
                sk,
                scores,
                rescore_timestamp,
                dry_run=args.dry_run,
            )
            rescored += 1

        except Exception as e:
            errors += 1
            if errors < 10:
                log.warning(f"Rescore error for position {idx}: {e}")

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info("RESCORE COMPLETE")
    log.info("=" * 60)
    log.info(f"Total positions:    {len(all_positions)}")
    log.info(f"Rescored:           {rescored}")
    log.info(f"Skipped (no FVT):   {skipped_no_features}")
    log.info(f"Errors:             {errors}")
    log.info(f"Backup:             {backup_path}")
    log.info(f"Elapsed:            {elapsed/60:.1f} min")

    if old_scores and new_scores:
        log.info("")
        log.info("SCORE DISTRIBUTION COMPARISON")
        log.info(
            f"  Old conviction: mean={statistics.mean(old_scores):.2f} "
            f"median={statistics.median(old_scores):.2f} "
            f"stdev={statistics.stdev(old_scores):.2f}"
        )
        log.info(
            f"  New conviction: mean={statistics.mean(new_scores):.2f} "
            f"median={statistics.median(new_scores):.2f} "
            f"stdev={statistics.stdev(new_scores):.2f}"
        )

    if args.dry_run:
        log.info("(dry-run — no writes performed)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rescore all paper positions with Policy v3.0.0 pillars"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without writing to DynamoDB (backup still written)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of positions (for testing)",
    )
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
