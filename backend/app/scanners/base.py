"""Base scanner interface for OSS opportunity discovery.

All scanners implement this interface, providing a consistent way to
scan tickers for opportunities with configurable thresholds.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import (
    DirectionHint,
    PolicyConfig,
    ScannerTrigger,
    ScannerType,
)
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


@dataclass
class ScanContext:
    """Context for a scanner run.

    Contains shared resources and data used across multiple ticker scans.
    """

    polygon: PolygonClient
    policy_config: PolicyConfig
    run_timestamp: str
    # Pre-fetched data that can be shared across tickers
    cached_data: dict[str, Any]

    @classmethod
    def create(
        cls,
        polygon: PolygonClient,
        policy_config: PolicyConfig,
    ) -> "ScanContext":
        """Create a new scan context."""
        return cls(
            polygon=polygon,
            policy_config=policy_config,
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            cached_data={},
        )


@dataclass
class ScanResult:
    """Result from scanning a single ticker.

    Contains the trigger (if any) and any metrics computed during the scan.
    """

    ticker: str
    triggered: bool
    trigger: Optional[ScannerTrigger] = None
    metrics: dict[str, Any] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.metrics is None:
            self.metrics = {}


class BaseScanner(ABC):
    """Abstract base class for all scanners.

    Each scanner:
    1. Implements `scanner_type` property to identify itself
    2. Implements `scan_ticker()` to evaluate a single ticker
    3. Inherits batch scanning capabilities from this base class
    """

    @property
    @abstractmethod
    def scanner_type(self) -> ScannerType:
        """Return the scanner type identifier."""
        pass

    @abstractmethod
    async def scan_ticker(
        self,
        ticker: str,
        context: ScanContext,
    ) -> ScanResult:
        """Scan a single ticker for opportunities.

        Args:
            ticker: The ticker symbol to scan
            context: Scan context with config and shared resources

        Returns:
            ScanResult with trigger info if conditions met
        """
        pass

    async def scan_batch(
        self,
        tickers: list[str],
        context: ScanContext,
        max_concurrent: int = 10,
    ) -> list[ScanResult]:
        """Scan multiple tickers with concurrency control.

        Args:
            tickers: List of ticker symbols to scan
            context: Scan context with config and shared resources
            max_concurrent: Maximum concurrent scans

        Returns:
            List of ScanResults (one per ticker)
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def scan_with_limit(ticker: str) -> ScanResult:
            async with semaphore:
                try:
                    return await self.scan_ticker(ticker, context)
                except Exception as e:
                    logger.error(f"Error scanning {ticker} with {self.scanner_type}: {e}")
                    return ScanResult(
                        ticker=ticker,
                        triggered=False,
                        error=str(e),
                    )

        tasks = [scan_with_limit(ticker) for ticker in tickers]
        results = await asyncio.gather(*tasks)
        return list(results)

    def get_triggers(self, results: list[ScanResult]) -> list[ScannerTrigger]:
        """Extract only the triggered results.

        Args:
            results: List of scan results

        Returns:
            List of ScannerTrigger objects from results that triggered
        """
        return [r.trigger for r in results if r.triggered and r.trigger is not None]

    def create_trigger(
        self,
        reason_codes: list[str],
        metrics: dict[str, float],
        direction_hint: DirectionHint = DirectionHint.NONE,
    ) -> ScannerTrigger:
        """Create a ScannerTrigger with this scanner's type.

        Args:
            reason_codes: List of reason codes for why triggered
            metrics: Dict of metric values computed during scan
            direction_hint: Suggested direction (CALL/PUT/NONE)

        Returns:
            ScannerTrigger instance
        """
        return ScannerTrigger(
            scanner_type=self.scanner_type,
            reason_codes=reason_codes,
            metrics=metrics,
            triggered_at=datetime.now(timezone.utc).isoformat(),
        )

    def log_scan_stats(self, results: list[ScanResult]) -> dict[str, int]:
        """Log and return scan statistics.

        Args:
            results: List of scan results

        Returns:
            Dict with scan statistics
        """
        total = len(results)
        triggered = sum(1 for r in results if r.triggered)
        errors = sum(1 for r in results if r.error)

        stats = {
            "total_scanned": total,
            "triggered": triggered,
            "errors": errors,
            "trigger_rate_pct": round(triggered / total * 100, 2) if total > 0 else 0,
        }

        logger.info(
            f"{self.scanner_type}: scanned {total} tickers, "
            f"{triggered} triggered ({stats['trigger_rate_pct']}%), "
            f"{errors} errors"
        )

        return stats
