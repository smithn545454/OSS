"""Tests for Polygon and selection drop counter telemetry (audit C3).

These counters are additive — they must never change the fail-open behavior
of the batch paths, only surface drops that were previously invisible.
"""

from __future__ import annotations

import pytest

from app.selection import contract_selector as contract_selector_module
from app.selection import evaluation_builder as evaluation_builder_module
from app.services.polygon import PolygonClient


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_polygon_client_initializes_drop_counters() -> None:
    client = PolygonClient()
    assert client.drop_counts == {
        "daily_bars_json": 0,
        "grouped_daily_json": 0,
        "options_chain_json": 0,
        "options_volume_json": 0,
        "previous_close_json": 0,
    }
    assert client.dropped_tickers == {
        "daily_bars": [],
        "options_chain": [],
        "options_volume": [],
        "previous_close": [],
    }


@pytest.mark.asyncio
async def test_polygon_options_chain_batch_counts_drops() -> None:
    client = PolygonClient()

    async def fake_fetch_ok(ticker: str, *_a, **_kw):
        return []

    async def fake_fetch_raise(ticker: str, *_a, **_kw):
        raise ValueError(f"malformed json for {ticker}")

    # Swap the underlying per-ticker fetch. The batch wraps it in fetch_one
    # which calls self.get_options_chain. Patch that directly.
    call_map = {"NVDA": fake_fetch_raise, "AAPL": fake_fetch_ok}

    async def dispatch(ticker: str, *a, **kw):
        return await call_map[ticker](ticker, *a, **kw)

    client.get_options_chain = dispatch  # type: ignore[assignment]

    result = await client.get_options_chain_batch(["NVDA", "AAPL"])

    assert client.drop_counts["options_chain_json"] == 1
    assert client.dropped_tickers["options_chain"] == ["NVDA"]
    # Fail-open preserved: AAPL still flowed through.
    assert "AAPL" in result


@pytest.mark.asyncio
async def test_polygon_previous_close_batch_counts_drops() -> None:
    client = PolygonClient()

    async def dispatch(ticker: str, *a, **kw):
        if ticker == "BOOM":
            raise RuntimeError("boom")
        return {"c": 100.0}

    client.get_previous_close = dispatch  # type: ignore[assignment]

    result = await client.get_previous_close_batch(["BOOM", "OK"])
    assert client.drop_counts["previous_close_json"] == 1
    assert client.dropped_tickers["previous_close"] == ["BOOM"]
    assert "OK" in result


def test_contract_selector_drop_counters_reset_and_get() -> None:
    contract_selector_module.reset_drop_counts()
    assert contract_selector_module.get_drop_counts() == {
        "contract_select": 0,
        "contract_parse": 0,
    }

    contract_selector_module._drop_counts["contract_parse"] = 7
    assert contract_selector_module.get_drop_counts()["contract_parse"] == 7

    contract_selector_module.reset_drop_counts()
    assert contract_selector_module.get_drop_counts()["contract_parse"] == 0


def test_evaluation_builder_drop_counters_reset_and_get() -> None:
    evaluation_builder_module.reset_drop_counts()
    assert evaluation_builder_module.get_drop_counts() == {
        "evaluation_build": 0,
        "evaluation_missing_opp": 0,
    }

    evaluation_builder_module._drop_counts["evaluation_build"] = 3
    assert evaluation_builder_module.get_drop_counts()["evaluation_build"] == 3

    evaluation_builder_module.reset_drop_counts()
    assert evaluation_builder_module.get_drop_counts()["evaluation_build"] == 0


def test_selection_counters_are_independent() -> None:
    """Ensure reset of one module doesn't clobber the other."""
    contract_selector_module.reset_drop_counts()
    evaluation_builder_module.reset_drop_counts()
    contract_selector_module._drop_counts["contract_select"] = 5
    evaluation_builder_module._drop_counts["evaluation_build"] = 2

    contract_selector_module.reset_drop_counts()
    assert contract_selector_module.get_drop_counts()["contract_select"] == 0
    assert evaluation_builder_module.get_drop_counts()["evaluation_build"] == 2

    evaluation_builder_module.reset_drop_counts()
    assert evaluation_builder_module.get_drop_counts()["evaluation_build"] == 0
