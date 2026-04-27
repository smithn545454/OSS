"""Convex Mode — Stage 2: Catalyst Layer.

Identifies which kinetically-eligible names have something pending. Stage 2
PASSES if any of four detectors fires:

    2A. Date-known catalyst within the configured window (earnings, FDA
        PDUFA, investor day, macro event for index proxies).
    2B. State-based compression — at least N of 5 signals firing
        (Bollinger Band Width percentile, ATR contraction, range
        compression, distance-to-level, volume contraction).
    2C. Unusual Volume — today's options volume materially exceeds the
        trailing 30-day average for the underlying. Captures directional
        skew (call-heavy vs put-heavy) for Stage 4 confirmation.
    2D. Sympathy — a peer in the same sector reported earnings within the
        last 5 trading days with a >5% reaction.

Strength is the **max** across the four detectors (one strong catalyst is
sufficient — stacking is not required). Strength formulas live in the
source plan §6:
    - date_known: 1.0 within 14d, scaling linearly to 0.5 at 30d
    - compression: 0.4 + 0.15 × (signals - 2), capped at 1.0
    - uv: 0.6 + 0.1 × (magnitude - threshold), capped at 1.0
    - sympathy: 0.5 fixed
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.core.schemas import (
    CatalystCalendarEntry,
    CatalystEventType,
    ConvexConfig,
    ConvexStagePayload,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection records
# ---------------------------------------------------------------------------


@dataclass
class DateKnownDetection:
    detected: bool
    # Stored as the Enum's *value* (string) because OSSBaseModel sets
    # ``use_enum_values=True``, so CatalystCalendarEntry.event_type
    # round-trips as a str. Avoids brittle ``.value`` accesses downstream.
    event_type: Optional[str] = None
    event_date: Optional[str] = None
    days_to_event: Optional[int] = None
    strength: float = 0.0


@dataclass
class CompressionDetection:
    detected: bool
    active_signals: list[str] = field(default_factory=list)
    inactive_signals: list[str] = field(default_factory=list)
    details: dict[str, float] = field(default_factory=dict)
    strength: float = 0.0


@dataclass
class UVDetection:
    detected: bool
    magnitude: Optional[float] = None  # today_volume / avg_volume_30d
    directional_skew: Optional[str] = None  # "call_heavy" | "put_heavy" | "balanced"
    strength: float = 0.0


@dataclass
class SympathyDetection:
    detected: bool
    peer_ticker: Optional[str] = None
    peer_move_pct: Optional[float] = None
    days_since_peer_event: Optional[int] = None
    strength: float = 0.0


# ---------------------------------------------------------------------------
# 2A — Date-known catalyst detector
# ---------------------------------------------------------------------------


def detect_date_known_catalyst(
    calendar_entries: Sequence[CatalystCalendarEntry],
    today_iso: str,
    config: ConvexConfig,
) -> DateKnownDetection:
    """Pick the soonest catalyst within the configured event window.

    Args:
        calendar_entries: Catalyst entries already filtered to the
            ticker (or "MACRO" for index/ETF proxies).
        today_iso: Pipeline as-of date (YYYY-MM-DD).
        config: Convex config with ``catalyst_event_window_min_days`` and
            ``catalyst_event_window_max_days`` thresholds.
    """
    from datetime import date

    today = date.fromisoformat(today_iso)
    best: Optional[CatalystCalendarEntry] = None
    best_days: Optional[int] = None

    for entry in calendar_entries:
        try:
            event_date = date.fromisoformat(entry.event_date)
        except ValueError:
            continue
        days = (event_date - today).days
        if (
            days < config.catalyst_event_window_min_days
            or days > config.catalyst_event_window_max_days
        ):
            continue
        if best_days is None or days < best_days:
            best = entry
            best_days = days

    if best is None or best_days is None:
        return DateKnownDetection(detected=False)

    strength = _date_known_strength(best_days, config)
    raw_event_type = best.event_type
    event_type_str = (
        raw_event_type.value
        if isinstance(raw_event_type, CatalystEventType)
        else str(raw_event_type)
    )
    return DateKnownDetection(
        detected=True,
        event_type=event_type_str,
        event_date=best.event_date,
        days_to_event=best_days,
        strength=strength,
    )


def _date_known_strength(days_to_event: int, config: ConvexConfig) -> float:
    """1.0 within 14 days; linear decay to 0.5 at the window's upper edge."""
    if days_to_event <= 14:
        return 1.0
    upper = config.catalyst_event_window_max_days
    if days_to_event >= upper:
        return 0.5
    # Linear from (14 → 1.0) to (upper → 0.5).
    span = upper - 14
    if span <= 0:
        return 1.0
    fraction = (days_to_event - 14) / span
    return max(0.5, 1.0 - 0.5 * fraction)


