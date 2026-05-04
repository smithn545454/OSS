"""Convex Mode — Stage 2: Catalyst + Momentum + Direction.

Identifies which kinetically-eligible names have something pending AND
resolves the trade direction. Stage 2 PASSES when both conditions hold:

    1. A catalyst fires (date-known OR compression OR sympathy).
    2. Direction resolves to bullish or bearish (not ambiguous).

Detectors:

    2A. Date-known catalyst within the configured window (earnings, FDA
        PDUFA, investor day, macro event for index proxies).
    2B. State-based compression — at least N of 5 signals firing.
    2C. Sympathy — a peer in the same sector reported earnings within the
        last 5 trading days with a >5% reaction.
    2D. Momentum — 5-day return on the underlying. Direction-bearing
        signal: positive momentum → bullish thesis; negative → bearish.
        |return| ≥ momentum_threshold_pct contributes to Tier A/B
        eligibility (the "aligned" flag), but momentum is NOT required for
        Stage 2 to PASS — it only adds direction-confirmation strength.

Direction is resolved from the (momentum × UV-skew × dir-strict) tuple:
    - When UV skew aligns with momentum direction → that direction.
    - Single-sided signal → that direction.
    - Conflict → ambiguous (Stage 2 fails).

UV is a ticker-level proxy — it remains computed for telemetry and to
contribute to direction resolution; the *production* UV scanner GSI is
consulted later (in tier mapping) for Tier A confirmation.

Strength is the **max** across the catalyst detectors plus a momentum
boost when |5d return| ≥ threshold:
    - date_known: 1.0 within 14d, scaling linearly to 0.5 at 30d
    - compression: 0.4 + 0.15 × (signals - 2), capped at 1.0
    - sympathy: 0.5 fixed
    - momentum: included as max(catalyst_strength, |return_pct| / 10),
      capped 1.0
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


@dataclass
class MomentumDetection:
    """5-day-return signal. ``aligned`` is set after direction resolution."""

    return_5d_pct: Optional[float] = None
    direction: str = "none"  # "bullish" | "bearish" | "none"
    magnitude_pct: float = 0.0  # absolute value of return_5d_pct
    above_threshold: bool = False  # |return| >= momentum_threshold_pct
    aligned: bool = False  # set later: True if direction matches resolved
    strength: float = 0.0  # 0..1, magnitude / 10 capped at 1.0


def detect_momentum_signal(
    closes: Sequence[float],
    config: ConvexConfig,
) -> MomentumDetection:
    """5-day return on the underlying. Direction-bearing per sign of return.

    Uses the trailing 6 closes already supplied to Stage 2 — same series
    the compression detectors consume, so no new fetch is required.
    """
    if len(closes) < 6:
        return MomentumDetection()
    try:
        prior = float(closes[-6])
        latest = float(closes[-1])
    except (TypeError, ValueError):
        return MomentumDetection()
    if prior <= 0:
        return MomentumDetection()

    pct = (latest / prior - 1.0) * 100.0
    direction = "bullish" if pct > 0 else ("bearish" if pct < 0 else "none")
    magnitude = abs(pct)
    above = magnitude >= config.momentum_threshold_pct
    strength = min(1.0, magnitude / 10.0)
    return MomentumDetection(
        return_5d_pct=round(pct, 4),
        direction=direction,
        magnitude_pct=round(magnitude, 4),
        above_threshold=above,
        strength=round(strength, 4),
    )


def resolve_direction(
    momentum: MomentumDetection, uv: "UVDetection"
) -> str:
    """Resolve trade direction from momentum + UV skew.

    Returns:
        "bullish" | "bearish" | "ambiguous"
    """
    momentum_dir = momentum.direction if momentum.above_threshold else "none"
    uv_dir = "none"
    if uv.detected:
        if uv.directional_skew == "call_heavy":
            uv_dir = "bullish"
        elif uv.directional_skew == "put_heavy":
            uv_dir = "bearish"

    # Both fire: must agree.
    if momentum_dir != "none" and uv_dir != "none":
        return momentum_dir if momentum_dir == uv_dir else "ambiguous"
    # Only momentum fires.
    if momentum_dir != "none":
        return momentum_dir
    # Only UV fires.
    if uv_dir != "none":
        return uv_dir
    # Neither: ambiguous (Stage 2 will fail).
    return "ambiguous"


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
    """Run catalyst + momentum detectors, resolve direction, return payload.

    Stage 2 fires on three CATALYST signals — the reasons something might
    explode in the near future:
        - date_known: a scheduled event (earnings, FDA, FOMC) ahead
        - compression: coiled-spring price pattern
        - sympathy: a sector peer just reacted with a meaningful move

    PLUS a directional signal:
        - momentum: 5-day return; sign indicates direction, magnitude
          determines whether the candidate is "momentum-aligned" for tier
          A/B eligibility.

    Stage 2 PASSES when (catalyst fires) AND (direction is non-ambiguous).
    Direction is resolved from momentum + UV skew (see ``resolve_direction``).

    Unusual volume is computed here as a ticker-level proxy that informs
    direction resolution; the production UV scanner GSI is consulted later
    in tier mapping for Tier A's "UV detected" requirement.

    The detections dict is returned alongside the payload so downstream
    stages can consult catalyst context, momentum, and resolved direction.
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
    sympathy = detect_sympathy(
        inputs.sector, inputs.ticker, inputs.peer_reactions, config
    )
    momentum = detect_momentum_signal(inputs.closes, config)
    uv = detect_unusual_volume(
        inputs.today_total_options_volume,
        inputs.avg_options_volume_30d,
        inputs.today_call_options_volume,
        inputs.today_put_options_volume,
        config,
    )

    direction = resolve_direction(momentum, uv)
    momentum.aligned = momentum.above_threshold and momentum.direction == direction

    catalyst_detected = any(
        (date_known.detected, compression.detected, sympathy.detected)
    )
    direction_resolved = direction in ("bullish", "bearish")
    detected = catalyst_detected and direction_resolved

    catalyst_strength = max(
        date_known.strength,
        compression.strength,
        sympathy.strength,
    )
    composite_strength = (
        max(catalyst_strength, momentum.strength) if detected else 0.0
    )
    selected_type = _pick_strongest_type(date_known, compression, uv, sympathy)

    summary = _build_summary(
        inputs.ticker,
        detected,
        catalyst_detected,
        direction,
        date_known,
        compression,
        uv,
        sympathy,
        momentum,
    )

    payload = ConvexStagePayload(
        stage=2,
        stage_name="Catalyst + Direction",
        result="PASS" if detected else "FAIL",
        summary=summary,
        criteria={
            "date_known": _date_known_dict(date_known),
            "state_based": _compression_dict(compression),
            "sympathy": _sympathy_dict(sympathy),
            "momentum": _momentum_dict(momentum),
            "unusual_volume": _uv_dict(uv),
        },
        strength=composite_strength,
        extras={
            "selected_catalyst_type": selected_type,
            "selected_catalyst_strength": catalyst_strength,
            "direction": direction,
            "momentum_aligned": momentum.aligned,
            "momentum_return_5d_pct": momentum.return_5d_pct,
        },
    )
    detections = {
        "date_known": date_known,
        "compression": compression,
        "sympathy": sympathy,
        "unusual_volume": uv,
        "momentum": momentum,
        "direction": direction,
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
    detected: bool,
    catalyst_detected: bool,
    direction: str,
    date_known: DateKnownDetection,
    compression: CompressionDetection,
    uv: UVDetection,
    sympathy: SympathyDetection,
    momentum: MomentumDetection,
) -> str:
    if not detected:
        if not catalyst_detected:
            return (
                f"{ticker}: no catalyst within window — no date-known event, "
                "no compression signature, no sympathy."
            )
        # Catalyst fired but direction unresolved.
        return (
            f"{ticker}: catalyst present but direction is ambiguous "
            f"(momentum={momentum.return_5d_pct}, uv_skew={uv.directional_skew})."
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
    if momentum.return_5d_pct is not None:
        aligned_str = " aligned" if momentum.aligned else ""
        parts.append(
            f"5d return {momentum.return_5d_pct:+.1f}%{aligned_str}"
        )
    return f"{ticker} ({direction}): " + "; ".join(parts) + "."


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


def _momentum_dict(d: MomentumDetection) -> dict[str, object]:
    return {
        "return_5d_pct": d.return_5d_pct,
        "direction": d.direction,
        "magnitude_pct": d.magnitude_pct,
        "above_threshold": d.above_threshold,
        "aligned": d.aligned,
        "strength": d.strength,
    }
