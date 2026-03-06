"""Individual gate implementations for Stage 6: Hard Gates.

Per Section 15.2 of OSS_Complete_Requirements.md, these are binary pass/fail
checks. Any failed enabled gate results in REJECT regardless of scores.

Gate Implementations:
1. GATE_MIN_OPEN_INTEREST - Liquidity check (OI >= 300)
2. GATE_MIN_VOLUME - Activity check (volume >= 75)
3. GATE_MAX_SPREAD_PCT - Spread tightness (spread <= 8%)
4. GATE_DTE_RANGE - Time window (7 <= DTE <= 120)
5. GATE_MOVE_SUFFICIENCY - Achievable move (time_adj_feasibility <= 1.25)
6. GATE_IV_PERCENTILE_MAX - IV not too high (IV percentile <= 85)
7. GATE_BREAKOUT_VOLUME - Conditional volume confirmation (volume_ratio >= 1.5)
8. GATE_GREEKS_COHERENCE - Data quality validation
9. GATE_THETA_BURDEN_MAX - Decay limit (theta_pct <= 4%)
"""

from __future__ import annotations

from typing import Callable, Optional

from app.core.schemas import GateConfig, GateOperator, GateResult
from app.gates.models import GateContext


# Type alias for gate function signature
GateFunction = Callable[[GateContext, GateConfig], GateResult]


def check_min_open_interest(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_MIN_OPEN_INTEREST: Ensures sufficient liquidity.
    
    Threshold: 300 contracts (>=)
    Rationale: Ensures sufficient liquidity for entry/exit
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    threshold = config.min_open_interest
    measured = ctx.open_interest
    passed = measured >= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_MIN_OPEN_INTEREST",
        enabled=True,
        passed=passed,
        measured_value=float(measured),
        threshold_value=float(threshold),
        operator=GateOperator.GTE,
        units="contracts",
        reason_code="GATE_PASS_MIN_OI" if passed else "GATE_FAIL_MIN_OI",
        notes=f"OI {measured} {'meets' if passed else 'below'} minimum {threshold}",
    )


def check_min_volume(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_MIN_VOLUME: Ensures active trading.
    
    Threshold: 75 contracts (>=)
    Rationale: Active trading indicates executable prices
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    threshold = config.min_volume
    measured = ctx.volume
    passed = measured >= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_MIN_VOLUME",
        enabled=True,
        passed=passed,
        measured_value=float(measured),
        threshold_value=float(threshold),
        operator=GateOperator.GTE,
        units="contracts",
        reason_code="GATE_PASS_MIN_VOLUME" if passed else "GATE_FAIL_MIN_VOLUME",
        notes=f"Volume {measured} {'meets' if passed else 'below'} minimum {threshold}",
    )


def check_max_spread_pct(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_MAX_SPREAD_PCT: Ensures tight bid-ask spread.
    
    Threshold: 8% (<=)
    Rationale: Wide spreads create P&L drag
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    threshold = config.max_spread_pct
    measured = ctx.spread_pct
    passed = measured <= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_MAX_SPREAD_PCT",
        enabled=True,
        passed=passed,
        measured_value=measured,
        threshold_value=threshold,
        operator=GateOperator.LTE,
        units="percent",
        reason_code="GATE_PASS_SPREAD" if passed else "GATE_FAIL_SPREAD",
        notes=f"Spread {measured:.2f}% {'within' if passed else 'exceeds'} max {threshold}%",
    )


def check_dte_range(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_DTE_RANGE: Ensures appropriate time to expiration.
    
    Range: 7-120 days
    Rationale: <7 DTE = gamma risk; >120 DTE = capital inefficiency
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    min_dte = config.dte_min
    max_dte = config.dte_max
    measured = ctx.dte
    
    if measured < min_dte:
        passed = False
        reason_code = "GATE_FAIL_DTE_TOO_SHORT"
        notes = f"DTE {measured} below minimum {min_dte} (gamma risk)"
    elif measured > max_dte:
        passed = False
        reason_code = "GATE_FAIL_DTE_TOO_LONG"
        notes = f"DTE {measured} exceeds maximum {max_dte} (capital efficiency)"
    else:
        passed = True
        reason_code = "GATE_PASS_DTE"
        notes = f"DTE {measured} within range [{min_dte}, {max_dte}]"
    
    # For the threshold_value, use the violated boundary or midpoint
    threshold_value = min_dte if measured < min_dte else (max_dte if measured > max_dte else (min_dte + max_dte) / 2)
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_DTE_RANGE",
        enabled=True,
        passed=passed,
        measured_value=float(measured),
        threshold_value=threshold_value,
        operator=GateOperator.BETWEEN,
        units="days",
        reason_code=reason_code,
        notes=notes,
    )


