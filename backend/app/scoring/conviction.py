"""Backend conviction score calculator.

Exact replica of frontend/src/lib/convictionScore.ts.
Ensures parity between frontend display and backend alert filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Default weights — must match frontend DEFAULT_WEIGHTS exactly
DEFAULT_WEIGHTS = {
    "theta_adjusted_ev": 0.40,
    "composite_pillar": 0.25,
    "gate_margin": 0.15,
    "scanner_convergence": 0.10,
    "time_sensitivity": 0.10,
}

# Theta-adjusted EV is per-contract dollars over a 5-day hold.
# $15 maps a strong EV to 100%.
DEFAULT_EV_BENCHMARK = 15.0

# Scanner urgency mapping — matches frontend URGENCY_BOOST
URGENCY_BOOST: dict[str, int] = {
    "act_now": 100,
    "hours": 50,
    "patient": 0,
}

# Scanner convergence bonus — matches frontend CONVERGENCE_BONUS
CONVERGENCE_BONUS: dict[int, int] = {
    1: 0,
    2: 50,
    3: 75,
    4: 100,
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


def get_convergence_bonus(convergence_count: int) -> int:
    """Bonus score for scanner convergence (1→0, 2→50, 3→75, 4+→100)."""
    if convergence_count >= 4:
        return 100
    return CONVERGENCE_BONUS.get(convergence_count, 0)


def get_time_sensitivity_boost(urgency: str) -> int:
    """Boost score based on urgency level."""
    return URGENCY_BOOST.get(urgency, 0)


def determine_urgency(scanner_types: list[str]) -> str:
    """Determine urgency level from scanner types.

    BREAKOUT/BREAKDOWN → act_now, UNUSUAL_VOLUME → hours, else → patient.
    """
    for s in scanner_types:
        if s in ("BREAKOUT", "BREAKDOWN"):
            return "act_now"
    for s in scanner_types:
        if s == "UNUSUAL_VOLUME":
            return "hours"
    return "patient"


def _round1(value: float) -> float:
    """Round to 1 decimal place — matches frontend Math.round(x * 10) / 10."""
    return round(value * 10) / 10


def calculate_conviction_score(
    theta_adj_ev: float,
    pillar_scores: dict[str, float],
    gate_margin: float,
    scanner_types: list[str],
    *,
    weights: dict[str, float] | None = None,
    ev_benchmark: float = DEFAULT_EV_BENCHMARK,
) -> ConvictionBreakdown:
    """Calculate conviction score with full breakdown.

    Args:
        theta_adj_ev: Theta-adjusted EV in dollars (per-contract, 5-day hold)
        pillar_scores: Dict with DIRECTIONAL, VOLATILITY, STRUCTURE (0-100)
        gate_margin: Minimum gate margin across passed gates (0-100)
        scanner_types: List of scanner type strings that fired
        weights: Override default component weights
        ev_benchmark: EV normalization benchmark (default $15)

    Returns:
        ConvictionBreakdown with total score and per-component details
    """
    w = weights or DEFAULT_WEIGHTS

    # 1. Theta-Adjusted EV (normalized)
    ev_raw = theta_adj_ev or 0.0
    ev_normalized = normalize_ev(ev_raw, ev_benchmark)
    ev_weighted = ev_normalized * w["theta_adjusted_ev"]

    # 2. Composite Pillar Score
    pillar_raw = calculate_composite_pillar(pillar_scores or {})
    pillar_normalized = pillar_raw  # Already 0-100
    pillar_weighted = pillar_normalized * w["composite_pillar"]

    # 3. Gate Margin Score
    margin_raw = gate_margin if gate_margin is not None else 50.0
    margin_normalized = max(0.0, min(100.0, margin_raw))
    margin_weighted = margin_normalized * w["gate_margin"]

    # 4. Scanner Convergence Bonus
    convergence_count = len(scanner_types) if scanner_types else 1
    convergence_raw = get_convergence_bonus(convergence_count)
    convergence_normalized = float(convergence_raw)
    convergence_weighted = convergence_normalized * w["scanner_convergence"]

    # 5. Time Sensitivity Boost
    urgency = determine_urgency(scanner_types or [])
    time_raw = get_time_sensitivity_boost(urgency)
    time_normalized = float(time_raw)
    time_weighted = time_normalized * w["time_sensitivity"]

    # Calculate total
    total = ev_weighted + pillar_weighted + margin_weighted + convergence_weighted + time_weighted

    return ConvictionBreakdown(
        total=_round1(total),
        components={
            "theta_adjusted_ev": ScoreComponent(
                raw=ev_raw,
                normalized=_round1(ev_normalized),
                weighted=_round1(ev_weighted),
            ),
            "composite_pillar": ScoreComponent(
                raw=pillar_raw,
                normalized=_round1(pillar_normalized),
                weighted=_round1(pillar_weighted),
            ),
            "gate_margin": ScoreComponent(
                raw=margin_raw,
                normalized=_round1(margin_normalized),
                weighted=_round1(margin_weighted),
            ),
            "scanner_convergence": ScoreComponent(
                raw=float(convergence_count),
                normalized=convergence_normalized,
                weighted=_round1(convergence_weighted),
            ),
            "time_sensitivity": ScoreComponent(
                raw=float(time_raw),
                normalized=time_normalized,
                weighted=_round1(time_weighted),
            ),
        },
    )
