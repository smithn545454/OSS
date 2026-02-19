"""DynamoDB table definitions and operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import (
    Decision,
    Evaluation,
    FeatureValue,
    GateResult,
    IVHistory,
    LLMUsage,
    OIHistory,
    Opportunity,
    PaperPosition,
    PillarScore,
    PipelineRun,
    Policy,
    StageEvent,
    TradeThesis,
)
from app.db.dynamodb import get_dynamodb

logger = logging.getLogger(__name__)

# Table suffixes
POLICIES_TABLE = "policies"
OPPORTUNITIES_TABLE = "opportunities"
EVALUATIONS_TABLE = "evaluations"
PIPELINE_RUNS_TABLE = "pipeline-runs"
STAGE_EVENTS_TABLE = "stage-events"
PAPER_POSITIONS_TABLE = "paper-positions"
FEATURE_VALUES_TABLE = "feature-values"
PILLAR_SCORES_TABLE = "pillar-scores"
GATE_RESULTS_TABLE = "gate-results"
IV_HISTORY_TABLE = "iv-history"
OI_HISTORY_TABLE = "oi-history"
TRADE_THESIS_TABLE = "trade-thesis"
LLM_USAGE_TABLE = "llm-usage"
PAPER_SNAPSHOTS_TABLE = "paper-snapshots"
CALIBRATION_REPORTS_TABLE = "calibration-reports"
SCAN_STATUS_TABLE = "scan-status"
SP500_TICKERS_TABLE = "sp500-tickers"


class PolicyTable:
    """Operations for the policies table."""

    TABLE = POLICIES_TABLE

    @staticmethod
    async def put(policy: Policy) -> None:
        """Store a policy version."""
        db = get_dynamodb()
        item = policy.to_dynamodb_item()
        item["PK"] = "POLICY"
        item["SK"] = policy.version
        await db.put_item(PolicyTable.TABLE, item)

    @staticmethod
    async def get(version: str) -> Optional[Policy]:
        """Get a policy by version."""
        db = get_dynamodb()
        item = await db.get_item(PolicyTable.TABLE, "POLICY", version)
        if item:
            # Remove DynamoDB keys before constructing model
            item.pop("PK", None)
            item.pop("SK", None)
            return Policy(**item)
        return None

    @staticmethod
    async def get_active() -> Optional[Policy]:
        """Get the currently active policy."""
        db = get_dynamodb()
        items = await db.query(PolicyTable.TABLE, "POLICY", limit=100, scan_forward=False)
        for item in items:
            if item.get("is_active"):
                item.pop("PK", None)
                item.pop("SK", None)
                return Policy(**item)
        return None

    @staticmethod
    async def list(limit: int = 20) -> list[Policy]:
        """List policy versions (most recent first)."""
        db = get_dynamodb()
        items = await db.query(PolicyTable.TABLE, "POLICY", limit=limit, scan_forward=False)
        policies = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            policies.append(Policy(**item))
        return policies

    @staticmethod
    async def set_active(version: str) -> None:
        """Set a policy as active (deactivate others)."""
        db = get_dynamodb()

        # Get all policies and deactivate them
        items = await db.query(PolicyTable.TABLE, "POLICY", limit=100)
        for item in items:
            if item.get("is_active") and item.get("SK") != version:
                await db.update_item(
                    PolicyTable.TABLE,
                    item["PK"],
                    item["SK"],
                    {"is_active": False},
                )

        # Activate the specified version
        await db.update_item(
            PolicyTable.TABLE,
            "POLICY",
            version,
            {"is_active": True},
        )


class OpportunityTable:
    """Operations for the opportunities table."""

    TABLE = OPPORTUNITIES_TABLE

    @staticmethod
    async def put(opportunity: Opportunity) -> None:
        """Store an opportunity."""
        db = get_dynamodb()
        item = opportunity.to_dynamodb_item()
        item["PK"] = f"OPP#{opportunity.underlying_ticker}"
        item["SK"] = f"{opportunity.timestamp_utc}#{opportunity.opportunity_id}"
        # GSI for date-based queries
        date_str = opportunity.timestamp_utc[:10]  # YYYY-MM-DD
        item["GSI1PK"] = f"DATE#{date_str}"
        item["GSI1SK"] = f"{opportunity.priority_score:03d}#{opportunity.underlying_ticker}"
        await db.put_item(OpportunityTable.TABLE, item)

    @staticmethod
    async def get(ticker: str, timestamp: str, opportunity_id: str) -> Optional[Opportunity]:
        """Get an opportunity by key."""
        db = get_dynamodb()
        item = await db.get_item(
            OpportunityTable.TABLE,
            f"OPP#{ticker}",
            f"{timestamp}#{opportunity_id}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            return Opportunity(**item)
        return None

    @staticmethod
    async def list_by_ticker(ticker: str, limit: int = 50) -> list[Opportunity]:
        """List opportunities for a ticker."""
        db = get_dynamodb()
        items = await db.query(
            OpportunityTable.TABLE,
            f"OPP#{ticker}",
            limit=limit,
            scan_forward=False,
        )
        opportunities = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            opportunities.append(Opportunity(**item))
        return opportunities

    @staticmethod
    async def list_by_date(date: str, limit: int = 100) -> list[Opportunity]:
        """List opportunities for a date."""
        db = get_dynamodb()
        items = await db.query(
            OpportunityTable.TABLE,
            f"DATE#{date}",
            limit=limit,
            scan_forward=False,
            index_name="GSI1",
        )
        opportunities = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            opportunities.append(Opportunity(**item))
        return opportunities


class EvaluationTable:
    """Operations for the evaluations table."""

    TABLE = EVALUATIONS_TABLE

    @staticmethod
    async def put(evaluation: Evaluation, decision: Optional[Decision] = None) -> None:
        """Store an evaluation with optional decision."""
        db = get_dynamodb()
        item = evaluation.to_dynamodb_item()
        item["PK"] = f"EVAL#{evaluation.underlying_ticker}"
        item["SK"] = f"{evaluation.evaluated_at}#{evaluation.evaluation_id}"

        if decision:
            item["decision"] = decision.to_dynamodb_item()
            # GSI for verdict-based queries
            item["GSI1PK"] = f"VERDICT#{decision.verdict}"
            item["GSI1SK"] = evaluation.evaluated_at
            # GSI for date-based queries
            date_str = evaluation.evaluated_at[:10]
            item["GSI2PK"] = f"DATE#{date_str}"
            item["GSI2SK"] = f"{decision.final_score:06.2f}"

        await db.put_item(EvaluationTable.TABLE, item)

    @staticmethod
    async def get(
        ticker: str, timestamp: str, evaluation_id: str
    ) -> Optional[dict[str, Any]]:
        """Get an evaluation with decision."""
        db = get_dynamodb()
        item = await db.get_item(
            EvaluationTable.TABLE,
            f"EVAL#{ticker}",
            f"{timestamp}#{evaluation_id}",
        )
        if item:
            # Clean up DynamoDB keys
            for key in ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"]:
                item.pop(key, None)
            return item
        return None

    @staticmethod
    async def list_by_verdict(
        verdict: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List evaluations by verdict."""
        db = get_dynamodb()
        items = await db.query(
            EvaluationTable.TABLE,
            f"VERDICT#{verdict}",
            limit=limit,
            scan_forward=False,
            index_name="GSI1",
        )
        for item in items:
            for key in ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"]:
                item.pop(key, None)
        return items

    @staticmethod
    async def list_by_date(
        date: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List evaluations for a date.

        Args:
            date: Date string in YYYY-MM-DD format
            limit: Maximum results to return

        Returns:
            List of evaluation dicts
        """
        db = get_dynamodb()
        items = await db.query(
            EvaluationTable.TABLE,
            f"DATE#{date}",
            limit=limit,
            scan_forward=False,
            index_name="GSI2",
        )
        for item in items:
            for key in ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"]:
                item.pop(key, None)
        return items

    @staticmethod
    async def get_by_id(
        ticker: str, evaluation_id: str
    ) -> Optional[dict[str, Any]]:
        """Get an evaluation by ticker and evaluation_id.

        Queries by PK and scans SK suffix for evaluation_id,
        avoiding URL-encoding issues with ISO timestamps.
        """
        db = get_dynamodb()
        items = await db.query(
            EvaluationTable.TABLE,
            f"EVAL#{ticker}",
            limit=None,
            scan_forward=False,
        )
        for item in items:
            sk = item.get("SK", "")
            if sk.endswith(evaluation_id):
                for key in ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"]:
                    item.pop(key, None)
                return item
        return None


class PipelineRunTable:
    """Operations for the pipeline-runs table."""

    TABLE = PIPELINE_RUNS_TABLE

    @staticmethod
    async def put(run: PipelineRun) -> None:
        """Store a pipeline run."""
        db = get_dynamodb()
        item = run.to_dynamodb_item()
        item["PK"] = "RUN"
        item["SK"] = f"{run.started_at}#{run.run_id}"
        await db.put_item(PipelineRunTable.TABLE, item)

    @staticmethod
    async def get(run_id: str, started_at: str) -> Optional[PipelineRun]:
        """Get a pipeline run."""
        db = get_dynamodb()
        item = await db.get_item(
            PipelineRunTable.TABLE,
            "RUN",
            f"{started_at}#{run_id}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return PipelineRun(**item)
        return None

    @staticmethod
    async def list(limit: int = 20, status: Optional[str] = None) -> list[PipelineRun]:
        """List pipeline runs (most recent first)."""
        db = get_dynamodb()
        items = await db.query(
            PipelineRunTable.TABLE,
            "RUN",
            limit=limit,
            scan_forward=False,
        )
        runs = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            run = PipelineRun(**item)
            if status is None or run.status == status:
                runs.append(run)
        return runs

    @staticmethod
    async def update(run_id: str, started_at: str, updates: dict[str, Any]) -> Optional[PipelineRun]:
        """Update a pipeline run."""
        db = get_dynamodb()
        item = await db.update_item(
            PipelineRunTable.TABLE,
            "RUN",
            f"{started_at}#{run_id}",
            updates,
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return PipelineRun(**item)
        return None

    @staticmethod
    async def list_recent(limit: int = 20) -> list[PipelineRun]:
        """List most recent pipeline runs. Alias for list()."""
        return await PipelineRunTable.list(limit=limit)

    @staticmethod
    async def list_by_date_range(
        start_iso: str, end_iso: str, limit: int = 100
    ) -> list[PipelineRun]:
        """List pipeline runs within a date range.

        SK format is {started_at}#{run_id}, so date range queries work
        via DynamoDB BETWEEN on the SK.
        """
        db = get_dynamodb()
        table = db.get_table(PipelineRunTable.TABLE)
        response = table.query(
            KeyConditionExpression="PK = :pk AND SK BETWEEN :start AND :end",
            ExpressionAttributeValues={
                ":pk": "RUN",
                ":start": start_iso,
                ":end": end_iso + "~",  # ~ sorts after all ISO chars
            },
            ScanIndexForward=False,
            Limit=limit,
        )
        runs = []
        for item in response.get("Items", []):
            item = db.convert_from_dynamodb(item)
            item.pop("PK", None)
            item.pop("SK", None)
            runs.append(PipelineRun(**item))
        return runs

    @staticmethod
    async def increment_chunks_completed(run_id: str, started_at: str) -> int:
        """Atomically increment chunks_completed and return the new value.

        Uses DynamoDB ADD expression for safe concurrent updates from workers.
        """
        db = get_dynamodb()
        table = db.get_table(PipelineRunTable.TABLE)
        response = table.update_item(
            Key={"PK": "RUN", "SK": f"{started_at}#{run_id}"},
            UpdateExpression="ADD chunks_completed :inc",
            ExpressionAttributeValues={":inc": 1},
            ReturnValues="ALL_NEW",
        )
        attrs = db.convert_from_dynamodb(response.get("Attributes", {}))
        return int(attrs.get("chunks_completed", 0))

    @staticmethod
    async def get_by_id(run_id: str) -> Optional[PipelineRun]:
        """Find a pipeline run by run_id (searches recent runs)."""
        db = get_dynamodb()
        items = await db.query(
            PipelineRunTable.TABLE,
            "RUN",
            limit=50,
            scan_forward=False,
        )
        for item in items:
            sk = item.get("SK", "")
            if sk.endswith(run_id):
                item.pop("PK", None)
                item.pop("SK", None)
                return PipelineRun(**item)
        return None


class StageEventTable:
    """Operations for the stage-events table."""

    TABLE = STAGE_EVENTS_TABLE

    @staticmethod
    async def put(event: StageEvent) -> None:
        """Store a stage event."""
        db = get_dynamodb()
        item = event.to_dynamodb_item()
        item["PK"] = f"RUN#{event.run_id}"
        item["SK"] = f"{event.started_at}#{event.stage}"
        await db.put_item(StageEventTable.TABLE, item)

    @staticmethod
    async def list_by_run(run_id: str) -> list[StageEvent]:
        """List stage events for a pipeline run."""
        db = get_dynamodb()
        items = await db.query(
            StageEventTable.TABLE,
            f"RUN#{run_id}",
            scan_forward=True,  # Chronological order
            limit=None,  # Paginate through all results
        )
        events = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            events.append(StageEvent(**item))
        return events


class PaperPositionTable:
    """Operations for the paper-positions table."""

    TABLE = PAPER_POSITIONS_TABLE

    @staticmethod
    async def put(position: PaperPosition) -> None:
        """Store a paper position."""
        db = get_dynamodb()
        item = position.to_dynamodb_item()
        status = getattr(position.status, "value", position.status)
        item["PK"] = f"POS#{status}"
        item["SK"] = f"{position.entry_date}#{position.position_id}"
        # GSI for evaluation_id lookups (duplicate prevention)
        item["GSI1PK"] = f"EVAL#{position.evaluation_id}"
        item["GSI1SK"] = position.entry_date
        await db.put_item(PaperPositionTable.TABLE, item)

    @staticmethod
    async def list_open(limit: Optional[int] = None) -> list[PaperPosition]:
        """List open paper positions."""
        db = get_dynamodb()
        items = await db.query(
            PaperPositionTable.TABLE,
            "POS#OPEN",
            limit=limit,
            scan_forward=False,
        )
        positions = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            positions.append(PaperPosition(**item))
        return positions

    @staticmethod
    async def list_closed(limit: Optional[int] = None) -> list[PaperPosition]:
        """List closed paper positions."""
        db = get_dynamodb()
        items = await db.query(
            PaperPositionTable.TABLE,
            "POS#CLOSED",
            limit=limit,
            scan_forward=False,
        )
        positions = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            positions.append(PaperPosition(**item))
        return positions

    @staticmethod
    async def get_by_evaluation_id(evaluation_id: str) -> Optional[PaperPosition]:
        """Get a position by evaluation_id (checks for duplicates).
        
        Uses GSI1 to find positions by evaluation_id.
        
        Args:
            evaluation_id: The evaluation ID to look up
            
        Returns:
            PaperPosition if found, None otherwise
        """
        db = get_dynamodb()
        items = await db.query(
            PaperPositionTable.TABLE,
            f"EVAL#{evaluation_id}",
            limit=1,
            scan_forward=False,
            index_name="GSI1",
        )
        if items:
            item = items[0]
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            return PaperPosition(**item)
        return None

    @staticmethod
    async def update(
        position: PaperPosition,
        updates: dict[str, Any],
    ) -> Optional[PaperPosition]:
        """Update a paper position.
        
        Note: Cannot change status via this method - use close() instead.
        
        Args:
            position: The position to update (needs entry_date, position_id, status)
            updates: Dict of fields to update
            
        Returns:
            Updated PaperPosition if successful, None otherwise
        """
        db = get_dynamodb()
        
        # Build PK/SK from position
        status = getattr(position.status, "value", position.status)
        pk = f"POS#{status}"
        sk = f"{position.entry_date}#{position.position_id}"
        
        # Add last_updated timestamp
        updates["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        item = await db.update_item(
            PaperPositionTable.TABLE,
            pk,
            sk,
            updates,
        )
        
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            return PaperPosition(**item)
        return None

    @staticmethod
    async def close(
        position: PaperPosition,
        exit_price: float,
        exit_reason: str,
    ) -> PaperPosition:
        """Close a position (moves from OPEN to CLOSED partition).

        Writes the closed position FIRST, then deletes the open position.
        This ordering ensures the position is never lost — in the worst
        case, it exists in both partitions temporarily, which is safe
        because list_open/list_closed filter by partition key.

        Args:
            position: The open position to close
            exit_price: The exit price
            exit_reason: The reason for exit (PROFIT_TARGET, STOP_LOSS, etc.)

        Returns:
            The closed PaperPosition
        """
        from app.core.schemas import ExitReason, PositionStatus

        db = get_dynamodb()

        # Calculate final P&L
        final_pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100

        # Create closed position
        now = datetime.now(timezone.utc).isoformat()
        closed_position = PaperPosition(
            position_id=position.position_id,
            evaluation_id=position.evaluation_id,
            option_ticker=position.option_ticker,
            entry_price=position.entry_price,
            entry_date=position.entry_date,
            quantity=position.quantity,
            verdict_at_entry=position.verdict_at_entry,
            quality_tier_at_entry=position.quality_tier_at_entry,
            exit_price=exit_price,
            exit_date=now[:10],  # YYYY-MM-DD
            exit_reason=ExitReason(exit_reason),
            current_price=exit_price,
            current_pnl_pct=final_pnl_pct,
            max_favorable_excursion=position.max_favorable_excursion,
            max_adverse_excursion=position.max_adverse_excursion,
            days_held=position.days_held,
            status=PositionStatus.CLOSED,
            last_updated=now,
        )

        # STEP 1: Write closed position FIRST (safe — creates duplicate at worst)
        await PaperPositionTable.put(closed_position)

        # STEP 2: Delete from OPEN partition
        old_pk = f"POS#{PositionStatus.OPEN.value}"
        old_sk = f"{position.entry_date}#{position.position_id}"
        await db.delete_item(PaperPositionTable.TABLE, old_pk, old_sk)

        return closed_position

    @staticmethod
    async def list_all(limit: Optional[int] = None) -> list[PaperPosition]:
        """List all positions (open and closed).
        
        Args:
            limit: Maximum positions to return per status
            
        Returns:
            Combined list of open and closed positions
        """
        open_positions = await PaperPositionTable.list_open(limit=limit)
        closed_positions = await PaperPositionTable.list_closed(limit=limit)
        
        # Combine and sort by entry_date descending
        all_positions = open_positions + closed_positions
        all_positions.sort(key=lambda p: p.entry_date, reverse=True)
        
        return all_positions


class FeatureValueTable:
    """Operations for the feature-values table.
    
    Per Section 18.1 of requirements: 90-day retention.
    """

    TABLE = FEATURE_VALUES_TABLE

    @staticmethod
    async def put(feature: FeatureValue) -> None:
        """Store a feature value."""
        db = get_dynamodb()
        item = feature.to_dynamodb_item()
        item["PK"] = f"EVAL#{feature.evaluation_id}"
        item["SK"] = f"FEATURE#{feature.feature_name}"
        await db.put_item(FeatureValueTable.TABLE, item)

    @staticmethod
    async def put_batch(features: list[FeatureValue]) -> None:
        """Store multiple feature values for an evaluation."""
        if not features:
            return
        db = get_dynamodb()
        items = []
        for feature in features:
            item = feature.to_dynamodb_item()
            item["PK"] = f"EVAL#{feature.evaluation_id}"
            item["SK"] = f"FEATURE#{feature.feature_name}"
            items.append(item)

        # DynamoDB BatchWriteItem limit is 25
        for i in range(0, len(items), 25):
            batch = items[i : i + 25]
            await db.batch_write(FeatureValueTable.TABLE, batch)

    @staticmethod
    async def list_by_evaluation(evaluation_id: str) -> list[FeatureValue]:
        """List all feature values for an evaluation."""
        db = get_dynamodb()
        items = await db.query(
            FeatureValueTable.TABLE,
            f"EVAL#{evaluation_id}",
            scan_forward=True,
        )
        features = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            features.append(FeatureValue(**item))
        return features


class PillarScoreTable:
    """Operations for the pillar-scores table.
    
    Per Section 18.1 of requirements: Permanent retention.
    """

    TABLE = PILLAR_SCORES_TABLE

    @staticmethod
    async def put(score: PillarScore) -> None:
        """Store a pillar score."""
        db = get_dynamodb()
        item = score.to_dynamodb_item()
        item["PK"] = f"EVAL#{score.evaluation_id}"
        item["SK"] = f"PILLAR#{score.pillar_id}"
        await db.put_item(PillarScoreTable.TABLE, item)

    @staticmethod
    async def put_batch(scores: list[PillarScore]) -> None:
        """Store all three pillar scores for an evaluation."""
        if not scores:
            return
        db = get_dynamodb()
        items = []
        for score in scores:
            item = score.to_dynamodb_item()
            item["PK"] = f"EVAL#{score.evaluation_id}"
            item["SK"] = f"PILLAR#{score.pillar_id}"
            items.append(item)

        for i in range(0, len(items), 25):
            batch = items[i : i + 25]
            await db.batch_write(PillarScoreTable.TABLE, batch)

    @staticmethod
    async def get(evaluation_id: str, pillar_id: str) -> Optional[PillarScore]:
        """Get a specific pillar score."""
        db = get_dynamodb()
        item = await db.get_item(
            PillarScoreTable.TABLE,
            f"EVAL#{evaluation_id}",
            f"PILLAR#{pillar_id}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return PillarScore(**item)
        return None

    @staticmethod
    async def list_by_evaluation(evaluation_id: str) -> list[PillarScore]:
        """List all pillar scores for an evaluation."""
        db = get_dynamodb()
        items = await db.query(
            PillarScoreTable.TABLE,
            f"EVAL#{evaluation_id}",
            scan_forward=True,
        )
        scores = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            scores.append(PillarScore(**item))
        return scores


class GateResultTable:
    """Operations for the gate-results table.
    
    Per Section 18.1 of requirements: Permanent retention.
    """

    TABLE = GATE_RESULTS_TABLE

    @staticmethod
    async def put(result: GateResult) -> None:
        """Store a gate result."""
        db = get_dynamodb()
        item = result.to_dynamodb_item()
        item["PK"] = f"EVAL#{result.evaluation_id}"
        item["SK"] = f"GATE#{result.gate_id}"
        if result.run_id:
            item["GSI1PK"] = f"RUN#{result.run_id}"
            item["GSI1SK"] = f"{result.evaluation_id}#{result.gate_id}"
        await db.put_item(GateResultTable.TABLE, item)

    @staticmethod
    async def put_batch(results: list[GateResult]) -> None:
        """Store all gate results for an evaluation."""
        if not results:
            return
        db = get_dynamodb()
        items = []
        for result in results:
            item = result.to_dynamodb_item()
            item["PK"] = f"EVAL#{result.evaluation_id}"
            item["SK"] = f"GATE#{result.gate_id}"
            if result.run_id:
                item["GSI1PK"] = f"RUN#{result.run_id}"
                item["GSI1SK"] = f"{result.evaluation_id}#{result.gate_id}"
            items.append(item)

        for i in range(0, len(items), 25):
            batch = items[i : i + 25]
            await db.batch_write(GateResultTable.TABLE, batch)

    @staticmethod
    async def get(evaluation_id: str, gate_id: str) -> Optional[GateResult]:
        """Get a specific gate result."""
        db = get_dynamodb()
        item = await db.get_item(
            GateResultTable.TABLE,
            f"EVAL#{evaluation_id}",
            f"GATE#{gate_id}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return GateResult(**item)
        return None

    @staticmethod
    async def list_by_evaluation(evaluation_id: str) -> list[GateResult]:
        """List all gate results for an evaluation."""
        db = get_dynamodb()
        items = await db.query(
            GateResultTable.TABLE,
            f"EVAL#{evaluation_id}",
            scan_forward=True,
        )
        results = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            results.append(GateResult(**item))
        return results

    @staticmethod
    async def list_failed_by_evaluation(evaluation_id: str) -> list[GateResult]:
        """List only failed gate results for an evaluation."""
        all_results = await GateResultTable.list_by_evaluation(evaluation_id)
        return [r for r in all_results if not r.passed]

    @staticmethod
    async def list_by_run(run_id: str, limit: int = 10000) -> list[GateResult]:
        """List all gate results for a pipeline run via GSI1.

        Requires gate results to have been written with run_id populated
        (GSI1PK=RUN#{run_id}). Gate results written before this GSI was
        populated will not appear.

        Args:
            run_id: The pipeline run ID
            limit: Maximum results to return

        Returns:
            List of GateResult objects from evaluations in this run
        """
        db = get_dynamodb()
        items = await db.query(
            GateResultTable.TABLE,
            f"RUN#{run_id}",
            index_name="GSI1",
            limit=limit,
            scan_forward=True,
        )
        results = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            results.append(GateResult(**item))
        return results


class IVHistoryTable:
    """Operations for the iv-history table.

    Used to store daily IV data for IV percentile calculation.
    Per plan: TTL set to 252 trading days (~365 calendar days) for cost control.
    """

    TABLE = IV_HISTORY_TABLE

    @staticmethod
    async def put(iv_history: IVHistory) -> None:
        """Store an IV history record."""
        db = get_dynamodb()
        item = iv_history.to_dynamodb_item()
        item["PK"] = f"TICKER#{iv_history.ticker}"
        item["SK"] = f"DATE#{iv_history.date}"
        await db.put_item(IVHistoryTable.TABLE, item)

    @staticmethod
    async def put_batch(records: list[IVHistory]) -> None:
        """Store multiple IV history records."""
        db = get_dynamodb()
        items = []
        for record in records:
            item = record.to_dynamodb_item()
            item["PK"] = f"TICKER#{record.ticker}"
            item["SK"] = f"DATE#{record.date}"
            items.append(item)

        # DynamoDB BatchWriteItem limit is 25
        for i in range(0, len(items), 25):
            batch = items[i : i + 25]
            await db.batch_write(IVHistoryTable.TABLE, batch)

    @staticmethod
    async def get(ticker: str, date: str) -> Optional[IVHistory]:
        """Get IV history for a specific ticker and date."""
        db = get_dynamodb()
        item = await db.get_item(
            IVHistoryTable.TABLE,
            f"TICKER#{ticker}",
            f"DATE#{date}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return IVHistory(**item)
        return None

    @staticmethod
    async def list_by_ticker(
        ticker: str,
        limit: int = 252,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[IVHistory]:
        """List IV history for a ticker.

        Args:
            ticker: The ticker symbol
            limit: Maximum records to return (default 252 for percentile calc)
            start_date: Optional start date filter (YYYY-MM-DD)
            end_date: Optional end date filter (YYYY-MM-DD)

        Returns:
            List of IVHistory records, most recent first
        """
        db = get_dynamodb()

        # Build sort key condition if date filters provided
        sk_condition = None
        if start_date and end_date:
            sk_condition = {"between": [f"DATE#{start_date}", f"DATE#{end_date}"]}
        elif start_date:
            sk_condition = {"gte": f"DATE#{start_date}"}
        elif end_date:
            sk_condition = {"lte": f"DATE#{end_date}"}

        items = await db.query(
            IVHistoryTable.TABLE,
            f"TICKER#{ticker}",
            limit=limit,
            scan_forward=False,  # Most recent first
            sk_condition=sk_condition,
        )

        records = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            records.append(IVHistory(**item))
        return records

    @staticmethod
    async def calculate_percentile(ticker: str, current_iv: float) -> Optional[float]:
        """Calculate IV percentile for a ticker.

        Args:
            ticker: The ticker symbol
            current_iv: Current IV value to rank

        Returns:
            Percentile (0-100) or None if insufficient data
        """
        records = await IVHistoryTable.list_by_ticker(ticker, limit=252)

        if len(records) < 20:  # Need at least 20 days for meaningful percentile
            logger.warning(
                f"Insufficient IV history for {ticker}: {len(records)} days"
            )
            return None

        # Count how many historical values are below current IV
        iv_values = [r.atm_iv for r in records]
        below_count = sum(1 for iv in iv_values if iv < current_iv)

        percentile = (below_count / len(iv_values)) * 100
        return round(percentile, 2)

    @staticmethod
    async def get_latest(ticker: str) -> Optional[IVHistory]:
        """Get the most recent IV history record for a ticker."""
        records = await IVHistoryTable.list_by_ticker(ticker, limit=1)
        return records[0] if records else None


class OIHistoryTable:
    """Operations for the oi-history table.

    Used to store daily Open Interest data per option contract
    for calculating oi_5d_change_pct feature.
    """

    TABLE = OI_HISTORY_TABLE

    @staticmethod
    async def put(oi_history: OIHistory) -> None:
        """Store an OI history record."""
        db = get_dynamodb()
        item = oi_history.to_dynamodb_item()
        item["PK"] = f"CONTRACT#{oi_history.option_ticker}"
        item["SK"] = f"DATE#{oi_history.date}"
        await db.put_item(OIHistoryTable.TABLE, item)

    @staticmethod
    async def put_batch(records: list[OIHistory]) -> None:
        """Store multiple OI history records."""
        db = get_dynamodb()
        items = []
        for record in records:
            item = record.to_dynamodb_item()
            item["PK"] = f"CONTRACT#{record.option_ticker}"
            item["SK"] = f"DATE#{record.date}"
            items.append(item)

        # DynamoDB BatchWriteItem limit is 25
        for i in range(0, len(items), 25):
            batch = items[i : i + 25]
            await db.batch_write(OIHistoryTable.TABLE, batch)

    @staticmethod
    async def get(option_ticker: str, date: str) -> Optional[OIHistory]:
        """Get OI history for a specific contract and date."""
        db = get_dynamodb()
        item = await db.get_item(
            OIHistoryTable.TABLE,
            f"CONTRACT#{option_ticker}",
            f"DATE#{date}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return OIHistory(**item)
        return None

    @staticmethod
    async def list_by_contract(
        option_ticker: str,
        limit: int = 10,
    ) -> list[OIHistory]:
        """List OI history for a contract (most recent first).

        Args:
            option_ticker: The option contract ticker
            limit: Maximum records to return (default 10)

        Returns:
            List of OIHistory records, most recent first
        """
        db = get_dynamodb()
        items = await db.query(
            OIHistoryTable.TABLE,
            f"CONTRACT#{option_ticker}",
            limit=limit,
            scan_forward=False,  # Most recent first
        )

        records = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            records.append(OIHistory(**item))
        return records

    @staticmethod
    async def calculate_5d_change(option_ticker: str, current_oi: int) -> Optional[float]:
        """Calculate 5-day OI change percentage.

        Args:
            option_ticker: The option contract ticker
            current_oi: Current open interest value

        Returns:
            Percentage change from 5 days ago, or None if insufficient data
        """
        records = await OIHistoryTable.list_by_contract(option_ticker, limit=6)

        if len(records) < 5:
            return None

        # Get OI from approximately 5 days ago
        oi_5d_ago = records[4].open_interest if len(records) > 4 else records[-1].open_interest

        if oi_5d_ago == 0:
            return None

        change_pct = ((current_oi - oi_5d_ago) / oi_5d_ago) * 100
        return round(change_pct, 2)


class TradeThesisTable:
    """Operations for the trade-thesis table.

    Stores LLM-generated trade theses for APPROVE evaluations.
    Per Section 21 of OSS_Complete_Requirements.md.
    """

    TABLE = TRADE_THESIS_TABLE

    @staticmethod
    async def put(thesis: TradeThesis) -> None:
        """Store a trade thesis."""
        db = get_dynamodb()
        item = thesis.to_dynamodb_item()
        item["PK"] = f"EVAL#{thesis.evaluation_id}"
        item["SK"] = f"THESIS#{thesis.thesis_id}"
        # GSI for date-based queries
        date_str = thesis.generated_at[:10]  # YYYY-MM-DD
        item["GSI1PK"] = f"DATE#{date_str}"
        item["GSI1SK"] = f"{thesis.status}#{thesis.thesis_id}"
        await db.put_item(TradeThesisTable.TABLE, item)

    @staticmethod
    async def get(evaluation_id: str, thesis_id: str) -> Optional[TradeThesis]:
        """Get a trade thesis by evaluation_id and thesis_id."""
        db = get_dynamodb()
        item = await db.get_item(
            TradeThesisTable.TABLE,
            f"EVAL#{evaluation_id}",
            f"THESIS#{thesis_id}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            return TradeThesis(**item)
        return None

    @staticmethod
    async def get_by_evaluation_id(evaluation_id: str) -> Optional[TradeThesis]:
        """Get the thesis for an evaluation.

        Args:
            evaluation_id: The evaluation ID to look up

        Returns:
            TradeThesis if found, None otherwise
        """
        db = get_dynamodb()
        items = await db.query(
            TradeThesisTable.TABLE,
            f"EVAL#{evaluation_id}",
            limit=1,
            scan_forward=False,  # Get most recent
        )
        if items:
            item = items[0]
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            return TradeThesis(**item)
        return None

    @staticmethod
    async def list_by_date(date: str, limit: int = 50) -> list[TradeThesis]:
        """List trade theses generated on a specific date.

        Args:
            date: Date string in YYYY-MM-DD format
            limit: Maximum records to return

        Returns:
            List of TradeThesis records
        """
        db = get_dynamodb()
        items = await db.query(
            TradeThesisTable.TABLE,
            f"DATE#{date}",
            limit=limit,
            scan_forward=False,
            index_name="GSI1",
        )
        theses = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            item.pop("GSI1PK", None)
            item.pop("GSI1SK", None)
            theses.append(TradeThesis(**item))
        return theses


class LLMUsageTable:
    """Operations for the llm-usage table.

    Tracks daily LLM API call counts for rate limiting.
    Per Section 21.4: Max 50 LLM calls per day.
    """

    TABLE = LLM_USAGE_TABLE

    @staticmethod
    async def put(usage: LLMUsage) -> None:
        """Store LLM usage record."""
        db = get_dynamodb()
        item = usage.to_dynamodb_item()
        item["PK"] = "USAGE"
        item["SK"] = f"DATE#{usage.date}"
        await db.put_item(LLMUsageTable.TABLE, item)

    @staticmethod
    async def get(date: str) -> Optional[LLMUsage]:
        """Get LLM usage for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format

        Returns:
            LLMUsage if found, None otherwise
        """
        db = get_dynamodb()
        item = await db.get_item(
            LLMUsageTable.TABLE,
            "USAGE",
            f"DATE#{date}",
        )
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return LLMUsage(**item)
        return None

    @staticmethod
    async def increment(
        date: str, tokens: int = 0, max_calls: Optional[int] = None,
    ) -> Optional[LLMUsage]:
        """Atomically increment usage count for a date.

        Uses DynamoDB ADD expression for safe concurrent updates.
        Creates new record if doesn't exist (ADD on missing attribute
        treats it as 0).

        When max_calls is provided, the increment is conditional — it only
        succeeds if the current count is below the limit. This prevents
        concurrent Lambda invocations from exceeding the daily cap.

        Args:
            date: Date string in YYYY-MM-DD format
            tokens: Number of tokens used in this call
            max_calls: If provided, only increment if calls_made < max_calls

        Returns:
            Updated LLMUsage record, or None if max_calls limit was hit
        """
        from botocore.exceptions import ClientError

        db = get_dynamodb()
        table = db.get_table(LLMUsageTable.TABLE)
        update_kwargs = {
            "Key": {"PK": "USAGE", "SK": f"DATE#{date}"},
            "UpdateExpression": (
                "ADD calls_made :inc, tokens_used :tokens "
                "SET #d = if_not_exists(#d, :date)"
            ),
            "ExpressionAttributeNames": {"#d": "date"},
            "ExpressionAttributeValues": {
                ":inc": 1,
                ":tokens": tokens,
                ":date": date,
            },
            "ReturnValues": "ALL_NEW",
        }

        if max_calls is not None:
            update_kwargs["ConditionExpression"] = (
                "attribute_not_exists(calls_made) OR calls_made < :limit"
            )
            update_kwargs["ExpressionAttributeValues"][":limit"] = max_calls

        try:
            response = table.update_item(**update_kwargs)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None  # Daily limit exceeded
            raise

        attrs = db.convert_from_dynamodb(response.get("Attributes", {}))
        attrs.pop("PK", None)
        attrs.pop("SK", None)
        return LLMUsage(
            date=attrs.get("date", date),
            calls_made=int(attrs.get("calls_made", 1)),
            tokens_used=int(attrs.get("tokens_used", tokens)),
        )

    @staticmethod
    async def list_recent(days: int = 7) -> list[LLMUsage]:
        """List recent LLM usage records.

        Args:
            days: Number of days to look back

        Returns:
            List of LLMUsage records, most recent first
        """
        db = get_dynamodb()
        items = await db.query(
            LLMUsageTable.TABLE,
            "USAGE",
            limit=days,
            scan_forward=False,  # Most recent first
        )
        records = []
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
            records.append(LLMUsage(**item))
        return records


class PaperSnapshotTable:
    """Operations for the paper-snapshots table.

    Stores daily snapshots of paper trading positions for historical tracking.
    Schema: PK=POS#{position_id}, SK=SNAP#{YYYY-MM-DD}
    """

    TABLE = PAPER_SNAPSHOTS_TABLE

    @staticmethod
    async def put_snapshot(
        position_id: str,
        snapshot_date: str,
        data: dict[str, Any],
    ) -> None:
        """Write one daily snapshot for a position."""
        db = get_dynamodb()
        item = {
            "PK": f"POS#{position_id}",
            "SK": f"SNAP#{snapshot_date}",
            "position_id": position_id,
            "snapshot_date": snapshot_date,
            **data,
        }
        await db.put_item(PaperSnapshotTable.TABLE, item)

    @staticmethod
    async def list_by_position(
        position_id: str,
        limit: int = 365,
    ) -> list[dict[str, Any]]:
        """Query all snapshots for a position, most recent first."""
        db = get_dynamodb()
        items = await db.query(
            PaperSnapshotTable.TABLE,
            f"POS#{position_id}",
            sk_prefix="SNAP#",
            limit=limit,
            scan_forward=False,
        )
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def list_by_position_range(
        position_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Query snapshots for a position within a date range (inclusive)."""
        db = get_dynamodb()
        items = await db.query(
            PaperSnapshotTable.TABLE,
            f"POS#{position_id}",
            sk_condition={"between": [f"SNAP#{start_date}", f"SNAP#{end_date}"]},
            scan_forward=True,
        )
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
        return items


class CalibrationReportTable:
    """Operations for the calibration-reports table.

    Stores weekly calibration reports with threshold suggestions.
    PK=REPORT, SK={generated_at}#{report_id}
    """

    TABLE = CALIBRATION_REPORTS_TABLE

    @staticmethod
    async def put(report: dict[str, Any] | Any) -> None:
        """Store a calibration report.

        Args:
            report: CalibrationReport (with to_dict) or dict
        """
        db = get_dynamodb()
        if hasattr(report, "to_dict"):
            item = report.to_dict()
            report_id = report.report_id
            generated_at = report.generated_at
        else:
            item = dict(report)
            report_id = item["report_id"]
            generated_at = item.get("generated_at", "")
        item["PK"] = "REPORT"
        item["SK"] = f"{generated_at}#{report_id}"
        await db.put_item(CalibrationReportTable.TABLE, item)

    @staticmethod
    async def get(report_id: str) -> Optional[dict[str, Any]]:
        """Get a calibration report by report_id.

        Queries PK=REPORT and scans for matching report_id.

        Args:
            report_id: The report ID

        Returns:
            Report dict or None
        """
        db = get_dynamodb()
        items = await db.query(
            CalibrationReportTable.TABLE,
            "REPORT",
            limit=100,
            scan_forward=False,
        )
        for item in items:
            if item.get("report_id") == report_id:
                item.pop("PK", None)
                item.pop("SK", None)
                return item
        return None

    @staticmethod
    async def list_recent(limit: int = 20) -> list[dict[str, Any]]:
        """List recent calibration reports.

        Args:
            limit: Maximum reports to return

        Returns:
            List of report dicts, most recent first
        """
        db = get_dynamodb()
        items = await db.query(
            CalibrationReportTable.TABLE,
            "REPORT",
            limit=limit,
            scan_forward=False,
        )
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def update_suggestion_status(
        report_id: str,
        suggestion_id: str,
        new_status: str,
        expected_current_status: Optional[str] = None,
    ) -> bool:
        """Update a suggestion's status within a report.

        Args:
            report_id: The report containing the suggestion
            suggestion_id: The suggestion to update
            new_status: New status value
            expected_current_status: If set, only update if current status matches

        Returns:
            True if updated, False if not found or status mismatch
        """
        db = get_dynamodb()
        # Find the report
        items = await db.query(
            CalibrationReportTable.TABLE,
            "REPORT",
            limit=100,
            scan_forward=False,
        )
        target_item = None
        for item in items:
            if item.get("report_id") == report_id:
                target_item = item
                break

        if target_item is None:
            return False

        suggestions = target_item.get("suggestions", [])
        for i, s in enumerate(suggestions):
            if s.get("suggestion_id") == suggestion_id:
                if expected_current_status and s.get("status") != expected_current_status:
                    return False
                suggestions[i]["status"] = new_status
                await db.update_item(
                    CalibrationReportTable.TABLE,
                    target_item["PK"],
                    target_item["SK"],
                    {"suggestions": suggestions},
                )
                return True

        return False


class SP500TickerTable:
    """Operations for the sp500-tickers table.

    Reads the S&P 500 ticker list maintained by the UV scanner infrastructure.
    PK=TICKER_LIST, SK={ticker}
    """

    TABLE = SP500_TICKERS_TABLE

    @staticmethod
    async def get_active_tickers() -> list[str]:
        """Get all active S&P 500 tickers, sorted.

        Returns:
            Sorted list of active ticker symbols
        """
        db = get_dynamodb()
        items = await db.query(
            SP500TickerTable.TABLE, "TICKER_LIST", limit=None, scan_forward=True
        )
        return sorted(item["ticker"] for item in items if item.get("is_active", True))


class ScanStatusTable:
    """Operations for the scan-status table.

    Stores scan run statuses for persistence beyond in-memory.
    PK=SCAN, SK={started_at}#{run_id}
    """

    TABLE = SCAN_STATUS_TABLE

    @staticmethod
    async def get(run_id: str) -> Optional[dict[str, Any]]:
        """Get scan status by run_id.

        Queries PK=SCAN and scans for matching run_id.

        Args:
            run_id: The scan run ID

        Returns:
            Status dict or None
        """
        db = get_dynamodb()
        items = await db.query(
            ScanStatusTable.TABLE,
            "SCAN",
            limit=100,
            scan_forward=False,
        )
        for item in items:
            if item.get("run_id") == run_id:
                item.pop("PK", None)
                item.pop("SK", None)
                return item
        return None

    @staticmethod
    async def list_recent(limit: int = 20) -> list[dict[str, Any]]:
        """List recent scan statuses.

        Args:
            limit: Maximum statuses to return

        Returns:
            List of status dicts, most recent first
        """
        db = get_dynamodb()
        items = await db.query(
            ScanStatusTable.TABLE,
            "SCAN",
            limit=limit,
            scan_forward=False,
        )
        for item in items:
            item.pop("PK", None)
            item.pop("SK", None)
        return items

    @staticmethod
    async def put(run_id: str, data: dict[str, Any]) -> None:
        """Store scan status.

        Args:
            run_id: The scan run ID
            data: Status data dict (should contain started_at, status, etc.)
        """
        db = get_dynamodb()
        item = dict(data)
        item["run_id"] = run_id
        item["PK"] = "SCAN"
        started_at = data.get("started_at", datetime.now(timezone.utc).isoformat())
        item["SK"] = f"{started_at}#{run_id}"
        await db.put_item(ScanStatusTable.TABLE, item)