def check_move_sufficiency(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_MOVE_SUFFICIENCY: Ensures required move is achievable.
    
    Threshold: time_adjusted_feasibility <= 1.25
    Rationale: Ensures required move is achievable within DTE window
    
    The time_adjusted_feasibility is calculated as:
    required_move_pct / (expected_move_pct × sqrt(DTE / 30))
    
    Values > 1.25 indicate the option requires an unrealistic price move.
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    threshold = config.move_sufficiency_max
    measured = ctx.time_adjusted_feasibility
    passed = measured <= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_MOVE_SUFFICIENCY",
        enabled=True,
        passed=passed,
        measured_value=measured,
        threshold_value=threshold,
        operator=GateOperator.LTE,
        units="ratio",
        reason_code="GATE_PASS_MOVE_SUFFICIENCY" if passed else "GATE_FAIL_MOVE_SUFFICIENCY",
        notes=f"Time-adjusted feasibility {measured:.3f} {'within' if passed else 'exceeds'} max {threshold}",
    )


def check_iv_percentile_max(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_IV_PERCENTILE_MAX: Ensures IV is not too elevated.
    
    Threshold: 85% (<=)
    Rationale: High IV = expensive options with IV crush risk
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    threshold = config.iv_percentile_max
    measured = ctx.iv_percentile
    
    # Fail when IV percentile data is missing — insufficient data to evaluate
    if measured is None:
        return GateResult(
            evaluation_id=ctx.evaluation_id,
            gate_id="GATE_IV_PERCENTILE_MAX",
            enabled=True,
            passed=False,
            measured_value=0.0,
            threshold_value=float(threshold),
            operator=GateOperator.LTE,
            units="percent",
            reason_code="GATE_FAIL_IV_PERCENTILE_MISSING",
            notes="IV percentile not available — insufficient data to evaluate",
        )
    
    passed = measured <= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_IV_PERCENTILE_MAX",
        enabled=True,
        passed=passed,
        measured_value=measured,
        threshold_value=float(threshold),
        operator=GateOperator.LTE,
        units="percent",
        reason_code="GATE_PASS_IV_PERCENTILE" if passed else "GATE_FAIL_IV_PERCENTILE",
        notes=f"IV percentile {measured:.1f}% {'within' if passed else 'exceeds'} max {threshold}%",
    )


