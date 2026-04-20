"""LLM input/output models for trade thesis generation.

Per Section 21.2 and 21.3 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class UnderlyingData:
    """Underlying stock data for thesis input."""

    ticker: str
    price: float
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    atr14: Optional[float] = None
    atr14_pct: Optional[float] = None


@dataclass
class ContractData:
    """Option contract data for thesis input."""

    option_type: str  # "CALL" or "PUT"
    strike: float
    expiration: str
    dte: int
    mid: float
    iv: float
    delta: float
    theta: float
    gamma: Optional[float] = None
    vega: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    spread_pct: Optional[float] = None
    breakeven_price: Optional[float] = None
    expected_move_pct: Optional[float] = None
    feasibility_ratio: Optional[float] = None
    theta_pct: Optional[float] = None


@dataclass
class ScoresData:
    """Pillar and final scores for thesis input.

    Carries v3, v4, and v5 score fields as optional attributes so the prompt
    builder can render whichever regime produced this decision. The
    ``regime`` field ("v3", "v4", or "v5") makes the active set explicit.

    v5 is the dual-conviction regime: HR Conviction (0–20) scores grand-slam
    probability and P Conviction (0–100) scores profitability. Both are
    Wilson-lower calibrated estimates multiplied by archetype fit and regime
    alignment — the prompt layer treats them as the primary thesis inputs.
    """

    final: float
    # v3 (Policy v3.x — retained through Phase 8)
    premium_leverage: Optional[float] = None
    underlying_behavior: Optional[float] = None
    setup_quality: Optional[float] = None
    # v4 (Policy v4.x — active from Phase 7)
    directional_conviction: Optional[float] = None
    move_potential: Optional[float] = None
    trade_structure: Optional[float] = None
    # v5 (Policy v4.1.1+ — active from 2026-04-20)
    # HR conviction track (grand-slam: P(MFE ≥ 200%))
    hr_conviction: Optional[float] = None        # 0–20
    hr_archetype_matched: Optional[str] = None
    hr_archetype_fit: Optional[float] = None     # 0–100 — min-fit across archetype conditions
    hr_p_point: Optional[float] = None           # Point HR200 estimate, [0, 1]
    hr_p_lower: Optional[float] = None           # Wilson lower bound, [0, 1]
    hr_p_upper: Optional[float] = None           # Wilson upper bound, [0, 1]
    hr_n_trades: Optional[int] = None            # Effective sample size for HR rate
    # P conviction track (profitability: Wilson_lower(P_win) × normalized_pnl)
    p_conviction: Optional[float] = None         # 0–100
    p_archetype_matched: Optional[str] = None
    p_archetype_fit: Optional[float] = None      # 0–100
    p_win_point: Optional[float] = None          # Point win-rate estimate, [0, 1]
    p_win_lower: Optional[float] = None          # Wilson lower bound on win rate, [0, 1]
    p_mean_pnl_estimate: Optional[float] = None  # Mean % P&L in cohort
    # Shared v5 modifiers
    regime_alignment: Optional[float] = None     # Market-regime multiplier, [0.5, 1.5]
    gbm_hr_score: Optional[float] = None         # GBM co-scorer P(HR200) × 100
    gbm_p_score: Optional[float] = None          # GBM co-scorer P(profit) × 100
    v5_scoring_version: Optional[str] = None     # e.g. "v5.0.0" when v5 wrote the decision
    # Inferred at prompt-build time: "HR" or "P" — which conviction drove the APPROVE
    verdict_driver: Optional[str] = None
    # Thresholds active at decision time (for framing how far above floor the score landed)
    v5_hr_threshold: Optional[float] = None
    v5_p_threshold: Optional[float] = None
    regime: str = "v3"


@dataclass
class PillarContributorData:
    """Individual pillar contributor data."""

    feature_name: str
    subscore: float
    weight: float
    weighted_contribution: float
    raw_value: Any


@dataclass
class ScannerTriggerData:
    """Scanner trigger data for thesis input."""

    scanner_type: str
    reason_codes: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThesisInput:
    """Complete input packet for LLM thesis generation.

    Per Section 21.2 of OSS_Complete_Requirements.md.
    """

    underlying: UnderlyingData
    contract: ContractData
    scores: ScoresData
    pillar_contributors: dict[str, list[PillarContributorData]]
    scanner_triggers: list[ScannerTriggerData]
    policy_version: str
    quality_tier: Optional[str] = None
    evaluation_id: Optional[str] = None
    setup_rule_matches: list[dict[str, Any]] = field(default_factory=list)
    total_active_rules: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for prompt formatting."""
        return {
            "underlying": {
                "ticker": self.underlying.ticker,
                "price": self.underlying.price,
                "sma20": self.underlying.sma20,
                "sma50": self.underlying.sma50,
                "return_5d": self.underlying.return_5d,
                "return_20d": self.underlying.return_20d,
                "atr14": self.underlying.atr14,
                "atr14_pct": self.underlying.atr14_pct,
            },
            "contract": {
                "type": self.contract.option_type,
                "strike": self.contract.strike,
                "expiration": self.contract.expiration,
                "dte": self.contract.dte,
                "mid": self.contract.mid,
                "iv": self.contract.iv,
                "delta": self.contract.delta,
                "theta": self.contract.theta,
                "gamma": self.contract.gamma,
                "vega": self.contract.vega,
                "open_interest": self.contract.open_interest,
                "volume": self.contract.volume,
                "spread_pct": self.contract.spread_pct,
                "breakeven_price": self.contract.breakeven_price,
                "expected_move_pct": self.contract.expected_move_pct,
                "feasibility_ratio": self.contract.feasibility_ratio,
                "theta_pct": self.contract.theta_pct,
            },
            "scores": {
                "final": self.scores.final,
                "regime": self.scores.regime,
                "premium_leverage": self.scores.premium_leverage,
                "underlying_behavior": self.scores.underlying_behavior,
                "setup_quality": self.scores.setup_quality,
                "directional_conviction": self.scores.directional_conviction,
                "move_potential": self.scores.move_potential,
                "trade_structure": self.scores.trade_structure,
                # v5
                "hr_conviction": self.scores.hr_conviction,
                "hr_archetype_matched": self.scores.hr_archetype_matched,
                "hr_archetype_fit": self.scores.hr_archetype_fit,
                "hr_p_point": self.scores.hr_p_point,
                "hr_p_lower": self.scores.hr_p_lower,
                "hr_p_upper": self.scores.hr_p_upper,
                "hr_n_trades": self.scores.hr_n_trades,
                "p_conviction": self.scores.p_conviction,
                "p_archetype_matched": self.scores.p_archetype_matched,
                "p_archetype_fit": self.scores.p_archetype_fit,
                "p_win_point": self.scores.p_win_point,
                "p_win_lower": self.scores.p_win_lower,
                "p_mean_pnl_estimate": self.scores.p_mean_pnl_estimate,
                "regime_alignment": self.scores.regime_alignment,
                "gbm_hr_score": self.scores.gbm_hr_score,
                "gbm_p_score": self.scores.gbm_p_score,
                "v5_scoring_version": self.scores.v5_scoring_version,
                "verdict_driver": self.scores.verdict_driver,
                "v5_hr_threshold": self.scores.v5_hr_threshold,
                "v5_p_threshold": self.scores.v5_p_threshold,
            },
            "pillar_contributors": {
                pillar: [
                    {
                        "feature": c.feature_name,
                        "subscore": c.subscore,
                        "weight": c.weight,
                        "contribution": c.weighted_contribution,
                        "value": c.raw_value,
                    }
                    for c in contributors
                ]
                for pillar, contributors in self.pillar_contributors.items()
            },
            "scanner_triggers": [
                {
                    "type": t.scanner_type,
                    "reasons": t.reason_codes,
                    "metrics": t.metrics,
                }
                for t in self.scanner_triggers
            ],
            "policy_version": self.policy_version,
            "quality_tier": self.quality_tier,
            "setup_rule_matches": self.setup_rule_matches,
            "total_active_rules": self.total_active_rules,
        }


