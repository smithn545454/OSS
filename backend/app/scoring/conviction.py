"""Backend conviction score calculator.

Exact replica of frontend/src/lib/convictionScore.ts.
Ensures parity between frontend display and backend alert filtering.

3-component formula:
  conviction = 0.35 × EV_norm + 0.30 × Return%_norm + 0.35 × Pillar_avg

Gate Margin and Scanner Convergence are excluded — gates are binary pass/fail,
and convergence is too rare to be useful for ranking. Urgency is excluded —
the time-sensitivity of cheap options is already captured by Return%.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Default weights — must match frontend DEFAULT_WEIGHTS exactly
DEFAULT_WEIGHTS = {
    "theta_adjusted_ev": 0.35,
    "return_pct": 0.30,
    "composite_pillar": 0.35,
}

# Theta-adjusted EV is per-contract dollars over a 5-day hold.
# $15 maps a strong EV to 100%.
DEFAULT_EV_BENCHMARK = 15.0

# Return% benchmark: 20% return on contract cost maps to a normalized score of 100.
# Cheap options with high return% score comparably to expensive options with high absolute EV.
DEFAULT_RETURN_PCT_BENCHMARK = 20.0

# Premium threshold for UNUSUAL_VOLUME urgency escalation in alerts.
# UV opportunities on cheap options (mid <= $1.50) are effectively "act now".
CHEAP_UV_PREMIUM_THRESHOLD = 1.50

# Freshness decay: hours until score reaches MIN_DECAY floor.
# 24 market-hours means end-of-next-day evals hit the floor.
FRESHNESS_DECAY_CONSTANT = 24.0

# Floor multiplier — stale evals retain at least 60% of base score.
FRESHNESS_MIN_DECAY = 0.6

# Scanner urgency mapping — used by alert service for urgency display
URGENCY_BOOST: dict[str, int] = {
    "act_now": 100,
    "hours": 50,
    "patient": 0,
}


@dataclass
class ScoreComponent:
    """Single component of the conviction score breakdown."""
    raw: float
    normalized: float
    weighted: float


@dataclass
class ConvictionBreakdown:
    """Full conviction score breakdown with all components."""
    total: float
    components: dict[str, ScoreComponent] = field(default_factory=dict)


def normalize_ev(ev: float, benchmark: float = DEFAULT_EV_BENCHMARK) -> float:
    """Normalize theta-adjusted EV to 0-100 scale.

    EV <= 0 maps to 0. EV > 0 maps linearly, capped at 100.
    """
    if ev <= 0:
        return 0.0
    return min(100.0, (ev / benchmark) * 100.0)


def calculate_composite_pillar(pillar_scores: dict[str, float]) -> float:
    """Average of DIRECTIONAL, VOLATILITY, STRUCTURE pillar scores."""
    directional = pillar_scores.get("DIRECTIONAL", 0.0)
    volatility = pillar_scores.get("VOLATILITY", 0.0)
    structure = pillar_scores.get("STRUCTURE", 0.0)
    return (directional + volatility + structure) / 3.0


def normalize_return_pct(
    theta_adj_ev: float, mid: float, benchmark: float = DEFAULT_RETURN_PCT_BENCHMARK
) -> float:
    """Normalize return% to 0-100 scale.

    Return% = (theta_adj_ev / (mid * 100)) * 100.
    Normalized: min(100, return_pct / benchmark * 100), clamped at 0.
    """
    if mid <= 0 or theta_adj_ev <= 0:
        return 0.0
    return_pct = (theta_adj_ev / (mid * 100)) * 100
    return min(100.0, (return_pct / benchmark) * 100.0)


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
    """Linear decay multiplier based on evaluation age.

    Returns value in [FRESHNESS_MIN_DECAY, 1.0].
    <1h: ~96-100%, 4h: ~83%, 8h: ~67%, 16h+: 60% floor.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    evaluated = datetime.fromisoformat(evaluated_at)
    if evaluated.tzinfo is None:
        evaluated = evaluated.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - evaluated).total_seconds() / 3600.0)
    return max(FRESHNESS_MIN_DECAY, 1.0 - age_hours / FRESHNESS_DECAY_CONSTANT)


def _round1(value: float) -> float:
    """Round to 1 decimal place — matches frontend Math.round(x * 10) / 10."""
    return round(value * 10) / 10


def calculate_conviction_score(
    theta_adj_ev: float,
    pillar_scores: dict[str, float],
    gate_margin: float,
    scanner_types: list[str],
    *,
    mid: float = 0.0,
    weights: dict[str, float] | None = None,
    ev_benchmark: float = DEFAULT_EV_BENCHMARK,
    return_pct_benchmark: float = DEFAULT_RETURN_PCT_BENCHMARK,
) -> ConvictionBreakdown:
    """Calculate conviction score with full breakdown.

    3-component formula: EV (35%) + Return% (30%) + Pillars (35%).
    gate_margin and scanner_types are accepted for API compatibility
    but not used in the score calculation.

    Args:
        theta_adj_ev: Theta-adjusted EV in dollars (per-contract, 5-day hold)
        pillar_scores: Dict with DIRECTIONAL, VOLATILITY, STRUCTURE (0-100)
        gate_margin: Accepted for compatibility, not used in score
        scanner_types: Accepted for compatibility, not used in score
        mid: Option premium (mid price) for return% calculation
        weights: Override default component weights
        ev_benchmark: EV normalization benchmark (default $15)
        return_pct_benchmark: Return% normalization benchmark (default 20%)

    Returns:
        ConvictionBreakdown with total score and per-component details
    """
    w = weights or DEFAULT_WEIGHTS

    # 1. Theta-Adjusted EV (normalized)
    ev_raw = theta_adj_ev or 0.0
    ev_normalized = normalize_ev(ev_raw, ev_benchmark)
    ev_weighted = ev_normalized * w["theta_adjusted_ev"]

    # 2. Return% (capital efficiency — cheap options with high return% score well)
    ret_raw = (ev_raw / (mid * 100)) * 100 if mid > 0 and ev_raw > 0 else 0.0
    ret_normalized = normalize_return_pct(ev_raw, mid, return_pct_benchmark)
    ret_weighted = ret_normalized * w["return_pct"]

    # 3. Composite Pillar Score
    pillar_raw = calculate_composite_pillar(pillar_scores or {})
    pillar_normalized = pillar_raw  # Already 0-100
    pillar_weighted = pillar_normalized * w["composite_pillar"]

    # Calculate total
    total = ev_weighted + ret_weighted + pillar_weighted

    return ConvictionBreakdown(
        total=_round1(total),
        components={
            "theta_adjusted_ev": ScoreComponent(
                raw=ev_raw,
                normalized=_round1(ev_normalized),
                weighted=_round1(ev_weighted),
            ),
            "return_pct": ScoreComponent(
                raw=_round1(ret_raw),
                normalized=_round1(ret_normalized),
                weighted=_round1(ret_weighted),
            ),
            "composite_pillar": ScoreComponent(
                raw=pillar_raw,
                normalized=_round1(pillar_normalized),
                weighted=_round1(pillar_weighted),
            ),
        },
    )
