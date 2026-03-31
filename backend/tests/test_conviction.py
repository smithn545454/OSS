"""Tests for backend conviction score calculator.

Conviction = pipeline final_score × freshness_decay.
"""

from datetime import datetime, timezone, timedelta

from app.scoring.conviction import (
    CHEAP_UV_PREMIUM_THRESHOLD,
    calculate_conviction_score,
    calculate_freshness_decay,
    determine_urgency,
)


class TestDetermineUrgency:
    def test_breakout(self):
        assert determine_urgency(["BREAKOUT"]) == "act_now"

    def test_unusual_volume_expensive(self):
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=5.0) == "hours"

    def test_unusual_volume_cheap_escalates(self):
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=1.0) == "act_now"
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=CHEAP_UV_PREMIUM_THRESHOLD) == "act_now"

    def test_unusual_volume_no_mid(self):
        assert determine_urgency(["UNUSUAL_VOLUME"], mid=None) == "hours"

    def test_compression(self):
        assert determine_urgency(["COMPRESSION"]) == "patient"

    def test_empty(self):
        assert determine_urgency([]) == "patient"


class TestCalculateFreshnessDecay:
    def test_within_grace_period(self):
        now = datetime.now(timezone.utc)
        evaluated_at = (now - timedelta(hours=4)).isoformat()
        assert calculate_freshness_decay(evaluated_at, now=now) == 1.0

    def test_at_grace_boundary(self):
        now = datetime.now(timezone.utc)
        evaluated_at = (now - timedelta(hours=8)).isoformat()
        assert calculate_freshness_decay(evaluated_at, now=now) == 1.0

    def test_after_grace_period(self):
        now = datetime.now(timezone.utc)
        evaluated_at = (now - timedelta(hours=20)).isoformat()
        decay = calculate_freshness_decay(evaluated_at, now=now)
        assert 0.5 <= decay < 1.0

    def test_floor(self):
        now = datetime.now(timezone.utc)
        evaluated_at = (now - timedelta(hours=100)).isoformat()
        assert calculate_freshness_decay(evaluated_at, now=now) == 0.75


class TestCalculateConvictionScore:
    def test_fresh_evaluation(self):
        """Fresh evaluation returns final_score unchanged."""
        now = datetime.now(timezone.utc)
        evaluated_at = (now - timedelta(hours=1)).isoformat()
        result = calculate_conviction_score(
            final_score=80.0,
            evaluated_at=evaluated_at,
        )
        assert result.total == 80.0

    def test_stale_evaluation_decayed(self):
        """Stale evaluation has decayed score."""
        now = datetime.now(timezone.utc)
        evaluated_at = (now - timedelta(hours=20)).isoformat()
        result = calculate_conviction_score(
            final_score=80.0,
            evaluated_at=evaluated_at,
        )
        assert result.total < 80.0
        assert result.total >= 80.0 * 0.75

    def test_zero_final_score(self):
        """Zero final_score stays zero."""
        now = datetime.now(timezone.utc)
        evaluated_at = now.isoformat()
        result = calculate_conviction_score(
            final_score=0.0,
            evaluated_at=evaluated_at,
        )
        assert result.total == 0.0
