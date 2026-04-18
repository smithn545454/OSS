#!/usr/bin/env python3
"""Rescore all paper positions with Pillar v4 (active policy).

For each position this script:
  1. Loads the evaluation's FeatureSet from FeatureValueTable.
  2. Reconstructs the v4-only features from price-history + earnings as
     of the position's entry_date (ma_150/200, high/low_52w,
     bb_width_percentile, sector_rs_20d, historical_move_magnitude).
  3. Builds a v4-aware ScoringContext.
  4. Runs the active (v4.0.0) PillarCalculator to get directional /
     move-potential / trade-structure pillar scores.
  5. Computes the weighted-geometric-mean composite.
  6. Writes the new v4 fields alongside (not replacing) the v3 fields.

Non-destructive: v3 pillar fields are preserved; the previous
``conviction_score`` is archived as ``conviction_score_v3``.

Usage:
    AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
    python3 scripts/rescore_all_positions_v4.py [--dry-run] [--limit N]
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schemas import FeatureValue  # noqa: E402
from app.db.tables import (  # noqa: E402
    EarningsHistoryTable,
    PolicyTable,
    PriceHistoryTable,
    SP500TickerTable,
)
from app.features.models import FeatureSet  # noqa: E402
from app.features.relative_strength import (  # noqa: E402
    SECTOR_ETF_MAP,
    compute_sector_relative_strength_20d,
    sector_etf_for,
)
from app.features.underlying import compute_underlying_features  # noqa: E402
from app.pillars.calculator import (  # noqa: E402
    PillarCalculator,
    compute_final_score_from_results,
)
from app.pillars.models import ScoringContext  # noqa: E402
from app.scanners.utils import calculate_returns  # noqa: E402
from app.services.polygon import DailyBar  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ETFs we need bars for: SPY + every sector ETF referenced by SECTOR_ETF_MAP.
ETF_UNIVERSE = sorted({"SPY"} | set(SECTOR_ETF_MAP.values()))


# ============================================================================
# Decimal / FVT helpers (mirrors the v3 rescore script)
# ============================================================================


def decimal_to_python(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: decimal_to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimal_to_python(i) for i in obj]
    return obj


def to_ddb_value(v: Any) -> dict[str, Any]:
    if v is None:
        return {"NULL": True}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float, Decimal)):
        return {"N": str(v)}
    return {"S": str(v)}


def load_feature_set_for_eval(
    fvt_table: Any, evaluation_id: str
) -> Optional[FeatureSet]:
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
        except Exception:
            continue
    try:
        return FeatureSet.from_feature_values(evaluation_id, feature_values)
    except Exception as e:
        log.debug(f"FeatureSet reconstruction failed for {evaluation_id}: {e}")
        return None


# ============================================================================
# Price-history cache (async, per-ticker, full 280-day blob)
# ============================================================================


class EvaluationCache:
    """Bulk-loads all evaluations for a ticker in one DDB query, then
    serves lookups by evaluation_id. Avoids the O(pagination) cost of
    calling ``EvaluationTable.get_by_id`` per position.
    """

    def __init__(self, table: Any) -> None:
        self._table = table
        self._by_ticker: dict[str, dict[str, dict[str, Any]]] = {}

    def get(self, ticker: str, evaluation_id: str) -> Optional[dict[str, Any]]:
        if ticker not in self._by_ticker:
            self._load_ticker(ticker)
        return self._by_ticker[ticker].get(evaluation_id)

    def _load_ticker(self, ticker: str) -> None:
        idx: dict[str, dict[str, Any]] = {}
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"EVAL#{ticker}"),
            "ScanIndexForward": False,
        }
        while True:
            resp = self._table.query(**kwargs)
            for item in resp.get("Items", []):
                eid = item.get("evaluation_id")
                if eid:
                    idx[str(eid)] = decimal_to_python(item)
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        self._by_ticker[ticker] = idx


class PriceHistoryCache:
    """Lazily loads the full available bar history for each ticker.

    Holds at most ``max_tickers`` tickers in memory with a simple LRU
    eviction; re-loads from DynamoDB on subsequent access. For the
    Russell 1000 universe this keeps memory bounded while still
    amortizing per-ticker DDB cost across the 1-20 positions a ticker
    typically has.
    """

    def __init__(self, max_tickers: int = 1200) -> None:
        self._cache: dict[str, list[DailyBar]] = {}
        self._max = max_tickers

    async def get(self, ticker: str) -> list[DailyBar]:
        bars = self._cache.get(ticker)
        if bars is not None:
            return bars
        records = await PriceHistoryTable.list_by_ticker(
            ticker, limit=300, scan_forward=True
        )
        bars = [
            DailyBar(
                ticker=r.ticker,
                date=r.date,
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=int(r.volume),
                vwap=float(r.vwap) if r.vwap is not None else None,
            )
            for r in records
        ]
        if len(self._cache) >= self._max:
            # Evict oldest inserted
            evict = next(iter(self._cache))
            del self._cache[evict]
        self._cache[ticker] = bars
        return bars


def slice_bars_up_to(
    bars: list[DailyBar], as_of: str, limit: int = 252
) -> list[DailyBar]:
    """Return at most ``limit`` bars with ``date <= as_of``, oldest first."""
    filtered = [b for b in bars if b.date <= as_of]
    return filtered[-limit:] if len(filtered) > limit else filtered


# ============================================================================
# v4 feature reconstruction
# ============================================================================


async def compute_v4_features_at_entry(
    ticker: str,
    entry_date: str,
    price_cache: PriceHistoryCache,
    etf_bars: dict[str, list[DailyBar]],
    sector_map: dict[str, str],
    earnings_cache: dict[str, list[Any]],
) -> dict[str, Optional[float]]:
    """Compute the v4-specific features for a (ticker, entry_date).

    Returns a dict with keys: ma_150, ma_200, high_52w, low_52w,
    bb_width_percentile, sma50, sector_rs_20d, historical_move_magnitude,
    historical_move_confidence, close.
    """
    out: dict[str, Optional[float]] = {
        "ma_150": None,
        "ma_200": None,
        "high_52w": None,
        "low_52w": None,
        "bb_width_percentile": None,
        "sma50": None,
        "close": None,
        "sector_rs_20d": None,
        "historical_move_magnitude": None,
        "historical_move_confidence": None,
        "adx_14": None,
        "plus_di": None,
        "minus_di": None,
        "obv_trend": None,
        "ema_9": None,
        "ema_21": None,
        "ema_50": None,
        "ema_200": None,
    }

    # Underlying features from ticker's own bars
    bars = slice_bars_up_to(await price_cache.get(ticker), entry_date, limit=252)
    if bars:
        underlying = compute_underlying_features(bars)
        if underlying is not None:
            out["ma_150"] = underlying.ma_150
            out["ma_200"] = underlying.ma_200
            out["high_52w"] = underlying.high_52w
            out["low_52w"] = underlying.low_52w
            out["bb_width_percentile"] = underlying.bb_width_percentile
            out["sma50"] = underlying.sma50
            out["close"] = bars[-1].close
            out["adx_14"] = underlying.adx_14
            out["plus_di"] = underlying.plus_di
            out["minus_di"] = underlying.minus_di
            out["obv_trend"] = underlying.obv_trend
            out["ema_9"] = underlying.ema_9
            out["ema_21"] = underlying.ema_21
            out["ema_50"] = underlying.ema_50
            out["ema_200"] = underlying.ema_200
        # Fallback: price-history was backfilled ~252 days from present, so
        # positions older than ~10 days have < 252 bars available at entry.
        # Use the longest-available window (>= 150 bars) so breakout-proximity
        # and 52w-low subscores still compute.
        if out["high_52w"] is None and len(bars) >= 150:
            closes = [b.close for b in bars]
            out["high_52w"] = max(closes)
            out["low_52w"] = min(closes)

    # Sector relative strength
    spy_bars = slice_bars_up_to(etf_bars.get("SPY", []), entry_date, limit=40)
    spy_ret_20d: Optional[float] = None
    if len(spy_bars) >= 21:
        spy_ret_20d = calculate_returns([b.close for b in spy_bars], 20)
    sector = sector_map.get(ticker)
    etf = sector_etf_for(sector) if sector else None
    if etf and spy_ret_20d is not None:
        etf_series = slice_bars_up_to(etf_bars.get(etf, []), entry_date, limit=40)
        if len(etf_series) >= 21:
            out["sector_rs_20d"] = compute_sector_relative_strength_20d(
                etf_series, spy_ret_20d
            )

    # Historical move magnitude — average |1-day move| over last 4 earnings
    # events that occurred BEFORE entry_date. Cached per ticker.
    if ticker not in earnings_cache:
        try:
            earnings_cache[ticker] = await EarningsHistoryTable.list_by_ticker(
                ticker, limit=20
            )
        except Exception:
            earnings_cache[ticker] = []
    all_events = earnings_cache[ticker]
    past_moves = [
        abs(e.one_day_move_pct)
        for e in all_events
        if e.one_day_move_pct is not None and e.earnings_date < entry_date
    ]
    past_moves = past_moves[:4]  # list_by_ticker is descending — take most recent 4
    if past_moves:
        out["historical_move_magnitude"] = sum(past_moves) / len(past_moves)
        out["historical_move_confidence"] = len(past_moves)

    return out


# ============================================================================
# ScoringContext construction
# ============================================================================


def build_scoring_context(
    position: dict[str, Any],
    evaluation: Optional[dict[str, Any]],
    feature_set: Optional[FeatureSet],
    v4_feats: dict[str, Optional[float]],
) -> ScoringContext:
    p = decimal_to_python(position)
    e = decimal_to_python(evaluation) if evaluation else {}
    scanner_list = p.get("scanner_list") or []
    convergence_count = int(p.get("convergence_count") or 0)

    def fs(attr: str, default: Any = None) -> Any:
        return getattr(feature_set, attr, default) if feature_set else default

    def pref(*vals: Any) -> Any:
        """Return first value that isn't None."""
        for v in vals:
            if v is not None:
                return v
        return None

    # Contract-level values prefer the evaluation record (has greeks + strike)
    # and fall back to the position denormalized fields.
    close = pref(v4_feats["close"], fs("close"), e.get("underlying_price"),
                 p.get("entry_underlying_price"), 0.0)
    delta = float(pref(e.get("delta"), p.get("entry_delta"), 0.0))
    gamma = e.get("gamma")
    theta = pref(e.get("theta"), p.get("entry_theta"))
    vega = e.get("vega")
    strike = e.get("strike")
    dte = int(pref(e.get("dte"), p.get("dte_at_entry"), 0))

    # 52w distance percentages
    dist_high = dist_low = None
    if v4_feats["high_52w"] and close:
        dist_high = (close - v4_feats["high_52w"]) / v4_feats["high_52w"] * 100
    if v4_feats["low_52w"] and close:
        dist_low = (close - v4_feats["low_52w"]) / v4_feats["low_52w"] * 100

    return ScoringContext(
        evaluation_id=str(p.get("evaluation_id", "")),
        underlying_ticker=str(p.get("underlying_ticker", "")),
        option_type=str(pref(e.get("option_type"), p.get("option_type"), "CALL")),
        dte_bucket=str(pref(e.get("dte_bucket"), p.get("dte_bucket"), "B")),
        scanner_triggers=[str(s) for s in scanner_list],
        direction_hint="NONE",
        convergence_count=convergence_count,
        close=float(close),
        sma20=fs("sma20"),
        sma50=pref(v4_feats["sma50"], fs("sma50")),
        atr14=fs("atr14"),
        atr14_pct=pref(fs("atr14_pct"), p.get("entry_atr14_pct")),
        return_5d=fs("return_5d"),
        return_20d=fs("return_20d"),
        trend_aligned_bullish=bool(fs("trend_aligned_bullish", False)),
        trend_aligned_bearish=bool(fs("trend_aligned_bearish", False)),
        rs_5d=fs("rs_5d"),
        rs_20d=pref(fs("rs_20d"), p.get("entry_rs_20d")),
        rv20=pref(fs("rv20"), p.get("entry_rv20")),
        iv=float(pref(e.get("iv"), fs("iv"), p.get("entry_iv"), 0.0)),
        iv_rv_ratio=pref(fs("iv_rv_ratio"), p.get("entry_iv_rv_ratio")),
        iv_percentile=pref(fs("iv_percentile"), p.get("entry_iv_percentile")),
        iv_regime=str(fs("iv_regime") or "IV_NEUTRAL_REGIME"),
        mid=float(pref(e.get("mid"), fs("mid"), p.get("entry_price"), 0.0)),
        spread_pct=float(pref(e.get("spread_pct"), fs("spread_pct"),
                              p.get("entry_spread_pct"), 0.0)),
        theta_pct=float(fs("theta_pct") or 0.0),
        theta_adjusted_edge=pref(fs("theta_adjusted_edge"),
                                 p.get("entry_theta_adjusted_edge")),
        required_move_pct=float(pref(e.get("required_move_pct"),
                                     fs("required_move_pct"), 0.0)),
        expected_move_pct=float(pref(e.get("expected_move_pct"),
                                     fs("expected_move_pct"), 0.0)),
        feasibility_ratio=float(pref(e.get("feasibility_ratio"),
                                     fs("feasibility_ratio"),
                                     p.get("entry_feasibility_ratio"), 0.0)),
        time_adjusted_feasibility=float(pref(e.get("time_adjusted_feasibility"),
                                             fs("time_adjusted_feasibility"), 0.0)),
        delta=delta,
        dte=dte,
        scanner_source=pref(
            e.get("scanner_source"),
            p.get("scanner_source"),
            scanner_list[0] if scanner_list else None,
        ),
        open_interest=int(pref(e.get("open_interest"), fs("open_interest"),
                               p.get("entry_open_interest"), 0)),
        volume=int(pref(e.get("volume"), fs("volume"),
                        p.get("entry_volume"), 0)),
        oi_5d_change_pct=pref(e.get("oi_5d_change_pct"), fs("oi_5d_change_pct")),
        # Catalyst
        days_to_earnings=pref(fs("days_to_earnings"), p.get("entry_days_to_earnings")),
        recent_sec_filing=bool(fs("recent_sec_filing", False)),
        # Technicals — prefer FVT, fall back to price-history-recomputed
        ema_9=pref(fs("ema_9"), v4_feats.get("ema_9")),
        ema_21=pref(fs("ema_21"), v4_feats.get("ema_21")),
        ema_50=pref(fs("ema_50"), v4_feats.get("ema_50")),
        ema_200=pref(fs("ema_200"), v4_feats.get("ema_200")),
        ema_alignment=fs("ema_alignment"),
        rsi_14=fs("rsi_14"),
        macd_histogram=fs("macd_histogram"),
        adx_14=pref(fs("adx_14"), p.get("entry_adx_14"), v4_feats.get("adx_14")),
        plus_di=pref(fs("plus_di"), p.get("entry_plus_di"), v4_feats.get("plus_di")),
        minus_di=pref(fs("minus_di"), p.get("entry_minus_di"), v4_feats.get("minus_di")),
        obv_trend=pref(fs("obv_trend"), v4_feats.get("obv_trend")),
        # v4 underlying (from price history at entry_date)
        ma_150=v4_feats["ma_150"],
        ma_200=v4_feats["ma_200"],
        high_52w=v4_feats["high_52w"],
        low_52w=v4_feats["low_52w"],
        dist_to_52w_high_pct=dist_high,
        dist_to_52w_low_pct=dist_low,
        bb_width=None,
        bb_width_percentile=v4_feats["bb_width_percentile"],
        sector=None,
        sector_rs_20d=v4_feats["sector_rs_20d"],
        historical_move_magnitude=v4_feats["historical_move_magnitude"],
        historical_move_confidence=v4_feats["historical_move_confidence"],
        # Option greeks — pulled from evaluation record
        gamma=gamma,
        theta=theta,
        vega=vega,
        strike=strike,
    )


