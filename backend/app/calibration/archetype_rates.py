"""Per-archetype rolling rate estimation for v5 dual-conviction scoring.

Estimates HR200 rate AND win rate for an archetype from closed paper
positions, with optional EWMA decay so that recent regime data carries
more weight. Returns Wilson confidence intervals so v5 conviction can
use the lower bound (penalizing small samples).

Phase 1 (this file): pure data-layer estimator. Takes a list of position
records and an archetype_id, computes bounds. No DynamoDB coupling yet —
that wires up in Phase 3 when the rate estimator joins the decision
pipeline.

The estimator does NOT do archetype matching itself. It expects each
position to already carry ``archetype_matched`` (or v5 ``hr_archetype_matched``
/ ``p_archetype_matched``) from the writer. This separation lets us
estimate rates independently of how matching is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.calibration.wilson import wilson_ci

HR200_THRESHOLD = 200.0  # MFE % threshold for grand slam
DEFAULT_WILSON_CONFIDENCE = 0.95


@dataclass(frozen=True)
class RateEstimate:
    """Bounds + sample size for a binomial rate (HR200 or win rate)."""

    point: float          # Point estimate, [0, 1]
    lower: float          # Wilson lower bound, [0, 1]
    upper: float          # Wilson upper bound, [0, 1]
    n_effective: int      # Effective sample size (raw n if no EWMA, else weighted)
    n_raw: int            # Total positions matched (pre-weighting)


@dataclass(frozen=True)
class ArchetypeRates:
    """Both HR200 and win-rate estimates for a single archetype."""

    archetype_id: str
    hr200: RateEstimate
    win_rate: RateEstimate
    mean_pnl_pct: float           # Plain mean (not Wilson-adjusted)
    median_pnl_pct: float


def filter_positions_for_archetype(
    positions: Iterable[dict[str, Any]],
    archetype_id: str,
    archetype_field: str = "archetype_matched",
) -> list[dict[str, Any]]:
    """Filter a position iterable down to rows matching a single archetype.

    ``archetype_field`` controls which field on the position to compare
    against ``archetype_id``. Defaults to the v4.1.0 ``archetype_matched``.
    Phase 3+ may pass ``hr_archetype_matched`` or ``p_archetype_matched``.
    """
    return [
        p for p in positions
        if str(p.get(archetype_field) or "") == archetype_id
    ]


def estimate_archetype_rates(
    positions: list[dict[str, Any]],
    *,
    archetype_id: str,
    confidence: float = DEFAULT_WILSON_CONFIDENCE,
    rolling_window_n: Optional[int] = None,
    ewma_half_life_n: Optional[int] = None,
) -> ArchetypeRates:
    """Compute Wilson-bound HR200 and win-rate estimates for an archetype.

    Args:
        positions: Pre-filtered closed paper positions matching this archetype.
            Each must expose ``max_favorable_excursion`` and ``current_pnl_pct``.
            Use :func:`filter_positions_for_archetype` to filter first.
        archetype_id: The archetype identifier (for the result label only).
        confidence: Wilson CI confidence level. Default 0.95.
        rolling_window_n: If set, only the most recent ``N`` positions are used.
            Positions must be sorted oldest→newest by the caller (typically
            entry_date). When None, the full population is used.
        ewma_half_life_n: If set, applies an exponential weighting where the
            weight of the trade k positions before the most recent is
            ``0.5 ** (k / half_life)``. ``n_effective`` becomes the sum of
            weights rather than the raw count. Combined with rolling_window_n,
            EWMA is applied within the window.

    Returns:
        :class:`ArchetypeRates` with both rate estimates plus mean/median P&L.
    """
    cohort = positions
    if rolling_window_n is not None and len(cohort) > rolling_window_n:
        cohort = cohort[-rolling_window_n:]

    # Build success/total counts for HR200 and win
    if ewma_half_life_n is not None and ewma_half_life_n > 0 and cohort:
        # Weighted: most recent position has weight 1.0, decay toward older
        weights = [
            0.5 ** ((len(cohort) - 1 - i) / ewma_half_life_n)
            for i in range(len(cohort))
        ]
        total_w = sum(weights)
        hr200_w = sum(
            w for p, w in zip(cohort, weights)
            if (p.get("max_favorable_excursion") or 0) >= HR200_THRESHOLD
        )
        wins_w = sum(
            w for p, w in zip(cohort, weights)
            if (p.get("current_pnl_pct") or 0) > 0
        )
        # Wilson needs integers — round weighted counts to nearest int but
        # use ceil(total_w) as effective n so we don't overstate certainty.
        n_eff = max(1, int(round(total_w)))
        hr200_count = max(0, min(n_eff, int(round(hr200_w * n_eff / total_w))))
        wins_count = max(0, min(n_eff, int(round(wins_w * n_eff / total_w))))
    else:
        n_eff = len(cohort)
        hr200_count = sum(
            1 for p in cohort
            if (p.get("max_favorable_excursion") or 0) >= HR200_THRESHOLD
        )
        wins_count = sum(
            1 for p in cohort
            if (p.get("current_pnl_pct") or 0) > 0
        )

    hr200_point, hr200_lower, hr200_upper = wilson_ci(hr200_count, n_eff, confidence)
    win_point, win_lower, win_upper = wilson_ci(wins_count, n_eff, confidence)

    pnls = [p["current_pnl_pct"] for p in cohort if p.get("current_pnl_pct") is not None]
    mean_pnl = sum(pnls) / len(pnls) if pnls else 0.0
    median_pnl = _median(pnls) if pnls else 0.0

    return ArchetypeRates(
        archetype_id=archetype_id,
        hr200=RateEstimate(
            point=hr200_point, lower=hr200_lower, upper=hr200_upper,
            n_effective=n_eff, n_raw=len(cohort),
        ),
        win_rate=RateEstimate(
            point=win_point, lower=win_lower, upper=win_upper,
            n_effective=n_eff, n_raw=len(cohort),
        ),
        mean_pnl_pct=mean_pnl,
        median_pnl_pct=median_pnl,
    )


def _median(values: list[float]) -> float:
    """Median without scipy. For odd n returns middle; for even n returns avg of middle two."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def normalize_pnl_pct(
    mean_pnl_pct: float,
    *,
    floor_pct: float = -50.0,
    ceiling_pct: float = 50.0,
) -> float:
    """Map mean P&L % into a [0, 2] multiplier for P-conviction.

    floor → 0, midpoint → 1, ceiling → 2. Linear in between, clamped outside.

    Examples:
        >>> normalize_pnl_pct(-50)
        0.0
        >>> normalize_pnl_pct(0)
        1.0
        >>> normalize_pnl_pct(50)
        2.0
        >>> normalize_pnl_pct(100)
        2.0
        >>> normalize_pnl_pct(-100)
        0.0
    """
    if ceiling_pct <= floor_pct:
        return 1.0  # Misconfigured — return neutral
    raw = (mean_pnl_pct - floor_pct) / (ceiling_pct - floor_pct) * 2.0
    return max(0.0, min(2.0, raw))