@dataclass
class TakeProfitTargetOutput:
    """Single take-profit tier from LLM output."""

    tier: int
    option_pnl_pct: float
    underlying_price: float
    rationale: str


@dataclass
class StopLossTargetOutput:
    """Structured stop loss from LLM output."""

    option_pnl_pct: float
    underlying_price: float
    rationale: str


@dataclass
class TimeExitTargetOutput:
    """Time-based exit rule from LLM output."""

    dte_threshold: int
    rationale: str


@dataclass
class ExitPlanOutput:
    """Exit plan section of thesis output."""

    # Legacy summary strings
    profit_target: str
    stop_loss: str
    time_exit: str
    # Structured targets
    take_profits: list[TakeProfitTargetOutput] = field(default_factory=list)
    stop_loss_level: Optional[StopLossTargetOutput] = None
    time_exit_level: Optional[TimeExitTargetOutput] = None


@dataclass
class ThesisOutput:
    """Structured output from LLM thesis generation.

    Per Section 21.3 of OSS_Complete_Requirements.md.
    """

    setup_summary: str
    thesis: str
    supporting_evidence: list[str]
    risks: list[str]
    invalidation_conditions: list[str]
    exit_plan: ExitPlanOutput

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThesisOutput:
        """Parse from LLM response dictionary."""
        exit_plan_data = data.get("exit_plan", {})

        # Parse structured take profit targets
        take_profits = []
        for tp in exit_plan_data.get("take_profits", []):
            take_profits.append(TakeProfitTargetOutput(
                tier=tp.get("tier", len(take_profits) + 1),
                option_pnl_pct=float(tp.get("option_pnl_pct", 0)),
                underlying_price=float(tp.get("underlying_price", 0)),
                rationale=tp.get("rationale", ""),
            ))

        # Parse structured stop loss
        sl_data = exit_plan_data.get("stop_loss_level")
        stop_loss_level = None
        if sl_data and isinstance(sl_data, dict):
            stop_loss_level = StopLossTargetOutput(
                option_pnl_pct=float(sl_data.get("option_pnl_pct", 0)),
                underlying_price=float(sl_data.get("underlying_price", 0)),
                rationale=sl_data.get("rationale", ""),
            )

        # Parse structured time exit
        te_data = exit_plan_data.get("time_exit_level")
        time_exit_level = None
        if te_data and isinstance(te_data, dict):
            time_exit_level = TimeExitTargetOutput(
                dte_threshold=int(te_data.get("dte_threshold", 5)),
                rationale=te_data.get("rationale", ""),
            )

        return cls(
            setup_summary=data.get("setup_summary", ""),
            thesis=data.get("thesis", ""),
            supporting_evidence=data.get("supporting_evidence", []),
            risks=data.get("risks", []),
            invalidation_conditions=data.get("invalidation_conditions", []),
            exit_plan=ExitPlanOutput(
                profit_target=exit_plan_data.get("profit_target", ""),
                stop_loss=exit_plan_data.get("stop_loss", ""),
                time_exit=exit_plan_data.get("time_exit", ""),
                take_profits=take_profits,
                stop_loss_level=stop_loss_level,
                time_exit_level=time_exit_level,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "setup_summary": self.setup_summary,
            "thesis": self.thesis,
            "supporting_evidence": self.supporting_evidence,
            "risks": self.risks,
            "invalidation_conditions": self.invalidation_conditions,
            "exit_plan": {
                "profit_target": self.exit_plan.profit_target,
                "stop_loss": self.exit_plan.stop_loss,
                "time_exit": self.exit_plan.time_exit,
                "take_profits": [
                    {
                        "tier": tp.tier,
                        "option_pnl_pct": tp.option_pnl_pct,
                        "underlying_price": tp.underlying_price,
                        "rationale": tp.rationale,
                    }
                    for tp in self.exit_plan.take_profits
                ],
                "stop_loss_level": {
                    "option_pnl_pct": self.exit_plan.stop_loss_level.option_pnl_pct,
                    "underlying_price": self.exit_plan.stop_loss_level.underlying_price,
                    "rationale": self.exit_plan.stop_loss_level.rationale,
                } if self.exit_plan.stop_loss_level else None,
                "time_exit_level": {
                    "dte_threshold": self.exit_plan.time_exit_level.dte_threshold,
                    "rationale": self.exit_plan.time_exit_level.rationale,
                } if self.exit_plan.time_exit_level else None,
            },
        }
        return result
