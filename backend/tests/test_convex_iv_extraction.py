"""Tests for the Convex Mode IV extraction (Phase 0.5 backfill).

Covers the per-ticker selectors that turn a list of contract rows into the
multi-tenor + 25-delta-skew IV metrics consumed by Stage 3.
"""

from __future__ import annotations

import pytest

from app.convex import (
    CompletenessReport,
    ContractRow,
    IVMetrics,
    extract_iv_metrics,
    summarise_completeness,
)
from app.convex.iv_extraction import (
    _mid_iv,
    _select_atm_iv_at_tenor,
    _select_legacy_atm_iv,
    _select_skew_leg,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    ticker: str = "NVDA",
    expiry: str = "2026-05-26",
    delta: float = 0.50,
    bid_iv: float = 0.30,
    ask_iv: float = 0.32,
) -> ContractRow:
    return ContractRow(
        ticker=ticker,
        expiry_date=expiry,
        delta=delta,
        bid_iv=bid_iv,
        ask_iv=ask_iv,
    )


# ---------------------------------------------------------------------------
# _mid_iv
# ---------------------------------------------------------------------------


class TestMidIv:

    def test_averages_when_both_present(self):
        assert _mid_iv(0.30, 0.32) == pytest.approx(0.31)

    def test_returns_single_when_other_missing(self):
        assert _mid_iv(0.30, None) == 0.30
        assert _mid_iv(None, 0.32) == 0.32

    def test_zero_treated_as_missing(self):
        assert _mid_iv(0.0, 0.32) == 0.32

    def test_returns_none_when_both_missing(self):
        assert _mid_iv(None, None) is None
        assert _mid_iv(0.0, 0.0) is None


# ---------------------------------------------------------------------------
# _select_legacy_atm_iv (back-compat with existing parquet)
# ---------------------------------------------------------------------------


class TestLegacyAtmIv:

    def test_filters_by_delta_band(self):
        rows = [
            _row(delta=0.10, bid_iv=0.50, ask_iv=0.50),  # too far OTM
            _row(delta=0.50, bid_iv=0.30, ask_iv=0.30),  # qualifies
            _row(delta=0.90, bid_iv=0.40, ask_iv=0.40),  # too deep ITM
        ]
        result = _select_legacy_atm_iv(rows, "2026-04-26")
        assert result == pytest.approx(0.30)

    def test_filters_by_dte_band(self):
        rows = [
            _row(expiry="2026-05-01"),  # 5 DTE — too short
            _row(expiry="2026-05-26"),  # 30 DTE — qualifies
            _row(expiry="2026-09-26"),  # 153 DTE — too long
        ]
        result = _select_legacy_atm_iv(rows, "2026-04-26")
        # Only the 30-DTE row contributes.
        assert result == pytest.approx(0.31)

    def test_returns_none_when_no_qualifying_rows(self):
        rows = [_row(delta=0.10)]
        assert _select_legacy_atm_iv(rows, "2026-04-26") is None


# ---------------------------------------------------------------------------
# _select_atm_iv_at_tenor (multi-tenor)
# ---------------------------------------------------------------------------


class TestAtmIvAtTenor:

    def test_picks_closest_to_target_dte(self):
        rows = [
            _row(expiry="2026-05-01", bid_iv=0.40, ask_iv=0.40),  # 5 DTE
            _row(expiry="2026-05-26", bid_iv=0.30, ask_iv=0.30),  # 30 DTE — closest
            _row(expiry="2026-06-25", bid_iv=0.20, ask_iv=0.20),  # 60 DTE
        ]
        result = _select_atm_iv_at_tenor(rows, "2026-04-26", target_dte=30, dte_tolerance=8)
        assert result == pytest.approx(0.30)

    def test_returns_none_when_outside_tolerance(self):
        rows = [_row(expiry="2026-09-26", bid_iv=0.20, ask_iv=0.20)]  # 153 DTE
        assert _select_atm_iv_at_tenor(rows, "2026-04-26", target_dte=30, dte_tolerance=8) is None

    def test_60d_picks_closest_to_60(self):
        rows = [
            _row(expiry="2026-05-26", bid_iv=0.30, ask_iv=0.30),  # 30 DTE
            _row(expiry="2026-06-25", bid_iv=0.25, ask_iv=0.25),  # 60 DTE
            _row(expiry="2026-07-25", bid_iv=0.22, ask_iv=0.22),  # 90 DTE
        ]
        result = _select_atm_iv_at_tenor(rows, "2026-04-26", target_dte=60, dte_tolerance=12)
        assert result == pytest.approx(0.25)

    def test_averages_call_and_put_at_same_tenor(self):
        # Both call and put at 30 DTE — average their IVs.
        rows = [
            _row(delta=0.50, expiry="2026-05-26", bid_iv=0.30, ask_iv=0.30),
            _row(delta=-0.50, expiry="2026-05-26", bid_iv=0.34, ask_iv=0.34),
        ]
        result = _select_atm_iv_at_tenor(rows, "2026-04-26", target_dte=30, dte_tolerance=8)
        assert result == pytest.approx(0.32)


# ---------------------------------------------------------------------------
# _select_skew_leg (25-delta legs)
# ---------------------------------------------------------------------------


