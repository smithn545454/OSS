"""Tests for StageMapper (observability/stage_mapper.py).

Covers detect_anomaly, aggregate_stage_events, and build_gates_for_stage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.schemas import (
    PipelineStage,
    StageEvent,
    StageStatus,
)
from app.observability.stage_mapper import (
    GATE_DEFS_BY_STAGE,
    STAGE_2_GATES,
    STAGE_6_GATES,
    STAGE_MAPPING,
    StageMapper,
)


@pytest.fixture
def mapper():
    return StageMapper()


# ---------------------------------------------------------------------------
# Tests: detect_anomaly
# ---------------------------------------------------------------------------


class TestDetectAnomaly:

    def test_healthy_stage(self, mapper):
        status, msg = mapper.detect_anomaly(100, 50)
        assert status == StageStatus.HEALTHY
        assert msg is None

    def test_zero_output_anomaly(self, mapper):
        status, msg = mapper.detect_anomaly(100, 0)
        assert status == StageStatus.ANOMALY
        assert "Zero contracts" in msg

    def test_output_exceeds_input(self, mapper):
        status, msg = mapper.detect_anomaly(50, 100)
        assert status == StageStatus.ANOMALY
        assert "Data integrity" in msg

    def test_low_pass_rate_anomaly(self, mapper):
        status, msg = mapper.detect_anomaly(10000, 5)
        assert status == StageStatus.ANOMALY
        assert "low pass rate" in msg

    def test_final_stage_no_low_pass_check(self, mapper):
        # Final stage should not trigger low pass rate anomaly
        status, msg = mapper.detect_anomaly(100, 1, is_final=True)
        assert status == StageStatus.HEALTHY

    def test_zero_input_healthy(self, mapper):
        status, msg = mapper.detect_anomaly(0, 0)
        assert status == StageStatus.HEALTHY


# ---------------------------------------------------------------------------
# Tests: aggregate_stage_events
# ---------------------------------------------------------------------------


def _event(run_id, stage, items_in, items_out, drop=None):
    now = datetime.now(timezone.utc).isoformat()
    return StageEvent(
        run_id=run_id, stage=stage,
        started_at=now, completed_at=now,
        items_in=items_in, items_out=items_out,
        items_dropped=items_in - items_out,
        drop_reasons=drop or {},
    )


class TestAggregateStageEvents:

    def test_single_stage(self, mapper):
        events = [_event("run-1", PipelineStage.OPPORTUNITY_DISCOVERY, 500, 50)]
        items_in, items_out, drops = mapper.aggregate_stage_events(events, 1)
        assert items_in == 500
        assert items_out == 50

    def test_multi_internal_stages(self, mapper):
        """Each stage now maps to exactly one internal stage (1:1)."""
        events = [
            _event("run-1", PipelineStage.FEATURE_COMPUTATION, 100, 100),
        ]
        items_in, items_out, drops = mapper.aggregate_stage_events(events, 4)
        assert items_in == 100
        assert items_out == 100

    def test_no_matching_events(self, mapper):
        events = [_event("run-1", PipelineStage.DECISION_LOGIC, 20, 5)]
        items_in, items_out, drops = mapper.aggregate_stage_events(events, 1)
        assert items_in == 0
        assert items_out == 0

    def test_multi_run_aggregation(self, mapper):
        events = [
            _event("run-1", PipelineStage.OPPORTUNITY_DISCOVERY, 200, 30),
            _event("run-2", PipelineStage.OPPORTUNITY_DISCOVERY, 300, 40),
        ]
        items_in, items_out, drops = mapper.aggregate_stage_events(events, 1)
        assert items_in == 500
        assert items_out == 70


# ---------------------------------------------------------------------------
# Tests: STAGE_MAPPING / gate definition constants
# ---------------------------------------------------------------------------


class TestMappingConstants:

    def test_all_8_stages_defined(self):
        assert set(STAGE_MAPPING.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_each_stage_has_one_internal_stage(self):
        for stage_id, stage_def in STAGE_MAPPING.items():
            assert len(stage_def["internal_stages"]) == 1, (
                f"Stage {stage_id} should map to exactly 1 internal stage"
            )

    def test_stage6_has_all_9_backend_gates(self):
        """Stage 6 should map all 9 real backend gate IDs."""
        all_backend_ids = set()
        for gate_def in STAGE_6_GATES.values():
            for _display_name, backend_id in gate_def["rules"]:
                all_backend_ids.add(backend_id)
        expected = {
            "GATE_MIN_OPEN_INTEREST", "GATE_MIN_VOLUME", "GATE_MAX_SPREAD_PCT",
            "GATE_DTE_RANGE", "GATE_MOVE_SUFFICIENCY", "GATE_BREAKOUT_VOLUME",
            "GATE_GREEKS_COHERENCE", "GATE_IV_PERCENTILE_MAX", "GATE_THETA_BURDEN_MAX",
        }
        assert all_backend_ids == expected

    def test_stage2_has_filter_rules(self):
        """Stage 2 should have underlying quality filter rules."""
        gate_def = STAGE_2_GATES["underlying_quality"]
        assert len(gate_def["rules"]) == 4
        backend_keys = {r[1] for r in gate_def["rules"]}
        assert "FILTER_FAIL_UNDERLYING_PRICE" in backend_keys
        assert "FILTER_FAIL_DOLLAR_VOLUME" in backend_keys

    def test_gate_defs_by_stage_only_has_stages_with_gates(self):
        """Only stages 1, 2, 6 should have gate definitions."""
        assert set(GATE_DEFS_BY_STAGE.keys()) == {1, 2, 6}