# ---------------------------------------------------------------------------
# 2B — State-based compression detector
# ---------------------------------------------------------------------------


COMPRESSION_SIGNAL_NAMES = (
    "bbw_compression",
    "atr_contraction",
    "range_compression",
    "breakout_proximity",
    "volume_contraction",
)


def _percentile_rank(value: float, sample: Sequence[float]) -> Optional[float]:
    """Percentile rank of ``value`` in ``sample`` (0-100). None if empty."""
    if not sample:
        return None
    below = sum(1 for s in sample if s < value)
    return (below / len(sample)) * 100.0


def _bollinger_band_width_series(closes: Sequence[float], window: int = 20) -> list[float]:
    """Bollinger Band Width = (upper - lower) / middle for each rolling window."""
    if len(closes) < window:
        return []
    widths: list[float] = []
    for i in range(window - 1, len(closes)):
        chunk = closes[i - window + 1: i + 1]
        mean = sum(chunk) / len(chunk)
        variance = sum((x - mean) ** 2 for x in chunk) / len(chunk)
        std = math.sqrt(variance)
        if mean == 0:
            continue
        width = (4 * std) / mean  # 2σ above + 2σ below = 4σ
        widths.append(width)
    return widths


def _atr_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int
) -> list[float]:
    """Simple moving-average ATR over ``period`` days."""
    if len(highs) != len(lows) or len(lows) != len(closes):
        return []
    if len(closes) < period + 1:
        return []
    true_ranges: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    atrs: list[float] = []
    for i in range(period - 1, len(true_ranges)):
        window = true_ranges[i - period + 1: i + 1]
        atrs.append(sum(window) / period)
    return atrs


