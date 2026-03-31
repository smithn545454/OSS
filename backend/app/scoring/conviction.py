"""Backend conviction score calculator.

Mirrors frontend/src/lib/convictionScore.ts.
Conviction = pipeline final_score × freshness_decay.

Used by the alert service to score evaluations for alert filtering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Premium threshold for UNUSUAL_VOLUME urgency escalation in alerts.
# UV opportunities on cheap options (mid <= $1.50) are effectively "act now".
CHEAP_UV_PREMIUM_THRESHOLD = 1.50

# Freshness decay: grace period with no penalty, then linear decay.
# First 8 hours: no decay (covers a full trading day).
# After grace period: linear decay over 24 hours to floor.
FRESHNESS_GRACE_HOURS = 8.0

# Hours of linear decay after grace period expires.
FRESHNESS_DECAY_WINDOW = 24.0

# Floor multiplier — stale evals retain at least 75% of base score.
FRESHNESS_MIN_DECAY = 0.75

# Scanner urgency mapping — used by alert service for urgency display
URGENCY_BOOST: dict[str, int] = {
    "act_now": 100,
    "hours": 50,
    "patient": 0,
}


@dataclass
class ConvictionResult:
    """Conviction score result."""
    total: float


def determine_urgency(scanner_types: list[str], *, mid: float | None = None) -> str:
    """Determine urgency level from scanner types.

    Used by the alert service for urgency display/filtering — not part of conviction score.
    BREAKOUT/BREAKDOWN → act_now.
    UNUSUAL_VOLUME → act_now if mid <= $1.50 (cheap, fleeting), else hours.
    Otherwise → patient.
    """
    for s in scanner_types:
        if s in ("BREAKOUT", "BREAKDOWN"):
            return "act_now"
    for s in scanner_types:
        if s == "UNUSUAL_VOLUME":
            if mid is not None and mid <= CHEAP_UV_PREMIUM_THRESHOLD:
                return "act_now"
            return "hours"
    return "patient"


def calculate_freshness_decay(
    evaluated_at: str,
    *,
    now: datetime | None = None,
) -> float:
    """Grace-period decay multiplier based on evaluation age.

    Returns value in [FRESHNESS_MIN_DECAY, 1.0].
    0-8h: 100% (no decay), then linear decay over 24h to 75% floor.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    evaluated = datetime.fromisoformat(evaluated_at)
    if evaluated.tzinfo is None:
        evaluated = evaluated.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - evaluated).total_seconds() / 3600.0)
    if age_hours <= FRESHNESS_GRACE_HOURS:
        return 1.0
    decay_age = age_hours - FRESHNESS_GRACE_HOURS
    return max(FRESHNESS_MIN_DECAY, 1.0 - decay_age / FRESHNESS_DECAY_WINDOW)


def _round1(value: float) -> float:
    """Round to 1 decimal place — matches frontend Math.round(x * 10) / 10."""
    return round(value * 10) / 10


def calculate_conviction_score(
    final_score: float,
    evaluated_at: str,
) -> ConvictionResult:
    """Calculate conviction score from pipeline final_score with freshness decay.

    Matches frontend logic: conviction = final_score × freshness_decay.

    Args:
        final_score: Pipeline decision final_score (0-100)
        evaluated_at: ISO timestamp of when the evaluation was created

    Returns:
        ConvictionResult with total score
    """
    decay = calculate_freshness_decay(evaluated_at)
    total = _round1(final_score * decay)
    return ConvictionResult(total=total)
