"""Decision Logic module for Stage 7.

This module implements the final decision stage that combines pillar scores
with gate results to produce verdicts (APPROVE/WATCH/REJECT), quality tiers,
and concentration warnings.

Per Section 16 of OSS_Complete_Requirements.md.
"""

from app.decision.calculator import (
    DecisionCalculator,
    DecisionContext,
    compute_decision,
    determine_verdict,
    assign_quality_tier,
)
from app.decision.concentration import (
    ConcentrationAnalysis,
    check_concentration_warnings,
    check_ticker_concentration,
    check_directional_concentration,
    analyze_concentration,
    update_decisions_with_warnings,
)
from app.decision.stage import (
    DecisionStage,
    run_decision_logic,
    get_approved_evaluations,
    get_watch_evaluations,
    get_rejected_evaluations,
    get_tier_1_evaluations,
    extract_decisions_for_paper_trading,
)

__all__ = [
    # Calculator
    "DecisionCalculator",
    "DecisionContext",
    "compute_decision",
    "determine_verdict",
    "assign_quality_tier",
    # Concentration
    "ConcentrationAnalysis",
    "check_concentration_warnings",
    "check_ticker_concentration",
    "check_directional_concentration",
    "analyze_concentration",
    "update_decisions_with_warnings",
    # Stage
    "DecisionStage",
    "run_decision_logic",
    "get_approved_evaluations",
    "get_watch_evaluations",
    "get_rejected_evaluations",
    "get_tier_1_evaluations",
    "extract_decisions_for_paper_trading",
]