def detect_compression_signals(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    nearest_significant_level_pct: Optional[float],
    config: ConvexConfig,
) -> CompressionDetection:
    """Run the five compression signals on the trailing 252 bars.

    ``nearest_significant_level_pct`` is the absolute percentage distance
    from current price to the closest significant level (52-week high,
    6-month high, or multi-touch resistance). When None the
    breakout-proximity signal is treated as inactive (no data).
    """
    active: list[str] = []
    details: dict[str, float] = {}

    # --- bbw_compression ---
    bbw_series = _bollinger_band_width_series(closes, window=20)
    if bbw_series:
        current_bbw = bbw_series[-1]
        pct = _percentile_rank(current_bbw, bbw_series)
        if pct is not None:
            details["bbw_percentile"] = round(pct, 2)
            if pct < config.catalyst_compression_bbw_percentile_max:
                active.append("bbw_compression")

    # --- atr_contraction ---
    atr14 = _atr_series(highs, lows, closes, 14)
    atr60 = _atr_series(highs, lows, closes, 60)
    if atr14 and atr60 and atr60[-1] > 0:
        ratio = atr14[-1] / atr60[-1]
        details["atr_ratio_14_60"] = round(ratio, 4)
        if ratio < config.catalyst_compression_atr_ratio_max:
            active.append("atr_contraction")

    # --- range_compression ---
    if len(highs) >= 252 and len(lows) >= 252 and len(closes) >= 252:
        # Use the full year of bars to compute 20-day high-low range as % of
        # current price for each rolling window; current value's percentile
        # rank determines the signal.
        recent_ranges: list[float] = []
        for i in range(19, len(closes)):
            window_high = max(highs[i - 19: i + 1])
            window_low = min(lows[i - 19: i + 1])
            denom = closes[i]
            if denom > 0:
                recent_ranges.append((window_high - window_low) / denom * 100.0)
        if recent_ranges:
            current_range = recent_ranges[-1]
            pct = _percentile_rank(current_range, recent_ranges)
            if pct is not None:
                details["range_pct_percentile"] = round(pct, 2)
                if pct < config.catalyst_compression_range_percentile_max:
                    active.append("range_compression")

    # --- breakout_proximity ---
    if nearest_significant_level_pct is not None:
        details["distance_to_level_pct"] = round(nearest_significant_level_pct, 2)
        if (
            nearest_significant_level_pct
            <= config.catalyst_compression_breakout_proximity_pct
        ):
            active.append("breakout_proximity")

    # --- volume_contraction ---
    if len(volumes) >= 90:
        avg20 = sum(volumes[-20:]) / 20
        avg90 = sum(volumes[-90:]) / 90
        if avg90 > 0:
            ratio = avg20 / avg90
            details["volume_ratio_20_90"] = round(ratio, 4)
            if ratio < config.catalyst_compression_volume_ratio_max:
                active.append("volume_contraction")

    inactive = [n for n in COMPRESSION_SIGNAL_NAMES if n not in active]
    detected = len(active) >= config.catalyst_compression_signals_required
    strength = _compression_strength(len(active), config) if detected else 0.0

    return CompressionDetection(
        detected=detected,
        active_signals=active,
        inactive_signals=inactive,
        details=details,
        strength=strength,
    )


def _compression_strength(signal_count: int, config: ConvexConfig) -> float:
    """0.4 at the configured floor, +0.15 per additional signal, capped at 1.0."""
    floor = config.catalyst_compression_signals_required
    bonus = max(0, signal_count - floor) * 0.15
    return min(1.0, 0.4 + bonus)


# ---------------------------------------------------------------------------
# 2C — Unusual Volume detector (ticker-level proxy)
# ---------------------------------------------------------------------------


def detect_unusual_volume(
    today_total_volume: Optional[float],
    avg_volume_30d: Optional[float],
    today_call_volume: Optional[float],
    today_put_volume: Optional[float],
    config: ConvexConfig,
) -> UVDetection:
    """Per-underlying UV detection: today's chain volume vs trailing 30d avg.

    Captures directional skew via call-vs-put volume ratio so Stage 4 can
    flag Smart Money Confirmation when skew aligns with the chosen thesis.

    A simplified replacement for the full per-contract UV scanner. The
    legacy UV Lambda is paused at cutover; Convex relies on this proxy
    plus the date-known + compression detectors to surface candidates.
    """
    if (
        today_total_volume is None
        or avg_volume_30d is None
        or avg_volume_30d <= 0
    ):
        return UVDetection(detected=False)

    magnitude = today_total_volume / avg_volume_30d
    if magnitude < config.catalyst_uv_volume_multiplier:
        return UVDetection(
            detected=False,
            magnitude=magnitude,
        )

    skew = _classify_uv_skew(today_call_volume, today_put_volume)
    strength = _uv_strength(magnitude, config)

    return UVDetection(
        detected=True,
        magnitude=magnitude,
        directional_skew=skew,
        strength=strength,
    )


def _classify_uv_skew(
    call_volume: Optional[float],
    put_volume: Optional[float],
) -> str:
    if call_volume is None or put_volume is None:
        return "balanced"
    total = call_volume + put_volume
    if total <= 0:
        return "balanced"
    call_share = call_volume / total
    if call_share >= 0.65:
        return "call_heavy"
    if call_share <= 0.35:
        return "put_heavy"
    return "balanced"


