"""Concentration Warning Detection for Stage 7.

Identifies potential portfolio concentration issues across a batch of decisions.

Per Section 16.4 of OSS_Complete_Requirements.md:
- WARN_CONCENTRATION_SAME_TICKER: >3 contracts same underlying
- WARN_CONCENTRATION_DIRECTIONAL: >70% approvals same direction
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from app.core.schemas import Decision, Evaluation, Verdict

logger = logging.getLogger(__name__)

# Warning thresholds
SAME_TICKER_THRESHOLD = 3  # >3 contracts same underlying
DIRECTIONAL_THRESHOLD = 0.70  # >70% same direction


@dataclass
class ConcentrationAnalysis:
    """Result of concentration analysis for a batch of decisions."""
    
    total_approves: int = 0
    total_watches: int = 0
    
    # Ticker concentration
    ticker_counts: dict[str, int] = None  # ticker -> count
    tickers_over_limit: list[str] = None  # tickers with >3 contracts
    
    # Directional concentration
    call_count: int = 0
    put_count: int = 0
    call_pct: float = 0.0
    put_pct: float = 0.0
    directional_warning: bool = False
    dominant_direction: str = "NONE"  # "CALL", "PUT", or "NONE"
    
    def __post_init__(self):
        if self.ticker_counts is None:
            self.ticker_counts = {}
        if self.tickers_over_limit is None:
            self.tickers_over_limit = []


def check_ticker_concentration(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    threshold: int = SAME_TICKER_THRESHOLD,
) -> dict[str, list[str]]:
    """Check for ticker concentration across APPROVE/WATCH decisions.
    
    Per Section 16.4:
    - WARN_CONCENTRATION_SAME_TICKER: >3 contracts same underlying
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        threshold: Maximum contracts per ticker before warning (default 3)
        
    Returns:
        Dict mapping evaluation_id to list of ticker concentration warnings
    """
    # Count contracts per ticker for APPROVE/WATCH verdicts
    ticker_contracts: dict[str, list[str]] = {}  # ticker -> [evaluation_ids]
    
    for evaluation in evaluations:
        eval_id = evaluation.evaluation_id
        decision = decisions.get(eval_id)
        
        if not decision:
            continue
        
        # Only count APPROVE and WATCH (not REJECT)
        if decision.verdict not in (Verdict.APPROVE, Verdict.WATCH):
            continue
        
        ticker = evaluation.underlying_ticker
        if ticker not in ticker_contracts:
            ticker_contracts[ticker] = []
        ticker_contracts[ticker].append(eval_id)
    
    # Identify tickers over limit and assign warnings
    warnings: dict[str, list[str]] = {}
    
    for ticker, eval_ids in ticker_contracts.items():
        if len(eval_ids) > threshold:
            logger.info(
                f"Ticker concentration warning: {ticker} has {len(eval_ids)} contracts "
                f"(threshold: {threshold})"
            )
            # Add warning to all evaluations for this ticker
            for eval_id in eval_ids:
                if eval_id not in warnings:
                    warnings[eval_id] = []
                warnings[eval_id].append(
                    f"WARN_CONCENTRATION_SAME_TICKER:{ticker}:{len(eval_ids)}"
                )
    
    return warnings


def check_directional_concentration(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    threshold: float = DIRECTIONAL_THRESHOLD,
) -> dict[str, list[str]]:
    """Check for directional concentration across APPROVE decisions.
    
    Per Section 16.4:
    - WARN_CONCENTRATION_DIRECTIONAL: >70% approvals same direction
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        threshold: Percentage threshold for directional warning (default 0.70)
        
    Returns:
        Dict mapping evaluation_id to list of directional concentration warnings
    """
    # Count direction for APPROVE verdicts only
    call_evals: list[str] = []
    put_evals: list[str] = []
    
    for evaluation in evaluations:
        eval_id = evaluation.evaluation_id
        decision = decisions.get(eval_id)
        
        if not decision:
            continue
        
        # Only count APPROVE verdicts
        if decision.verdict != Verdict.APPROVE:
            continue
        
        if evaluation.option_type == "CALL":
            call_evals.append(eval_id)
        elif evaluation.option_type == "PUT":
            put_evals.append(eval_id)
    
    total_approves = len(call_evals) + len(put_evals)
    
    if total_approves == 0:
        return {}
    
    # Calculate percentages
    call_pct = len(call_evals) / total_approves
    put_pct = len(put_evals) / total_approves
    
    # Check for directional concentration
    warnings: dict[str, list[str]] = {}
    
    if call_pct > threshold:
        logger.info(
            f"Directional concentration warning: {call_pct:.1%} CALLs "
            f"(threshold: {threshold:.0%})"
        )
        # Add warning to all APPROVE evaluations
        for eval_id in call_evals + put_evals:
            if eval_id not in warnings:
                warnings[eval_id] = []
            warnings[eval_id].append(
                f"WARN_CONCENTRATION_DIRECTIONAL:CALL:{call_pct:.2f}"
            )
    
    elif put_pct > threshold:
        logger.info(
            f"Directional concentration warning: {put_pct:.1%} PUTs "
            f"(threshold: {threshold:.0%})"
        )
        # Add warning to all APPROVE evaluations
        for eval_id in call_evals + put_evals:
            if eval_id not in warnings:
                warnings[eval_id] = []
            warnings[eval_id].append(
                f"WARN_CONCENTRATION_DIRECTIONAL:PUT:{put_pct:.2f}"
            )
    
    return warnings


def check_concentration_warnings(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    ticker_threshold: int = SAME_TICKER_THRESHOLD,
    directional_threshold: float = DIRECTIONAL_THRESHOLD,
) -> dict[str, list[str]]:
    """Check for all concentration warnings.
    
    Combines ticker and directional concentration checks.
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        ticker_threshold: Max contracts per ticker (default 3)
        directional_threshold: Max percentage same direction (default 0.70)
        
    Returns:
        Dict mapping evaluation_id to list of all concentration warnings
    """
    # Get both types of warnings
    ticker_warnings = check_ticker_concentration(
        evaluations, decisions, ticker_threshold
    )
    directional_warnings = check_directional_concentration(
        evaluations, decisions, directional_threshold
    )
    
    # Merge warnings
    all_warnings: dict[str, list[str]] = {}
    
    # Add ticker warnings
    for eval_id, warnings in ticker_warnings.items():
        if eval_id not in all_warnings:
            all_warnings[eval_id] = []
        all_warnings[eval_id].extend(warnings)
    
    # Add directional warnings
    for eval_id, warnings in directional_warnings.items():
        if eval_id not in all_warnings:
            all_warnings[eval_id] = []
        all_warnings[eval_id].extend(warnings)
    
    return all_warnings


def analyze_concentration(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
) -> ConcentrationAnalysis:
    """Analyze concentration across a batch of decisions.
    
    Provides detailed statistics about portfolio concentration.
    
    Args:
        evaluations: List of Evaluation records
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        ConcentrationAnalysis with detailed statistics
    """
    analysis = ConcentrationAnalysis()
    
    # Build evaluation lookup
    eval_map = {e.evaluation_id: e for e in evaluations}
    
    # Analyze APPROVE and WATCH decisions
    ticker_counts: Counter = Counter()
    call_count = 0
    put_count = 0
    
    for eval_id, decision in decisions.items():
        if decision.verdict == Verdict.APPROVE:
            analysis.total_approves += 1
        elif decision.verdict == Verdict.WATCH:
            analysis.total_watches += 1
        else:
            continue  # Skip REJECT
        
        evaluation = eval_map.get(eval_id)
        if not evaluation:
            continue
        
        # Count by ticker
        ticker_counts[evaluation.underlying_ticker] += 1
        
        # Count by direction (only for APPROVE)
        if decision.verdict == Verdict.APPROVE:
            if evaluation.option_type == "CALL":
                call_count += 1
            elif evaluation.option_type == "PUT":
                put_count += 1
    
    # Store ticker counts
    analysis.ticker_counts = dict(ticker_counts)
    analysis.tickers_over_limit = [
        ticker for ticker, count in ticker_counts.items()
        if count > SAME_TICKER_THRESHOLD
    ]
    
    # Store directional counts
    analysis.call_count = call_count
    analysis.put_count = put_count
    
    total_approves = call_count + put_count
    if total_approves > 0:
        analysis.call_pct = call_count / total_approves
        analysis.put_pct = put_count / total_approves
        
        if analysis.call_pct > DIRECTIONAL_THRESHOLD:
            analysis.directional_warning = True
            analysis.dominant_direction = "CALL"
        elif analysis.put_pct > DIRECTIONAL_THRESHOLD:
            analysis.directional_warning = True
            analysis.dominant_direction = "PUT"
    
    return analysis


def update_decisions_with_warnings(
    decisions: dict[str, Decision],
    warnings: dict[str, list[str]],
) -> dict[str, Decision]:
    """Update Decision records with concentration warnings.

    Uses Pydantic's ``model_copy(update=...)`` so every field on the
    Decision (including v4.1.0 archetype fields and v5 dual-conviction
    fields) is preserved. The previous hand-rolled reconstructor silently
    dropped any field not in its explicit list, wiping v5_scoring_version,
    hr_conviction, p_conviction, archetype_matched, and every other v5
    field whenever a concentration warning fired. That was the root
    cause of CHEAP_OPTIONS never carrying v5 fields in persisted
    decisions (audit C1) — CHEAP hits concentration warnings near-100%
    of the time, so its v5 data always got wiped here.

    Args:
        decisions: Dict mapping evaluation_id to Decision
        warnings: Dict mapping evaluation_id to list of warnings

    Returns:
        Updated dict mapping evaluation_id to Decision with warnings
    """
    updated: dict[str, Decision] = {}

    for eval_id, decision in decisions.items():
        eval_warnings = warnings.get(eval_id, [])

        if eval_warnings:
            all_warnings = list(decision.concentration_warnings) + eval_warnings
            updated[eval_id] = decision.model_copy(
                update={"concentration_warnings": all_warnings}
            )
        else:
            updated[eval_id] = decision

    return updated
