"""Lookup helpers for the legacy UV scanner's per-contract detections.

Convex Stage 4 queries ``oss-dev-unusual-volume-candidates`` via the
``underlying-ticker-index`` GSI to answer "did the UV scanner flag any
contracts for this ticker today?" The legacy scanner runs every 15
minutes in market hours and writes one row per flagged contract with
``today_volume`` and ``avg_volume_20d``. We aggregate those rows into
a single ticker-level UV signal that the Convex Decision can carry.

This is a pure read path — Convex never writes to the UV scanner's
table. The legacy 8-stage pipeline that historically consumed these
detections is disabled (per policy.scanner.unusual_volume.enabled =
False), but the scanner itself runs purely as a data producer for
Convex's smart-money confirmation flag.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3

logger = logging.getLogger(__name__)


@dataclass
class UVSignal:
    """Aggregated UV signal for one ticker over a recent lookback window."""

    ticker: str
    detection_count: int          # Number of contracts flagged
    total_today_volume: float     # Sum of today's volume across flagged contracts
    total_avg_volume: float       # Sum of 20d-avg volume across flagged contracts
    volume_ratio: Optional[float] # total_today / total_avg (None if no baseline)
    call_volume: float            # Total today_volume on flagged calls
    put_volume: float             # Total today_volume on flagged puts
    directional_skew: str         # "call_heavy" | "put_heavy" | "balanced"
    is_unusual: bool              # True when ratio ≥ unusual_threshold
    lookback_hours: int

    def aligns_with(self, direction: str) -> bool:
        """True when directional skew agrees with the Stage 3 thesis direction."""
        if direction == "bullish":
            return self.directional_skew == "call_heavy"
        if direction == "bearish":
            return self.directional_skew == "put_heavy"
        return False  # ambiguous (straddle): not applicable


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


_TABLE_NAME_DEFAULT = "oss-dev-unusual-volume-candidates"
_INDEX_NAME = "underlying-ticker-index"


async def lookup_uv_signal(
    ticker: str,
    *,
    lookback_hours: int = 24,
    unusual_ratio_threshold: float = 3.0,
    skew_balanced_band: float = 0.40,  # ±40% of total → balanced
    table_name: str = _TABLE_NAME_DEFAULT,
    region: str = "us-west-1",
) -> Optional[UVSignal]:
    """Aggregate UV detections from the last N hours into a ticker signal.

    Args:
        ticker: Underlying symbol.
        lookback_hours: How far back to scan (default 24h — captures the
            last full trading day's UV scans).
        unusual_ratio_threshold: total_today / total_avg ≥ this means
            "unusual" (3× by default — matches the legacy scanner's
            default sensitivity).
        skew_balanced_band: When call/put share is within ±this of even,
            treat skew as ``balanced``. Default 40% → call_share between
            30% and 70% counts as balanced; outside is heavy.
        table_name / region: DynamoDB plumbing overrides for tests.

    Returns:
        ``UVSignal`` describing the aggregated signal, or ``None`` when
        the GSI query fails (table missing, GSI not yet active, etc.).
        Returns a zero-detection ``UVSignal`` when no rows match — the
        caller should treat ``detection_count == 0`` as "no UV today",
        not as a failure.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_iso = cutoff.isoformat()

    try:
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        items: list[dict] = []
        kwargs: dict = {
            "IndexName": _INDEX_NAME,
            "KeyConditionExpression": (
                "underlying_ticker = :t AND created_at >= :since"
            ),
            "ExpressionAttributeValues": {":t": ticker, ":since": cutoff_iso},
        }
        # Paginate — hot tickers (NVDA, TSLA) can have thousands of UV
        # detections per 24h across the scanner's 15-min cadence.
        while True:
            resp = table.query(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key or len(items) >= 5000:
                break
            kwargs["ExclusiveStartKey"] = last_key
    except Exception as e:
        logger.warning("UV GSI query failed for %s: %s", ticker, e)
        return None
    detection_count = len(items)
    if detection_count == 0:
        return UVSignal(
            ticker=ticker,
            detection_count=0,
            total_today_volume=0.0,
            total_avg_volume=0.0,
            volume_ratio=None,
            call_volume=0.0,
            put_volume=0.0,
            directional_skew="balanced",
            is_unusual=False,
            lookback_hours=lookback_hours,
        )

    total_today = 0.0
    total_avg = 0.0
    call_vol = 0.0
    put_vol = 0.0
    for item in items:
        today = float(item.get("today_volume") or 0)
        avg = float(item.get("avg_volume_20d") or 0)
        total_today += today
        total_avg += avg
        opt_type = (item.get("option_type") or "").upper()
        if opt_type == "CALL":
            call_vol += today
        elif opt_type == "PUT":
            put_vol += today

    ratio: Optional[float] = None
    if total_avg > 0:
        ratio = total_today / total_avg

    skew = _classify_skew(call_vol, put_vol, balanced_band=skew_balanced_band)
    is_unusual = ratio is not None and ratio >= unusual_ratio_threshold

    return UVSignal(
        ticker=ticker,
        detection_count=detection_count,
        total_today_volume=total_today,
        total_avg_volume=total_avg,
        volume_ratio=ratio,
        call_volume=call_vol,
        put_volume=put_vol,
        directional_skew=skew,
        is_unusual=is_unusual,
        lookback_hours=lookback_hours,
    )


def _classify_skew(
    call_volume: float, put_volume: float, balanced_band: float = 0.40
) -> str:
    """Classify directional skew from total flagged call vs put volume.

    Returns ``call_heavy`` / ``put_heavy`` / ``balanced``. The
    ``balanced_band`` parameter sets the call-share window around 0.5
    that counts as balanced (default 0.40 → 0.30-0.70 = balanced).
    """
    total = call_volume + put_volume
    if total <= 0:
        return "balanced"
    call_share = call_volume / total
    half_band = balanced_band / 2
    if call_share > 0.5 + half_band:
        return "call_heavy"
    if call_share < 0.5 - half_band:
        return "put_heavy"
    return "balanced"


def to_dict(signal: Optional[UVSignal]) -> Optional[dict]:
    """JSON-safe representation for the Decision payload."""
    if signal is None:
        return None
    return {
        "detection_count": signal.detection_count,
        "total_today_volume": round(signal.total_today_volume, 1),
        "total_avg_volume": round(signal.total_avg_volume, 1),
        "volume_ratio": (
            round(signal.volume_ratio, 2) if signal.volume_ratio is not None else None
        ),
        "call_volume": round(signal.call_volume, 1),
        "put_volume": round(signal.put_volume, 1),
        "directional_skew": signal.directional_skew,
        "is_unusual": signal.is_unusual,
        "lookback_hours": signal.lookback_hours,
    }
