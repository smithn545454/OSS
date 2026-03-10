"""Tests for Black-Scholes greeks fallback module."""

import math

import pytest

from app.selection.greeks import (
    bs_price,
    compute_greeks,
    implied_volatility,
    norm_cdf,
    norm_pdf,
)


class TestNormCdf:
    def test_zero(self):
        assert abs(norm_cdf(0) - 0.5) < 1e-10

    def test_large_positive(self):
        assert norm_cdf(5.0) > 0.999

    def test_large_negative(self):
        assert norm_cdf(-5.0) < 0.001

    def test_symmetry(self):
        assert abs(norm_cdf(1.0) + norm_cdf(-1.0) - 1.0) < 1e-10


class TestNormPdf:
    def test_zero(self):
        expected = 1.0 / math.sqrt(2 * math.pi)
        assert abs(norm_pdf(0) - expected) < 1e-10

    def test_positive(self):
        assert norm_pdf(1.0) < norm_pdf(0)


class TestBsPrice:
    def test_call_atm(self):
        # ATM call: S=100, K=100, T=0.25, r=0.045, sigma=0.30
        price = bs_price(100, 100, 0.25, 0.045, 0.30, "call")
        assert 5.0 < price < 10.0  # Reasonable ATM call price

    def test_put_atm(self):
        price = bs_price(100, 100, 0.25, 0.045, 0.30, "put")
        assert 3.0 < price < 8.0

    def test_deep_itm_call(self):
        price = bs_price(150, 100, 0.25, 0.045, 0.30, "call")
        assert price > 49.0  # At least intrinsic value

    def test_deep_otm_call(self):
        price = bs_price(100, 200, 0.25, 0.045, 0.30, "call")
        assert price < 1.0  # Very cheap


class TestImpliedVolatility:
    def test_roundtrip(self):
        """Compute price from known IV, solve back to same IV."""
        true_iv = 0.35
        price = bs_price(100, 100, 0.25, 0.045, true_iv, "call")
        solved_iv = implied_volatility(price, 100, 100, 0.25, 0.045, "call")
        assert solved_iv is not None
        assert abs(solved_iv - true_iv) < 0.001

    def test_roundtrip_put(self):
        true_iv = 0.40
        price = bs_price(100, 105, 0.5, 0.045, true_iv, "put")
        solved_iv = implied_volatility(price, 100, 105, 0.5, 0.045, "put")
        assert solved_iv is not None
        assert abs(solved_iv - true_iv) < 0.001

    def test_bad_inputs_returns_none(self):
        assert implied_volatility(0, 100, 100, 0.25) is None
        assert implied_volatility(5, 0, 100, 0.25) is None
        assert implied_volatility(5, 100, 100, 0) is None


class TestComputeGreeks:
    def test_call_atm(self):
        result = compute_greeks(
            S=100, K=100, T=30 / 365.0, option_type="call", market_price=4.0
        )
        assert result is not None
        assert 0.4 < result["delta"] < 0.7  # ATM call delta
        assert result["gamma"] > 0
        assert result["theta"] < 0  # Theta is negative
        assert result["vega"] > 0
        assert 0.05 < result["iv"] < 2.0

    def test_put_atm(self):
        result = compute_greeks(
            S=100, K=100, T=30 / 365.0, option_type="put", market_price=3.5
        )
        assert result is not None
        assert -0.7 < result["delta"] < -0.3  # ATM put delta

    def test_call_otm(self):
        result = compute_greeks(
            S=100, K=110, T=30 / 365.0, option_type="call", market_price=1.5
        )
        assert result is not None
        assert 0.05 < result["delta"] < 0.45  # OTM call delta

    def test_bad_inputs_returns_none(self):
        assert compute_greeks(0, 100, 0.1, "call", 5) is None
        assert compute_greeks(100, 0, 0.1, "call", 5) is None
        assert compute_greeks(100, 100, 0, "call", 5) is None
        assert compute_greeks(100, 100, 0.1, "call", 0) is None

    def test_realistic_aapl_option(self):
        """Test with realistic AAPL-like values from Polygon data."""
        # AAPL ~$262, $260 call, 38 DTE, mid ~$10.68
        result = compute_greeks(
            S=262, K=260, T=38 / 365.0, option_type="call", market_price=10.68
        )
        assert result is not None
        assert 0.45 < result["delta"] < 0.75
        assert 0.10 < result["iv"] < 0.60
