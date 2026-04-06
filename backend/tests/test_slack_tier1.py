"""Tests for Tier 1 fast-path Slack alerts.

Verifies that Tier 1 opportunities bypass soft filters (score threshold,
urgency/convergence, quiet hours) while still respecting hard checks
(enabled, webhooks, daily cap, cooldown).
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

from app.services.slack import SlackAlertService


def _make_config(**overrides):
    """Build a test alert config with sensible defaults."""
    base = {
        "enabled": True,
        "score_threshold": 75,
        "require_urgency_or_convergence": True,
        "cooldown_minutes": 30,
        "daily_cap": 10,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "08:00",
        "webhook_channels": [{"channel_name": "#test", "url": "https://hooks.slack.com/test"}],
        "setup_rule_filter_ids": [],
        "verdicts": ["APPROVE"],
        "max_premium": None,
        "cheap_gem_enabled": False,
        "cheap_gem_threshold": 60,
        "cheap_gem_max_premium": 1.50,
        "tier_1_bypass": True,
    }
    base.update(overrides)
    return base


def _make_service(config=None):
    """Create a SlackAlertService with pre-loaded config."""
    svc = SlackAlertService()
    svc.configure(config or _make_config())
    return svc


class TestTier1FastPath:
    """Tier 1 bypasses soft filters but respects hard checks."""

    @pytest.mark.asyncio
    async def test_tier1_bypasses_score_threshold(self):
        svc = _make_service(_make_config(score_threshold=90))
        # Score 60 would normally fail the 90 threshold
        result, reason = await svc.should_alert(
            conviction_score=60, urgency="patient", convergence=1,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is True
        assert reason == "tier_1"

    @pytest.mark.asyncio
    async def test_tier1_bypasses_urgency_convergence(self):
        svc = _make_service(_make_config(require_urgency_or_convergence=True))
        # Single scanner + patient urgency would normally fail
        result, reason = await svc.should_alert(
            conviction_score=90, urgency="patient", convergence=1,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is True
        assert reason == "tier_1"

    @pytest.mark.asyncio
    async def test_tier1_bypasses_quiet_hours(self):
        svc = _make_service()
        with patch.object(svc, "_is_within_quiet_hours", return_value=True):
            result, reason = await svc.should_alert(
                conviction_score=90, urgency="patient", convergence=1,
                contract_id="AAPL-C-200", quality_tier="TIER_1",
            )
        assert result is True
        assert reason == "tier_1"

    @pytest.mark.asyncio
    async def test_tier1_respects_enabled_false(self):
        svc = _make_service(_make_config(enabled=False))
        result, reason = await svc.should_alert(
            conviction_score=90, urgency="act_now", convergence=3,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert reason == "Alerts disabled"

    @pytest.mark.asyncio
    async def test_tier1_respects_no_webhooks(self):
        svc = _make_service(_make_config(webhook_channels=[]))
        svc._legacy_webhook_url = None
        result, reason = await svc.should_alert(
            conviction_score=90, urgency="act_now", convergence=3,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert reason == "No webhook URLs configured"

    @pytest.mark.asyncio
    async def test_tier1_respects_daily_cap(self):
        svc = _make_service(_make_config(daily_cap=5))
        svc._daily_count = 5
        svc._last_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result, reason = await svc.should_alert(
            conviction_score=90, urgency="act_now", convergence=3,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert "Daily alert cap" in reason

    @pytest.mark.asyncio
    async def test_tier1_respects_cooldown(self):
        svc = _make_service(_make_config(cooldown_minutes=30))
        svc._alert_timestamps["AAPL-C-200"] = datetime.now(timezone.utc)
        result, reason = await svc.should_alert(
            conviction_score=90, urgency="act_now", convergence=3,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert "cooldown" in reason

    @pytest.mark.asyncio
    async def test_tier1_bypass_disabled_via_config(self):
        svc = _make_service(_make_config(
            tier_1_bypass=False,
            score_threshold=90,
            require_urgency_or_convergence=True,
        ))
        # Without bypass, score 60 + patient/1 convergence → fails
        result, reason = await svc.should_alert(
            conviction_score=60, urgency="patient", convergence=1,
            contract_id="AAPL-C-200", quality_tier="TIER_1",
        )
        assert result is False
        assert "Score" in reason


class TestNonTier1Unchanged:
    """Non-Tier-1 evaluations behave exactly as before."""

    @pytest.mark.asyncio
    async def test_tier2_does_not_bypass(self):
        svc = _make_service(_make_config(score_threshold=90))
        result, reason = await svc.should_alert(
            conviction_score=60, urgency="patient", convergence=1,
            contract_id="AAPL-C-200", quality_tier="TIER_2",
        )
        assert result is False
        assert "Score" in reason

    @pytest.mark.asyncio
    async def test_none_tier_does_not_bypass(self):
        svc = _make_service(_make_config(score_threshold=90))
        result, reason = await svc.should_alert(
            conviction_score=60, urgency="patient", convergence=1,
            contract_id="AAPL-C-200",
        )
        assert result is False
        assert "Score" in reason

    @pytest.mark.asyncio
    async def test_non_tier1_blocked_by_quiet_hours(self):
        svc = _make_service()
        with patch.object(svc, "_is_within_quiet_hours", return_value=True):
            result, reason = await svc.should_alert(
                conviction_score=80, urgency="act_now", convergence=2,
                contract_id="AAPL-C-200", quality_tier="TIER_2",
            )
        assert result is False
        assert reason == "Within quiet hours"

    @pytest.mark.asyncio
    async def test_normal_approve_passes_all_checks(self):
        svc = _make_service(_make_config(
            require_urgency_or_convergence=False,
        ))
        with patch.object(svc, "_is_within_quiet_hours", return_value=False):
            result, reason = await svc.should_alert(
                conviction_score=80, urgency="patient", convergence=1,
                contract_id="AAPL-C-200",
            )
        assert result is True
        assert reason is None
