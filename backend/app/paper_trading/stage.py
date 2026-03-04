"""Stage 8: Paper Trading Pipeline Stage.

Integrates paper trading into the pipeline orchestration.
Per Section 17 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.core.schemas import (
    Decision,
    Evaluation,
    PipelineStage,
    TrackingConfig,
    Verdict,
)
from app.core.pipeline import PipelineOrchestrator
from app.gates.models import GateEvaluation
from app.paper_trading.position_manager import create_positions_from_decisions
from app.paper_trading.shadow_tracker import (
    create_shadow_positions,
    select_shadow_candidates,
)

logger = logging.getLogger(__name__)


class PaperTradingStage:
    """Stage 8: Paper Trading.
    
    Creates paper positions for APPROVE and WATCH decisions,
    and selects shadow positions for REJECT validation.
    
    This stage is the final stage in the evaluation pipeline.
    """
    
    def __init__(
        self,
        orchestrator: PipelineOrchestrator,
        config: Optional[TrackingConfig] = None,
    ) -> None:
        """Initialize the paper trading stage.
        
        Args:
            orchestrator: Pipeline orchestrator for event tracking
            config: Optional tracking configuration
        """
        self._orchestrator = orchestrator or PipelineOrchestrator()
        self._config = config or TrackingConfig()
    
    async def execute(
        self,
        run_id: str,
        evaluations: Sequence[Evaluation],
        decisions: dict[str, Decision],
        gate_evaluations: dict[str, GateEvaluation],
        create_positions: bool = True,
        track_shadows: bool = True,
    ) -> dict:
        """Execute paper trading stage.
        
        Args:
            run_id: Pipeline run ID for tracking
            evaluations: Evaluations from Stage 3
            decisions: Dict mapping evaluation_id to Decision
            gate_evaluations: Dict mapping evaluation_id to GateEvaluation
            create_positions: If True, create paper positions for APPROVE/WATCH
            track_shadows: If True, select and track shadow REJECTs
            
        Returns:
            Dict with stage execution results
        """
        logger.info(f"Stage 8: Paper trading for {len(evaluations)} evaluations")
        
        # Update current stage
        await self._orchestrator.update_current_stage(run_id, PipelineStage.PAPER_TRADING)
        
        results = {
            "positions_created": 0,
            "approve_positions": 0,
            "watch_positions": 0,
            "shadow_candidates": 0,
            "near_miss_shadows": 0,
            "single_gate_shadows": 0,
            "random_shadows": 0,
        }
        
        if not evaluations:
            # Record empty stage event
            await self._orchestrator.record_stage_event(
                run_id=run_id,
                stage=PipelineStage.PAPER_TRADING,
                items_in=0,
                items_out=0,
                metadata=results,
            )
            return results
        
        # Step 1: Create paper positions for APPROVE/WATCH
        created_positions = []
        if create_positions:
            created_positions = await create_positions_from_decisions(
                evaluations=evaluations,
                decisions=decisions,
                config=self._config,
            )
            
            results["positions_created"] = len(created_positions)
            
            # Count by verdict
            for pos in created_positions:
                if hasattr(pos.verdict_at_entry, 'value'):
                    verdict = pos.verdict_at_entry.value
                else:
                    verdict = str(pos.verdict_at_entry)
                
                if verdict == "APPROVE":
                    results["approve_positions"] += 1
                elif verdict == "WATCH":
                    results["watch_positions"] += 1
        
        # Step 2: Select shadow candidates for REJECT tracking
        shadow_ids: list[str] = []
        shadow_positions = []
        if track_shadows:
            shadow_ids = select_shadow_candidates(
                evaluations=evaluations,
                decisions=decisions,
                gate_evaluations=gate_evaluations,
                config=self._config,
            )
            
            results["shadow_candidates"] = len(shadow_ids)
            
            # Create shadow position objects
            shadow_positions = create_shadow_positions(
                evaluations=evaluations,
                decisions=decisions,
                gate_evaluations=gate_evaluations,
                selected_ids=shadow_ids,
            )
            
            # Count by sample type
            for shadow in shadow_positions:
                if shadow.sample_type == "NEAR_MISS":
                    results["near_miss_shadows"] += 1
                elif shadow.sample_type == "SINGLE_GATE":
                    results["single_gate_shadows"] += 1
                else:
                    results["random_shadows"] += 1
        
        # Record stage event
        items_out = len(created_positions) + len(shadow_positions)
        
        await self._orchestrator.record_stage_event(
            run_id=run_id,
            stage=PipelineStage.PAPER_TRADING,
            items_in=len(evaluations),
            items_out=items_out,
            metadata=results,
        )
        
        logger.info(
            f"Stage 8 complete: {results['positions_created']} positions created "
            f"({results['approve_positions']} APPROVE, {results['watch_positions']} WATCH), "
            f"{results['shadow_candidates']} shadow candidates"
        )
        
        return results


async def run_paper_trading(
    run_id: str,
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    gate_evaluations: dict[str, GateEvaluation],
    orchestrator: PipelineOrchestrator,
    config: Optional[TrackingConfig] = None,
    create_positions: bool = True,
    track_shadows: bool = True,
) -> dict:
    """Convenience function to run paper trading stage.
    
    Args:
        run_id: Pipeline run ID
        evaluations: Evaluations from Stage 3
        decisions: Dict mapping evaluation_id to Decision
        gate_evaluations: Dict mapping evaluation_id to GateEvaluation
        orchestrator: Pipeline orchestrator
        config: Optional tracking configuration
        create_positions: Whether to create paper positions
        track_shadows: Whether to track shadow REJECTs
        
    Returns:
        Dict with stage execution results
    """
    stage = PaperTradingStage(
        orchestrator=orchestrator,
        config=config,
    )
    return await stage.execute(
        run_id=run_id,
        evaluations=evaluations,
        decisions=decisions,
        gate_evaluations=gate_evaluations,
        create_positions=create_positions,
        track_shadows=track_shadows,
    )


def get_actionable_decisions(
    decisions: dict[str, Decision],
) -> list[tuple[str, Decision]]:
    """Filter decisions to only actionable ones (APPROVE/WATCH).
    
    Args:
        decisions: Dict mapping evaluation_id to Decision
        
    Returns:
        List of (evaluation_id, Decision) tuples for actionable verdicts
    """
    actionable = []
    for eval_id, decision in decisions.items():
        if decision.verdict in (Verdict.APPROVE, Verdict.WATCH):
            actionable.append((eval_id, decision))
    return actionable


def summarize_pipeline_results(
    evaluations: Sequence[Evaluation],
    decisions: dict[str, Decision],
    paper_trading_results: dict,
) -> dict:
    """Create a summary of pipeline results including paper trading.
    
    Args:
        evaluations: All evaluations
        decisions: All decisions
        paper_trading_results: Results from paper trading stage
        
    Returns:
        Summary dict for reporting
    """
    # Count by verdict
    approve_count = sum(
        1 for d in decisions.values() if d.verdict == Verdict.APPROVE
    )
    watch_count = sum(
        1 for d in decisions.values() if d.verdict == Verdict.WATCH
    )
    reject_count = sum(
        1 for d in decisions.values() if d.verdict == Verdict.REJECT
    )
    
    # Count by quality tier
    tier_counts = {"TIER_1": 0, "TIER_2": 0, "TIER_3": 0}
    for decision in decisions.values():
        if decision.verdict == Verdict.APPROVE and decision.quality_tier:
            tier = str(decision.quality_tier)
            if hasattr(decision.quality_tier, 'value'):
                tier = decision.quality_tier.value
            if tier in tier_counts:
                tier_counts[tier] += 1
    
    return {
        "total_evaluations": len(evaluations),
        "verdicts": {
            "approve": approve_count,
            "watch": watch_count,
            "reject": reject_count,
        },
        "quality_tiers": tier_counts,
        "paper_trading": {
            "positions_created": paper_trading_results.get("positions_created", 0),
            "approve_positions": paper_trading_results.get("approve_positions", 0),
            "watch_positions": paper_trading_results.get("watch_positions", 0),
        },
        "shadow_tracking": {
            "total_candidates": paper_trading_results.get("shadow_candidates", 0),
            "near_miss": paper_trading_results.get("near_miss_shadows", 0),
            "single_gate": paper_trading_results.get("single_gate_shadows", 0),
            "random": paper_trading_results.get("random_shadows", 0),
        },
    }
