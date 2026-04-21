"""Tests for the v5 calibration rate-lookup loop (Phase 2).

Covers:
- CalibrationRatesTable roundtrip (save_latest, get_latest, save_version).
- _materialize_rate_lookups shape conversion from persisted dict to
  RateEstimate / PRateEstimate.
- compute_rate_lookups over synthetic position rows.
- Lambda handler dispatches the calibration_weekly action.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.calibration.archetype_rates import RateEstimate
from app.calibration.weekly_runner import compute_rate_lookups
from app.core.schemas import ArchetypeConfig, ArchetypeDefinition
from app.db.tables import CalibrationRatesTable
from app.decision.stage import _materialize_rate_lookups
from app.v5.p_conviction import PRateEstimate


@pytest.mark.asyncio
async def test_calibration_rates_table_roundtrip(moto_dynamodb) -> None:
    hr = {"UV_LOTTERY_CALL": {"point": 0.12, "lower": 0.07, "upper": 0.18, "n_trades": 53}}
    p = {
        "BREAKDOWN_GRIND": {
            "win_point": 0.61, "win_lower": 0.49, "win_upper": 0.71,
            "mean_pnl_pct": 18.3, "n_trades": 42,
        }
    }

    await CalibrationRatesTable.save_latest(
        hr_rates=hr,
        p_rates=p,
        window_start="2026-02-20T00:00:00+00:00",
        window_end="2026-04-20T00:00:00+00:00",
        policy_version="v4.1.1",
    )

    latest = await CalibrationRatesTable.get_latest()
    assert latest is not None
    assert "UV_LOTTERY_CALL" in latest["hr_rates"]
    assert float(latest["hr_rates"]["UV_LOTTERY_CALL"]["point"]) == pytest.approx(0.12)
    assert latest["policy_version"] == "v4.1.1"
    assert latest["window_start"] == "2026-02-20T00:00:00+00:00"


@pytest.mark.asyncio
async def test_calibration_rates_table_get_latest_missing(moto_dynamodb) -> None:
    assert await CalibrationRatesTable.get_latest() is None


@pytest.mark.asyncio
async def test_calibration_rates_table_save_version_appends(moto_dynamodb) -> None:
    ts = await CalibrationRatesTable.save_version(hr_rates={}, p_rates={})
    assert ts.startswith("20")  # ISO timestamp
    # Both writes persist; get_latest still returns None because save_version
    # only writes VERSION#ts rows.
    assert await CalibrationRatesTable.get_latest() is None


def test_materialize_rate_lookups_drops_zero_n_archetypes() -> None:
    raw = {
        "hr_rates": {
            "ALIVE": {"point": 0.1, "lower": 0.05, "upper": 0.16, "n_trades": 40},
            "EMPTY": {"point": 0.0, "lower": 0.0, "upper": 0.0, "n_trades": 0},
        },
        "p_rates": {
            "GRINDER": {
                "win_point": 0.6, "win_lower": 0.48, "win_upper": 0.72,
                "mean_pnl_pct": 15.0, "n_trades": 25,
            },
            "NO_DATA": {
                "win_point": 0.0, "win_lower": 0.0, "win_upper": 0.0,
                "mean_pnl_pct": 0.0, "n_trades": 0,
            },
        },
    }
    hr, p = _materialize_rate_lookups(raw)
    assert hr is not None and "ALIVE" in hr and "EMPTY" not in hr
    assert isinstance(hr["ALIVE"], RateEstimate)
    assert hr["ALIVE"].point == pytest.approx(0.1)
    assert p is not None and "GRINDER" in p and "NO_DATA" not in p
    assert isinstance(p["GRINDER"], PRateEstimate)
    assert p["GRINDER"].win_lower == pytest.approx(0.48)


def test_materialize_rate_lookups_handles_none_and_empty() -> None:
    assert _materialize_rate_lookups(None) == (None, None)
    assert _materialize_rate_lookups({}) == (None, None)
    # Only zero-n archetypes → returns (None, None)
    empty = {
        "hr_rates": {"X": {"point": 0.0, "lower": 0.0, "upper": 0.0, "n_trades": 0}},
        "p_rates": {},
    }
    assert _materialize_rate_lookups(empty) == (None, None)


def _mk_arch(aid: str) -> ArchetypeDefinition:
    return ArchetypeDefinition(
        archetype_id=aid,
        display_name=aid,
        description=aid,
        conditions=[],
        historical_n=0,
        historical_hr200_rate=0.05,
        historical_win_rate=0.5,
        historical_mean_pnl_pct=0.0,
    )


def test_compute_rate_lookups_filters_by_archetype_matched() -> None:
    hr_lib = ArchetypeConfig(archetypes=[_mk_arch("HR_ONE")])
    p_lib = ArchetypeConfig(archetypes=[_mk_arch("P_ONE")])
    positions = [
        # 2 HR_ONE matches (1 grand-slam at 250%, 1 loser at -30%)
        {
            "hr_archetype_matched": "HR_ONE", "p_archetype_matched": "",
            "max_favorable_excursion": 250.0, "current_pnl_pct": 180.0,
        },
        {
            "hr_archetype_matched": "HR_ONE", "p_archetype_matched": "",
            "max_favorable_excursion": 15.0, "current_pnl_pct": -30.0,
        },
        # 3 P_ONE matches (2 wins, 1 loss)
        {
            "hr_archetype_matched": "", "p_archetype_matched": "P_ONE",
            "max_favorable_excursion": 80.0, "current_pnl_pct": 22.0,
        },
        {
            "hr_archetype_matched": "", "p_archetype_matched": "P_ONE",
            "max_favorable_excursion": 40.0, "current_pnl_pct": 5.0,
        },
        {
            "hr_archetype_matched": "", "p_archetype_matched": "P_ONE",
            "max_favorable_excursion": 10.0, "current_pnl_pct": -20.0,
        },
        # Unrelated noise
        {
            "hr_archetype_matched": "OTHER", "p_archetype_matched": "OTHER",
            "max_favorable_excursion": 0.0, "current_pnl_pct": 0.0,
        },
    ]

    hr_rates, p_rates = compute_rate_lookups(positions, hr_lib, p_lib)

    assert hr_rates["HR_ONE"]["n_trades"] == 2
    # 1 of 2 hit >= 200 MFE → point == 0.5
    assert hr_rates["HR_ONE"]["point"] == pytest.approx(0.5)
    assert hr_rates["HR_ONE"]["lower"] < hr_rates["HR_ONE"]["point"]

    assert p_rates["P_ONE"]["n_trades"] == 3
    assert p_rates["P_ONE"]["win_point"] == pytest.approx(2.0 / 3.0)
    # mean P&L = (22 + 5 + -20) / 3 = 7/3 ≈ 2.33
    assert p_rates["P_ONE"]["mean_pnl_pct"] == pytest.approx(7.0 / 3.0)


def test_compute_rate_lookups_handles_none_libraries() -> None:
    hr, p = compute_rate_lookups([], None, None)
    assert hr == {} and p == {}


def test_compute_rate_lookups_empty_archetype_has_zero_n() -> None:
    hr_lib = ArchetypeConfig(archetypes=[_mk_arch("GHOST")])
    hr, _ = compute_rate_lookups([], hr_lib, None)
    assert hr["GHOST"] == {"point": 0.0, "lower": 0.0, "upper": 0.0, "n_trades": 0}


@pytest.mark.asyncio
async def test_lambda_handler_dispatches_calibration_weekly() -> None:
    """calibration_weekly action should route into run_weekly_discovery."""
    from app import main

    fake_summary = {
        "generated_at": "2026-04-21T08:00:00+00:00",
        "n_positions": 420,
        "hr_rates_count": 5,
        "p_rates_count": 3,
        "persisted": True,
        "policy_version": "v4.1.1",
    }

    with patch(
        "app.calibration.weekly_runner.run_weekly_discovery",
        new=AsyncMock(return_value=fake_summary),
    ) as mock_run:
        result = await main._run_calibration_weekly(
            {"source": "oss.scheduler", "action": "calibration_weekly"}
        )

    mock_run.assert_awaited_once()
    assert result["status"] == "success"
    assert result["n_positions"] == 420
    assert result["hr_rates_count"] == 5
    assert result["persisted"] is True
