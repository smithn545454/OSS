"""Shadow tracking for REJECT validation.

Per Section 17.5 of OSS_Complete_Requirements.md.

Shadow tracking samples REJECTs to measure false negatives:
- Random 5% of REJECTs
- All near-miss REJECTs (score 60-65)
- All single-gate-failure REJECTs
"""

from __future__ import annotations

import logging
import random
from typing import Optional, Sequence

from app.core.schemas import Decision, Evaluation, TrackingConfig, Verdict
from app.gates.models import GateEvaluation
from app.paper_trading.models import ShadowPosition

logger = logging.getLogger(__name__)


def select_shadow_candidates(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    gate_evaluations: dict[str, GateEvaluation],
    config: Optional[TrackingConfig] = None,
) -> list[str]:
    """Select REJECT evaluations for shadow tracking.
    
    Per Section 17.5:
    - Random 5% of all REJECTs
    - All near-miss REJECTs (score 60-65)
    - All single-gate-failure REJECTs
    
    Args:
        evaluations: List of all evaluations
        decisions: Dict mapping evaluation_id to Decision
        gate_evaluations: Dict mapping evaluation_id to GateEvaluation
        config: Optional tracking configuration
        
    Returns:
        List of evaluation_ids selected for shadow tracking
    """
    if config is None:
        config = TrackingConfig()
    
    selected: set[str] = set()
    
    # Find all REJECT evaluations
    reject_evals = [
        e for e in evaluations
        if e.evaluation_id in decisions
        and decisions[e.evaluation_id].verdict == Verdict.REJECT
    ]
    
    if not reject_evals:
        return []
    
    # Categorize REJECTs
    near_miss_ids: list[str] = []
    single_gate_ids: list[str] = []
    random_pool: list[str] = []
    
    for evaluation in reject_evals:
        eval_id = evaluation.evaluation_id
        decision = decisions[eval_id]
        gate_eval = gate_evaluations.get(eval_id)
        
        # Check for near-miss (score 60-65, rejected by score not gates)
        if is_near_miss(decision, config):
            near_miss_ids.append(eval_id)
            selected.add(eval_id)
            continue
        
        # Check for single gate failure
        if gate_eval and is_single_gate_failure(gate_eval):
            single_gate_ids.append(eval_id)
            selected.add(eval_id)
            continue
        
        # Add to random pool
        random_pool.append(eval_id)
    
    # Random sample from remaining REJECTs
    sample_count = int(len(random_pool) * config.shadow_sample_rate)
    sample_count = max(1, sample_count)  # At least 1 if any available
    
    if random_pool and sample_count > 0:
        random_sample = random.sample(
            random_pool,
            min(sample_count, len(random_pool))
        )
        selected.update(random_sample)
    
    logger.info(
        f"Shadow tracking selection: {len(near_miss_ids)} near-miss, "
        f"{len(single_gate_ids)} single-gate, {len(selected) - len(near_miss_ids) - len(single_gate_ids)} random"
    )
    
    return list(selected)


def is_near_miss(
    decision: Decision,
    config: Optional[TrackingConfig] = None,
) -> bool:
    """Check if a decision is a near-miss REJECT.
    
    Near-miss criteria:
    - Score between 60 and 65 (configurable threshold)
    - Rejected by score, not by gates
    
    Args:
        decision: The decision to check
        config: Optional tracking configuration
        
    Returns:
        True if this is a near-miss REJECT
    """
    if config is None:
        config = TrackingConfig()
    
    # Must be rejected by score, not gates
    if decision.primary_reason_code == "REJECTED_BY_GATES":
        return False
    
    # Check score range (60 to approve threshold - 10)
    # Default approve threshold is 65, so near-miss is 55-65
    # Per Section 17.5: score 60-65
    min_threshold = config.shadow_near_miss_threshold  # Default 60
    max_threshold = 65  # Just below WATCH threshold
    
    return min_threshold <= decision.final_score < max_threshold


def is_single_gate_failure(gate_eval: GateEvaluation) -> bool:
    """Check if evaluation failed exactly one gate.
    
    Single gate failures are interesting because the evaluation
    would have passed if that one gate had different thresholds.
    
    Args:
        gate_eval: The gate evaluation results
        
    Returns:
        True if exactly one gate failed
    """
    failed_count = len(gate_eval.failed_gates)
    return failed_count == 1


def create_shadow_positions(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    gate_evaluations: dict[str, GateEvaluation],
    selected_ids: Sequence[str],
) -> list[ShadowPosition]:
    """Create shadow position records for selected REJECTs.
    
    Args:
        evaluations: List of evaluations
        decisions: Dict mapping evaluation_id to Decision
        gate_evaluations: Dict mapping evaluation_id to GateEvaluation
        selected_ids: List of evaluation IDs selected for shadow tracking
        
    Returns:
        List of ShadowPosition objects
    """
    from datetime import datetime, timezone
    
    eval_map = {e.evaluation_id: e for e in evaluations}
    shadow_positions: list[ShadowPosition] = []
    
    for eval_id in selected_ids:
        evaluation = eval_map.get(eval_id)
        decision = decisions.get(eval_id)
        gate_eval = gate_evaluations.get(eval_id)
        
        if not evaluation or not decision:
            continue
        
        # Determine sample type
        sample_type = determine_sample_type(decision, gate_eval)
        
        # Get failed gates list
        failed_gates: list[str] = []
        if gate_eval:
            failed_gates = list(gate_eval.failed_gates)
        
        # Create shadow position
        now = datetime.now(timezone.utc).isoformat()
        
        shadow = ShadowPosition(
            evaluation_id=eval_id,
            option_ticker=evaluation.option_ticker,
            entry_price=evaluation.mid,
            entry_date=now[:10],
            rejection_reason=decision.primary_reason_code,
            final_score=decision.final_score,
            failed_gates=failed_gates,
            sample_type=sample_type,
            current_price=evaluation.mid,
            current_pnl_pct=0.0,
        )
        
        shadow_positions.append(shadow)
    
    return shadow_positions


