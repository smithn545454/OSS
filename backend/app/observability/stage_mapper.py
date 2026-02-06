"""Stage mapper service for Pipeline Monitor.

Maps the internal 8-stage pipeline to the 5-stage display model
per oss-pipeline-monitor-requirements.md.

Stage Mapping:
- Stage 1 (Discovery): OPPORTUNITY_DISCOVERY
- Stage 2 (Quality Filters): UNDERLYING_FILTERS  
- Stage 3 (Selection): CONTRACT_SELECTION
- Stage 4 (Evaluation): FEATURE_COMPUTATION + PILLAR_SCORING + HARD_GATES
- Stage 5 (Output): DECISION_LOGIC
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.schemas import (
    DisplayFailureOverlap,
    DisplayGate,
    DisplayRule,
    DisplayStage,
    GateResult,
    PipelineMonitorData,
    PipelineRun,
    PipelineRunListItem,
    PipelineStage,
    RuleSeverity,
    ScannerType,
    StageEvent,
    StageStatus,
    VerdictBreakdown,
)
from app.db.tables import (
    GateResultTable,
    PipelineRunTable,
    StageEventTable,
)

logger = logging.getLogger(__name__)

# Stage mapping configuration
STAGE_MAPPING = {
    1: {
        "name": "Discovery",
        "description": "Initial contract universe filtering",
        "internal_stages": [PipelineStage.OPPORTUNITY_DISCOVERY],
    },
    2: {
        "name": "Initial Scoring",
        "description": "Directional & volatility signal assessment",
        "internal_stages": [PipelineStage.UNDERLYING_FILTERS],
    },
    3: {
        "name": "Structure Analysis",
        "description": "Options-specific quality checks",
        "internal_stages": [PipelineStage.CONTRACT_SELECTION],
    },
    4: {
        "name": "Final Scoring",
        "description": "Composite score calculation & ranking",
        "internal_stages": [
            PipelineStage.FEATURE_COMPUTATION,
            PipelineStage.PILLAR_SCORING,
            PipelineStage.HARD_GATES,
        ],
    },
    5: {
        "name": "Output",
        "description": "Final verdict determination",
        "internal_stages": [PipelineStage.DECISION_LOGIC],
    },
}

# Gate definitions with their rules, organized by stage
GATE_DEFINITIONS = {
    # Stage 1 (Discovery) gates
    "liquidity_gate": {
        "name": "Liquidity Gate",
        "stage": 1,
        "rules": [
            "Min Volume ≥ 100",
            "Open Interest ≥ 500",
            "Bid-Ask Spread ≤ 15%",
        ],
    },
    "basic_eligibility": {
        "name": "Basic Eligibility",
        "stage": 1,
        "rules": [
            "DTE Range 7-120",
            "Strike within expected move",
        ],
    },
    # Stage 2 (Initial Scoring) gates
    "directional_confidence": {
        "name": "Directional Confidence",
        "stage": 2,
        "rules": [
            "Trend Alignment Score ≥ 0.6",
            "Multi-Timeframe Confluence",
            "Recent Momentum Positive",
        ],
    },
    "volatility_assessment": {
        "name": "Volatility Assessment",
        "stage": 2,
        "rules": [
            "IV Percentile ≤ 85%",
            "IV/RV Ratio favorable",
            "Theta Burden ≤ 4%",
        ],
    },
    # Stage 3 (Structure Analysis) gates
    "premium_quality": {
        "name": "Premium Quality",
        "stage": 3,
        "rules": [
            "Time-Adjusted Feasibility ≤ 1.25",
            "Expected Move Coverage",
        ],
    },
    "risk_parameters": {
        "name": "Risk Parameters",
        "stage": 3,
        "rules": [
            "Delta in valid range",
            "Greeks coherent",
            "Max loss acceptable",
        ],
    },
    # Stage 4 (Final Scoring) gates
    "composite_threshold": {
        "name": "Composite Threshold",
        "stage": 4,
        "rules": [
            "Combined Score ≥ 75",
            "No Pillar Below 60",
            "Confidence Interval Met",
        ],
    },
}


class StageMapper:
    """Maps internal pipeline stages to display format."""

    def detect_anomaly(
        self, stage_input: int, stage_output: int, is_final: bool = False
    ) -> tuple[StageStatus, Optional[str]]:
        """Detect anomalies per spec section 5.11.1.
        
        Returns:
            Tuple of (status, anomaly_message)
        """
        if stage_output == 0 and stage_input > 0:
            return StageStatus.ANOMALY, f"Zero contracts passed — all {stage_input} filtered out"
        
        if stage_output > stage_input:
            return StageStatus.ANOMALY, "Data integrity issue: output exceeds input"
        
        if not is_final and stage_input > 0:
            pass_rate = (stage_output / stage_input) * 100
            if pass_rate < 1:
                return StageStatus.ANOMALY, f"Unusually low pass rate: {pass_rate:.1f}%"
        
        return StageStatus.HEALTHY, None

    def aggregate_stage_events(
        self,
        events: list[StageEvent],
        display_stage_id: int,
    ) -> tuple[int, int, dict[str, int]]:
        """Aggregate metrics from internal stages to a display stage.
        
        Args:
            events: List of stage events from the run
            display_stage_id: The display stage ID (1-5)
            
        Returns:
            Tuple of (items_in, items_out, drop_reasons)
        """
        internal_stages = STAGE_MAPPING[display_stage_id]["internal_stages"]
        
        # Find matching events
        matching_events = [
            e for e in events 
            if e.stage in internal_stages
        ]
        
        if not matching_events:
            return 0, 0, {}
        
        # For combined stages (like Evaluation), chain the metrics
        if len(matching_events) == 1:
            event = matching_events[0]
            return event.items_in, event.items_out, event.drop_reasons
        
        # Sort by stage order to get first input and last output
        stage_order = list(internal_stages)
        matching_events.sort(key=lambda e: stage_order.index(e.stage))
        
        items_in = matching_events[0].items_in
        items_out = matching_events[-1].items_out
        
        # Combine all drop reasons
        combined_reasons: dict[str, int] = {}
        for event in matching_events:
            for reason, count in event.drop_reasons.items():
                combined_reasons[reason] = combined_reasons.get(reason, 0) + count
        
        return items_in, items_out, combined_reasons

    async def build_gates_for_stage(
        self,
        run_id: str,
        stage_id: int,
        gate_results: list[GateResult],
    ) -> list[DisplayGate]:
        """Build gate display data from gate results.
        
        Args:
            run_id: The pipeline run ID
            stage_id: The display stage ID
            gate_results: List of gate results for the run
            
        Returns:
            List of DisplayGate objects for the specified stage
        """
        # Filter gate definitions for this stage
        stage_gates = {
            gid: gdef for gid, gdef in GATE_DEFINITIONS.items()
            if gdef.get("stage") == stage_id
        }
        
        if not stage_gates:
            return []
        
        # Group results by gate
        results_by_gate: dict[str, list[GateResult]] = defaultdict(list)
        for result in gate_results:
            # Map gate_id to our gate definitions
            gate_key = self._map_gate_id(result.gate_id)
            if gate_key:
                results_by_gate[gate_key].append(result)
        
        gates: list[DisplayGate] = []
        
        for gate_id, gate_def in stage_gates.items():
            results = results_by_gate.get(gate_id, [])
            
            # Build rules from results
            rules = self._build_rules(gate_def["rules"], results)
            
            # Calculate totals
            total_passed = sum(1 for r in results if r.passed)
            total_failed = sum(1 for r in results if not r.passed)
            
            # Build overlap data
            overlaps = self._compute_overlaps(results)
            
            gates.append(DisplayGate(
                id=gate_id,
                name=gate_def["name"],
                passed=total_passed,
                failed=total_failed,
                rules=rules,
                overlaps=overlaps,
            ))
        
        return gates

    def _map_gate_id(self, gate_id: str) -> Optional[str]:
        """Map internal gate ID to display gate ID."""
        mapping = {
            # Stage 1 - Discovery gates
            "GATE_MIN_OPEN_INTEREST": "liquidity_gate",
            "GATE_MIN_VOLUME": "liquidity_gate",
            "GATE_MAX_SPREAD_PCT": "liquidity_gate",
            "GATE_DTE_RANGE": "basic_eligibility",
            # Stage 2 - Initial Scoring gates
            "GATE_DIRECTIONAL": "directional_confidence",
            "GATE_TREND_ALIGNMENT": "directional_confidence",
            "GATE_MOMENTUM": "directional_confidence",
            "GATE_IV_PERCENTILE_MAX": "volatility_assessment",
            "GATE_IV_RV_RATIO": "volatility_assessment",
            "GATE_THETA_BURDEN_MAX": "volatility_assessment",
            # Stage 3 - Structure Analysis gates
            "GATE_MOVE_SUFFICIENCY": "premium_quality",
            "GATE_EXPECTED_MOVE": "premium_quality",
            "GATE_GREEKS_COHERENCE": "risk_parameters",
            "GATE_DELTA_RANGE": "risk_parameters",
            "GATE_MAX_LOSS": "risk_parameters",
            # Stage 4 - Final Scoring gates
            "GATE_COMBINED_SCORE": "composite_threshold",
            "GATE_PILLAR_MINIMUM": "composite_threshold",
            "GATE_CONFIDENCE_INTERVAL": "composite_threshold",
        }
        return mapping.get(gate_id)

    def _build_rules(
        self,
        rule_names: list[str],
        results: list[GateResult],
    ) -> list[DisplayRule]:
        """Build rule display data from gate results."""
        rules: list[DisplayRule] = []
        
        for rule_name in rule_names:
            # Count pass/fail for this rule
            # In a real implementation, you'd match specific results to rules
            passed = sum(1 for r in results if r.passed)
            failed = sum(1 for r in results if not r.passed)
            
            # Determine severity
            severity = RuleSeverity.CRITICAL if passed == 0 and failed > 0 else RuleSeverity.NORMAL
            
            rules.append(DisplayRule(
                name=rule_name,
                passed=passed,
                failed=failed,
                severity=severity,
            ))
        
        return rules

    def _compute_overlaps(
        self,
        results: list[GateResult],
    ) -> list[DisplayFailureOverlap]:
        """Compute multi-rule failure overlaps.
        
        Identifies contracts that failed multiple rules simultaneously.
        """
        # Group failures by evaluation_id
        failures_by_eval: dict[str, list[str]] = defaultdict(list)
        
        for result in results:
            if not result.passed:
                failures_by_eval[result.evaluation_id].append(result.gate_id)
        
        # Count combinations
        combo_counts: dict[tuple[str, ...], int] = defaultdict(int)
        
        for eval_id, failed_gates in failures_by_eval.items():
            if len(failed_gates) >= 2:
                # Sort to normalize the combination
                combo = tuple(sorted(failed_gates))
                combo_counts[combo] += 1
        
        # Convert to DisplayFailureOverlap
        overlaps: list[DisplayFailureOverlap] = []
        
        for combo, count in sorted(combo_counts.items(), key=lambda x: -x[1]):
            overlaps.append(DisplayFailureOverlap(
                rules=list(combo),
                count=count,
            ))
        
        return overlaps[:10]  # Limit to top 10 combinations

    def _build_default_gates_for_stage(self, stage_id: int) -> list[DisplayGate]:
        """Build default empty gates for a stage when no data is available.
        
        Args:
            stage_id: The display stage ID (1-5)
            
        Returns:
            List of DisplayGate objects with zero counts
        """
        # Filter gate definitions for this stage
        stage_gates = {
            gid: gdef for gid, gdef in GATE_DEFINITIONS.items()
            if gdef.get("stage") == stage_id
        }
        
        if not stage_gates:
            return []
        
        gates: list[DisplayGate] = []
        
        for gate_id, gate_def in stage_gates.items():
            rules = [
                DisplayRule(
                    name=rule_name,
                    passed=0,
                    failed=0,
                    severity=RuleSeverity.NORMAL,
                )
                for rule_name in gate_def["rules"]
            ]
            
            gates.append(DisplayGate(
                id=gate_id,
                name=gate_def["name"],
                passed=0,
                failed=0,
                rules=rules,
                overlaps=[],
            ))
        
        return gates

    async def build_display_stage(
        self,
        stage_id: int,
        events: list[StageEvent],
        gate_results: list[GateResult],
        run_metadata: Optional[dict[str, Any]] = None,
    ) -> DisplayStage:
        """Build a display stage from internal data.
        
        Args:
            stage_id: Display stage ID (1-5)
            events: Stage events from the run
            gate_results: Gate results for the run
            run_metadata: Optional run metadata for verdict counts
            
        Returns:
            DisplayStage object
        """
        stage_def = STAGE_MAPPING[stage_id]
        items_in, items_out, drop_reasons = self.aggregate_stage_events(events, stage_id)
        
        is_final = stage_id == 5
        status, anomaly_msg = self.detect_anomaly(items_in, items_out, is_final)
        
        # Build gates for evaluation stage
        gates = await self.build_gates_for_stage("", stage_id, gate_results)
        
        # Build verdict breakdown for output stage
        breakdown = None
        if is_final and run_metadata:
            breakdown = VerdictBreakdown(
                approve=run_metadata.get("approves", 0),
                watch=run_metadata.get("watches", 0),
                reject=run_metadata.get("rejects", 0),
            )
        
        return DisplayStage(
            id=stage_id,
            name=stage_def["name"],
            description=stage_def["description"],
            input=items_in,
            output=items_out,
            status=status,
            anomaly_message=anomaly_msg,
            gates=gates if gates else None,
            breakdown=breakdown,
        )

    async def build_pipeline_data(
        self,
        run: PipelineRun,
        events: list[StageEvent],
        gate_results: list[GateResult],
        time_range_label: str = "Today",
        scanner_label: str = "All Scanners",
    ) -> PipelineMonitorData:
        """Build complete pipeline display data for a run.
        
        Args:
            run: The pipeline run
            events: Stage events for the run
            gate_results: Gate results for the run
            time_range_label: Display label for time range
            scanner_label: Display label for scanner filter
            
        Returns:
            PipelineMonitorData object
        """
        # Get run metadata for verdict counts
        run_metadata = {
            "approves": run.total_approves,
            "watches": run.total_watches,
            "rejects": run.total_rejects,
        }
        
        # Build all 5 display stages
        stages: list[DisplayStage] = []
        for stage_id in range(1, 6):
            stage = await self.build_display_stage(
                stage_id, events, gate_results, run_metadata
            )
            stages.append(stage)
        
        # Get total input from first stage
        total_input = stages[0].input if stages else 0
        
        return PipelineMonitorData(
            time_range=time_range_label,
            scanner_type=scanner_label,
            total_input=total_input,
            stages=stages,
        )

    def run_to_list_item(
        self,
        run: PipelineRun,
        scanner_type: Optional[ScannerType] = None,
    ) -> PipelineRunListItem:
        """Convert a PipelineRun to a list item for the sidebar.
        
        Args:
            run: The pipeline run
            scanner_type: Optional scanner type filter
            
        Returns:
            PipelineRunListItem
        """
        # Determine status based on any anomalies
        status = StageStatus.HEALTHY
        if run.status == "FAILED":
            status = StageStatus.ANOMALY
        
        return PipelineRunListItem(
            id=run.run_id,
            timestamp=run.started_at,
            scanner_type=scanner_type,
            total_contracts=run.total_evaluations,
            approved_count=run.total_approves,
            status=status,
        )


class PipelineAggregator:
    """Aggregates pipeline data across multiple runs."""

    def __init__(self):
        self.mapper = StageMapper()

    async def get_runs_for_time_range(
        self,
        start: datetime,
        end: datetime,
        scanner_type: Optional[ScannerType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PipelineRun], int, bool]:
        """Get pipeline runs within a time range.
        
        Args:
            start: Start of time range
            end: End of time range
            scanner_type: Optional scanner filter
            limit: Max runs to return
            offset: Pagination offset
            
        Returns:
            Tuple of (runs, total_count, has_more)
        """
        # Get all recent runs
        all_runs = await PipelineRunTable.list(limit=1000)
        
        # Filter by time range
        filtered_runs = []
        for run in all_runs:
            run_time = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
            if start <= run_time <= end:
                filtered_runs.append(run)
        
        # TODO: Filter by scanner_type when that metadata is stored
        
        total = len(filtered_runs)
        has_more = offset + limit < total
        
        return filtered_runs[offset:offset + limit], total, has_more

    async def build_aggregate_data(
        self,
        start: datetime,
        end: datetime,
        scanner_type: Optional[ScannerType] = None,
    ) -> PipelineMonitorData:
        """Build aggregated pipeline data across multiple runs.
        
        Args:
            start: Start of time range
            end: End of time range
            scanner_type: Optional scanner filter
            
        Returns:
            Aggregated PipelineMonitorData
        """
        runs, _, _ = await self.get_runs_for_time_range(start, end, scanner_type)
        
        if not runs:
            # Return empty structure with default gates
            return PipelineMonitorData(
                time_range=self._format_time_range(start, end),
                scanner_type=self._format_scanner_type(scanner_type),
                total_input=0,
                stages=[
                    DisplayStage(
                        id=i,
                        name=STAGE_MAPPING[i]["name"],
                        description=STAGE_MAPPING[i]["description"],
                        input=0,
                        output=0,
                        status=StageStatus.HEALTHY,
                        gates=self.mapper._build_default_gates_for_stage(i),
                    )
                    for i in range(1, 6)
                ],
            )
        
        # Aggregate across runs
        all_events: list[StageEvent] = []
        all_gate_results: list[GateResult] = []
        total_approves = 0
        total_watches = 0
        total_rejects = 0
        
        for run in runs:
            events = await StageEventTable.list_by_run(run.run_id)
            all_events.extend(events)
            
            # Get gate results if available
            try:
                results = await GateResultTable.list_by_run(run.run_id)
                all_gate_results.extend(results)
            except Exception:
                pass  # Gate results may not exist
            
            total_approves += run.total_approves
            total_watches += run.total_watches
            total_rejects += run.total_rejects
        
        # Build aggregated stages
        stages: list[DisplayStage] = []
        for stage_id in range(1, 6):
            items_in, items_out, _ = self.mapper.aggregate_stage_events(all_events, stage_id)
            status, anomaly_msg = self.mapper.detect_anomaly(items_in, items_out, stage_id == 5)
            
            gates = await self.mapper.build_gates_for_stage("", stage_id, all_gate_results)
            
            breakdown = None
            if stage_id == 5:
                breakdown = VerdictBreakdown(
                    approve=total_approves,
                    watch=total_watches,
                    reject=total_rejects,
                )
            
            stages.append(DisplayStage(
                id=stage_id,
                name=STAGE_MAPPING[stage_id]["name"],
                description=STAGE_MAPPING[stage_id]["description"],
                input=items_in,
                output=items_out,
                status=status,
                anomaly_message=anomaly_msg,
                gates=gates if gates else None,
                breakdown=breakdown,
            ))
        
        total_input = stages[0].input if stages else 0
        
        return PipelineMonitorData(
            time_range=self._format_time_range(start, end),
            scanner_type=self._format_scanner_type(scanner_type),
            total_input=total_input,
            stages=stages,
        )

    def _format_time_range(self, start: datetime, end: datetime) -> str:
        """Format time range for display."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        if start >= today_start:
            return "Today"
        elif start >= today_start - timedelta(days=1):
            return "Yesterday"
        elif start >= today_start - timedelta(days=7):
            return "Last 7 Days"
        elif start >= today_start - timedelta(days=30):
            return "Last 30 Days"
        else:
            return f"{start.strftime('%Y-%m-%d')} - {end.strftime('%Y-%m-%d')}"

    def _format_scanner_type(self, scanner_type: Optional[ScannerType]) -> str:
        """Format scanner type for display."""
        if scanner_type is None:
            return "All Scanners"
        
        labels = {
            ScannerType.UNUSUAL_VOLUME: "Unusual Volume",
            ScannerType.BREAKOUT: "Breakout Detection",
            ScannerType.BREAKDOWN: "Breakdown Detection",
            ScannerType.COMPRESSION_EXPANSION: "Compression",
            ScannerType.CHEAP_OPTIONS: "Cheap Options",
        }
        return labels.get(scanner_type, str(scanner_type.value))


# Singleton instances
stage_mapper = StageMapper()
pipeline_aggregator = PipelineAggregator()
