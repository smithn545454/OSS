"""Gates module for Stage 6: Hard Gates.

Binary pass/fail checks that can reject evaluations regardless of pillar scores.
Per Section 15 of OSS_Complete_Requirements.md.
"""

from app.gates.models import GateContext, GateEvaluation
from app.gates.gates import (
    check_min_open_interest,
    check_min_volume,
    check_max_spread_pct,
    check_dte_range,
    check_move_sufficiency,
    check_iv_percentile_max,
    check_breakout_volume,
    check_greeks_coherence,
    check_theta_burden_max,
    ALL_GATES,
)
from app.gates.calculator import GateCalculator, evaluate_all_gates

__all__ = [
    # Models
    "GateContext",
    "GateEvaluation",
    # Gate functions
    "check_min_open_interest",
    "check_min_volume",
    "check_max_spread_pct",
    "check_dte_range",
    "check_move_sufficiency",
    "check_iv_percentile_max",
    "check_breakout_volume",
    "check_greeks_coherence",
    "check_theta_burden_max",
    "ALL_GATES",
    # Calculator
    "GateCalculator",
    "evaluate_all_gates",
]
