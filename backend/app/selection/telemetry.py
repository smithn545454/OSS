"""Selection telemetry for tracking contract selection statistics.

Implements the telemetry schema from Section 12.4 of OSS_Complete_Requirements.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SelectedContract:
    """Details of a selected contract."""

    option_ticker: str
    strike: float
    dte: int
    delta: float
    rank_score: float
    open_interest: int
    volume: int
    spread_pct: float
    mid: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "option_ticker": self.option_ticker,
            "strike": self.strike,
            "dte": self.dte,
            "delta": round(self.delta, 4),
            "rank_score": round(self.rank_score, 2),
            "open_interest": self.open_interest,
            "volume": self.volume,
            "spread_pct": round(self.spread_pct, 2),
            "mid": round(self.mid, 4),
        }


@dataclass
class BucketStats:
    """Statistics for a single DTE bucket and side combination."""

    bucket: str
    side: str  # "CALL" or "PUT"
    contracts_in_dte_range: int = 0
    survived_delta_filter: int = 0
    survived_liquidity_filter: int = 0
    survived_moneyness_filter: int = 0
    selected_count: int = 0
    selected_contracts: list[SelectedContract] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "bucket": self.bucket,
            "side": self.side,
            "contracts_in_dte_range": self.contracts_in_dte_range,
            "survived_delta_filter": self.survived_delta_filter,
            "survived_liquidity_filter": self.survived_liquidity_filter,
            "survived_moneyness_filter": self.survived_moneyness_filter,
            "selected_count": self.selected_count,
            "selected_contracts": [c.to_dict() for c in self.selected_contracts],
        }


@dataclass
class TickerSelectionStats:
    """Selection statistics for a single ticker."""

    underlying_ticker: str
    underlying_price: float
    contracts_in_chain: int = 0
    bucket_stats: list[BucketStats] = field(default_factory=list)
    total_selected: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "underlying_ticker": self.underlying_ticker,
            "underlying_price": round(self.underlying_price, 2),
            "contracts_in_chain": self.contracts_in_chain,
            "bucket_stats": [b.to_dict() for b in self.bucket_stats],
            "total_selected": self.total_selected,
        }


class SelectionTelemetry:
    """Tracks contract selection statistics across all tickers.
    
    Per Section 12.4, tracks per ticker:
    - contracts_in_chain
    - bucket_stats per (bucket, side) combination
    - Selection funnel metrics
    """

    def __init__(self) -> None:
        """Initialize telemetry tracker."""
        self._ticker_stats: dict[str, TickerSelectionStats] = {}
        self._total_contracts_evaluated: int = 0
        self._total_contracts_selected: int = 0

    def start_ticker(
        self,
        ticker: str,
        underlying_price: float,
        contracts_in_chain: int,
    ) -> None:
        """Start tracking a ticker.
        
        Args:
            ticker: Underlying ticker symbol
            underlying_price: Current underlying price
            contracts_in_chain: Total contracts in the options chain
        """
        self._ticker_stats[ticker] = TickerSelectionStats(
            underlying_ticker=ticker,
            underlying_price=underlying_price,
            contracts_in_chain=contracts_in_chain,
        )
        self._total_contracts_evaluated += contracts_in_chain

    def record_bucket_stats(
        self,
        ticker: str,
        stats: BucketStats,
    ) -> None:
        """Record statistics for a bucket/side combination.
        
        Args:
            ticker: Underlying ticker symbol
            stats: BucketStats for this bucket/side
        """
        if ticker not in self._ticker_stats:
            return

        self._ticker_stats[ticker].bucket_stats.append(stats)
        self._ticker_stats[ticker].total_selected += stats.selected_count
        self._total_contracts_selected += stats.selected_count

    def get_ticker_stats(self, ticker: str) -> Optional[TickerSelectionStats]:
        """Get statistics for a specific ticker.
        
        Args:
            ticker: Underlying ticker symbol
            
        Returns:
            TickerSelectionStats if found, None otherwise
        """
        return self._ticker_stats.get(ticker)

    def get_all_stats(self) -> dict[str, TickerSelectionStats]:
        """Get statistics for all tickers.
        
        Returns:
            Dict mapping ticker to TickerSelectionStats
        """
        return self._ticker_stats.copy()

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics across all tickers.
        
        Returns:
            Summary dictionary
        """
        # Aggregate by bucket and side
        bucket_totals: dict[str, dict[str, int]] = {}
        
        for ticker_stats in self._ticker_stats.values():
            for bucket_stat in ticker_stats.bucket_stats:
                key = f"{bucket_stat.bucket}_{bucket_stat.side}"
                if key not in bucket_totals:
                    bucket_totals[key] = {
                        "contracts_evaluated": 0,
                        "contracts_selected": 0,
                    }
                bucket_totals[key]["contracts_evaluated"] += bucket_stat.contracts_in_dte_range
                bucket_totals[key]["contracts_selected"] += bucket_stat.selected_count

        return {
            "tickers_processed": len(self._ticker_stats),
            "total_contracts_in_chains": self._total_contracts_evaluated,
            "total_contracts_selected": self._total_contracts_selected,
            "bucket_totals": bucket_totals,
            "selection_rate_pct": (
                round(self._total_contracts_selected / max(self._total_contracts_evaluated, 1) * 100, 2)
            ),
        }

    def to_stage_metadata(self) -> dict[str, Any]:
        """Convert to metadata for stage event recording.
        
        Returns:
            Dictionary suitable for StageEvent metadata
        """
        return {
            "summary": self.get_summary(),
            "ticker_stats": {
                ticker: stats.to_dict()
                for ticker, stats in self._ticker_stats.items()
            },
        }
