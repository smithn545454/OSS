"""Extended tests for paper_trading/position_manager.py.

Covers update_position, update_open_positions, _update_position_from_chain,
close_position_manually, and edge cases.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.schemas import (
    Decision,
    ExitReason,
    PaperPosition,
    PositionStatus,
    TrackingConfig,
    Verdict,
)
from app.paper_trading.position_manager import (
    update_position,
    update_open_positions,
    close_position_manually,
    _update_position_from_chain,
)
from app.paper_trading.models import UpdateResult


def _make_position(
    pos_id="pos-1",
    entry_price=5.0,
    current_price=5.0,
    pnl=0.0,
    mfe=0.0,
    mae=0.0,
    days=0,
):
    return PaperPosition(
        position_id=pos_id,
        evaluation_id=f"eval-{pos_id}",
        option_ticker="O:AAPL260320C00185000",
        entry_price=entry_price,
        entry_date="2026-01-15",
        verdict_at_entry=Verdict.APPROVE,
        current_price=current_price,
        current_pnl_pct=pnl,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        days_held=days,
        status=PositionStatus.OPEN,
    )


class TestUpdatePosition:

    @pytest.mark.asyncio
    async def test_price_increase_no_exit(self):
        position = _make_position(entry_price=5.0, current_price=5.0)
        config = TrackingConfig()

        with patch("app.paper_trading.position_manager.check_exit_conditions", return_value=None), \
             patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.update = AsyncMock()
            result = await update_position(position, 6.0, "2026-03-20", config)

        assert result.exit_triggered is False
        assert result.current_price == 6.0
        assert result.current_pnl_pct == pytest.approx(20.0)
        assert result.mfe_updated is True

    @pytest.mark.asyncio
    async def test_price_decrease_updates_mae(self):
        position = _make_position(entry_price=5.0, current_price=5.0)
        config = TrackingConfig()

        with patch("app.paper_trading.position_manager.check_exit_conditions", return_value=None), \
             patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.update = AsyncMock()
            result = await update_position(position, 4.0, "2026-03-20", config)

        assert result.mae_updated is True
        assert result.current_pnl_pct == pytest.approx(-20.0)

    @pytest.mark.asyncio
    async def test_exit_triggered(self):
        position = _make_position(entry_price=5.0, current_price=5.0)
        config = TrackingConfig()

        with patch("app.paper_trading.position_manager.check_exit_conditions", return_value=ExitReason.PROFIT_TARGET), \
             patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.close = AsyncMock(return_value=position)
            result = await update_position(position, 8.0, "2026-03-20", config)

        assert result.exit_triggered is True
        assert result.exit_reason == ExitReason.PROFIT_TARGET

    @pytest.mark.asyncio
    async def test_zero_entry_price(self):
        position = _make_position(entry_price=0.0, current_price=0.0)

        with patch("app.paper_trading.position_manager.check_exit_conditions", return_value=None), \
             patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.update = AsyncMock()
            result = await update_position(position, 1.0, "2026-03-20")

        assert result.current_pnl_pct == 0.0

    @pytest.mark.asyncio
    async def test_default_config(self):
        position = _make_position(entry_price=5.0, current_price=5.0)

        with patch("app.paper_trading.position_manager.check_exit_conditions", return_value=None), \
             patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.update = AsyncMock()
            result = await update_position(position, 5.5, "2026-03-20")

        assert result.exit_triggered is False


class TestUpdateOpenPositions:

    @pytest.mark.asyncio
    async def test_no_open_positions(self):
        polygon = AsyncMock()
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[])
            results = await update_open_positions(polygon)

        assert results == []

    @pytest.mark.asyncio
    async def test_with_positions(self):
        position = _make_position()
        polygon = AsyncMock()

        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table, \
             patch("app.paper_trading.position_manager._update_position_from_chain") as mock_update:
            mock_table.list_open = AsyncMock(return_value=[position])
            polygon.get_options_chain = AsyncMock(return_value=[{
                "details": {"ticker": "O:AAPL260320C00185000"},
                "day": {"close": 5.5},
            }])
            mock_update.return_value = UpdateResult(
                position_id="pos-1",
                option_ticker="O:AAPL260320C00185000",
                previous_price=5.0,
                current_price=5.5,
                previous_pnl_pct=0.0,
                current_pnl_pct=10.0,
                mfe_updated=True,
                mae_updated=False,
                exit_triggered=False,
            )
            results = await update_open_positions(polygon)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_chain_fetch_error(self):
        position = _make_position()
        polygon = AsyncMock()

        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[position])
            polygon.get_options_chain = AsyncMock(side_effect=Exception("API error"))
            results = await update_open_positions(polygon)

        assert len(results) == 1
        assert results[0].error == "API error"


class TestUpdatePositionFromChain:

    @pytest.mark.asyncio
    async def test_contract_found(self):
        position = _make_position()
        contract_data = {
            "O:AAPL260320C00185000": {
                "details": {
                    "ticker": "O:AAPL260320C00185000",
                    "expiration_date": "2026-03-20",
                },
                "day": {"close": 5.5},
                "last_quote": {"bid": 5.3, "ask": 5.7},
            }
        }

        with patch("app.paper_trading.position_manager.update_position") as mock_up:
            mock_up.return_value = UpdateResult(
                position_id="pos-1",
                option_ticker="O:AAPL260320C00185000",
                previous_price=5.0,
                current_price=5.5,
                previous_pnl_pct=0.0,
                current_pnl_pct=10.0,
                mfe_updated=True,
                mae_updated=False,
                exit_triggered=False,
            )
            result = await _update_position_from_chain(position, contract_data)
            assert result.exit_triggered is False

    @pytest.mark.asyncio
    async def test_contract_not_found_not_expired(self):
        position = _make_position()
        # Ticker indicates far future expiration
        position.option_ticker = "O:AAPL990320C00185000"

        with patch("app.paper_trading.position_manager.calculate_dte_from_expiration", return_value=100):
            result = await _update_position_from_chain(position, {})
        assert result.error == "Contract not found in chain"

    @pytest.mark.asyncio
    async def test_contract_not_found_expired(self):
        position = _make_position()
        position.option_ticker = "O:AAPL250115C00185000"  # Past date

        with patch("app.paper_trading.position_manager.calculate_dte_from_expiration", return_value=-5), \
             patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.close = AsyncMock(return_value=position)
            result = await _update_position_from_chain(position, {})
        assert result.exit_triggered is True
        assert result.exit_reason == ExitReason.EXPIRATION


class TestClosePositionManually:

    @pytest.mark.asyncio
    async def test_position_found(self):
        position = _make_position(pos_id="pos-manual")
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[position])
            mock_table.close = AsyncMock(return_value=position)
            result = await close_position_manually("pos-manual", exit_price=6.0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_position_not_found(self):
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[])
            result = await close_position_manually("missing-pos")
        assert result is None

    @pytest.mark.asyncio
    async def test_close_with_default_price(self):
        position = _make_position(pos_id="pos-default", current_price=7.0)
        with patch("app.paper_trading.position_manager.PaperPositionTable") as mock_table:
            mock_table.list_open = AsyncMock(return_value=[position])
            mock_table.close = AsyncMock(return_value=position)
            result = await close_position_manually("pos-default")
        assert result is not None