class TestSkewLeg:

    def test_picks_25d_call_in_band(self):
        rows = [
            _row(delta=0.10, bid_iv=0.40, ask_iv=0.40),  # 10Δ — too far OTM
            _row(delta=0.25, bid_iv=0.32, ask_iv=0.32),  # 25Δ — qualifies
            _row(delta=0.50, bid_iv=0.30, ask_iv=0.30),  # 50Δ — too in
        ]
        result = _select_skew_leg(rows, "2026-04-26", delta_low=0.20, delta_high=0.30)
        assert result == pytest.approx(0.32)

    def test_picks_25d_put_in_band(self):
        rows = [
            _row(delta=-0.10, bid_iv=0.40, ask_iv=0.40),
            _row(delta=-0.25, bid_iv=0.36, ask_iv=0.36),  # 25Δ put
            _row(delta=-0.50, bid_iv=0.30, ask_iv=0.30),
        ]
        result = _select_skew_leg(rows, "2026-04-26", delta_low=-0.30, delta_high=-0.20)
        assert result == pytest.approx(0.36)

    def test_prefers_closer_delta_when_dte_ties(self):
        rows = [
            _row(delta=0.21, bid_iv=0.40, ask_iv=0.40),
            _row(delta=0.25, bid_iv=0.32, ask_iv=0.32),  # closest to 25Δ centre
            _row(delta=0.29, bid_iv=0.36, ask_iv=0.36),
        ]
        result = _select_skew_leg(rows, "2026-04-26", delta_low=0.20, delta_high=0.30)
        assert result == pytest.approx(0.32)

    def test_returns_none_when_no_match(self):
        rows = [_row(delta=0.50)]
        assert _select_skew_leg(rows, "2026-04-26", delta_low=0.20, delta_high=0.30) is None


# ---------------------------------------------------------------------------
# extract_iv_metrics — top-level integrator
# ---------------------------------------------------------------------------


class TestExtractIvMetrics:

    def test_full_extraction_per_ticker(self):
        rows = [
            # NVDA 30-DTE call ATM
            _row(ticker="NVDA", expiry="2026-05-26", delta=0.50, bid_iv=0.30, ask_iv=0.30),
            # NVDA 60-DTE call ATM
            _row(ticker="NVDA", expiry="2026-06-25", delta=0.50, bid_iv=0.25, ask_iv=0.25),
            # NVDA 25Δ call
            _row(ticker="NVDA", expiry="2026-05-26", delta=0.25, bid_iv=0.32, ask_iv=0.32),
            # NVDA 25Δ put
            _row(ticker="NVDA", expiry="2026-05-26", delta=-0.25, bid_iv=0.36, ask_iv=0.36),
        ]
        result = extract_iv_metrics(rows, "2026-04-26")
        assert len(result) == 1
        m = result[0]
        assert m.ticker == "NVDA"
        assert m.date == "2026-04-26"
        assert m.atm_iv is not None
        assert m.iv_30d == pytest.approx(0.30)
        assert m.iv_60d == pytest.approx(0.25)
        assert m.iv_25d_call == pytest.approx(0.32)
        assert m.iv_25d_put == pytest.approx(0.36)

    def test_drops_tickers_with_no_metrics(self):
        rows = [_row(ticker="X", expiry="2026-09-26", delta=0.10)]  # nothing in any band
        result = extract_iv_metrics(rows, "2026-04-26")
        assert result == []

    def test_partial_coverage_preserved(self):
        # Only 30-DTE ATM data — skew columns should be None.
        rows = [
            _row(ticker="NVDA", expiry="2026-05-26", delta=0.50, bid_iv=0.30, ask_iv=0.30),
        ]
        result = extract_iv_metrics(rows, "2026-04-26")
        assert len(result) == 1
        m = result[0]
        assert m.iv_30d is not None
        assert m.iv_25d_call is None
        assert m.iv_25d_put is None
        assert m.iv_60d is None

    def test_groups_by_ticker(self):
        rows = [
            _row(ticker="NVDA", expiry="2026-05-26", delta=0.50, bid_iv=0.30, ask_iv=0.30),
            _row(ticker="TSLA", expiry="2026-05-26", delta=0.50, bid_iv=0.55, ask_iv=0.55),
        ]
        result = extract_iv_metrics(rows, "2026-04-26")
        assert len(result) == 2
        tickers = {m.ticker for m in result}
        assert tickers == {"NVDA", "TSLA"}


# ---------------------------------------------------------------------------
# Completeness report
# ---------------------------------------------------------------------------


class TestCompletenessReport:

    def test_zero_rows(self):
        report = summarise_completeness([])
        assert report.total_rows == 0
        coverage = report.coverage_pct()
        assert all(v == 0.0 for v in coverage.values())

    def test_partial_coverage_pct(self):
        metrics = [
            IVMetrics(ticker="A", date="2026-04-26", atm_iv=0.30, iv_30d=0.30),
            IVMetrics(
                ticker="B", date="2026-04-26",
                atm_iv=0.30, iv_30d=0.30, iv_60d=0.25,
                iv_25d_put=0.34, iv_25d_call=0.28,
            ),
        ]
        report = summarise_completeness(metrics)
        assert report.total_rows == 2
        coverage = report.coverage_pct()
        assert coverage["atm_iv"] == 100.0
        assert coverage["iv_30d"] == 100.0
        assert coverage["iv_60d"] == 50.0
        assert coverage["iv_25d_put"] == 50.0
        assert coverage["iv_25d_call"] == 50.0

    def test_returned_dataclass_structure(self):
        report = summarise_completeness([
            IVMetrics(ticker="A", date="2026-04-26", atm_iv=0.30),
        ])
        assert isinstance(report, CompletenessReport)
        assert report.rows_with_atm_iv == 1
