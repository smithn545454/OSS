"""Stage mapper service for Pipeline Monitor.

Maps the internal 8-stage pipeline to the 8-stage display model (1:1).

Stage Mapping:
- Stage 1 (Opportunity Discovery): OPPORTUNITY_DISCOVERY
- Stage 2 (Underlying Filters): UNDERLYING_FILTERS
- Stage 3 (Contract Selection): CONTRACT_SELECTION
- Stage 4 (Feature Computation): FEATURE_COMPUTATION
- Stage 5 (Pillar Scoring): PILLAR_SCORING
- Stage 6 (Hard Gates): HARD_GATES
- Stage 7 (Decision Logic): DECISION_LOGIC
- Stage 8 (Paper Trading): PAPER_TRADING
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import get_settings
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

# Stage mapping configuration (1:1 with backend pipeline stages)
STAGE_MAPPING = {
    1: {
        "name": "Opportunity Discovery",
        "description": "Scanner-driven ticker identification",
        "internal_stages": [PipelineStage.OPPORTUNITY_DISCOVERY],
    },
    2: {
        "name": "Underlying Filters",
        "description": "Remove low-quality underlyings",
        "internal_stages": [PipelineStage.UNDERLYING_FILTERS],
    },
    3: {
        "name": "Contract Selection",
        "description": "Select contracts per DTE/delta bucket",
        "internal_stages": [PipelineStage.CONTRACT_SELECTION],
    },
    4: {
        "name": "Feature Computation",
        "description": "Calculate scoring inputs (liquidity, volatility, catalyst)",
        "internal_stages": [PipelineStage.FEATURE_COMPUTATION],
    },
    5: {
        "name": "Pillar Scoring",
        "description": "Score Directional, Volatility, and Structure pillars",
        "internal_stages": [PipelineStage.PILLAR_SCORING],
    },
    6: {
        "name": "Hard Gates",
        "description": "Binary pass/fail checks — any failure rejects",
        "internal_stages": [PipelineStage.HARD_GATES],
    },
    7: {
        "name": "Decision Logic",
        "description": "Final verdict: APPROVE / WATCH / REJECT",
        "internal_stages": [PipelineStage.DECISION_LOGIC],
    },
    8: {
        "name": "Paper Trading",
        "description": "Track simulated performance of approved trades",
        "internal_stages": [PipelineStage.PAPER_TRADING],
    },
}

# --- Gate/filter definitions per stage ---
# Each rule is a (display_name, backend_key) tuple where backend_key is:
#   - a gate ID for Stage 6 (looked up in pass_by_gate / failure_by_gate)
#   - a drop_reason key for Stage 2 (looked up in stage event drop_reasons)

STAGE_1_GATES = {
    "scanner_triggers": {
        "name": "Liquidity Gate",
        "rules": [],  # Built dynamically from scanner_stats metadata
    },
    "basic_eligibility": {
        "name": "Basic Eligibility",
        "rules": [],  # Built dynamically from scanner_stats metadata
    },
}

STAGE_2_GATES = {
    "underlying_quality": {
        "name": "Underlying Quality",
        "rules": [
            ("Price ≥ $5", "FILTER_FAIL_UNDERLYING_PRICE"),
            ("Dollar Volume ≥ $20M", "FILTER_FAIL_DOLLAR_VOLUME"),
            ("Data Completeness", "FILTER_FAIL_MISSING_BARS"),
            ("Earnings Window", "FILTER_FAIL_EARNINGS_WINDOW"),
        ],
    },
}

STAGE_6_GATES = {
    "liquidity": {
        "name": "Liquidity",
        "rules": [
            ("Open Interest ≥ 300", "GATE_MIN_OPEN_INTEREST"),
            ("Volume ≥ 75", "GATE_MIN_VOLUME"),
            ("Spread ≤ 8%", "GATE_MAX_SPREAD_PCT"),
        ],
    },
    "structure": {
        "name": "Structure",
        "rules": [
            ("DTE 7-120 days", "GATE_DTE_RANGE"),
            ("Move Sufficiency ≤ 1.25", "GATE_MOVE_SUFFICIENCY"),
            ("Breakout Volume ≥ 1.5×", "GATE_BREAKOUT_VOLUME"),
        ],
    },
    "data_quality": {
        "name": "Data Quality",
        "rules": [
            ("Greeks Coherence", "GATE_GREEKS_COHERENCE"),
            ("IV Percentile ≤ 85%", "GATE_IV_PERCENTILE_MAX"),
            ("Theta Burden ≤ 4%/day", "GATE_THETA_BURDEN_MAX"),
        ],
    },
}

# Combined lookup for default gate building
GATE_DEFS_BY_STAGE: dict[int, dict] = {
    1: STAGE_1_GATES,
    2: STAGE_2_GATES,
    6: STAGE_6_GATES,
}


class StageMapper:
    """Maps internal pipeline stages to display format."""

    # Stages where output > input is normal (fan-out stages)
    FAN_OUT_STAGES = {3, 8}  # Contract Selection, Paper Trading

    def detect_anomaly(
        self, stage_input: int, stage_output: int, is_final: bool = False,
        stage_id: int = 0,
    ) -> tuple[StageStatus, Optional[str]]:
        """Detect anomalies per spec section 5.11.1."""
        if stage_output == 0 and stage_input > 0:
            return StageStatus.ANOMALY, f"Zero contracts passed — all {stage_input} filtered out"

        if stage_output > stage_input and stage_id not in self.FAN_OUT_STAGES:
            return StageStatus.ANOMALY, "Data integrity issue: output exceeds input"

        if not is_final and stage_input > 0 and stage_id not in self.FAN_OUT_STAGES:
            pass_rate = (stage_output / stage_input) * 100
            if pass_rate < 1:
                return StageStatus.ANOMALY, f"Unusually low pass rate: {pass_rate:.1f}%"

        return StageStatus.HEALTHY, None

    def aggregate_stage_events(
        self,
        events: list[StageEvent],
        display_stage_id: int,
    ) -> tuple[int, int, dict[str, int]]:
        """Aggregate metrics from internal stages to a display stage."""
        internal_stages = STAGE_MAPPING[display_stage_id]["internal_stages"]

        matching_events = [
            e for e in events
            if e.stage in internal_stages
        ]

        if not matching_events:
            return 0, 0, {}

        items_in = sum(e.items_in for e in matching_events)
        items_out = sum(e.items_out for e in matching_events)

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
        events: list[StageEvent] | None = None,
    ) -> list[DisplayGate]:
        """Build gate display data for a stage from real backend data.

        Routes to stage-specific builders:
        - Stage 1: Scanner trigger breakdown from scanner_stats metadata
        - Stage 2: Filter breakdown from drop_reasons
        - Stage 6: All 9 hard gates from pass_by_gate/failure_by_gate metadata
        """
        if not events:
            return self._build_default_gates_for_stage(stage_id)

        if stage_id == 1:
            return self._build_stage1_gates(events)
        elif stage_id == 2:
            return self._build_stage2_gates(events)
        elif stage_id == 6:
            if gate_results:
                return self._build_stage6_gates_from_results(gate_results)
            return self._build_stage6_gates(events)

        return []

    # --- Stage 1: Scanner trigger breakdown ---

    def _build_stage1_gates(self, events: list[StageEvent]) -> list[DisplayGate]:
        """Build Stage 1 gates from scanner_stats metadata."""
        # Aggregate scanner stats across all OPPORTUNITY_DISCOVERY events
        scanner_totals: dict[str, dict[str, int]] = {}
        for event in events:
            if event.stage != PipelineStage.OPPORTUNITY_DISCOVERY:
                continue
            meta = event.metadata or {}
            scanner_stats = meta.get("scanner_stats", {})
            for scanner_name, stats in scanner_stats.items():
                if scanner_name not in scanner_totals:
                    scanner_totals[scanner_name] = {"total_scanned": 0, "triggered": 0}
                scanner_totals[scanner_name]["total_scanned"] += int(stats.get("total_scanned", 0))
                scanner_totals[scanner_name]["triggered"] += int(stats.get("triggered", 0))

        if not scanner_totals:
            return self._build_default_gates_for_stage(1)

        # Build a single gate group showing per-scanner trigger rates
        scanner_labels = {
            "BREAKOUT": "Breakout Scanner",
            "COMPRESSION": "Compression Scanner",
            "CHEAP_OPTIONS": "Cheap Options Scanner",
            "UNUSUAL_VOLUME": "Unusual Volume Scanner",
        }

        rules = []
        total_triggered = 0
        total_not_triggered = 0
        for scanner_name, stats in sorted(scanner_totals.items()):
            triggered = stats["triggered"]
            not_triggered = stats["total_scanned"] - triggered
            total_triggered += triggered
            total_not_triggered += not_triggered
            label = scanner_labels.get(scanner_name, scanner_name)
            rules.append(DisplayRule(
                name=label,
                passed=triggered,
                failed=not_triggered,
                severity=RuleSeverity.NORMAL,
            ))

        return [DisplayGate(
            id="scanner_triggers",
            name="Scanner Triggers",
            passed=total_triggered,
            failed=total_not_triggered,
            rules=rules,
            overlaps=[],
        )]

    # --- Stage 2: Underlying filter breakdown ---

    def _build_stage2_gates(self, events: list[StageEvent]) -> list[DisplayGate]:
        """Build Stage 2 gates from drop_reasons in stage events."""
        # Aggregate drop_reasons and items_in across all UNDERLYING_FILTERS events
        total_items_in = 0
        drop_totals: dict[str, int] = {}
        for event in events:
            if event.stage != PipelineStage.UNDERLYING_FILTERS:
                continue
            total_items_in += event.items_in
            for reason, count in event.drop_reasons.items():
                drop_totals[reason] = drop_totals.get(reason, 0) + int(count)

        if total_items_in == 0:
            return self._build_default_gates_for_stage(2)

        gate_def = STAGE_2_GATES["underlying_quality"]
        rules = []
        total_passed = 0
        total_failed = 0
        for rule_name, drop_reason_key in gate_def["rules"]:
            failed = drop_totals.get(drop_reason_key, 0)
            passed = total_items_in - failed
            total_passed += passed
            total_failed += failed
            rules.append(DisplayRule(
                name=rule_name,
                passed=passed,
                failed=failed,
                severity=(
                    RuleSeverity.CRITICAL
                    if passed == 0 and failed > 0
                    else RuleSeverity.NORMAL
                ),
            ))

        return [DisplayGate(
            id="underlying_quality",
            name=gate_def["name"],
            passed=total_passed,
            failed=total_failed,
            rules=rules,
            overlaps=[],
        )]

    # --- Stage 6: All 9 hard gates ---

    def _build_stage6_gates(self, events: list[StageEvent]) -> list[DisplayGate]:
        """Build Stage 6 gates from pass_by_gate/failure_by_gate in metadata."""
        # Aggregate pass/fail counts from all HARD_GATES events
        pass_by_gate: dict[str, int] = {}
        fail_by_gate: dict[str, int] = {}
        for event in events:
            if event.stage != PipelineStage.HARD_GATES:
                continue
            meta = event.metadata or {}
            for gid, count in meta.get("pass_by_gate", {}).items():
                pass_by_gate[gid] = pass_by_gate.get(gid, 0) + int(count)
            for gid, count in meta.get("failure_by_gate", {}).items():
                fail_by_gate[gid] = fail_by_gate.get(gid, 0) + int(count)

        gates: list[DisplayGate] = []
        for gate_id, gate_def in STAGE_6_GATES.items():
            rules = []
            group_passed = 0
            group_failed = 0
            for rule_name, backend_gate_id in gate_def["rules"]:
                passed = pass_by_gate.get(backend_gate_id, 0)
                failed = fail_by_gate.get(backend_gate_id, 0)
                group_passed += passed
                group_failed += failed
                rules.append(DisplayRule(
                    name=rule_name,
                    passed=passed,
                    failed=failed,
                    severity=(
                        RuleSeverity.CRITICAL
                        if passed == 0 and failed > 0
                        else RuleSeverity.NORMAL
                    ),
                ))

            gates.append(DisplayGate(
                id=gate_id,
                name=gate_def["name"],
                passed=group_passed,
                failed=group_failed,
                rules=rules,
                overlaps=[],
            ))

        return gates

    def _build_stage6_gates_from_results(
        self,
        gate_results: list[GateResult],
    ) -> list[DisplayGate]:
        """Build Stage 6 gates from individual GateResult objects."""
        # Group results by backend gate ID
        results_by_gate: dict[str, list[GateResult]] = defaultdict(list)
        for result in gate_results:
            results_by_gate[result.gate_id].append(result)

        gates: list[DisplayGate] = []
        for gate_id, gate_def in STAGE_6_GATES.items():
            rules = []
            group_passed = 0
            group_failed = 0
            all_results_for_overlap: list[GateResult] = []

            for rule_name, backend_gate_id in gate_def["rules"]:
                results = results_by_gate.get(backend_gate_id, [])
                all_results_for_overlap.extend(results)
                passed = sum(1 for r in results if r.passed)
                failed = sum(1 for r in results if not r.passed)
                group_passed += passed
                group_failed += failed
                rules.append(DisplayRule(
                    name=rule_name,
                    passed=passed,
                    failed=failed,
                    severity=(
                        RuleSeverity.CRITICAL
                        if passed == 0 and failed > 0
                        else RuleSeverity.NORMAL
                    ),
                ))

            overlaps = self._compute_overlaps(all_results_for_overlap)
            gates.append(DisplayGate(
                id=gate_id,
                name=gate_def["name"],
                passed=group_passed,
                failed=group_failed,
                rules=rules,
                overlaps=overlaps,
            ))

        return gates

    # --- Default / helper methods ---

    def _build_default_gates_for_stage(self, stage_id: int) -> list[DisplayGate]:
        """Build default empty gates for a stage when no data is available."""
        gate_defs = GATE_DEFS_BY_STAGE.get(stage_id, {})
        if not gate_defs:
            return []

        gates: list[DisplayGate] = []
        for gate_id, gate_def in gate_defs.items():
            rules = []
            for rule in gate_def["rules"]:
                # rules can be plain strings or (name, backend_id) tuples
                rule_name = rule[0] if isinstance(rule, tuple) else rule
                rules.append(DisplayRule(
                    name=rule_name,
                    passed=0,
                    failed=0,
                    severity=RuleSeverity.NORMAL,
                ))
            gates.append(DisplayGate(
                id=gate_id,
                name=gate_def["name"],
                passed=0,
                failed=0,
                rules=rules,
                overlaps=[],
            ))
        return gates

    def _compute_overlaps(
        self,
        results: list[GateResult],
    ) -> list[DisplayFailureOverlap]:
        """Compute multi-rule failure overlaps."""
        failures_by_eval: dict[str, list[str]] = defaultdict(list)
        for result in results:
            if not result.passed:
                failures_by_eval[result.evaluation_id].append(result.gate_id)

        combo_counts: dict[tuple[str, ...], int] = defaultdict(int)
        for eval_id, failed_gates in failures_by_eval.items():
            if len(failed_gates) >= 2:
                combo = tuple(sorted(failed_gates))
                combo_counts[combo] += 1

        overlaps: list[DisplayFailureOverlap] = []
        for combo, count in sorted(combo_counts.items(), key=lambda x: -x[1]):
            overlaps.append(DisplayFailureOverlap(
                rules=list(combo),
                count=count,
            ))
        return overlaps[:10]

    async def build_display_stage(
        self,
        stage_id: int,
        events: list[StageEvent],
        gate_results: list[GateResult],
        run_metadata: Optional[dict[str, Any]] = None,
    ) -> DisplayStage:
        """Build a display stage from internal data."""
        stage_def = STAGE_MAPPING[stage_id]
        items_in, items_out, drop_reasons = self.aggregate_stage_events(events, stage_id)

        is_final = stage_id == 8
        status, anomaly_msg = self.detect_anomaly(items_in, items_out, is_final, stage_id=stage_id)

        gates = await self.build_gates_for_stage("", stage_id, gate_results, events=events)

        breakdown = None
        if stage_id == 7 and run_metadata:
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
        """Build complete pipeline display data for a run."""
        run_metadata = {
            "approves": run.total_approves,
            "watches": run.total_watches,
            "rejects": run.total_rejects,
        }

        stages: list[DisplayStage] = []
        for stage_id in range(1, 9):
            stage = await self.build_display_stage(
                stage_id, events, gate_results, run_metadata
            )
            stages.append(stage)

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
        """Convert a PipelineRun to a list item for the sidebar."""
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
        """Get pipeline runs within a time range."""
        all_runs = await PipelineRunTable.list(limit=1000)

        filtered_runs = []
        for run in all_runs:
            run_time = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
            if start <= run_time <= end:
                filtered_runs.append(run)

        total = len(filtered_runs)
        has_more = offset + limit < total

        return filtered_runs[offset:offset + limit], total, has_more

    async def build_aggregate_data(
        self,
        start: datetime,
        end: datetime,
        scanner_type: Optional[ScannerType] = None,
    ) -> PipelineMonitorData:
        """Build aggregated pipeline data across multiple runs."""
        runs, _, _ = await self.get_runs_for_time_range(start, end, scanner_type)

        if not runs:
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
                    for i in range(1, 9)
                ],
            )

        all_events: list[StageEvent] = []
        all_gate_results: list[GateResult] = []
        total_approves = 0
        total_watches = 0
        total_rejects = 0

        for run in runs:
            events = await StageEventTable.list_by_run(run.run_id)
            all_events.extend(events)

            try:
                results = await GateResultTable.list_by_run(run.run_id)
                all_gate_results.extend(results)
            except Exception:
                pass

            total_approves += run.total_approves
            total_watches += run.total_watches
            total_rejects += run.total_rejects

        stages: list[DisplayStage] = []
        for stage_id in range(1, 9):
            items_in, items_out, _ = self.mapper.aggregate_stage_events(all_events, stage_id)
            status, anomaly_msg = self.mapper.detect_anomaly(items_in, items_out, stage_id == 8, stage_id=stage_id)

            gates = await self.mapper.build_gates_for_stage(
                "", stage_id, all_gate_results, events=all_events,
            )

            breakdown = None
            if stage_id == 7:
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
        pacific = get_settings().display_tz
        now_local = now.astimezone(pacific)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
            timezone.utc
        )

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
