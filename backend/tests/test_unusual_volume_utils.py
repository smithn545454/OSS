"""Unit tests for Unusual Volume Scanner utility modules.

Tests the OCC parser, bucket calculators, and priority scoring functions.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

# Import utilities from Lambda package
import sys
sys.path.insert(0, "lambdas/unusual_volume")

from lambdas.unusual_volume.utils.occ_parser import (
    OCCSymbol,
    build_occ_symbol,
    parse_occ_symbol,
)
from lambdas.unusual_volume.utils.buckets import (
    build_bucket_key,
    get_dte_bucket,
    get_moneyness_bucket,
    is_scannable_dte,
)
from lambdas.unusual_volume.utils.scoring import (
    calculate_priority_score,
    get_trigger_reasons,
)


class TestOCCParser:
    """Tests for OCC symbol parsing."""

    def test_parse_basic_call(self):
        """Parse a basic call option symbol."""
        result = parse_occ_symbol("AAPL250117C00200000")
        assert result is not None
        assert result.underlying == "AAPL"
        assert result.expiration == date(2025, 1, 17)
        assert result.option_type == "CALL"
        assert result.strike == 200.0
        assert result.raw_symbol == "AAPL250117C00200000"

    def test_parse_basic_put(self):
        """Parse a basic put option symbol."""
        result = parse_occ_symbol("SPY240315P00450500")
        assert result is not None
        assert result.underlying == "SPY"
        assert result.expiration == date(2024, 3, 15)
        assert result.option_type == "PUT"
        assert result.strike == 450.50

    def test_parse_with_polygon_prefix(self):
        """Parse symbol with O: prefix (Polygon format)."""
        result = parse_occ_symbol("O:TSLA250620C01200000")
        assert result is not None
        assert result.underlying == "TSLA"
        assert result.strike == 1200.0
        assert result.raw_symbol == "TSLA250620C01200000"  # Prefix stripped

    def test_parse_short_underlying(self):
        """Parse symbol with short underlying (1 character)."""
        result = parse_occ_symbol("C250620C00050000")
        assert result is not None
        assert result.underlying == "C"
        assert result.strike == 50.0

    def test_parse_invalid_symbol(self):
        """Invalid symbol returns None."""
        assert parse_occ_symbol("INVALID") is None
        assert parse_occ_symbol("") is None
        assert parse_occ_symbol("AAPL") is None
        assert parse_occ_symbol("AAPL250117X00200000") is None  # Invalid type

    def test_build_occ_symbol(self):
        """Build OCC symbol from components."""
        symbol = build_occ_symbol(
            underlying="AAPL",
            expiration=date(2025, 1, 17),
            option_type="CALL",
            strike=200.0,
        )
        assert symbol == "AAPL250117C00200000"

    def test_build_put_symbol(self):
        """Build put option symbol."""
        symbol = build_occ_symbol(
            underlying="SPY",
            expiration=date(2024, 3, 15),
            option_type="PUT",
            strike=450.50,
        )
        assert symbol == "SPY240315P00450500"

    def test_roundtrip(self):
        """Parse and rebuild produces same symbol."""
        original = "NVDA260821C00800000"
        parsed = parse_occ_symbol(original)
        assert parsed is not None
        rebuilt = build_occ_symbol(
            parsed.underlying,
            parsed.expiration,
            parsed.option_type,
            parsed.strike,
        )
        assert rebuilt == original

    def test_occ_symbol_properties(self):
        """Test OCCSymbol computed properties."""
        parsed = parse_occ_symbol("AAPL250117C00200000")
        assert parsed is not None
        assert parsed.expiration_str == "2025-01-17"
        assert parsed.is_call() is True
        assert parsed.is_put() is False


class TestBucketCalculators:
    """Tests for moneyness and DTE bucket calculations."""

    # Moneyness bucket tests
    def test_moneyness_atm_call(self):
        """ATM call (strike near price)."""
        # -5% to +5%
        assert get_moneyness_bucket(100.0, 100.0, "CALL") == "ATM"
        assert get_moneyness_bucket(102.0, 100.0, "CALL") == "ATM"
        assert get_moneyness_bucket(95.0, 100.0, "CALL") == "ATM"

    def test_moneyness_itm_call(self):
        """ITM call (strike below price)."""
        # -15% to -5%
        assert get_moneyness_bucket(90.0, 100.0, "CALL") == "ITM"

    def test_moneyness_deep_itm_call(self):
        """Deep ITM call (strike much below price)."""
        # < -15%
        assert get_moneyness_bucket(80.0, 100.0, "CALL") == "DEEP_ITM"

    def test_moneyness_otm_call(self):
        """OTM call (strike above price)."""
        # +5% to +15%
        assert get_moneyness_bucket(110.0, 100.0, "CALL") == "OTM"

    def test_moneyness_deep_otm_call(self):
        """Deep OTM call (strike much above price)."""
        # > +15%
        assert get_moneyness_bucket(120.0, 100.0, "CALL") == "DEEP_OTM"

    def test_moneyness_put_inverted(self):
        """PUT moneyness is inverted from CALL."""
        # For puts, strike > price = ITM
        assert get_moneyness_bucket(110.0, 100.0, "PUT") == "ITM"
        assert get_moneyness_bucket(120.0, 100.0, "PUT") == "DEEP_ITM"
        # For puts, strike < price = OTM
        assert get_moneyness_bucket(90.0, 100.0, "PUT") == "OTM"
        assert get_moneyness_bucket(80.0, 100.0, "PUT") == "DEEP_OTM"

    def test_moneyness_invalid_price(self):
        """Invalid underlying price defaults to ATM."""
        assert get_moneyness_bucket(100.0, 0, "CALL") == "ATM"
        assert get_moneyness_bucket(100.0, -10, "PUT") == "ATM"

    # DTE bucket tests
    def test_dte_bucket_a(self):
        """DTE bucket A (0-7 days)."""
        assert get_dte_bucket(0) == "A"
        assert get_dte_bucket(5) == "A"
        assert get_dte_bucket(7) == "A"

    def test_dte_bucket_b(self):
        """DTE bucket B (8-21 days)."""
        assert get_dte_bucket(8) == "B"
        assert get_dte_bucket(14) == "B"
        assert get_dte_bucket(21) == "B"

    def test_dte_bucket_c(self):
        """DTE bucket C (22-45 days)."""
        assert get_dte_bucket(22) == "C"
        assert get_dte_bucket(30) == "C"
        assert get_dte_bucket(45) == "C"

    def test_dte_bucket_d(self):
        """DTE bucket D (46-90 days)."""
        assert get_dte_bucket(46) == "D"
        assert get_dte_bucket(60) == "D"
        assert get_dte_bucket(90) == "D"

    def test_dte_bucket_x(self):
        """DTE bucket X (91+ days, excluded)."""
        assert get_dte_bucket(91) == "X"
        assert get_dte_bucket(180) == "X"
        assert get_dte_bucket(365) == "X"

    def test_dte_bucket_negative(self):
        """Negative DTE (expired) defaults to A."""
        assert get_dte_bucket(-1) == "A"
        assert get_dte_bucket(-30) == "A"

    def test_is_scannable_dte(self):
        """Test scannable DTE check."""
        assert is_scannable_dte(30) is True
        assert is_scannable_dte(90) is True
        assert is_scannable_dte(91) is False
        assert is_scannable_dte(180) is False

    def test_build_bucket_key(self):
        """Test bucket key construction."""
        key = build_bucket_key("AAPL", "CALL", "ATM", "C")
        assert key == "BUCKET#AAPL#CALL#ATM#C"

        key = build_bucket_key("SPY", "PUT", "OTM", "B")
        assert key == "BUCKET#SPY#PUT#OTM#B"


class TestPriorityScoring:
    """Tests for priority score calculation."""

    def test_high_volume_ratio_score(self):
        """High volume ratio increases score."""
        low_ratio = calculate_priority_score(
            volume_ratio=2.0,
            oi_change_pct=0,
            trigger_count=1,
            spread_pct=2.0,
            today_volume=1000,
            today_oi=5000,
        )

        high_ratio = calculate_priority_score(
            volume_ratio=10.0,
            oi_change_pct=0,
            trigger_count=1,
            spread_pct=2.0,
            today_volume=1000,
            today_oi=5000,
        )

        assert high_ratio > low_ratio

    def test_oi_change_increases_score(self):
        """OI change increases score."""
        no_change = calculate_priority_score(
            volume_ratio=3.0,
            oi_change_pct=0,
            trigger_count=1,
            spread_pct=2.0,
            today_volume=1000,
            today_oi=5000,
        )

        high_change = calculate_priority_score(
            volume_ratio=3.0,
            oi_change_pct=50.0,
            trigger_count=2,
            spread_pct=2.0,
            today_volume=1000,
            today_oi=5000,
        )

        assert high_change > no_change

    def test_lower_spread_increases_score(self):
        """Lower spread increases score."""
        high_spread = calculate_priority_score(
            volume_ratio=5.0,
            oi_change_pct=20.0,
            trigger_count=2,
            spread_pct=8.0,
            today_volume=1000,
            today_oi=5000,
        )

        low_spread = calculate_priority_score(
            volume_ratio=5.0,
            oi_change_pct=20.0,
            trigger_count=2,
            spread_pct=0.5,
            today_volume=1000,
            today_oi=5000,
        )

        assert low_spread > high_spread

    def test_bucket_fallback_penalty(self):
        """Bucket fallback source gets 10% penalty."""
        contract_specific = calculate_priority_score(
            volume_ratio=5.0,
            oi_change_pct=20.0,
            trigger_count=2,
            spread_pct=2.0,
            today_volume=5000,
            today_oi=10000,
            volume_source="CONTRACT_SPECIFIC",
        )

        bucket_fallback = calculate_priority_score(
            volume_ratio=5.0,
            oi_change_pct=20.0,
            trigger_count=2,
            spread_pct=2.0,
            today_volume=5000,
            today_oi=10000,
            volume_source="BUCKET_FALLBACK",
        )

        assert bucket_fallback < contract_specific
        # Should be approximately 90% of contract_specific
        assert abs(bucket_fallback - contract_specific * 0.9) < 1.0

    def test_score_range(self):
        """Score is always in 0-100 range."""
        # Minimum inputs
        low_score = calculate_priority_score(
            volume_ratio=1.0,
            oi_change_pct=0,
            trigger_count=0,
            spread_pct=50.0,
            today_volume=10,
            today_oi=100,
        )
        assert 0 <= low_score <= 100

        # Maximum inputs
        high_score = calculate_priority_score(
            volume_ratio=50.0,
            oi_change_pct=200.0,
            trigger_count=4,
            spread_pct=0.1,
            today_volume=100000,
            today_oi=500000,
        )
        assert 0 <= high_score <= 100


class TestTriggerReasons:
    """Tests for trigger reason determination."""

    def test_vol_spike_trigger(self):
        """Volume spike triggers VOL_SPIKE."""
        reasons = get_trigger_reasons(
            volume_ratio=3.0,
            oi_change_pct=0,
        )
        assert "VOL_SPIKE" in reasons
        assert "OI_INCREASE" not in reasons

    def test_oi_increase_trigger(self):
        """OI increase triggers OI_INCREASE."""
        reasons = get_trigger_reasons(
            volume_ratio=1.0,
            oi_change_pct=20.0,
        )
        assert "OI_INCREASE" in reasons
        assert "VOL_SPIKE" not in reasons

    def test_oi_decrease_trigger(self):
        """OI decrease triggers OI_DECREASE."""
        reasons = get_trigger_reasons(
            volume_ratio=1.0,
            oi_change_pct=-25.0,
        )
        assert "OI_DECREASE" in reasons
        assert "OI_INCREASE" not in reasons

    def test_combo_trigger(self):
        """Volume spike + OI change triggers combo."""
        reasons = get_trigger_reasons(
            volume_ratio=3.0,
            oi_change_pct=20.0,
        )
        assert "VOL_SPIKE" in reasons
        assert "OI_INCREASE" in reasons
        assert "VOL_OI_COMBO" in reasons

    def test_no_triggers(self):
        """No triggers when below thresholds."""
        reasons = get_trigger_reasons(
            volume_ratio=1.5,
            oi_change_pct=5.0,
        )
        assert len(reasons) == 0

    def test_custom_thresholds(self):
        """Custom thresholds work correctly."""
        reasons = get_trigger_reasons(
            volume_ratio=1.5,
            oi_change_pct=10.0,
            volume_ratio_threshold=1.5,
            oi_increase_threshold_pct=10.0,
        )
        assert "VOL_SPIKE" in reasons
        assert "OI_INCREASE" in reasons