def _uv_strength(magnitude: float, config: ConvexConfig) -> float:
    """0.6 floor at the multiplier threshold; +0.1 per additional N×; cap 1.0."""
    threshold = config.catalyst_uv_volume_multiplier
    if magnitude < threshold:
        return 0.0
    bonus = (magnitude - threshold) * 0.1
    return min(1.0, 0.6 + bonus)


# ---------------------------------------------------------------------------
# 2D — Sympathy detector
# ---------------------------------------------------------------------------


@dataclass
class PeerEarningsReaction:
    """Earnings event for a sector-peer used in sympathy detection."""

    ticker: str
    event_date: str
    days_ago: int
    move_pct: float  # 1-day post-event % move


def detect_sympathy(
    candidate_sector: Optional[str],
    candidate_ticker: str,
    peer_reactions: Sequence[PeerEarningsReaction],
    config: ConvexConfig,
) -> SympathyDetection:
    """Flag sympathy when a sector peer reported with a >5% reaction recently.

    Caller is responsible for filtering peer reactions to the candidate's
    sector and to the trailing ``catalyst_sympathy_lookback_days`` window.
    """
    if not candidate_sector:
        return SympathyDetection(detected=False)

    threshold = config.catalyst_sympathy_peer_move_threshold_pct
    eligible = [
        r for r in peer_reactions
        if (
            r.ticker != candidate_ticker
            and r.days_ago <= config.catalyst_sympathy_lookback_days
            and abs(r.move_pct) >= threshold
        )
    ]
    if not eligible:
        return SympathyDetection(detected=False)

    # Pick the most recent peer move; ties broken by largest magnitude.
    eligible.sort(key=lambda r: (r.days_ago, -abs(r.move_pct)))
    best = eligible[0]
    return SympathyDetection(
        detected=True,
        peer_ticker=best.ticker,
        peer_move_pct=best.move_pct,
        days_since_peer_event=best.days_ago,
        strength=0.5,
    )


# ---------------------------------------------------------------------------
# Integrator
# ---------------------------------------------------------------------------


@dataclass
class Stage2Inputs:
    """Per-ticker pre-computed inputs for Stage 2 evaluation."""

    ticker: str
    sector: Optional[str]
    closes: Sequence[float]
    highs: Sequence[float]
    lows: Sequence[float]
    volumes: Sequence[float]
    nearest_significant_level_pct: Optional[float]
    calendar_entries: Sequence[CatalystCalendarEntry]
    today_total_options_volume: Optional[float]
    avg_options_volume_30d: Optional[float]
    today_call_options_volume: Optional[float]
    today_put_options_volume: Optional[float]
    peer_reactions: Sequence[PeerEarningsReaction]


def evaluate_stage2(
    inputs: Stage2Inputs,
    today_iso: str,
    config: ConvexConfig,
) -> tuple[ConvexStagePayload, dict[str, object]]:
    """Run all four detectors and return a Stage 2 payload + raw detections.

    The detections dict is returned alongside the payload so Stage 4 can
    consult UV directional skew when flagging Smart Money Confirmation.
    """
    date_known = detect_date_known_catalyst(
        inputs.calendar_entries, today_iso, config
    )
    compression = detect_compression_signals(
        inputs.closes,
        inputs.highs,
        inputs.lows,
        inputs.volumes,
        inputs.nearest_significant_level_pct,
        config,
    )
    uv = detect_unusual_volume(
        inputs.today_total_options_volume,
        inputs.avg_options_volume_30d,
        inputs.today_call_options_volume,
        inputs.today_put_options_volume,
        config,
    )
    sympathy = detect_sympathy(
        inputs.sector, inputs.ticker, inputs.peer_reactions, config
    )

    detected_any = any(
        (date_known.detected, compression.detected, uv.detected, sympathy.detected)
    )

    selected_strength = max(
        date_known.strength,
        compression.strength,
        uv.strength,
        sympathy.strength,
    )
    selected_type = _pick_strongest_type(
        date_known, compression, uv, sympathy
    )

    summary = _build_summary(
        inputs.ticker, detected_any, date_known, compression, uv, sympathy
    )

    payload = ConvexStagePayload(
        stage=2,
        stage_name="Catalyst Layer",
        result="PASS" if detected_any else "FAIL",
        summary=summary,
        criteria={
            "date_known": _date_known_dict(date_known),
            "state_based": _compression_dict(compression),
            "unusual_volume": _uv_dict(uv),
            "sympathy": _sympathy_dict(sympathy),
        },
        strength=selected_strength if detected_any else 0.0,
        extras={
            "selected_catalyst_type": selected_type,
            "selected_catalyst_strength": selected_strength,
        },
    )
    detections = {
        "date_known": date_known,
        "compression": compression,
        "unusual_volume": uv,
        "sympathy": sympathy,
    }
    return payload, detections


