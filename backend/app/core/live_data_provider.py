"""LiveDataProvider — wraps existing Polygon / DynamoDB clients.

Pure delegation layer; no new logic. Each method maps to an existing
service call so the pipeline can use DataProvider uniformly.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from app.core.data_provider import (
    AggregatedOptionsVolume,
    DailyBar,
    IVHistoryRecord,
    MarketContextData,
    OIHistoryRecord,
    StockSnapshot,
)

logger = logging.getLogger(__name__)


class LiveDataProvider:
    """DataProvider backed by Polygon API + DynamoDB tables."""

    def __init__(
        self,
        polygon_client: Any,
        earnings_cache: Any | None = None,
        catalyst_service: Any | None = None,
    ) -> None:
        self.polygon = polygon_client
        self.earnings_cache = earnings_cache
        self.catalyst_service = catalyst_service

    # ------------------------------------------------------------------
    # Stock data
    # ------------------------------------------------------------------

    async def get_daily_bars(
        self,
        ticker: str,
        end_date: date,
        lookback_days: int = 60,
    ) -> list[DailyBar]:
        from_date = end_date - timedelta(days=lookback_days)
        bars = await self.polygon.get_daily_bars_parsed(
            ticker,
            from_date.isoformat(),
            end_date.isoformat(),
        )
        # Convert polygon DailyBar dataclass to our DailyBar
        return [
            DailyBar(
                ticker=b.ticker,
                date=b.date,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                vwap=getattr(b, "vwap", None),
            )
            for b in bars
        ]

    async def get_daily_bars_batch(
        self,
        tickers: list[str],
        end_date: date,
        lookback_days: int = 60,
    ) -> dict[str, list[DailyBar]]:
        raw = await self.polygon.get_daily_bars_batch(tickers, days=lookback_days)
        result: dict[str, list[DailyBar]] = {}
        for t, bars in raw.items():
            result[t] = [
                DailyBar(
                    ticker=b.ticker,
                    date=b.date,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    vwap=getattr(b, "vwap", None),
                )
                for b in bars
            ]
        return result

    async def get_stock_snapshot(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[StockSnapshot]:
        prev = await self.polygon.get_previous_close(ticker)
        if not prev:
            return None
        return StockSnapshot(
            ticker=ticker,
            date=as_of.isoformat(),
            close=prev.get("close", 0.0),
            volume=prev.get("volume", 0),
            vwap=prev.get("vwap"),
            open=prev.get("open"),
            high=prev.get("high"),
            low=prev.get("low"),
        )

    async def get_stock_snapshots_batch(
        self,
        tickers: list[str],
        as_of: date,
    ) -> dict[str, StockSnapshot]:
        raw = await self.polygon.get_previous_close_batch(tickers)
        result: dict[str, StockSnapshot] = {}
        for t, data in raw.items():
            result[t] = StockSnapshot(
                ticker=t,
                date=as_of.isoformat(),
                close=data.get("close", 0.0),
                volume=data.get("volume", 0),
                vwap=data.get("vwap"),
                open=data.get("open"),
                high=data.get("high"),
                low=data.get("low"),
            )
        return result

    # ------------------------------------------------------------------
    # Options data
    # ------------------------------------------------------------------

    async def get_options_chain(
        self,
        ticker: str,
        as_of: date,
        min_dte: int = 7,
        max_dte: int = 120,
    ) -> list[dict[str, Any]]:
        exp_gte = (as_of + timedelta(days=min_dte)).isoformat()
        exp_lte = (as_of + timedelta(days=max_dte)).isoformat()
        return await self.polygon.get_options_chain(
            ticker,
            expiration_date_gte=exp_gte,
            expiration_date_lte=exp_lte,
        )

    async def get_options_chain_minimal(
        self,
        ticker: str,
        as_of: date,
        min_dte: int = 7,
        max_dte: int = 90,
        strike_range_pct: float = 0.10,
    ) -> list[dict[str, Any]]:
        exp_gte = (as_of + timedelta(days=min_dte)).isoformat()
        exp_lte = (as_of + timedelta(days=max_dte)).isoformat()
        return await self.polygon.get_options_chain_minimal(
            ticker,
            expiration_date_gte=exp_gte,
            expiration_date_lte=exp_lte,
        )

    async def get_aggregated_options_volume(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[AggregatedOptionsVolume]:
        vol = await self.polygon.get_aggregated_options_volume(ticker)
        if not vol:
            return None
        return AggregatedOptionsVolume(
            ticker=vol.ticker,
            total_call_volume=vol.total_call_volume,
            total_put_volume=vol.total_put_volume,
            total_call_oi=vol.total_call_oi,
            total_put_oi=vol.total_put_oi,
            call_put_volume_ratio=vol.call_put_volume_ratio,
            timestamp=vol.timestamp,
        )

    # ------------------------------------------------------------------
    # Volatility / Liquidity history
    # ------------------------------------------------------------------

    async def get_iv_history(
        self,
        ticker: str,
        as_of: date,
        lookback_days: int = 252,
    ) -> list[IVHistoryRecord]:
        from app.db.tables import IVHistoryTable

        start = (as_of - timedelta(days=lookback_days)).isoformat()
        records = await IVHistoryTable.list_by_ticker(
            ticker, limit=lookback_days, start_date=start, end_date=as_of.isoformat()
        )
        return [
            IVHistoryRecord(
                ticker=r.ticker,
                date=r.date,
                atm_iv=r.atm_iv,
                atm_call_iv=getattr(r, "atm_call_iv", None),
                atm_put_iv=getattr(r, "atm_put_iv", None),
                rv20=getattr(r, "rv20", None),
                iv_rv_ratio=getattr(r, "iv_rv_ratio", None),
            )
            for r in records
        ]

    async def get_oi_history(
        self,
        contract: str,
        as_of: date,
        lookback_days: int = 5,
    ) -> list[OIHistoryRecord]:
        from app.db.tables import OIHistoryTable

        records = await OIHistoryTable.list_by_contract(contract, limit=lookback_days)
        return [
            OIHistoryRecord(
                option_ticker=r.option_ticker,
                date=r.date,
                open_interest=r.open_interest,
                volume=getattr(r, "volume", None),
            )
            for r in records
        ]

    # ------------------------------------------------------------------
    # Market context
    # ------------------------------------------------------------------

    async def get_market_context(
        self,
        as_of: date,
    ) -> Optional[MarketContextData]:
        # Live mode: fetch SPY/VIX from Polygon
        try:
            prev = await self.polygon.get_previous_close("SPY")
            spy_close = prev.get("close", 0.0) if prev else 0.0
            return MarketContextData(
                date=as_of.isoformat(),
                spy_close=spy_close,
            )
        except Exception as e:
            logger.warning(f"Failed to get market context: {e}")
            return None

    # ------------------------------------------------------------------
    # Catalyst
    # ------------------------------------------------------------------

    async def get_days_to_earnings(
        self,
        ticker: str,
        as_of: date,
    ) -> Optional[int]:
        if self.earnings_cache:
            return await self.earnings_cache.get_days_to_earnings(ticker)
        if self.catalyst_service:
            return await self.catalyst_service.get_days_to_earnings(ticker)
        return None

    async def get_recent_sec_filing(
        self,
        ticker: str,
        as_of: date,
    ) -> bool:
        if self.catalyst_service:
            return await self.catalyst_service.get_recent_sec_filing(ticker)
        return False
