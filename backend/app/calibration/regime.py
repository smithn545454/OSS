"""Regime alignment multiplier for v5 dual-conviction scoring.

Applies a market-regime correction to conviction scores. The intuition:

- Bullish, calm regimes favor CALL archetypes (long delta benefits from
  sustained drift; low IV makes premium cheap).
- Bearish, fearful regimes favor PUT archetypes (downside trends + IV
  expansion make puts cheaper to hold and richer at exit).
- Choppy / mixed regimes favor neither — multiplier slides toward 1.0
  or below.

The multiplier is sign-aware: an archetype's `option_type` matters.
Same regime can favor calls and disfavor puts.

This is intentionally a coarse heuristic for v5.0.0 launch. Phase 8 (the
auto-discovery system) can later replace this with a learned regime score
based on observed archetype performance per regime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RegimeState:
    """Snapshot of market regime at decision time."""

    spy_return_20d_pct: Optional[float] = None     # SPY 20-trading-day total return %
    vix_level: Optional[float] = None              # VIX spot level
    spy_return_5d_pct: Optional[float] = None      # Short-term momentum (optional)


def compute_regime_alignment(
    regime: RegimeState,
    *,
    option_type: Optional[str] = None,
    multiplier_min: float = 0.5,
    multiplier_max: float = 1.5,
) -> float:
    """Map market regime + option type to a conviction multiplier.

    Args:
        regime: Current market state (SPY 20d return, VIX level).
        option_type: ``"CALL"`` or ``"PUT"`` (or None for direction-agnostic).
        multiplier_min: Floor on returned multiplier. Default 0.5.
        multiplier_max: Ceiling on returned multiplier. Default 1.5.

    Returns:
        Multiplier in [multiplier_min, multiplier_max]. Returns 1.0 when
        regime data is missing (fail-open — don't penalize for unknown
        regime).

    The four regime archetypes used:

    - **Bullish calm** (SPY 20d > +5%, VIX < 18): CALL × 1.3, PUT × 0.7
    - **Bearish fear** (SPY 20d < -5%, VIX > 25): CALL × 0.7, PUT × 1.3
    - **Chop** (|SPY 20d| < 2%, VIX 20-25): both × 0.85 (worst regime for
      directional bets)
    - **Mid** (everything else): both × 1.0

    Examples:
        >>> # Bullish calm CALL
        >>> r = RegimeState(spy_return_20d_pct=6.0, vix_level=15.0)
        >>> round(compute_regime_alignment(r, option_type="CALL"), 2)
        1.3
        >>> round(compute_regime_alignment(r, option_type="PUT"), 2)
        0.7

        >>> # Chop — disfavors both
        >>> r = RegimeState(spy_return_20d_pct=0.5, vix_level=22.0)
        >>> round(compute_regime_alignment(r, option_type="CALL"), 2)
        0.85
        >>> round(compute_regime_alignment(r, option_type="PUT"), 2)
        0.85

        >>> # Missing data → neutral 1.0 (fail-open)
        >>> compute_regime_alignment(RegimeState(), option_type="CALL")
        1.0
    """
    spy = regime.spy_return_20d_pct
    vix = regime.vix_level
    if spy is None or vix is None:
        return 1.0

    # Identify regime
    is_bullish_calm = spy > 5.0 and vix < 18.0
    is_bearish_fear = spy < -5.0 and vix > 25.0
    is_chop = abs(spy) < 2.0 and 20.0 <= vix <= 25.0

    if is_bullish_calm:
        base = 1.3 if option_type == "CALL" else (0.7 if option_type == "PUT" else 1.0)
    elif is_bearish_fear:
        base = 1.3 if option_type == "PUT" else (0.7 if option_type == "CALL" else 1.0)
    elif is_chop:
        base = 0.85
    else:
        base = 1.0

    return max(multiplier_min, min(multiplier_max, base))
