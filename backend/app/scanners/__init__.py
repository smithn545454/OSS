"""Scanner package for OSS opportunity discovery.

This package contains the scanners defined in Section 10 of the requirements:
- Breakout/Breakdown (Section 10.3)
- Compression → Expansion (Section 10.4)
- Cheap Options (Section 10.5)

Note: Unusual Options Volume (Section 10.2) has been moved to a serverless
      architecture in backend/lambdas/unusual_volume/. See the Lambda
      functions for the contract-level unusual volume scanner.

Plus supporting modules:
- base: Abstract scanner interface
- utils: Shared calculation utilities (ATR, RV, SMA, etc.)
- merger: Opportunity merge logic (Section 10.6)
- orchestrator: Batch execution and coordination
"""

from app.scanners.base import BaseScanner, ScanContext, ScanResult
from app.scanners.breakout import BreakoutScanner
from app.scanners.cheap_options import CheapOptionsScanner
from app.scanners.compression import CompressionScanner
from app.scanners.merger import OpportunityMerger, merge_scan_results
from app.scanners.orchestrator import ScannerOrchestrator, run_opportunity_scan
from app.scanners.utils import (
    IVProxyResult,
    calculate_atr,
    calculate_iv_proxy,
    calculate_returns,
    calculate_rv,
    calculate_sma,
)

__all__ = [
    # Base classes
    "BaseScanner",
    "ScanContext",
    "ScanResult",
    # Scanners
    "BreakoutScanner",
    "CompressionScanner",
    "CheapOptionsScanner",
    # Merger
    "OpportunityMerger",
    "merge_scan_results",
    # Orchestrator
    "ScannerOrchestrator",
    "run_opportunity_scan",
    # Utilities
    "IVProxyResult",
    "calculate_atr",
    "calculate_iv_proxy",
    "calculate_returns",
    "calculate_rv",
    "calculate_sma",
]