def _pick_strongest_type(
    date_known: DateKnownDetection,
    compression: CompressionDetection,
    uv: UVDetection,
    sympathy: SympathyDetection,
) -> Optional[str]:
    candidates = [
        ("date_known", date_known.strength, date_known.detected),
        ("compression", compression.strength, compression.detected),
        ("unusual_volume", uv.strength, uv.detected),
        ("sympathy", sympathy.strength, sympathy.detected),
    ]
    detected = [c for c in candidates if c[2]]
    if not detected:
        return None
    detected.sort(key=lambda c: c[1], reverse=True)
    return detected[0][0]


def _build_summary(
    ticker: str,
    detected_any: bool,
    date_known: DateKnownDetection,
    compression: CompressionDetection,
    uv: UVDetection,
    sympathy: SympathyDetection,
) -> str:
    if not detected_any:
        return (
            f"{ticker}: no catalyst within window — no date-known event, "
            "no compression signature, no unusual volume, no sympathy."
        )
    parts: list[str] = []
    if date_known.detected and date_known.event_type:
        parts.append(
            f"{date_known.event_type.lower()} in "
            f"{date_known.days_to_event} days"
        )
    if compression.detected:
        parts.append(
            f"state-based compression ({len(compression.active_signals)} of "
            f"{len(COMPRESSION_SIGNAL_NAMES)} signals firing)"
        )
    if uv.detected and uv.magnitude is not None:
        parts.append(
            f"unusual volume ({uv.magnitude:.1f}× avg, {uv.directional_skew})"
        )
    if sympathy.detected and sympathy.peer_ticker:
        parts.append(
            f"sympathy from {sympathy.peer_ticker} "
            f"({sympathy.peer_move_pct:+.1f}%)"
        )
    return f"{ticker}: " + "; ".join(parts) + "."


def _date_known_dict(d: DateKnownDetection) -> dict[str, object]:
    return {
        "detected": d.detected,
        "event_type": d.event_type,
        "event_date": d.event_date,
        "days_to_event": d.days_to_event,
        "strength": d.strength,
    }


def _compression_dict(d: CompressionDetection) -> dict[str, object]:
    return {
        "detected": d.detected,
        "active_signals": list(d.active_signals),
        "inactive_signals": list(d.inactive_signals),
        "details": dict(d.details),
        "strength": d.strength,
    }


def _uv_dict(d: UVDetection) -> dict[str, object]:
    return {
        "detected": d.detected,
        "magnitude": d.magnitude,
        "directional_skew": d.directional_skew,
        "strength": d.strength,
    }


def _sympathy_dict(d: SympathyDetection) -> dict[str, object]:
    return {
        "detected": d.detected,
        "peer_ticker": d.peer_ticker,
        "peer_move_pct": d.peer_move_pct,
        "days_since_peer_event": d.days_since_peer_event,
        "strength": d.strength,
    }