def check_breakout_volume(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_BREAKOUT_VOLUME: Conditional volume confirmation for breakouts.
    
    Threshold: 1.5× average volume (>=)
    Applies When: Scanner trigger includes BREAKOUT or BREAKDOWN
    Rationale: Low-volume breakouts often fail
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail/skip status
    """
    threshold = config.breakout_volume_min
    
    # Check if this is a breakout/breakdown trigger
    is_breakout = any(
        trigger in ("BREAKOUT", "BREAKDOWN")
        for trigger in ctx.scanner_triggers
    )
    
    if not is_breakout:
        # Gate doesn't apply - skip it
        return GateResult(
            evaluation_id=ctx.evaluation_id,
            gate_id="GATE_BREAKOUT_VOLUME",
            enabled=False,  # Mark as disabled when not applicable
            passed=True,
            measured_value=0.0,
            threshold_value=threshold,
            operator=GateOperator.GTE,
            units="ratio",
            reason_code="GATE_SKIP_NOT_BREAKOUT",
            notes="Gate not applicable - no breakout/breakdown trigger",
        )
    
    # Get volume ratio from scanner metrics
    measured = ctx.volume_ratio
    
    if measured is None:
        # Fail when volume ratio data is missing for a breakout trigger
        return GateResult(
            evaluation_id=ctx.evaluation_id,
            gate_id="GATE_BREAKOUT_VOLUME",
            enabled=True,
            passed=False,
            measured_value=0.0,
            threshold_value=threshold,
            operator=GateOperator.GTE,
            units="ratio",
            reason_code="GATE_FAIL_BREAKOUT_VOLUME_MISSING",
            notes="Volume ratio not available — insufficient data to confirm breakout",
        )
    
    passed = measured >= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_BREAKOUT_VOLUME",
        enabled=True,
        passed=passed,
        measured_value=measured,
        threshold_value=threshold,
        operator=GateOperator.GTE,
        units="ratio",
        reason_code="GATE_PASS_BREAKOUT_VOLUME" if passed else "GATE_FAIL_BREAKOUT_VOLUME",
        notes=f"Breakout volume ratio {measured:.2f}x {'meets' if passed else 'below'} min {threshold}x",
    )


def check_greeks_coherence(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_GREEKS_COHERENCE: Validates Greeks are mathematically consistent.
    
    Validates:
    - Delta within expected range for option type
      - CALL: 0 < delta <= 1.0
      - PUT: -1.0 <= delta < 0
    - Theta < 0 (always negative for long options)
    - Vega > 0 (positive for long options)
    - Gamma > 0 (positive for long options)
    
    Rationale: Catches bad/stale data from data provider
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    issues: list[str] = []
    
    # Check delta range based on option type
    if ctx.option_type == "CALL":
        if not (0 < ctx.delta <= 1.0):
            issues.append(f"CALL delta {ctx.delta:.4f} not in (0, 1.0]")
    elif ctx.option_type == "PUT":
        if not (-1.0 <= ctx.delta < 0):
            issues.append(f"PUT delta {ctx.delta:.4f} not in [-1.0, 0)")
    
    # Theta should be negative for long options
    if ctx.theta >= 0:
        issues.append(f"Theta {ctx.theta:.4f} should be negative")
    
    # Vega should be positive
    if ctx.vega <= 0:
        issues.append(f"Vega {ctx.vega:.4f} should be positive")
    
    # Gamma should be positive
    if ctx.gamma <= 0:
        issues.append(f"Gamma {ctx.gamma:.6f} should be positive")
    
    passed = len(issues) == 0
    
    # Determine specific reason code
    if passed:
        reason_code = "GATE_PASS_GREEKS_COHERENCE"
    elif "delta" in str(issues):
        reason_code = "GATE_FAIL_GREEKS_DELTA"
    elif "Theta" in str(issues):
        reason_code = "GATE_FAIL_GREEKS_THETA"
    elif "Vega" in str(issues):
        reason_code = "GATE_FAIL_GREEKS_VEGA"
    elif "Gamma" in str(issues):
        reason_code = "GATE_FAIL_GREEKS_GAMMA"
    else:
        reason_code = "GATE_FAIL_GREEKS_COHERENCE"
    
    notes = "All Greeks coherent" if passed else "; ".join(issues)
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_GREEKS_COHERENCE",
        enabled=True,
        passed=passed,
        measured_value=ctx.delta,  # Use delta as representative value
        threshold_value=0.0,  # No single threshold for this gate
        operator=GateOperator.EQUALS,  # Placeholder - this is a validation gate
        units="validation",
        reason_code=reason_code,
        notes=notes,
    )


def check_theta_burden_max(ctx: GateContext, config: GateConfig) -> GateResult:
    """GATE_THETA_BURDEN_MAX: Ensures daily decay is manageable.
    
    Threshold: 4% per day (<=)
    Calculation: abs(theta) / mid × 100
    Rationale: High theta decay is problematic for trade success
    
    Args:
        ctx: Gate evaluation context
        config: Gate configuration with thresholds
        
    Returns:
        GateResult with pass/fail status
    """
    threshold = config.theta_burden_max
    
    # Calculate theta burden if not pre-calculated
    if ctx.theta_pct is not None:
        measured = ctx.theta_pct
    elif ctx.mid > 0:
        measured = abs(ctx.theta) / ctx.mid * 100
    else:
        # Fail when mid price is 0 — insufficient data to evaluate theta burden
        return GateResult(
            evaluation_id=ctx.evaluation_id,
            gate_id="GATE_THETA_BURDEN_MAX",
            enabled=True,
            passed=False,
            measured_value=0.0,
            threshold_value=threshold,
            operator=GateOperator.LTE,
            units="percent",
            reason_code="GATE_FAIL_THETA_BURDEN_MISSING",
            notes="Cannot calculate theta burden (mid price is 0) — insufficient data",
        )
    
    passed = measured <= threshold
    
    return GateResult(
        evaluation_id=ctx.evaluation_id,
        gate_id="GATE_THETA_BURDEN_MAX",
        enabled=True,
        passed=passed,
        measured_value=measured,
        threshold_value=threshold,
        operator=GateOperator.LTE,
        units="percent",
        reason_code="GATE_PASS_THETA_BURDEN" if passed else "GATE_FAIL_THETA_BURDEN",
        notes=f"Theta burden {measured:.2f}%/day {'within' if passed else 'exceeds'} max {threshold}%",
    )


# Registry of all gate functions
ALL_GATES: list[tuple[str, GateFunction]] = [
    ("GATE_MIN_OPEN_INTEREST", check_min_open_interest),
    ("GATE_MIN_VOLUME", check_min_volume),
    ("GATE_MAX_SPREAD_PCT", check_max_spread_pct),
    ("GATE_DTE_RANGE", check_dte_range),
    ("GATE_MOVE_SUFFICIENCY", check_move_sufficiency),
    ("GATE_IV_PERCENTILE_MAX", check_iv_percentile_max),
    ("GATE_BREAKOUT_VOLUME", check_breakout_volume),
    ("GATE_GREEKS_COHERENCE", check_greeks_coherence),
    ("GATE_THETA_BURDEN_MAX", check_theta_burden_max),
]
