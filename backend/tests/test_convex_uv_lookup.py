"""Tests for app.convex.uv_lookup — UV signal aggregation from the
legacy scanner's per-contract detections.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.convex.uv_lookup import (
    UVSignal,
    _classify_skew,
    lookup_uv_signal,
    to_dict,
)


# ---------------------------------------------------------------------------
# Skew classification
# ---------------------------------------------------------------------------


class TestClassifySkew:

    def test_balanced_when_within_band(self):
        # 50/50 → balanced
        assert _classify_skew(500, 500) == "balanced"
        # 60/40 (within ±20%) → balanced at default 0.40 band
        assert _classify_skew(600, 400) == "balanced"

    def test_call_heavy(self):
        # 80/20 → call_heavy
        assert _classify_skew(800, 200) == "call_heavy"

    def test_put_heavy(self):
        assert _classify_skew(150, 850) == "put_heavy"

    def test_zero_volume_balanced(self):
        assert _classify_skew(0, 0) == "balanced"

    def test_custom_band(self):
        # Tighter band: 60/40 outside ±0.10 → call_heavy
        assert _classify_skew(600, 400, balanced_band=0.10) == "call_heavy"


# ---------------------------------------------------------------------------
# UVSignal alignment
# ---------------------------------------------------------------------------


class TestUVSignalAlignment:

    def _signal(self, skew: str) -> UVSignal:
        return UVSignal(
            ticker="NVDA",
            detection_count=5,
            total_today_volume=10000,
            total_avg_volume=2000,
            volume_ratio=5.0,
            call_volume=8000,
            put_volume=2000,
            directional_skew=skew,
            is_unusual=True,
            lookback_hours=24,
        )

    def test_call_heavy_aligns_with_bullish(self):
        assert self._signal("call_heavy").aligns_with("bullish") is True
        assert self._signal("call_heavy").aligns_with("bearish") is False

    def test_put_heavy_aligns_with_bearish(self):
        assert self._signal("put_heavy").aligns_with("bearish") is True
        assert self._signal("put_heavy").aligns_with("bullish") is False

    def test_balanced_never_aligns(self):
        assert self._signal("balanced").aligns_with("bullish") is False
        assert self._signal("balanced").aligns_with("bearish") is False

    def test_ambiguous_direction_never_aligns(self):
        assert self._signal("call_heavy").aligns_with("ambiguous") is False


# ---------------------------------------------------------------------------
# DynamoDB lookup
# ---------------------------------------------------------------------------


class TestLookupUVSignal:

    @pytest.mark.asyncio
    async def test_zero_detections_returns_neutral_signal(self):
        fake_table = MagicMock()
        fake_table.query.return_value = {"Items": []}
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table

        with patch(
            "app.convex.uv_lookup.boto3.resource", return_value=fake_resource
        ):
            signal = await lookup_uv_signal("NVDA")

        assert signal is not None
        assert signal.detection_count == 0
        assert signal.is_unusual is False
        assert signal.directional_skew == "balanced"

    @pytest.mark.asyncio
    async def test_aggregates_call_heavy_unusual(self):
        # 4 contracts: 3 calls (high volume), 1 put (low) → call_heavy + unusual
        items = [
            {
                "today_volume": 5000,
                "avg_volume_20d": 500,
                "option_type": "CALL",
            },
            {
                "today_volume": 4000,
                "avg_volume_20d": 800,
                "option_type": "CALL",
            },
            {
                "today_volume": 1000,
                "avg_volume_20d": 200,
                "option_type": "CALL",
            },
            {
                "today_volume": 200,
                "avg_volume_20d": 100,
                "option_type": "PUT",
            },
        ]
        fake_table = MagicMock()
        fake_table.query.return_value = {"Items": items}
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table

        with patch(
            "app.convex.uv_lookup.boto3.resource", return_value=fake_resource
        ):
            signal = await lookup_uv_signal("NVDA")

        assert signal is not None
        assert signal.detection_count == 4
        assert signal.total_today_volume == 10200
        assert signal.total_avg_volume == 1600
        # 10200 / 1600 = 6.375 ≥ 3.0 threshold
        assert signal.volume_ratio == pytest.approx(6.375)
        assert signal.is_unusual is True
        assert signal.directional_skew == "call_heavy"
        assert signal.aligns_with("bullish") is True

    @pytest.mark.asyncio
    async def test_query_failure_returns_none(self):
        fake_table = MagicMock()
        fake_table.query.side_effect = Exception("GSI not yet active")
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table

        with patch(
            "app.convex.uv_lookup.boto3.resource", return_value=fake_resource
        ):
            signal = await lookup_uv_signal("NVDA")

        assert signal is None


class TestToDict:

    def test_none_returns_none(self):
        assert to_dict(None) is None

    def test_round_trips_signal(self):
        s = UVSignal(
            ticker="NVDA",
            detection_count=3,
            total_today_volume=1234.5,
            total_avg_volume=300.5,
            volume_ratio=4.107,
            call_volume=900.0,
            put_volume=334.5,
            directional_skew="call_heavy",
            is_unusual=True,
            lookback_hours=24,
        )
        d = to_dict(s)
        assert d is not None
        assert d["detection_count"] == 3
        assert d["volume_ratio"] == 4.11
        assert d["directional_skew"] == "call_heavy"
        assert d["is_unusual"] is True
