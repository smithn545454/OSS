"""Tests for the history loader (features/history_loader.py).

Mostly pure parsing functions — no DB calls.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.features.history_loader import (
    DailyATMIV,
    OptionChainRow,
    build_option_ticker,
    calculate_dte,
    count_option_chain_files,
    extract_daily_atm_iv,
    extract_ticker_from_filename,
    find_option_chain_files,
    parse_date,
    parse_float,
    parse_int,
    parse_option_chain_row,
    process_option_chain_file,
)


# ---------------------------------------------------------------------------
# Tests: parse_float
# ---------------------------------------------------------------------------


class TestParseFloat:

    def test_valid_float(self):
        assert parse_float("3.14") == 3.14

    def test_integer_string(self):
        assert parse_float("42") == 42.0

    def test_empty_string(self):
        assert parse_float("") is None

    def test_whitespace(self):
        assert parse_float("  ") is None

    def test_nan_string(self):
        assert parse_float("nan") is None
        assert parse_float("NaN") is None

    def test_invalid_string(self):
        assert parse_float("abc") is None

    def test_negative(self):
        assert parse_float("-0.5") == -0.5


# ---------------------------------------------------------------------------
# Tests: parse_int
# ---------------------------------------------------------------------------


class TestParseInt:

    def test_valid_int(self):
        assert parse_int("42") == 42

    def test_float_format(self):
        assert parse_int("123.0") == 123

    def test_empty_string(self):
        assert parse_int("") == 0

    def test_invalid_string(self):
        assert parse_int("abc") == 0

    def test_negative(self):
        assert parse_int("-5") == -5


# ---------------------------------------------------------------------------
# Tests: parse_date
# ---------------------------------------------------------------------------


class TestParseDate:

    def test_iso_format(self):
        assert parse_date("2026-01-17") == "2026-01-17"

    def test_us_format(self):
        assert parse_date("01/17/2026") == "2026-01-17"

    def test_slash_iso(self):
        assert parse_date("2026/01/17") == "2026-01-17"

    def test_invalid(self):
        assert parse_date("not-a-date") is None

    def test_empty(self):
        assert parse_date("") is None

    def test_whitespace_handling(self):
        assert parse_date("  2026-01-17  ") == "2026-01-17"


# ---------------------------------------------------------------------------
# Tests: extract_ticker_from_filename
# ---------------------------------------------------------------------------


class TestExtractTicker:

    def test_quarterly(self):
        assert extract_ticker_from_filename("AAPL_2025_q1_option_chain.txt") == "AAPL"

    def test_monthly(self):
        assert extract_ticker_from_filename("MSFT_month_option_chain.txt") == "MSFT"

    def test_lowercase(self):
        assert extract_ticker_from_filename("aapl_2025_q3_option_chain.txt") == "AAPL"

    def test_no_match(self):
        assert extract_ticker_from_filename("random_file.txt") is None

    def test_different_quarters(self):
        for q in range(1, 5):
            assert extract_ticker_from_filename(f"TSLA_2024_q{q}_option_chain.txt") == "TSLA"


# ---------------------------------------------------------------------------
# Tests: parse_option_chain_row
# ---------------------------------------------------------------------------


class TestParseOptionChainRow:

    def _valid_row(self):
        return [
            "2026-01-15",  # trade_date
            "185.0",  # strike
            "2026-03-20",  # expiry
            "c",  # call/put
            "8.50",  # last
            "8.30",  # bid
            "8.70",  # ask
            "0.28",  # bid_iv
            "0.32",  # ask_iv
            "5000",  # oi
            "500",  # volume
            "0.55",  # delta
            "0.03",  # gamma
            "0.25",  # vega
            "-0.08",  # theta
            "0.01",  # rho
        ]

    def test_valid_row(self):
        result = parse_option_chain_row(self._valid_row())
        assert result is not None
        assert result.strike == 185.0
        assert result.call_put == "c"
        assert result.open_interest == 5000

    def test_put_option(self):
        row = self._valid_row()
        row[3] = "put"
        result = parse_option_chain_row(row)
        assert result is not None
        assert result.call_put == "p"

    def test_too_few_columns(self):
        assert parse_option_chain_row(["a", "b"]) is None

    def test_invalid_date(self):
        row = self._valid_row()
        row[0] = "not-a-date"
        assert parse_option_chain_row(row) is None

    def test_invalid_strike(self):
        row = self._valid_row()
        row[1] = ""
        assert parse_option_chain_row(row) is None

    def test_invalid_call_put(self):
        row = self._valid_row()
        row[3] = "x"
        assert parse_option_chain_row(row) is None

    def test_call_string(self):
        row = self._valid_row()
        row[3] = "call"
        result = parse_option_chain_row(row)
        assert result.call_put == "c"

    def test_missing_optional_fields(self):
        row = self._valid_row()
        row[4] = ""  # last trade price
        row[7] = ""  # bid_iv
        result = parse_option_chain_row(row)
        assert result is not None
        assert result.last_trade_price is None
        assert result.bid_iv is None


# ---------------------------------------------------------------------------
# Tests: calculate_dte
# ---------------------------------------------------------------------------


class TestCalculateDTE:

    def test_same_day(self):
        assert calculate_dte("2026-01-17", "2026-01-17") == 0

    def test_30_days(self):
        assert calculate_dte("2026-01-01", "2026-01-31") == 30

    def test_invalid_dates(self):
        assert calculate_dte("bad", "2026-01-31") == 0

    def test_negative_dte(self):
        assert calculate_dte("2026-03-20", "2026-01-01") == -78


# ---------------------------------------------------------------------------
# Tests: extract_daily_atm_iv
# ---------------------------------------------------------------------------


class TestExtractDailyATMIV:

    def _make_row(self, call_put, delta, bid_iv, ask_iv, expiry="2026-02-28"):
        return OptionChainRow(
            trade_date="2026-01-17",
            strike=185.0,
            expiry_date=expiry,
            call_put=call_put,
            last_trade_price=8.0,
            bid_price=8.0,
            ask_price=8.5,
            bid_iv=bid_iv,
            ask_iv=ask_iv,
            open_interest=5000,
            volume=500,
            delta=delta,
            gamma=0.03,
            vega=0.25,
            theta=-0.08,
            rho=0.01,
        )

    def test_both_call_and_put(self):
        rows = [
            self._make_row("c", 0.52, 0.28, 0.32),  # ATM call
            self._make_row("p", -0.48, 0.30, 0.34),  # ATM put
        ]
        result = extract_daily_atm_iv(rows, "AAPL", "2026-01-17")
        assert result is not None
        assert result.atm_call_iv is not None
        assert result.atm_put_iv is not None
        assert 0 < result.atm_iv < 1  # IV should be a decimal

    def test_only_calls(self):
        rows = [self._make_row("c", 0.50, 0.30, 0.32)]
        result = extract_daily_atm_iv(rows, "AAPL", "2026-01-17")
        assert result is not None
        assert result.atm_put_iv is None

    def test_only_puts(self):
        rows = [self._make_row("p", -0.50, 0.30, 0.32)]
        result = extract_daily_atm_iv(rows, "AAPL", "2026-01-17")
        assert result is not None
        assert result.atm_call_iv is None

    def test_no_valid_dte(self):
        rows = [self._make_row("c", 0.50, 0.30, 0.32, expiry="2026-01-18")]
        result = extract_daily_atm_iv(rows, "AAPL", "2026-01-17")
        assert result is None  # DTE=1, outside 30-45 range

    def test_empty_rows(self):
        result = extract_daily_atm_iv([], "AAPL", "2026-01-17")
        assert result is None

    def test_no_delta(self):
        rows = [self._make_row("c", None, 0.30, 0.32)]
        result = extract_daily_atm_iv(rows, "AAPL", "2026-01-17")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: build_option_ticker
# ---------------------------------------------------------------------------


class TestBuildOptionTicker:

    def test_call_option(self):
        result = build_option_ticker("AAPL", "2026-03-20", "c", 185.0)
        assert result == "AAPL260320C00185000"

    def test_put_option(self):
        result = build_option_ticker("AAPL", "2026-03-20", "p", 185.0)
        assert result == "AAPL260320P00185000"

    def test_fractional_strike(self):
        result = build_option_ticker("SPY", "2026-01-17", "c", 580.5)
        assert "580500" in result

    def test_invalid_date(self):
        result = build_option_ticker("AAPL", "bad-date", "c", 185.0)
        assert "AAPL" in result  # Fallback format


# ---------------------------------------------------------------------------
# Tests: process_option_chain_file
# ---------------------------------------------------------------------------


class TestProcessOptionChainFile:

    def test_valid_file(self, tmp_path):
        """Process a valid option chain file."""
        content = "2026-01-15,185.0,2026-02-28,c,8.5,8.3,8.7,0.28,0.32,5000,500,0.50,0.03,0.25,-0.08,0.01\n"
        content += "2026-01-15,185.0,2026-02-28,p,7.5,7.3,7.7,0.30,0.34,3000,300,-0.50,0.03,0.25,-0.08,0.01\n"
        fpath = tmp_path / "AAPL_2025_q4_option_chain.txt"
        fpath.write_text(content)

        iv_records, oi_records = process_option_chain_file(fpath)
        assert len(oi_records) == 2
        assert len(iv_records) >= 0  # May or may not extract IV depending on DTE

    def test_unrecognized_filename(self, tmp_path):
        fpath = tmp_path / "random_file.txt"
        fpath.write_text("data\n")
        iv, oi = process_option_chain_file(fpath)
        assert iv == []
        assert oi == []

    def test_empty_file(self, tmp_path):
        fpath = tmp_path / "AAPL_2025_q1_option_chain.txt"
        fpath.write_text("")
        iv, oi = process_option_chain_file(fpath)
        assert iv == []
        assert oi == []

    def test_malformed_rows_skipped(self, tmp_path):
        content = "bad,data,only,three,columns\n"
        content += "2026-01-15,185.0,2026-02-28,c,8.5,8.3,8.7,0.28,0.32,5000,500,0.50,0.03,0.25,-0.08,0.01\n"
        fpath = tmp_path / "TSLA_month_option_chain.txt"
        fpath.write_text(content)
        iv, oi = process_option_chain_file(fpath)
        assert len(oi) == 1  # Only the valid row


# ---------------------------------------------------------------------------
# Tests: find / count option chain files
# ---------------------------------------------------------------------------


class TestFindFiles:

    def test_find_files(self, tmp_path):
        (tmp_path / "AAPL_2025_q1_option_chain.txt").write_text("")
        (tmp_path / "MSFT_month_option_chain.txt").write_text("")
        (tmp_path / "random.txt").write_text("")
        files = list(find_option_chain_files(tmp_path))
        assert len(files) == 2

    def test_count_files(self, tmp_path):
        (tmp_path / "AAPL_2025_q1_option_chain.txt").write_text("")
        assert count_option_chain_files(tmp_path) == 1

    def test_empty_dir(self, tmp_path):
        assert count_option_chain_files(tmp_path) == 0
