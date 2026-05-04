"""Premium Leverage (PL) pillar — reconstructed from the legacy v5 pillar.

Direction-agnostic 0-100 score that measures how cheap and asymmetric an
option contract is. Recovered from the legacy
``backend/app/pillars/premium_leverage.py`` (commit 8a3dda4^) using the
Policy v3.0.0 breakpoints from ``backend/scripts/output/policy_v3_default.json``.

Walk-forward analysis on 3 months of paper trades showed PL ≥ 85 alone
produces ~60% win rate / 19.6% big-rate (≥100% MFE) / +36% avg PnL — the
strongest single signal among all factors tested. Combined with 5-day
momentum and UV scanner alignment it lifts to ~65-70% win rate / +50%+
avg PnL stably across regime halves.

Pure function. No I/O, no DynamoDB, no config indirection — the formula is
intentionally locked here. Future tuning happens by editing constants.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Breakpoint:
    value: float
    score: float


# ---------------------------------------------------------------------------
# Policy v3.0.0 breakpoints (locked).
# Source: scripts/output/policy_v3_default.json @ commit 8a3dda4^
# ---------------------------------------------------------------------------

_IV_BREAKPOINTS: tuple[_Breakpoint, ...] = (
    _Breakpoint(0.1340, 67.9),
    _Breakpoint(0.2972, 90.0),
    _Breakpoint(0.3618, 86.1),
    _Breakpoint(0.4560, 73.1),
    _Breakpoint(1.0237, 10.0),
)

_ABS_DELTA_BREAKPOINTS: tuple[_Breakpoint, ...] = (
    _Breakpoint(0.1701, 10.0),
    _Breakpoint(0.3782, 55.5),
    _Breakpoint(0.4617, 71.1),
    _Breakpoint(0.5544, 36.8),
    _Breakpoint(0.8058, 90.0),
)

_IV_PERCENTILE_BREAKPOINTS: tuple[_Breakpoint, ...] = (
    _Breakpoint(5.555, 90.0),
    _Breakpoint(21.18, 47.6),
    _Breakpoint(40.25, 10.0),
    _Breakpoint(59.24, 33.5),
    _Breakpoint(77.115, 35.8),
)

_IV_RV_RATIO_BREAKPOINTS: tuple[_Breakpoint, ...] = (
    _Breakpoint(0.3678, 63.9),
    _Breakpoint(0.7961, 34.0),
    _Breakpoint(0.9141, 90.0),
    _Breakpoint(1.0418, 10.0),
    _Breakpoint(3.7591, 10.8),
)

_IV_WEIGHT = 0.5161
_ABS_DELTA_WEIGHT = 0.2748
_IV_PERCENTILE_WEIGHT = 0.1449
_IV_RV_RATIO_WEIGHT = 0.0642


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_pl_score(
    *,
    iv: float | None,
    abs_delta: float | None,
    iv_percentile: float | None = None,
    iv_rv_ratio: float | None = None,
) -> tuple[float, dict[str, float | None]]:
    """Compute the PL pillar score (0-100) and per-feature subscores.

    Returns a tuple ``(pl_score, subscores)``. ``subscores`` always carries
    all four feature names; values are ``None`` for features that were not
    supplied (their weight is redistributed proportionally across the
    available features).

    Returns ``(0.0, {...})`` if neither ``iv`` nor ``abs_delta`` is provided
    — these two together carry ~79% of the formula weight, so without them
    a meaningful PL cannot be computed.
    """
    iv_sub = _score_breakpoints(iv, _IV_BREAKPOINTS)
    delta_sub = _score_breakpoints(abs_delta, _ABS_DELTA_BREAKPOINTS)
    ivp_sub = _score_breakpoints(iv_percentile, _IV_PERCENTILE_BREAKPOINTS)
    ivrv_sub = _score_breakpoints(iv_rv_ratio, _IV_RV_RATIO_BREAKPOINTS)

    subscores: dict[str, float | None] = {
        "iv": iv_sub,
        "abs_delta": delta_sub,
        "iv_percentile": ivp_sub,
        "iv_rv_ratio": ivrv_sub,
    }

    weighted = [
        (iv_sub, _IV_WEIGHT),
        (delta_sub, _ABS_DELTA_WEIGHT),
        (ivp_sub, _IV_PERCENTILE_WEIGHT),
        (ivrv_sub, _IV_RV_RATIO_WEIGHT),
    ]
    total_weight = sum(w for s, w in weighted if s is not None)
    if total_weight <= 0.0:
        return 0.0, subscores

    weighted_sum = sum(s * w for s, w in weighted if s is not None)
    pl = weighted_sum / total_weight
    return _clamp(pl), subscores


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _score_breakpoints(
    value: float | None, bps: tuple[_Breakpoint, ...]
) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # bps are defined sorted by value at module load.
    if v <= bps[0].value:
        return _clamp(bps[0].score)
    if v >= bps[-1].value:
        return _clamp(bps[-1].score)
    for i in range(len(bps) - 1):
        b0, b1 = bps[i], bps[i + 1]
        if b0.value <= v <= b1.value:
            span = b1.value - b0.value
            if span < 1e-15:
                return _clamp(b0.score)
            t = (v - b0.value) / span
            return _clamp(b0.score + t * (b1.score - b0.score))
    return 50.0  # unreachable for sorted breakpoints


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, float(score)))