def determine_sample_type(
    decision: Decision,
    gate_eval: Optional[GateEvaluation],
    config: Optional[TrackingConfig] = None,
) -> str:
    """Determine the sample type for a shadow position.
    
    Args:
        decision: The decision
        gate_eval: Optional gate evaluation
        config: Optional tracking configuration
        
    Returns:
        Sample type string: "NEAR_MISS", "SINGLE_GATE", or "RANDOM"
    """
    if is_near_miss(decision, config):
        return "NEAR_MISS"
    
    if gate_eval and is_single_gate_failure(gate_eval):
        return "SINGLE_GATE"
    
    return "RANDOM"


def analyze_shadow_tracking_results(
    shadow_positions: Sequence[ShadowPosition],
) -> dict:
    """Analyze shadow tracking results to measure false negatives.
    
    Per Section 4.5:
    - Target: REJECT false negative rate < 10%
    
    Args:
        shadow_positions: List of shadow positions (ideally after tracking period)
        
    Returns:
        Analysis results dict
    """
    if not shadow_positions:
        return {
            "total_tracked": 0,
            "false_negatives": 0,
            "false_negative_rate": 0.0,
            "by_sample_type": {},
        }
    
    # Categorize by sample type
    by_type: dict[str, list[ShadowPosition]] = {
        "NEAR_MISS": [],
        "SINGLE_GATE": [],
        "RANDOM": [],
    }
    
    false_negatives = 0
    
    for shadow in shadow_positions:
        sample_type = shadow.sample_type
        if sample_type in by_type:
            by_type[sample_type].append(shadow)
        else:
            by_type["RANDOM"].append(shadow)
        
        if shadow.is_false_negative:
            false_negatives += 1
    
    # Analyze by type
    type_analysis = {}
    for sample_type, positions in by_type.items():
        if not positions:
            continue
        
        type_false_negatives = sum(1 for p in positions if p.is_false_negative)
        type_would_hit_target = sum(1 for p in positions if p.would_have_hit_target)
        type_would_hit_stop = sum(1 for p in positions if p.would_have_hit_stop)
        
        avg_peak = sum(p.peak_pnl_pct for p in positions) / len(positions)
        avg_trough = sum(p.trough_pnl_pct for p in positions) / len(positions)
        
        type_analysis[sample_type] = {
            "count": len(positions),
            "false_negatives": type_false_negatives,
            "false_negative_rate": (type_false_negatives / len(positions)) * 100,
            "would_hit_target": type_would_hit_target,
            "would_hit_stop": type_would_hit_stop,
            "avg_peak_pnl": round(avg_peak, 2),
            "avg_trough_pnl": round(avg_trough, 2),
        }
    
    return {
        "total_tracked": len(shadow_positions),
        "false_negatives": false_negatives,
        "false_negative_rate": (false_negatives / len(shadow_positions)) * 100,
        "by_sample_type": type_analysis,
    }


def get_false_negative_insights(
    shadow_positions: Sequence[ShadowPosition],
) -> list[dict]:
    """Get insights on why REJECTs were false negatives.
    
    Useful for tuning gate thresholds.
    
    Args:
        shadow_positions: List of shadow positions
        
    Returns:
        List of insight dicts for false negatives
    """
    false_negatives = [p for p in shadow_positions if p.is_false_negative]
    
    insights = []
    for shadow in false_negatives:
        insights.append({
            "evaluation_id": shadow.evaluation_id,
            "option_ticker": shadow.option_ticker,
            "sample_type": shadow.sample_type,
            "final_score": shadow.final_score,
            "rejection_reason": shadow.rejection_reason,
            "failed_gates": shadow.failed_gates,
            "peak_pnl_pct": round(shadow.peak_pnl_pct, 2),
            "would_have_hit_target": shadow.would_have_hit_target,
            "days_tracked": shadow.days_tracked,
            "recommendation": _get_recommendation(shadow),
        })
    
    return insights


def _get_recommendation(shadow: ShadowPosition) -> str:
    """Generate a recommendation for tuning based on false negative.
    
    Args:
        shadow: The false negative shadow position
        
    Returns:
        Recommendation string
    """
    if shadow.sample_type == "SINGLE_GATE" and shadow.failed_gates:
        gate = shadow.failed_gates[0]
        return f"Consider relaxing {gate} threshold"
    
    if shadow.sample_type == "NEAR_MISS":
        return "Consider lowering WATCH threshold from 65"
    
    return "Review rejection criteria"
