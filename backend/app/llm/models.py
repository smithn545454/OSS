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
    """Pillar and final scores for thesis input."""

    final: float
    directional: float
    volatility: float
    structure: float


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
                "directional": self.scores.directional,
                "volatility": self.scores.volatility,
                "structure": self.scores.structure,
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
