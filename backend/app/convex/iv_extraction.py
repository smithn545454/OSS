"""Pure-function IV metrics extraction for the Convex Mode IV backfill.

Reads per-contract options data (already loaded into Python lists/dicts) and
emits per-ticker per-date IV metrics:

    - ``atm_iv``: legacy field — average IV of all ATM-ish contracts (DTE
      20-60, |delta| 0.35-0.65). Preserved verbatim from the previous
      derive script for back-compat with existing parquet readers.
    - ``iv_30d``: front-month IV — average bid/ask IV across calls and
      puts closest to 30 DTE with |delta| in [0.45, 0.55].
    - ``iv_60d``: 60-day tenor IV — same selector but DTE closest to 60.
    - ``iv_25d_put``: 25-delta put IV — DTE closest to 30, delta in
      [-0.30, -0.20].
    - ``iv_25d_call``: 25-delta call IV — DTE closest to 30, delta in
      [0.20, 0.30].

The extractor is intentionally lenient about missing data: each metric is
computed independently. A ticker with no 25Δ contracts will still get an
``atm_iv`` and ``iv_30d`` record with the skew fields set to ``None``.
This keeps the downstream Stage 3 gate "fail open on missing skew" rather
than tossing the entire ticker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContractRow:
    """Minimal per-contract input for IV extraction.

    Fields mirror the columns produced by Polygon's options-chains parquet
    used elsewhere in OSS (see backend/scripts/derive_iv_history.py for
    the legacy reader).
    """

    ticker: str
    expiry_date: str  # YYYY-MM-DD
    delta: Optional[float]
    bid_iv: Optional[float]
    ask_iv: Optional[float]


@dataclass
class IVMetrics:
    """Per-ticker IV metrics extracted for a single trade date."""

    ticker: str
    date: str
    atm_iv: Optional[float] = None
    iv_30d: Optional[float] = None
    iv_60d: Optional[float] = None
    iv_25d_put: Optional[float] = None
    iv_25d_call: Optional[float] = None

    def has_any_metric(self) -> bool:
        return any(
            v is not None
            for v in (
                self.atm_iv,
                self.iv_30d,
                self.iv_60d,
                self.iv_25d_put,
                self.iv_25d_call,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mid_iv(bid_iv: Optional[float], ask_iv: Optional[float]) -> Optional[float]:
    """Average bid_iv and ask_iv, ignoring zero/missing values.

    Returns None when neither side is positive.
    """
    bid = bid_iv if (bid_iv is not None and bid_iv > 0) else None
    ask = ask_iv if (ask_iv is not None and ask_iv > 0) else None
    if bid is None and ask is None:
        return None
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid or ask


def _dte(trade_date: str, expiry: str) -> Optional[int]:
    try:
        td = _date.fromisoformat(trade_date)
        ed = _date.fromisoformat(expiry)
    except (TypeError, ValueError):
        return None
    return (ed - td).days


# ---------------------------------------------------------------------------
# Per-ticker selectors
# ---------------------------------------------------------------------------


def _select_legacy_atm_iv(
    rows: Iterable[ContractRow],
    trade_date: str,
    delta_min: float = 0.35,
    delta_max: float = 0.65,
    dte_min: int = 20,
    dte_max: int = 60,
) -> Optional[float]:
    """Average mid IV of contracts with |delta| in band and DTE in band.

    Mirrors the legacy ``compute_atm_iv_for_date`` selector so the new
    extractor produces a back-compatible ``atm_iv`` column.
    """
    ivs: list[float] = []
    for r in rows:
        if r.delta is None:
            continue
        ad = abs(r.delta)
        if ad < delta_min or ad > delta_max:
            continue
        d = _dte(trade_date, r.expiry_date)
        if d is None or d < dte_min or d > dte_max:
            continue
        mid = _mid_iv(r.bid_iv, r.ask_iv)
        if mid is not None:
            ivs.append(mid)
    if not ivs:
        return None
    return round(sum(ivs) / len(ivs), 6)


def _select_atm_iv_at_tenor(
    rows: Iterable[ContractRow],
    trade_date: str,
    target_dte: int,
    dte_tolerance: int,
    delta_min: float = 0.45,
    delta_max: float = 0.55,
) -> Optional[float]:
    """Average mid IV of ATM contracts (calls and puts) near a target DTE.

    Looks for contracts with |delta| in [delta_min, delta_max] AND DTE
    within ``dte_tolerance`` of ``target_dte``. Picks the contract with
    the smallest DTE delta, ties broken by closest to 0.50 |delta|.
    Averages across the call and put closest to the target.
    """
    candidates: list[tuple[int, float, float]] = []  # (dte_delta, delta_distance, mid_iv)
    for r in rows:
        if r.delta is None:
            continue
        ad = abs(r.delta)
        if ad < delta_min or ad > delta_max:
            continue
        d = _dte(trade_date, r.expiry_date)
        if d is None:
            continue
        dte_delta = abs(d - target_dte)
        if dte_delta > dte_tolerance:
            continue
        mid = _mid_iv(r.bid_iv, r.ask_iv)
        if mid is None:
            continue
        candidates.append((dte_delta, abs(ad - 0.50), mid))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    # Use the best DTE-bucket candidates (could be one call + one put). Take
    # the top two and average; if only one, just use it.
    best = candidates[: min(2, len(candidates))]
    avg = sum(c[2] for c in best) / len(best)
    return round(avg, 6)


def _select_skew_leg(
    rows: Iterable[ContractRow],
    trade_date: str,
    delta_low: float,
    delta_high: float,
    target_dte: int = 30,
    dte_tolerance: int = 10,
) -> Optional[float]:
    """IV of the contract with delta in [low, high] closest to target DTE.

    Used for the 25Δ skew legs. ``delta_low`` and ``delta_high`` are
    signed (e.g., ``-0.30, -0.20`` for 25Δ puts; ``0.20, 0.30`` for calls).
    """
    candidates: list[tuple[int, float, float]] = []
    for r in rows:
        if r.delta is None:
            continue
        if not (delta_low <= r.delta <= delta_high):
            continue
        d = _dte(trade_date, r.expiry_date)
        if d is None:
            continue
        dte_delta = abs(d - target_dte)
        if dte_delta > dte_tolerance:
            continue
        mid = _mid_iv(r.bid_iv, r.ask_iv)
        if mid is None:
            continue
        # Prefer contracts whose delta is closest to the band's centre
        # (so 25Δ exactly is preferred over 21Δ or 29Δ).
        target_delta = (delta_low + delta_high) / 2
        delta_distance = abs(r.delta - target_delta)
        candidates.append((dte_delta, delta_distance, mid))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    return round(candidates[0][2], 6)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_iv_metrics(
    rows: Iterable[ContractRow],
    trade_date: str,
) -> list[IVMetrics]:
    """Group rows by ticker and emit per-ticker IV metrics for ``trade_date``.

    Tickers with no extractable metric are dropped.
    """
    by_ticker: dict[str, list[ContractRow]] = {}
    for row in rows:
        by_ticker.setdefault(row.ticker, []).append(row)

    out: list[IVMetrics] = []
    for ticker, ticker_rows in by_ticker.items():
        atm_iv = _select_legacy_atm_iv(ticker_rows, trade_date)
        iv_30d = _select_atm_iv_at_tenor(ticker_rows, trade_date, target_dte=30, dte_tolerance=8)
        iv_60d = _select_atm_iv_at_tenor(ticker_rows, trade_date, target_dte=60, dte_tolerance=12)
        iv_25d_put = _select_skew_leg(
            ticker_rows, trade_date, delta_low=-0.30, delta_high=-0.20
        )
        iv_25d_call = _select_skew_leg(
            ticker_rows, trade_date, delta_low=0.20, delta_high=0.30
        )

        metrics = IVMetrics(
            ticker=ticker,
            date=trade_date,
            atm_iv=atm_iv,
            iv_30d=iv_30d,
            iv_60d=iv_60d,
            iv_25d_put=iv_25d_put,
            iv_25d_call=iv_25d_call,
        )
        if metrics.has_any_metric():
            out.append(metrics)
    return out


# ---------------------------------------------------------------------------
# Data-completeness audit
# ---------------------------------------------------------------------------


@dataclass
class CompletenessReport:
    """Summary of which tickers / dates have which IV metrics populated.

    Used by the data-completeness audit step at the end of the backfill.
    Stage 3 fails *open* on missing skew (treats it as "no signal" rather
    than rejecting the candidate), so partial coverage is acceptable —
    but a coverage report makes calibration tuning easier.
    """

    total_rows: int
    rows_with_atm_iv: int
    rows_with_iv_30d: int
    rows_with_iv_60d: int
    rows_with_iv_25d_put: int
    rows_with_iv_25d_call: int

    def coverage_pct(self) -> dict[str, float]:
        if self.total_rows == 0:
            return {k: 0.0 for k in (
                "atm_iv", "iv_30d", "iv_60d", "iv_25d_put", "iv_25d_call"
            )}
        return {
            "atm_iv": 100.0 * self.rows_with_atm_iv / self.total_rows,
            "iv_30d": 100.0 * self.rows_with_iv_30d / self.total_rows,
            "iv_60d": 100.0 * self.rows_with_iv_60d / self.total_rows,
            "iv_25d_put": 100.0 * self.rows_with_iv_25d_put / self.total_rows,
            "iv_25d_call": 100.0 * self.rows_with_iv_25d_call / self.total_rows,
        }


def summarise_completeness(metrics: Iterable[IVMetrics]) -> CompletenessReport:
    """Count populated fields across a stream of IVMetrics."""
    total = 0
    atm = iv30 = iv60 = put25 = call25 = 0
    for m in metrics:
        total += 1
        if m.atm_iv is not None:
            atm += 1
        if m.iv_30d is not None:
            iv30 += 1
        if m.iv_60d is not None:
            iv60 += 1
        if m.iv_25d_put is not None:
            put25 += 1
        if m.iv_25d_call is not None:
            call25 += 1
    return CompletenessReport(
        total_rows=total,
        rows_with_atm_iv=atm,
        rows_with_iv_30d=iv30,
        rows_with_iv_60d=iv60,
        rows_with_iv_25d_put=put25,
        rows_with_iv_25d_call=call25,
    )
