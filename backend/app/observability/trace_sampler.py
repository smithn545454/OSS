"""Representative trace sampling for observability.

Per Section 18.3 of OSS_Complete_Requirements.md.

Provides sampling of interesting edge cases for debugging and analysis:
- Common Gate Failures: 10 samples
- Highest REJECT Scores: 10 samples (near-miss rejects)
- Lowest APPROVE Scores: 10 samples (borderline approves)
- TIER_1 Approvals: All high-confidence trades
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from app.db.tables import EvaluationTable, GateResultTable

logger = logging.getLogger(__name__)


@dataclass
class TraceSample:
    """A single trace sample with evaluation details."""
    
    evaluation_id: str
    ticker: str
    option_ticker: str
    option_type: str
    strike: float
    dte: int
    dte_bucket: str
    final_score: float
    verdict: str
    quality_tier: Optional[str]
    evaluated_at: str
    # For linking to detail page
    timestamp: str  # extracted from evaluated_at
    
    # Optional context
    failed_gates: list[str] = field(default_factory=list)
    primary_reason: Optional[str] = None


@dataclass
class GateFailureSample:
    """Sample of gate failure with affected evaluations."""
    
    gate_id: str
    failure_count: int
    sample_evaluations: list[TraceSample] = field(default_factory=list)


@dataclass
class RepresentativeTraces:
    """Complete set of representative trace samples."""
    
    common_gate_failures: list[GateFailureSample] = field(default_factory=list)
    highest_reject_scores: list[TraceSample] = field(default_factory=list)
    lowest_approve_scores: list[TraceSample] = field(default_factory=list)
    tier_1_approvals: list[TraceSample] = field(default_factory=list)
    
    # Summary statistics
    total_rejects_sampled: int = 0
    total_approves_sampled: int = 0
    total_tier_1: int = 0
    unique_gates_failed: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "common_gate_failures": [
                {
                    "gate_id": gf.gate_id,
                    "failure_count": gf.failure_count,
                    "sample_evaluations": [_sample_to_dict(s) for s in gf.sample_evaluations],
                }
                for gf in self.common_gate_failures
            ],
            "highest_reject_scores": [_sample_to_dict(s) for s in self.highest_reject_scores],
            "lowest_approve_scores": [_sample_to_dict(s) for s in self.lowest_approve_scores],
            "tier_1_approvals": [_sample_to_dict(s) for s in self.tier_1_approvals],
            "summary": {
                "total_rejects_sampled": self.total_rejects_sampled,
                "total_approves_sampled": self.total_approves_sampled,
                "total_tier_1": self.total_tier_1,
                "unique_gates_failed": self.unique_gates_failed,
            },
        }


def _sample_to_dict(sample: TraceSample) -> dict[str, Any]:
    """Convert TraceSample to dictionary."""
    return {
        "evaluation_id": sample.evaluation_id,
        "ticker": sample.ticker,
        "option_ticker": sample.option_ticker,
        "option_type": sample.option_type,
        "strike": sample.strike,
        "dte": sample.dte,
        "dte_bucket": sample.dte_bucket,
        "final_score": sample.final_score,
        "verdict": sample.verdict,
        "quality_tier": sample.quality_tier,
        "evaluated_at": sample.evaluated_at,
        "timestamp": sample.timestamp,
        "failed_gates": sample.failed_gates,
        "primary_reason": sample.primary_reason,
    }


def _evaluation_to_sample(eval_dict: dict[str, Any]) -> TraceSample:
    """Convert evaluation dict to TraceSample."""
    decision = eval_dict.get("decision", {}) or {}
    evaluated_at = eval_dict.get("evaluated_at", "")
    
    return TraceSample(
        evaluation_id=eval_dict.get("evaluation_id", ""),
        ticker=eval_dict.get("underlying_ticker", ""),
        option_ticker=eval_dict.get("option_ticker", ""),
        option_type=eval_dict.get("option_type", ""),
        strike=eval_dict.get("strike", 0.0),
        dte=eval_dict.get("dte", 0),
        dte_bucket=eval_dict.get("dte_bucket", ""),
        final_score=decision.get("final_score", 0.0),
        verdict=decision.get("verdict", ""),
        quality_tier=decision.get("quality_tier"),
        evaluated_at=evaluated_at,
        timestamp=evaluated_at,  # Full timestamp for linking
        failed_gates=decision.get("failed_gates", []) or [],
        primary_reason=decision.get("primary_reason_code"),
    )


class TraceSampler:
    """Samples representative traces for observability."""
    
    def __init__(
        self,
        gate_failure_limit: int = 10,
        reject_sample_limit: int = 10,
        approve_sample_limit: int = 10,
    ):
        """Initialize the trace sampler.
        
        Args:
            gate_failure_limit: Max number of common gate failures to return
            reject_sample_limit: Max number of highest-scoring rejects to return
            approve_sample_limit: Max number of lowest-scoring approves to return
        """
        self._gate_failure_limit = gate_failure_limit
        self._reject_sample_limit = reject_sample_limit
        self._approve_sample_limit = approve_sample_limit
    
    async def get_common_gate_failures(self) -> list[GateFailureSample]:
        """Get the most common gate failures with sample evaluations.
        
        Per Section 18.3: Sample 10 common gate failures.
        
        Returns:
            List of GateFailureSample sorted by failure count descending
        """
        # Get recent REJECT evaluations that were rejected by gates
        rejects = await EvaluationTable.list_by_verdict("REJECT", limit=200)
        
        # Filter to only gate-rejected evaluations
        gate_rejected = [
            e for e in rejects
            if e.get("decision", {}).get("primary_reason_code") == "REJECTED_BY_GATES"
        ]
        
        # Count failures by gate
        gate_counter: Counter[str] = Counter()
        gate_samples: dict[str, list[TraceSample]] = {}
        
        for eval_dict in gate_rejected:
            decision = eval_dict.get("decision", {}) or {}
            failed_gates = decision.get("failed_gates", []) or []
            
            for gate_id in failed_gates:
                gate_counter[gate_id] += 1
                
                # Keep up to 3 sample evaluations per gate
                if gate_id not in gate_samples:
                    gate_samples[gate_id] = []
                
                if len(gate_samples[gate_id]) < 3:
                    gate_samples[gate_id].append(_evaluation_to_sample(eval_dict))
        
        # Build result sorted by count
        results: list[GateFailureSample] = []
        for gate_id, count in gate_counter.most_common(self._gate_failure_limit):
            results.append(GateFailureSample(
                gate_id=gate_id,
                failure_count=count,
                sample_evaluations=gate_samples.get(gate_id, []),
            ))
        
        return results
    
    async def get_highest_reject_scores(self) -> list[TraceSample]:
        """Get REJECT evaluations with the highest scores (near-misses).
        
        Per Section 18.3: Sample 10 highest REJECT scores.
        These are interesting because they almost passed.
        
        Returns:
            List of TraceSample sorted by final_score descending
        """
        rejects = await EvaluationTable.list_by_verdict("REJECT", limit=100)
        
        # Convert to samples
        samples = [_evaluation_to_sample(e) for e in rejects]
        
        # Sort by final_score descending (highest first)
        samples.sort(key=lambda s: s.final_score, reverse=True)
        
        return samples[:self._reject_sample_limit]
    
    async def get_lowest_approve_scores(self) -> list[TraceSample]:
        """Get APPROVE evaluations with the lowest scores (borderline).
        
        Per Section 18.3: Sample 10 lowest APPROVE scores.
        These are interesting because they barely passed.
        
        Returns:
            List of TraceSample sorted by final_score ascending
        """
        approves = await EvaluationTable.list_by_verdict("APPROVE", limit=100)
        
        # Convert to samples
        samples = [_evaluation_to_sample(e) for e in approves]
        
        # Sort by final_score ascending (lowest first)
        samples.sort(key=lambda s: s.final_score)
        
        return samples[:self._approve_sample_limit]
    
    async def get_tier_1_approvals(self) -> list[TraceSample]:
        """Get all TIER_1 quality approvals.
        
        Per Section 18.3: Return all TIER_1 approvals (high-confidence trades).
        
        Returns:
            List of TraceSample for TIER_1 evaluations
        """
        approves = await EvaluationTable.list_by_verdict("APPROVE", limit=200)
        
        # Filter to TIER_1 only
        tier_1 = [
            e for e in approves
            if e.get("decision", {}).get("quality_tier") == "TIER_1"
        ]
        
        # Convert to samples and sort by score descending
        samples = [_evaluation_to_sample(e) for e in tier_1]
        samples.sort(key=lambda s: s.final_score, reverse=True)
        
        return samples
    
    async def get_all_traces(self) -> RepresentativeTraces:
        """Get all representative trace samples.
        
        Returns:
            RepresentativeTraces with all sample types
        """
        # Fetch all sample types in parallel could be done with asyncio.gather
        # but keeping it simple for now
        gate_failures = await self.get_common_gate_failures()
        highest_rejects = await self.get_highest_reject_scores()
        lowest_approves = await self.get_lowest_approve_scores()
        tier_1_approves = await self.get_tier_1_approvals()
        
        return RepresentativeTraces(
            common_gate_failures=gate_failures,
            highest_reject_scores=highest_rejects,
            lowest_approve_scores=lowest_approves,
            tier_1_approvals=tier_1_approves,
            total_rejects_sampled=len(highest_rejects),
            total_approves_sampled=len(lowest_approves),
            total_tier_1=len(tier_1_approves),
            unique_gates_failed=len(gate_failures),
        )


async def get_representative_traces(
    gate_failure_limit: int = 10,
    reject_sample_limit: int = 10,
    approve_sample_limit: int = 10,
) -> RepresentativeTraces:
    """Convenience function to get all representative traces.
    
    Args:
        gate_failure_limit: Max number of common gate failures
        reject_sample_limit: Max number of highest-scoring rejects
        approve_sample_limit: Max number of lowest-scoring approves
        
    Returns:
        RepresentativeTraces with all sample types
    """
    sampler = TraceSampler(
        gate_failure_limit=gate_failure_limit,
        reject_sample_limit=reject_sample_limit,
        approve_sample_limit=approve_sample_limit,
    )
    return await sampler.get_all_traces()