# ============================================================================
# DynamoDB write (additive SET; preserves v3 fields)
# ============================================================================


def update_position_v4(
    dynamodb_client: Any,
    table_name: str,
    pk: str,
    sk: str,
    new_scores: dict[str, float],
    old_conviction: Optional[float],
    rescored_at: str,
    dry_run: bool = False,
) -> None:
    if dry_run:
        return

    expr_parts = [
        "#pdc = :pdc",
        "#pmp = :pmp",
        "#pts = :pts",
        "#cs = :cs",
        "#ra = :ra",
        "#lu = :lu",
        "#sr = :sr",
    ]
    names = {
        "#pdc": "pillar_directional_conviction",
        "#pmp": "pillar_move_potential",
        "#pts": "pillar_trade_structure",
        "#cs": "conviction_score",
        "#ra": "rescored_v4_at",
        "#lu": "last_updated",
        "#sr": "scoring_regime",
    }
    values: dict[str, Any] = {
        ":pdc": {"N": str(new_scores["pillar_directional_conviction"])},
        ":pmp": {"N": str(new_scores["pillar_move_potential"])},
        ":pts": {"N": str(new_scores["pillar_trade_structure"])},
        ":cs": {"N": str(new_scores["conviction_score"])},
        ":ra": {"S": rescored_at},
        ":lu": {"S": rescored_at},
        ":sr": {"S": "v4"},
    }

    # Only write conviction_score_v3 on the first v4 rescore so re-runs
    # don't clobber the original v3 value.
    if old_conviction is not None:
        expr_parts.append("#csv3 = if_not_exists(#csv3, :csv3)")
        names["#csv3"] = "conviction_score_v3"
        values[":csv3"] = {"N": str(float(old_conviction))}

    dynamodb_client.update_item(
        TableName=table_name,
        Key={"PK": {"S": pk}, "SK": {"S": sk}},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# ============================================================================
# Main
# ============================================================================


def query_all_positions(table: Any, pk: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        results.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return results


async def preload_etf_bars() -> dict[str, list[DailyBar]]:
    """Load the full available bar history for SPY + all sector ETFs."""
    out: dict[str, list[DailyBar]] = {}
    for etf in ETF_UNIVERSE:
        recs = await PriceHistoryTable.list_by_ticker(etf, limit=300, scan_forward=True)
        out[etf] = [
            DailyBar(
                ticker=r.ticker,
                date=r.date,
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=int(r.volume),
                vwap=float(r.vwap) if r.vwap is not None else None,
            )
            for r in recs
        ]
        log.info(f"  preloaded {etf}: {len(out[etf])} bars")
    return out


async def main_async(args: argparse.Namespace) -> None:
    log.info(f"Connecting to DynamoDB region={AWS_REGION} prefix={TABLE_PREFIX}")
    os.environ.setdefault("AWS_REGION", AWS_REGION)
    os.environ.setdefault("DYNAMODB_TABLE_PREFIX", TABLE_PREFIX)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
    positions_table = dynamodb.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dynamodb.Table(f"{TABLE_PREFIX}-feature-values")
    evaluations_table = dynamodb.Table(f"{TABLE_PREFIX}-evaluations")
    positions_table_name = f"{TABLE_PREFIX}-paper-positions"
    eval_cache = EvaluationCache(evaluations_table)

    # Active (v4) policy
    log.info("Loading active policy...")
    policy = await PolicyTable.get_active()
    if policy is None:
        log.error("No active policy found — aborting.")
        return
    pillar_config = policy.config.pillars
    log.info(
        f"  active: {policy.version} "
        f"(composite_formula={pillar_config.composite_formula})"
    )
    if pillar_config.composite_formula != "weighted_geometric_mean":
        log.warning(
            "Active policy is NOT v4 (composite_formula != weighted_geometric_mean). "
            "Proceeding anyway but results will not be v4 scores."
        )
    calculator = PillarCalculator(pillar_config)

    # Preload market ETFs + sector map
    log.info("Preloading SPY + sector ETF bars...")
    etf_bars = await preload_etf_bars()
    log.info("Loading sector map...")
    sector_map = await SP500TickerTable.get_sector_map()
    log.info(f"  sector map: {len(sector_map)} tickers")

    # Query positions
    log.info("Querying positions...")
    closed = query_all_positions(positions_table, "POS#CLOSED")
    open_ = query_all_positions(positions_table, "POS#OPEN")
    all_positions = closed + open_
    log.info(
        f"  closed={len(closed)} open={len(open_)} total={len(all_positions)}"
    )
    if args.sample:
        import random
        random.seed(42)
        all_positions = random.sample(
            all_positions, min(args.sample, len(all_positions))
        )
        log.info(f"  random sample: {len(all_positions)}")
    elif args.limit:
        all_positions = all_positions[: args.limit]
        log.info(f"  limited to {args.limit}")

    # Sort by ticker so the EvaluationCache and PriceHistoryCache get
    # maximum hit rates: each ticker's partition is loaded exactly once.
    all_positions.sort(key=lambda p: (str(p.get("underlying_ticker") or ""),
                                      str(p.get("entry_date") or "")))

    # Backup
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = OUTPUT_DIR / f"position_scores_backup_v4_{ts}.json"
    log.info(f"Backing up old scores to {backup_path}...")
    backup_records: list[dict[str, Any]] = []
    for pos in all_positions:
        p = decimal_to_python(pos)
        backup_records.append(
            {
                "position_id": p.get("position_id"),
                "evaluation_id": p.get("evaluation_id"),
                "ticker": p.get("underlying_ticker"),
                "entry_date": p.get("entry_date"),
                "status": str(p.get("status")),
                "old_conviction_score": p.get("conviction_score"),
                "old_pillar_premium_leverage": p.get("pillar_premium_leverage"),
                "old_pillar_underlying_behavior": p.get("pillar_underlying_behavior"),
                "old_pillar_setup_quality": p.get("pillar_setup_quality"),
            }
        )
    with open(backup_path, "w") as f:
        json.dump(backup_records, f, indent=2)
    log.info(f"  wrote {len(backup_records)} records")

    # Rescore
    price_cache = PriceHistoryCache(max_tickers=1200)
    earnings_cache: dict[str, list[Any]] = {}
    ts_iso = datetime.now(timezone.utc).isoformat()
    dry_run_rows: list[dict[str, Any]] = []
    rescored = errors = skipped = 0
    old_cs: list[float] = []
    new_cs: list[float] = []
    start = time.time()

    for idx, pos in enumerate(all_positions):
        if idx % 200 == 0 and idx > 0:
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed else 0
            eta = (len(all_positions) - idx) / rate if rate > 0 else 0
            tickers_loaded = len(eval_cache._by_ticker)
            log.info(
                f"  {idx}/{len(all_positions)} "
                f"(rescored={rescored} skipped={skipped} errors={errors} "
                f"tickers_cached={tickers_loaded}) "
                f"{rate:.1f}/s  ETA {eta/60:.1f}m"
            )

        try:
            p = decimal_to_python(pos)
            eval_id = str(p.get("evaluation_id") or "")
            ticker = str(p.get("underlying_ticker") or "")
            entry_date = str(p.get("entry_date") or "")
            if not eval_id or not ticker or not entry_date:
                errors += 1
                continue

            feature_set = load_feature_set_for_eval(fvt_table, eval_id)
            evaluation = eval_cache.get(ticker, eval_id)
            v4_feats = await compute_v4_features_at_entry(
                ticker, entry_date, price_cache, etf_bars, sector_map,
                earnings_cache,
            )
            ctx = build_scoring_context(p, evaluation, feature_set, v4_feats)

            results = calculator.compute_pillars(
                evaluation=None, feature_set=None, opportunity=None, context=ctx
            )
            if len(results) != 3:
                errors += 1
                if errors < 5:
                    log.warning(
                        f"Expected 3 pillars, got {len(results)} for {eval_id}"
                    )
                continue

            # Map results by pillar id
            score_by_id: dict[str, float] = {}
            for r in results:
                pid = str(r.pillar_id.value) if hasattr(r.pillar_id, "value") else str(r.pillar_id)
                score_by_id[pid] = round(r.score, 2)

            composite = compute_final_score_from_results(
                results, pillar_config, scanner_source=ctx.scanner_source
            )
            composite = round(composite, 2)

            new_scores = {
                "pillar_directional_conviction": score_by_id.get(
                    "DIRECTIONAL_CONVICTION", 0.0
                ),
                "pillar_move_potential": score_by_id.get("MOVE_POTENTIAL", 0.0),
                "pillar_trade_structure": score_by_id.get("TRADE_STRUCTURE", 0.0),
                "conviction_score": composite,
            }

            old_conv = p.get("conviction_score")
            try:
                old_val = float(old_conv) if old_conv is not None else None
            except (TypeError, ValueError):
                old_val = None
            if old_val is not None:
                old_cs.append(old_val)
            new_cs.append(composite)

            if args.dry_run:
                dry_run_rows.append(
                    {
                        "position_id": p.get("position_id"),
                        "ticker": ticker,
                        "entry_date": entry_date,
                        "scanner": ctx.scanner_source,
                        "old_conv": old_val,
                        **new_scores,
                        "delta_vs_old": (
                            round(composite - old_val, 2) if old_val is not None else None
                        ),
                    }
                )

            status = str(p.get("status"))
            pk = f"POS#{status}"
            sk = f"{p.get('entry_date')}#{p.get('position_id')}"
            update_position_v4(
                dynamodb_client,
                positions_table_name,
                pk,
                sk,
                new_scores,
                old_val,
                ts_iso,
                dry_run=args.dry_run,
            )
            rescored += 1
        except Exception as e:
            errors += 1
            if errors < 10:
                log.warning(f"Rescore error at {idx}: {e}", exc_info=True)

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info("RESCORE v4 COMPLETE")
    log.info("=" * 60)
    log.info(f"Total:     {len(all_positions)}")
    log.info(f"Rescored:  {rescored}")
    log.info(f"Skipped:   {skipped}")
    log.info(f"Errors:    {errors}")
    log.info(f"Backup:    {backup_path}")
    log.info(f"Elapsed:   {elapsed/60:.1f} min")

    if old_cs and new_cs:
        log.info("")
        log.info("CONVICTION DISTRIBUTION")
        log.info(
            f"  old (v3): mean={statistics.mean(old_cs):.2f} "
            f"median={statistics.median(old_cs):.2f} "
            f"stdev={statistics.stdev(old_cs):.2f} "
            f"min={min(old_cs):.2f} max={max(old_cs):.2f}"
        )
        log.info(
            f"  new (v4): mean={statistics.mean(new_cs):.2f} "
            f"median={statistics.median(new_cs):.2f} "
            f"stdev={statistics.stdev(new_cs):.2f} "
            f"min={min(new_cs):.2f} max={max(new_cs):.2f}"
        )
        # Tier distribution at v4 thresholds (92 / 82 / 72 / 62)
        tier1 = sum(1 for s in new_cs if s >= 92)
        tier2 = sum(1 for s in new_cs if 82 <= s < 92)
        tier3 = sum(1 for s in new_cs if 72 <= s < 82)
        watch = sum(1 for s in new_cs if 62 <= s < 72)
        reject = sum(1 for s in new_cs if s < 62)
        log.info(
            f"  v4 tiers: T1(>=92)={tier1} T2(82-91)={tier2} "
            f"T3(72-81)={tier3} WATCH(62-71)={watch} REJECT(<62)={reject}"
        )

    if args.dry_run:
        dry_path = OUTPUT_DIR / f"rescore_v4_dryrun_{ts}.json"
        with open(dry_path, "w") as f:
            json.dump(dry_run_rows, f, indent=2)
        log.info(f"Dry-run detail: {dry_path}")
        log.info("(dry-run — no writes performed)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rescore all paper positions with v4 pillars"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Randomly sample N positions across the full set (seed=42)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
