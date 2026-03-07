"""Evaluation Builder.

Constructs Evaluation records from selected contract candidates.

Computes all required fields per Section 8.2 and Section 13 of OSS_Complete_Requirements.md:
- breakeven_price: strike + mid (CALL) or strike - mid (PUT)
- required_move_pct: |breakeven - underlying| / underlying * 100
- expected_move_pct: iv * sqrt(DTE/365) * 100
- feasibility_ratio: required_move_pct / expected_move_pct
- time_adjusted_feasibility: required_move_pct / (expected_move_pct * sqrt(DTE/30))
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from app.core.schemas import DTEBucket, Evaluation, Opportunity, OptionType
from app.selection.contract_selector import ContractCandidate

logger = logging.getLogger(__name__)


class EvaluationBuilder:
    """Builds Evaluation records from selected contract candidates."""

    def __init__(self, policy_version: str, policy_hash: str, policy_snapshot_id: Optional[str] = None) -> None:
        """Initialize the evaluation builder.

        Args:
            policy_version: Current policy version for tagging evaluations
            policy_hash: SHA-256 hash of policy config per Section 7.3
            policy_snapshot_id: Foreign key to archived policy snapshot per Section 7.3
        """
        self._policy_version = policy_version
        self._policy_hash = policy_hash
        self._policy_snapshot_id = policy_snapshot_id

    def build_evaluation(
        self,
        candidate: ContractCandidate,
        opportunity: Opportunity,
    ) -> Evaluation:
        """Build an Evaluation record from a contract candidate.
        
        Args:
            candidate: Selected contract candidate
            opportunity: Parent opportunity for this evaluation
            
        Returns:
            Evaluation record with all computed fields
        """
        # Calculate breakeven price
        # CALL: breakeven = strike + premium (mid)
        # PUT: breakeven = strike - premium (mid)
        if candidate.option_type == OptionType.CALL:
            breakeven_price = candidate.strike + candidate.mid
        else:
            breakeven_price = candidate.strike - candidate.mid

        # Calculate required move percentage
        # required_move_pct = |breakeven - underlying| / underlying * 100
        if candidate.underlying_price > 0:
            required_move_pct = (
                abs(breakeven_price - candidate.underlying_price) 
                / candidate.underlying_price * 100
            )
        else:
            required_move_pct = 999.0  # Unfeasible — no valid underlying price

        # Calculate expected move percentage
        # expected_move_pct = iv * sqrt(DTE/365) * 100
        # IV is already a decimal (e.g., 0.25 for 25%)
        if candidate.dte > 0 and candidate.iv > 0:
            expected_move_pct = candidate.iv * math.sqrt(candidate.dte / 365) * 100
        else:
            expected_move_pct = 0.01  # Avoid division by zero

        # Calculate feasibility ratio
        # feasibility_ratio = required_move_pct / expected_move_pct
        if expected_move_pct > 0:
            feasibility_ratio = required_move_pct / expected_move_pct
        else:
            feasibility_ratio = 999.0  # Indicates unfeasible

        # Calculate time-adjusted feasibility
        # time_adjusted_feasibility = required_move_pct / (expected_move_pct * sqrt(DTE/30))
        if candidate.dte > 0 and expected_move_pct > 0:
            time_factor = math.sqrt(candidate.dte / 30)
            time_adjusted_feasibility = required_move_pct / (expected_move_pct * time_factor)
        else:
            time_adjusted_feasibility = 999.0

        # Determine rank score
        rank_score = (
            candidate.ranking_scores.rank_score 
            if candidate.ranking_scores 
            else 0.0
        )

        # Extract primary scanner source from opportunity
        scanner_source = None
        if opportunity.scanner_triggers:
            st = opportunity.scanner_triggers[0].scanner_type
            scanner_source = st.value if hasattr(st, "value") else str(st)

        # Build evaluation
        evaluation = Evaluation(
            opportunity_id=opportunity.opportunity_id,
            underlying_ticker=candidate.underlying_ticker,
            option_ticker=candidate.option_ticker,
            scanner_source=scanner_source,
            option_type=candidate.option_type,
            expiration_date=candidate.expiration_date,
            dte=candidate.dte,
            strike=candidate.strike,
            underlying_price=candidate.underlying_price,
            moneyness_pct=candidate.moneyness_pct,
            bid=candidate.bid,
            ask=candidate.ask,
            mid=candidate.mid,
            spread_abs=candidate.spread_abs,
            spread_pct=candidate.spread_pct,
            iv=candidate.iv,
            delta=candidate.delta,
            gamma=candidate.gamma,
            theta=candidate.theta,
            vega=candidate.vega,
            open_interest=candidate.open_interest,
            volume=candidate.volume,
            oi_5d_change_pct=None,  # Calculated in feature computation stage
            breakeven_price=breakeven_price,
            required_move_pct=required_move_pct,
            expected_move_pct=expected_move_pct,
            feasibility_ratio=feasibility_ratio,
            time_adjusted_feasibility=time_adjusted_feasibility,
            dte_bucket=candidate.dte_bucket,
            rank_score=rank_score,
            policy_version=self._policy_version,
            policy_hash=self._policy_hash,
            policy_snapshot_id=self._policy_snapshot_id,
        )

        return evaluation

    def build_evaluations(
        self,
        candidates: list[ContractCandidate],
        opportunities: list[Opportunity],
    ) -> list[Evaluation]:
        """Build Evaluation records for all selected candidates.
        
        Args:
            candidates: List of selected contract candidates
            opportunities: List of opportunities (for linking)
            
        Returns:
            List of Evaluation records
        """
        # Create a mapping from ticker to opportunity
        opp_by_ticker: dict[str, Opportunity] = {
            opp.underlying_ticker: opp for opp in opportunities
        }

        evaluations: list[Evaluation] = []
        
        for candidate in candidates:
            opportunity = opp_by_ticker.get(candidate.underlying_ticker)
            if not opportunity:
                logger.warning(
                    f"No opportunity found for ticker {candidate.underlying_ticker}"
                )
                continue

            try:
                evaluation = self.build_evaluation(candidate, opportunity)
                evaluations.append(evaluation)
            except Exception as e:
                logger.error(
                    f"Error building evaluation for {candidate.option_ticker}: {e}"
                )
                continue

        logger.info(f"Built {len(evaluations)} evaluations from {len(candidates)} candidates")
        return evaluations

    @staticmethod
    def compute_breakeven(
        strike: float,
        mid: float,
        option_type: OptionType,
    ) -> float:
        """Compute breakeven price.
        
        Args:
            strike: Strike price
            mid: Mid price (premium)
            option_type: CALL or PUT
            
        Returns:
            Breakeven price
        """
        if option_type == OptionType.CALL:
            return strike + mid
        else:
            return strike - mid

    @staticmethod
    def compute_required_move_pct(
        breakeven_price: float,
        underlying_price: float,
    ) -> float:
        """Compute required move percentage.
        
        Args:
            breakeven_price: Breakeven price
            underlying_price: Current underlying price
            
        Returns:
            Required move as percentage
        """
        return abs(breakeven_price - underlying_price) / underlying_price * 100

    @staticmethod
    def compute_expected_move_pct(
        iv: float,
        dte: int,
    ) -> float:
        """Compute expected move percentage.
        
        Args:
            iv: Implied volatility (decimal)
            dte: Days to expiration
            
        Returns:
            Expected move as percentage
        """
        if dte <= 0 or iv <= 0:
            return 0.01
        return iv * math.sqrt(dte / 365) * 100

    @staticmethod
    def compute_feasibility_ratio(
        required_move_pct: float,
        expected_move_pct: float,
    ) -> float:
        """Compute feasibility ratio.
        
        Args:
            required_move_pct: Required move percentage
            expected_move_pct: Expected move percentage
            
        Returns:
            Feasibility ratio
        """
        if expected_move_pct <= 0:
            return 999.0
        return required_move_pct / expected_move_pct

    @staticmethod
    def compute_time_adjusted_feasibility(
        required_move_pct: float,
        expected_move_pct: float,
        dte: int,
    ) -> float:
        """Compute time-adjusted feasibility.
        
        Per Section 15.1 (GATE_MOVE_SUFFICIENCY):
        time_adjusted_feasibility = required_move_pct / (expected_move_pct * sqrt(DTE/30))
        
        Args:
            required_move_pct: Required move percentage
            expected_move_pct: Expected move percentage
            dte: Days to expiration
            
        Returns:
            Time-adjusted feasibility ratio
        """
        if dte <= 0 or expected_move_pct <= 0:
            return 999.0
        time_factor = math.sqrt(dte / 30)
        return required_move_pct / (expected_move_pct * time_factor)
