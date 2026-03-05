"""Pipeline orchestration service.

Handles pipeline run tracking, stage event logging, and telemetry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from app.core.schemas import (
    PipelineRun,
    PipelineStage,
    RunStatus,
    StageEvent,
)
from app.db.tables import PipelineRunTable, StageEventTable

logger = logging.getLogger(__name__)


class NoOpOrchestrator:
    """No-op orchestrator that skips DynamoDB writes.

    Used in streaming backtest mode where per-evaluation stage events
    are wasteful (N×3 DynamoDB writes). Aggregate events are recorded
    once after the streaming loop completes.
    """

    async def update_current_stage(self, run_id: str, stage: PipelineStage) -> None:
        pass

    async def record_stage_event(self, **kwargs: Any) -> StageEvent:
        return StageEvent(
            run_id=kwargs.get("run_id", ""),
            stage=kwargs.get("stage", PipelineStage.OPPORTUNITY_DISCOVERY),
            started_at="",
            completed_at="",
            items_in=kwargs.get("items_in", 0),
            items_out=kwargs.get("items_out", 0),
            items_dropped=0,
        )

    async def complete_run(self, run_id: str, status: str = "completed") -> None:
        pass


class PipelineOrchestrator:
    """Orchestrator for pipeline execution and monitoring."""

    async def start_run(self, policy_version: str) -> PipelineRun:
        """Start a new pipeline run.

        Args:
            policy_version: Version of policy to use for this run

        Returns:
            The created PipelineRun
        """
        run = PipelineRun(
            run_id=str(uuid4()),
            policy_version=policy_version,
            status=RunStatus.RUNNING,
            current_stage=PipelineStage.OPPORTUNITY_DISCOVERY,
        )

        await PipelineRunTable.put(run)
        logger.info(f"Started pipeline run {run.run_id} with policy {policy_version}")

        return run

    async def get_run(self, run_id: str) -> Optional[PipelineRun]:
        """Get a pipeline run by ID.

        Note: This requires knowing the started_at timestamp. For now,
        we search recent runs to find the matching ID.

        Args:
            run_id: The run ID to find

        Returns:
            The PipelineRun if found, None otherwise
        """
        # Search recent runs for the matching ID
        runs = await self.list_runs(limit=100)
        for run in runs:
            if run.run_id == run_id:
                return run
        return None

    async def list_runs(
        self,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> list[PipelineRun]:
        """List recent pipeline runs.

        Args:
            limit: Maximum number of runs to return
            status: Optional status filter

        Returns:
            List of PipelineRun objects (most recent first)
        """
        return await PipelineRunTable.list(limit=limit, status=status)

    async def record_stage_event(
        self,
        run_id: str,
        stage: PipelineStage,
        items_in: int,
        items_out: int,
        drop_reasons: Optional[dict[str, int]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> StageEvent:
        """Record a stage completion event.

        Args:
            run_id: The pipeline run ID
            stage: The stage that completed
            items_in: Number of items entering the stage
            items_out: Number of items exiting the stage
            drop_reasons: Optional dict of reason codes to counts
            metadata: Optional additional metadata

        Returns:
            The created StageEvent
        """
        now = datetime.now(timezone.utc).isoformat()
        items_dropped = items_in - items_out

        event = StageEvent(
            run_id=run_id,
            stage=stage,
            started_at=now,
            completed_at=now,
            items_in=items_in,
            items_out=items_out,
            items_dropped=items_dropped,
            drop_reasons=drop_reasons or {},
            metadata=metadata or {},
        )

        await StageEventTable.put(event)
        logger.info(
            f"Run {run_id} stage {stage}: {items_in} in, {items_out} out, {items_dropped} dropped"
        )

        return event

    async def get_stage_events(self, run_id: str) -> list[StageEvent]:
        """Get all stage events for a pipeline run.

        Args:
            run_id: The pipeline run ID

        Returns:
            List of StageEvent objects in chronological order
        """
        return await StageEventTable.list_by_run(run_id)

    async def complete_run(
        self,
        run_id: str,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> Optional[PipelineRun]:
        """Mark a pipeline run as complete.

        Args:
            run_id: The pipeline run ID
            status: Final status (completed, failed, cancelled)
            error_message: Optional error message for failures

        Returns:
            The updated PipelineRun if found, None otherwise
        """
        run = await self.get_run(run_id)
        if not run:
            return None

        now = datetime.now(timezone.utc).isoformat()

        # Map string status to enum
        status_map = {
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }
        run_status = status_map.get(status.lower(), RunStatus.COMPLETED)

        # Count results from stage events
        events = await self.get_stage_events(run_id)
        total_opportunities = 0
        total_evaluations = 0
        total_approves = 0
        total_watches = 0
        total_rejects = 0

        for event in events:
            if event.stage == PipelineStage.OPPORTUNITY_DISCOVERY:
                total_opportunities = event.items_out
            elif event.stage == PipelineStage.CONTRACT_SELECTION:
                total_evaluations = event.items_out
            elif event.stage == PipelineStage.DECISION_LOGIC:
                # Get verdict counts from metadata
                total_approves = event.metadata.get("approves", 0)
                total_watches = event.metadata.get("watches", 0)
                total_rejects = event.metadata.get("rejects", 0)

        # Build list of completed stages
        stages_completed = [e.stage for e in events]

        updates = {
            "status": run_status.value,
            "completed_at": now,
            "current_stage": None,
            "stages_completed": stages_completed,
            "total_opportunities": total_opportunities,
            "total_evaluations": total_evaluations,
            "total_approves": total_approves,
            "total_watches": total_watches,
            "total_rejects": total_rejects,
        }

        if error_message:
            updates["error_message"] = error_message

        updated_run = await PipelineRunTable.update(
            run_id, run.started_at, updates
        )

        if updated_run:
            logger.info(f"Pipeline run {run_id} completed with status: {status}")

        return updated_run

    async def update_current_stage(
        self,
        run_id: str,
        stage: PipelineStage,
    ) -> Optional[PipelineRun]:
        """Update the current stage of a pipeline run.

        Args:
            run_id: The pipeline run ID
            stage: The new current stage

        Returns:
            The updated PipelineRun if found, None otherwise
        """
        run = await self.get_run(run_id)
        if not run:
            return None

        return await PipelineRunTable.update(
            run_id,
            run.started_at,
            {"current_stage": stage.value},
        )

    async def get_stats(self, days: int = 7) -> dict[str, Any]:
        """Get pipeline statistics for the specified time period.

        Args:
            days: Number of days to include in stats

        Returns:
            Dictionary of statistics
        """
        runs = await self.list_runs(limit=1000)

        # Filter to recent runs
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        recent_runs = [
            r for r in runs
            if datetime.fromisoformat(r.started_at.replace("Z", "+00:00")) > cutoff
        ]

        # Compute stats
        total_runs = len(recent_runs)
        completed_runs = [r for r in recent_runs if r.status == RunStatus.COMPLETED]
        failed_runs = [r for r in recent_runs if r.status == RunStatus.FAILED]

        total_opportunities = sum(r.total_opportunities for r in completed_runs)
        total_evaluations = sum(r.total_evaluations for r in completed_runs)
        total_approves = sum(r.total_approves for r in completed_runs)
        total_watches = sum(r.total_watches for r in completed_runs)
        total_rejects = sum(r.total_rejects for r in completed_runs)

        # Compute averages
        if completed_runs:
            avg_opportunities = total_opportunities / len(completed_runs)
            avg_evaluations = total_evaluations / len(completed_runs)
        else:
            avg_opportunities = 0
            avg_evaluations = 0

        # Compute rates
        if total_evaluations > 0:
            approve_rate = total_approves / total_evaluations * 100
            watch_rate = total_watches / total_evaluations * 100
            reject_rate = total_rejects / total_evaluations * 100
        else:
            approve_rate = watch_rate = reject_rate = 0

        return {
            "period_days": days,
            "total_runs": total_runs,
            "completed_runs": len(completed_runs),
            "failed_runs": len(failed_runs),
            "total_opportunities": total_opportunities,
            "total_evaluations": total_evaluations,
            "total_approves": total_approves,
            "total_watches": total_watches,
            "total_rejects": total_rejects,
            "avg_opportunities_per_run": round(avg_opportunities, 1),
            "avg_evaluations_per_run": round(avg_evaluations, 1),
            "approve_rate_pct": round(approve_rate, 2),
            "watch_rate_pct": round(watch_rate, 2),
            "reject_rate_pct": round(reject_rate, 2),
        }
