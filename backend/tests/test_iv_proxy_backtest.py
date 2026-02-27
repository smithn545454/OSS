"""Tests for calculate_iv_proxy with backtest as_of_date parameter."""

from datetime import date

from app.scanners.utils import calculate_iv_proxy


def _make_contract(
    contract_type: str, strike: float, expiration: str, iv: float
) -> dict:
    """Build a minimal option contract dict for testing."""
    return {
        "details": {
            "contract_type": contract_type,
            "strike_price": strike,
            "expiration_date": expiration,
            "ticker": f"O:TEST{strike:.0f}{contract_type[0]}",
        },
        "implied_volatility": iv,
        "greeks": {},
        "day": {"close": 1.0, "volume": 100, "vwap": 1.0},
        "open_interest": 500,
        "last_quote": {"bid": 0.95, "ask": 1.05},
    }


def test_iv_proxy_returns_none_for_expired_contracts():
    """Without as_of_date, historical contracts appear expired and get filtered."""
    # Contracts from June 2024 — expired from today's perspective
    chain = [
        _make_contract("CALL", 150.0, "2024-07-19", iv=0.25),
        _make_contract("PUT", 150.0, "2024-07-19", iv=0.28),
    ]
    result = calculate_iv_proxy(chain, 150.0)
    assert result is None, "Should return None for contracts that appear expired"


def test_iv_proxy_works_with_as_of_date():
    """With as_of_date, DTE is calculated correctly for historical contracts."""
    as_of = date(2024, 6, 3)
    chain = [
        _make_contract("CALL", 150.0, "2024-07-19", iv=0.25),
        _make_contract("PUT", 150.0, "2024-07-19", iv=0.28),
    ]
    # DTE from 2024-06-03 to 2024-07-19 = 46 days (within 7-90 range)
    result = calculate_iv_proxy(chain, 150.0, as_of_date=as_of)
    assert result is not None, "Should find ATM options with correct as_of_date"
    assert abs(result.iv_proxy - 0.265) < 0.001
    assert abs(result.atm_call_iv - 0.25) < 0.001
    assert abs(result.atm_put_iv - 0.28) < 0.001


def test_iv_proxy_dte_boundaries():
    """Verify DTE filter respects min/max with as_of_date."""
    as_of = date(2024, 6, 3)

    # Contract expiring in 5 days — below min_dte of 7
    short_chain = [
        _make_contract("CALL", 150.0, "2024-06-08", iv=0.30),
        _make_contract("PUT", 150.0, "2024-06-08", iv=0.32),
    ]
    result = calculate_iv_proxy(short_chain, 150.0, as_of_date=as_of)
    assert result is None, "Contracts with DTE < 7 should be filtered out"

    # Contract expiring in 100 days — above max_dte of 90
    long_chain = [
        _make_contract("CALL", 150.0, "2024-09-11", iv=0.22),
        _make_contract("PUT", 150.0, "2024-09-11", iv=0.24),
    ]
    result = calculate_iv_proxy(long_chain, 150.0, as_of_date=as_of)
    assert result is None, "Contracts with DTE > 90 should be filtered out"


def test_iv_proxy_selects_atm_strike():
    """Verify ATM selection picks closest strike to underlying."""
    as_of = date(2024, 6, 3)
    chain = [
        _make_contract("CALL", 140.0, "2024-07-19", iv=0.30),
        _make_contract("CALL", 150.0, "2024-07-19", iv=0.25),  # ATM
        _make_contract("CALL", 160.0, "2024-07-19", iv=0.20),
        _make_contract("PUT", 140.0, "2024-07-19", iv=0.32),
        _make_contract("PUT", 150.0, "2024-07-19", iv=0.28),   # ATM
        _make_contract("PUT", 160.0, "2024-07-19", iv=0.22),
    ]
    result = calculate_iv_proxy(chain, 151.0, as_of_date=as_of)
    assert result is not None
    assert result.atm_call_strike == 150.0
    assert result.atm_put_strike == 150.0


def test_iv_proxy_backward_compatible():
    """Without as_of_date, function still works for live (non-expired) contracts."""
    # Build contracts expiring 30 days from now
    from datetime import timedelta
    today = date.today()
    exp = (today + timedelta(days=30)).isoformat()

    chain = [
        _make_contract("CALL", 200.0, exp, iv=0.20),
        _make_contract("PUT", 200.0, exp, iv=0.22),
    ]
    # Should work without as_of_date (uses today)
    result = calculate_iv_proxy(chain, 200.0)
    assert result is not None
    assert abs(result.iv_proxy - 0.21) < 0.001
