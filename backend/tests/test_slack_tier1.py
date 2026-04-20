"""Tests for the v5 dual-conviction Slack alert service.

Covers:
- Hard checks (enabled / webhooks / verdict / daily cap / cooldown)
- Tier 1 (Sharpshooter) fast-path bypass
- HR vs P conviction gating
- require_hr_archetype (sharpshooter-only mode)
- min_archetype_fit, min_regime_alignment
- infer_verdict_driver
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault("DYNAMODB_TABLE_PREFIX", "oss-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("POLYGON_API_KEY", "fake-test-key")

from app.services.slack import (  # noqa: E402
    SlackAlertService,
    V5_HR_FLOOR,
    V5_P_FLOOR,
    infer_verdict_driver,
)


def _make_config(**overrides):
    """Build a v5 test alert config with sensible defaults."""
    base = {
        "enabled": True,
        "hr_conviction_min": 10.0,
        "p_conviction_min": 70.0,
        "require_hr_archetype": False,
        "min_archetype_fit": 60.0,
        "min_regime_alignment": 0.0,
        "max_premium": None,
        "tier_1_bypass": True,
        "cooldown_minutes": 30,
        "daily_cap": 10,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "08:00",
        "webhook_channels": [
            {"channel_name": "#test", "url": "https://hooks.slack.com/test"}
        ],
        "verdicts": ["APPROVE"],
    }
    base.update(overrides)
    return base


def _make_service(config=None):
    svc = SlackAlertService()
    svc.configure(config or _make_config())
    return svc


# Sensible baseline v5 decision — clears HR track cleanly (above both
# user's default 10.0 min AND system 7.0 APPROVE floor).
_HR_PASS_KW = dict(
    hr_conviction=12.0,
    p_conviction=40.0,
    hr_archetype_matched="HR_MOMENTUM",
    hr_archetype_fit=85.0,
    p_archetype_fit=30.0,
    regime_alignment=1.1,
)

# Sensible baseline v5 decision — clears P track cleanly.
_P_PASS_KW = dict(
    hr_conviction=3.0,
    p_conviction=80.0,
    hr_archetype_matched=None,
    hr_archetype_fit=0.0,
    p_archetype_fit=88.0,
    regime_alignment=1.05,
)


# ============================================================================
# Tier 1 fast path
# ============================================================================


class TestTier1FastPath:
    """TIER_1 (Sharpshooter) bypasses soft filters."""

    @pytest.mark.asyncio
    async def test_tier1_bypasses_conviction_thresholds(self):
        svc = _make_service(_make_config(hr_conviction_min=19, p_conviction_min=99))
        # Neither track would normally clear these absurd thresholds
        result, reason = await svc.should_alert(
            hr_conviction=1.0, p_conviction=1.0,
            hr_archetype_matched=None, hr_archetype_fit=0.0,
            p_archetype_fit=0.0, regime_alignment=1.0,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is True
        assert reason == "tier_1"

    @pytest.mark.asyncio
    async def test_tier1_bypasses_quiet_hours(self):
        svc = _make_service()
        with patch.object(svc, "_is_within_quiet_hours", return_value=True):
            result, reason = await svc.should_alert(
                hr_conviction=15.0, p_conviction=60.0,
                hr_archetype_matched="HR_X", hr_archetype_fit=90.0,
                p_archetype_fit=0.0, regime_alignment=1.0,
                contract_id="AAPL-C-200", quality_tier="TIER_1",
            )
        assert result is True
        assert reason == "tier_1"

    @pytest.mark.asyncio
    async def test_tier1_bypasses_archetype_requirement(self):
        """Sharpshooter-only mode is ignored for TIER_1 (it's already a sharpshooter)."""
        svc = _make_service(_make_config(require_hr_archetype=True))
        result, reason = await svc.should_alert(
            hr_conviction=15.0, p_conviction=60.0,
            hr_archetype_matched=None,  # no HR archetype — but TIER_1
            hr_archetype_fit=0.0, p_archetype_fit=80.0,
            regime_alignment=1.0,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is True
        assert reason == "tier_1"

    @pytest.mark.asyncio
    async def test_tier1_respects_enabled_false(self):
        svc = _make_service(_make_config(enabled=False))
        result, reason = await svc.should_alert(
            **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert reason == "Alerts disabled"

    @pytest.mark.asyncio
    async def test_tier1_respects_no_webhooks(self):
        svc = _make_service(_make_config(webhook_channels=[]))
        svc._legacy_webhook_url = None
        result, reason = await svc.should_alert(
            **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert reason == "No webhook URLs configured"

    @pytest.mark.asyncio
    async def test_tier1_respects_daily_cap(self):
        svc = _make_service(_make_config(daily_cap=5))
        svc._daily_count = 5
        svc._last_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result, reason = await svc.should_alert(
            **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert "Daily alert cap" in reason

    @pytest.mark.asyncio
    async def test_tier1_respects_cooldown(self):
        svc = _make_service(_make_config(cooldown_minutes=30))
        svc._alert_timestamps["AAPL-C-200"] = datetime.now(timezone.utc)
        result, reason = await svc.should_alert(
            **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert "cooldown" in reason

    @pytest.mark.asyncio
    async def test_tier1_bypass_disabled_via_config(self):
        """tier_1_bypass=False → TIER_1 gets the normal soft-filter treatment."""
        svc = _make_service(_make_config(
            tier_1_bypass=False, hr_conviction_min=15, p_conviction_min=90,
        ))
        result, reason = await svc.should_alert(
            hr_conviction=5.0, p_conviction=20.0,
            hr_archetype_matched=None, hr_archetype_fit=0.0,
            p_archetype_fit=0.0, regime_alignment=1.0,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert "Neither track passed" in reason


# ============================================================================
# HR / P conviction gating (non-Tier-1)
# ============================================================================


class TestConvictionGating:
    """Soft-filter behavior under v5 dual-conviction."""

    @pytest.mark.asyncio
    async def test_hr_track_passes_alone(self):
        svc = _make_service(_make_config(hr_conviction_min=10, p_conviction_min=99))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is True
        assert reason == "HR"

    @pytest.mark.asyncio
    async def test_p_track_passes_alone(self):
        svc = _make_service(_make_config(hr_conviction_min=19, p_conviction_min=70))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                **_P_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is True
        assert reason == "P"

    @pytest.mark.asyncio
    async def test_both_tracks_fail_when_below_mins(self):
        svc = _make_service(_make_config(hr_conviction_min=15, p_conviction_min=85))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                hr_conviction=8.0, p_conviction=55.0,
                hr_archetype_matched="HR_X", hr_archetype_fit=90.0,
                p_archetype_fit=90.0, regime_alignment=1.0,
                contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is False
        assert "Neither track passed" in reason

    @pytest.mark.asyncio
    async def test_conviction_passes_but_fit_too_low(self):
        """High HR conviction but weak archetype fit is rejected."""
        svc = _make_service(_make_config(
            hr_conviction_min=10, min_archetype_fit=75,
        ))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                hr_conviction=14.0, p_conviction=20.0,
                hr_archetype_matched="HR_X", hr_archetype_fit=50.0,  # < 75
                p_archetype_fit=30.0, regime_alignment=1.0,
                contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is False
        assert "Neither track passed" in reason

    @pytest.mark.asyncio
    async def test_require_hr_archetype_blocks_p_only(self):
        """Sharpshooter-only mode filters out pure-P trades."""
        svc = _make_service(_make_config(require_hr_archetype=True))
        result, reason = await svc.should_alert(
            **_P_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_2",
        )
        assert result is False
        assert "sharpshooter-only" in reason.lower()

    @pytest.mark.asyncio
    async def test_require_hr_archetype_allows_hr_trade(self):
        svc = _make_service(_make_config(require_hr_archetype=True))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is True
        assert reason == "HR"

    @pytest.mark.asyncio
    async def test_regime_headwind_filter(self):
        svc = _make_service(_make_config(min_regime_alignment=1.0))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                hr_conviction=12.0, p_conviction=80.0,
                hr_archetype_matched="HR_X", hr_archetype_fit=90.0,
                p_archetype_fit=90.0, regime_alignment=0.85,  # headwind
                contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is False
        assert "Regime alignment" in reason

    @pytest.mark.asyncio
    async def test_regime_filter_off_by_default(self):
        """min_regime_alignment=0 accepts any regime, including 0.5× headwind."""
        svc = _make_service()  # default min_regime_alignment=0
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                **{**_HR_PASS_KW, "regime_alignment": 0.5},
                contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is True
        assert reason == "HR"

    @pytest.mark.asyncio
    async def test_max_premium_blocks_expensive_contract(self):
        svc = _make_service(_make_config(max_premium=5.0))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                **_HR_PASS_KW, contract_id="AAPL-C-200",
                quality_tier="TIER_2", premium=10.0,
            )
        assert result is False
        assert "above max" in reason

    @pytest.mark.asyncio
    async def test_non_tier1_blocked_by_quiet_hours(self):
        svc = _make_service()
        with patch.object(svc, "_is_within_quiet_hours", return_value=True):
            result, reason = await svc.should_alert(
                **_HR_PASS_KW, contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is False
        assert reason == "Within quiet hours"


# ============================================================================
# infer_verdict_driver
# ============================================================================


class TestInferVerdictDriver:
    """HR vs P driver attribution mirrors the thesis generator."""

    def test_hr_only_cleared(self):
        assert infer_verdict_driver(10.0, 20.0) == "HR"

    def test_p_only_cleared(self):
        assert infer_verdict_driver(3.0, 80.0) == "P"

    def test_both_cleared_hr_margin_larger(self):
        # hr=14 vs floor 7 → 100% margin; p=55 vs floor 50 → 10% margin
        assert infer_verdict_driver(14.0, 55.0) == "HR"

    def test_both_cleared_p_margin_larger(self):
        # hr=7.5 vs floor 7 → 7% margin; p=95 vs floor 50 → 90% margin
        assert infer_verdict_driver(7.5, 95.0) == "P"

    def test_both_cleared_equal_margin_defaults_hr(self):
        # Equal margins → HR wins the tiebreak (sharpshooter-biased).
        hr = V5_HR_FLOOR * 1.5
        p = V5_P_FLOOR * 1.5
        assert infer_verdict_driver(hr, p) == "HR"

    def test_neither_cleared(self):
        assert infer_verdict_driver(3.0, 20.0) is None

    def test_handles_none_values(self):
        assert infer_verdict_driver(None, None) is None
        assert infer_verdict_driver(None, 80.0) == "P"
        assert infer_verdict_driver(15.0, None) == "HR"


# ============================================================================
# Legacy config keys are stripped
# ============================================================================


class TestConfigMigration:
    """Legacy v3/v4 config keys must not leak into the v5 service."""

    def test_configure_strips_legacy_keys(self):
        svc = SlackAlertService()
        svc.configure({
            "enabled": True,
            "hr_conviction_min": 12.0,
            # Retired keys — should be ignored silently:
            "score_threshold": 75,
            "require_urgency_or_convergence": True,
            "cheap_gem_enabled": True,
            "per_scanner_thresholds": {"BREAKOUT": 60},
            "excluded_scanners": ["CHEAP_OPTIONS"],
            "setup_rule_filter_ids": ["rule-1"],
        })
        assert svc._config is not None
        assert "score_threshold" not in svc._config
        assert "cheap_gem_enabled" not in svc._config
        assert "per_scanner_thresholds" not in svc._config
        assert "excluded_scanners" not in svc._config
        assert "setup_rule_filter_ids" not in svc._config
        assert "require_urgency_or_convergence" not in svc._config
        # v5 keys carried through
        assert svc._config["hr_conviction_min"] == 12.0
