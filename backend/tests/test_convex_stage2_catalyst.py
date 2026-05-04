"""Tests for Convex Mode Stage 2 (Catalyst Layer).

Pure-function detectors plus the integrator. Pipeline wiring is covered
in test_convex_pipeline.py.
"""

from __future__ import annotations

import math
import random

import pytest

from app.convex import (
    PeerEarningsReaction,
    Stage2Inputs,
    detect_compression_signals,
    detect_date_known_catalyst,
    detect_momentum_signal,
    detect_sympathy,
    detect_unusual_volume,
    evaluate_stage2,
    resolve_direction,
)
from app.convex.stage2_catalyst import (
    COMPRESSION_SIGNAL_NAMES,
    MomentumDetection,
    UVDetection,
    _compression_strength,
    _date_known_strength,
    _uv_strength,
)
from app.core.schemas import (
    CatalystCalendarEntry,
    CatalystEventType,
    ConvexConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def random_walk(n: int, sigma: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, sigma))))
    return closes


def synthetic_ohlcv(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Generate synthetic highs/lows/volumes around a closes series."""
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    volumes = [1_000_000.0 for _ in closes]
    return highs, lows, volumes


# ---------------------------------------------------------------------------
# 2A — Date-known catalysts
# ---------------------------------------------------------------------------


class TestDateKnownDetector:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def _earnings_entry(self, date: str) -> CatalystCalendarEntry:
        return CatalystCalendarEntry(
            ticker="NVDA",
            event_date=date,
            event_type=CatalystEventType.EARNINGS,
            confirmed=True,
        )

    def test_no_catalyst_returns_not_detected(self):
        result = detect_date_known_catalyst([], "2026-04-26", self.cfg)
        assert result.detected is False
        assert result.event_type is None

    def test_event_within_window_passes(self):
        entries = [self._earnings_entry("2026-05-14")]  # 18 days out
        result = detect_date_known_catalyst(entries, "2026-04-26", self.cfg)
        assert result.detected is True
        # OSSBaseModel uses use_enum_values=True so the entry.event_type
        # round-trips as the string value, not the Enum instance.
        assert result.event_type == CatalystEventType.EARNINGS.value
        assert result.days_to_event == 18

    def test_event_too_close_excluded(self):
        # Event 2 days out — below 5-day floor.
        entries = [self._earnings_entry("2026-04-28")]
        result = detect_date_known_catalyst(entries, "2026-04-26", self.cfg)
        assert result.detected is False

    def test_event_too_far_excluded(self):
        # 60 days out — beyond 30-day ceiling.
        entries = [self._earnings_entry("2026-06-25")]
        result = detect_date_known_catalyst(entries, "2026-04-26", self.cfg)
        assert result.detected is False

    def test_picks_soonest_when_multiple(self):
        entries = [
            self._earnings_entry("2026-05-26"),  # 30 days out
            self._earnings_entry("2026-05-06"),  # 10 days out — should win
        ]
        result = detect_date_known_catalyst(entries, "2026-04-26", self.cfg)
        assert result.days_to_event == 10

    def test_strength_full_within_14_days(self):
        assert _date_known_strength(7, self.cfg) == 1.0
        assert _date_known_strength(14, self.cfg) == 1.0

    def test_strength_decays_to_half_at_window_edge(self):
        # Window upper edge is 30 days; strength = 0.5.
        assert _date_known_strength(30, self.cfg) == pytest.approx(0.5)

    def test_strength_decays_linearly(self):
        # 22 days = halfway between 14 and 30 → strength = 0.75
        s = _date_known_strength(22, self.cfg)
        assert 0.7 < s < 0.8


# ---------------------------------------------------------------------------
# 2B — Compression detector
# ---------------------------------------------------------------------------


class TestCompressionDetector:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_no_signals_when_data_thin(self):
        # 10 closes is too few for any signal.
        result = detect_compression_signals(
            closes=[100.0] * 10,
            highs=[101.0] * 10,
            lows=[99.0] * 10,
            volumes=[100.0] * 10,
            nearest_significant_level_pct=None,
            config=self.cfg,
        )
        assert result.detected is False
        assert result.active_signals == []

    def test_volatile_then_quiet_fires_bbw_compression(self):
        # 200 days of vol, then 60 days of quiet — current BBW should be
        # near the bottom of the trailing distribution.
        rng = random.Random(99)
        closes: list[float] = [100.0]
        for _ in range(200):
            closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, 0.04))))
        for _ in range(60):
            closes.append(max(0.01, closes[-1] * math.exp(rng.gauss(0, 0.001))))
        highs, lows, volumes = synthetic_ohlcv(closes)
        result = detect_compression_signals(
            closes, highs, lows, volumes, None, self.cfg
        )
        assert "bbw_compression" in result.active_signals

    def test_recent_quiet_volume_fires_volume_contraction(self):
        # Volume drops from 1M to 100K in the last 20 days.
        volumes = [1_000_000.0] * 70 + [100_000.0] * 20
        closes = [100.0] * 90
        highs = [100.5] * 90
        lows = [99.5] * 90
        result = detect_compression_signals(
            closes, highs, lows, volumes, None, self.cfg
        )
        assert "volume_contraction" in result.active_signals

    def test_breakout_proximity_uses_explicit_distance(self):
        result = detect_compression_signals(
            closes=[100.0] * 100,
            highs=[100.5] * 100,
            lows=[99.5] * 100,
            volumes=[1.0] * 100,
            nearest_significant_level_pct=1.5,  # Within 3% threshold
            config=self.cfg,
        )
        assert "breakout_proximity" in result.active_signals

    def test_breakout_proximity_skipped_when_too_far(self):
        result = detect_compression_signals(
            closes=[100.0] * 100,
            highs=[100.5] * 100,
            lows=[99.5] * 100,
            volumes=[1.0] * 100,
            nearest_significant_level_pct=10.0,
            config=self.cfg,
        )
        assert "breakout_proximity" not in result.active_signals

    def test_inactive_signals_complement_active(self):
        result = detect_compression_signals(
            closes=[100.0] * 100,
            highs=[100.5] * 100,
            lows=[99.5] * 100,
            volumes=[1.0] * 100,
            nearest_significant_level_pct=None,
            config=self.cfg,
        )
        assert set(result.active_signals).isdisjoint(set(result.inactive_signals))
        assert (
            set(result.active_signals) | set(result.inactive_signals)
        ).issubset(set(COMPRESSION_SIGNAL_NAMES))

    def test_strength_floor_at_threshold(self):
        # Default floor is 2 signals.
        assert _compression_strength(2, self.cfg) == pytest.approx(0.4)

    def test_strength_increases_with_extra_signals(self):
        s2 = _compression_strength(2, self.cfg)
        s3 = _compression_strength(3, self.cfg)
        s5 = _compression_strength(5, self.cfg)
        assert s2 < s3 < s5
        assert s5 <= 1.0


# ---------------------------------------------------------------------------
# 2C — Unusual Volume
# ---------------------------------------------------------------------------


class TestUVDetector:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_returns_not_detected_when_data_missing(self):
        assert detect_unusual_volume(None, 100, 10, 10, self.cfg).detected is False
        assert detect_unusual_volume(100, None, 10, 10, self.cfg).detected is False

    def test_below_multiplier_threshold_not_detected(self):
        # Default multiplier is 4×; ratio 2× is well below.
        result = detect_unusual_volume(
            today_total_volume=200,
            avg_volume_30d=100,
            today_call_volume=120,
            today_put_volume=80,
            config=self.cfg,
        )
        assert result.detected is False
        assert result.magnitude == 2.0

    def test_above_multiplier_threshold_detected(self):
        result = detect_unusual_volume(
            today_total_volume=600,
            avg_volume_30d=100,
            today_call_volume=400,
            today_put_volume=200,
            config=self.cfg,
        )
        assert result.detected is True
        assert result.magnitude == 6.0
        assert result.directional_skew == "call_heavy"

    def test_skew_classification(self):
        # call_heavy threshold is 65% calls.
        result = detect_unusual_volume(
            today_total_volume=600,
            avg_volume_30d=100,
            today_call_volume=420,
            today_put_volume=180,
            config=self.cfg,
        )
        assert result.directional_skew == "call_heavy"

        result_put = detect_unusual_volume(
            today_total_volume=600,
            avg_volume_30d=100,
            today_call_volume=180,
            today_put_volume=420,
            config=self.cfg,
        )
        assert result_put.directional_skew == "put_heavy"

        result_balanced = detect_unusual_volume(
            today_total_volume=600,
            avg_volume_30d=100,
            today_call_volume=300,
            today_put_volume=300,
            config=self.cfg,
        )
        assert result_balanced.directional_skew == "balanced"

    def test_strength_floor_at_threshold(self):
        assert _uv_strength(self.cfg.catalyst_uv_volume_multiplier, self.cfg) == pytest.approx(0.6)

    def test_strength_caps_at_one(self):
        assert _uv_strength(100.0, self.cfg) == 1.0


# ---------------------------------------------------------------------------
# 2D — Sympathy
# ---------------------------------------------------------------------------


class TestSympathyDetector:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_no_sector_returns_not_detected(self):
        result = detect_sympathy(
            None,
            "NVDA",
            [PeerEarningsReaction("AMD", "2026-04-22", days_ago=4, move_pct=8.0)],
            self.cfg,
        )
        assert result.detected is False

    def test_no_eligible_peers(self):
        result = detect_sympathy("Technology", "NVDA", [], self.cfg)
        assert result.detected is False

    def test_peer_below_threshold_excluded(self):
        # Default threshold is 5%; 3% reaction is below.
        peers = [PeerEarningsReaction("AMD", "2026-04-22", days_ago=4, move_pct=3.0)]
        result = detect_sympathy("Technology", "NVDA", peers, self.cfg)
        assert result.detected is False

    def test_peer_too_old_excluded(self):
        # Default lookback is 5 days; 7 days ago is too old.
        peers = [PeerEarningsReaction("AMD", "2026-04-19", days_ago=7, move_pct=10.0)]
        result = detect_sympathy("Technology", "NVDA", peers, self.cfg)
        assert result.detected is False

    def test_eligible_peer_triggers_detection(self):
        peers = [PeerEarningsReaction("AMD", "2026-04-22", days_ago=4, move_pct=8.0)]
        result = detect_sympathy("Technology", "NVDA", peers, self.cfg)
        assert result.detected is True
        assert result.peer_ticker == "AMD"
        assert result.peer_move_pct == 8.0
        assert result.strength == 0.5

    def test_self_excluded(self):
        # NVDA's own earnings shouldn't trigger NVDA's sympathy detector.
        peers = [PeerEarningsReaction("NVDA", "2026-04-22", days_ago=4, move_pct=10.0)]
        result = detect_sympathy("Technology", "NVDA", peers, self.cfg)
        assert result.detected is False

    def test_picks_most_recent_peer(self):
        peers = [
            PeerEarningsReaction("AMD", "2026-04-22", days_ago=4, move_pct=8.0),
            PeerEarningsReaction("INTC", "2026-04-25", days_ago=1, move_pct=6.0),
        ]
        result = detect_sympathy("Technology", "NVDA", peers, self.cfg)
        assert result.peer_ticker == "INTC"


# ---------------------------------------------------------------------------
# Integrator
# ---------------------------------------------------------------------------


class TestEvaluateStage2:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def _stage2_inputs(
        self,
        calendar_entries=(),
        peer_reactions=(),
        today_options_volume=None,
        avg_options_volume_30d=None,
        nearest_level_pct=None,
        closes=None,
    ):
        if closes is None:
            # A modest random walk that's not so quiet it triggers compression
            # on its own. Tests that need a *guaranteed* clean inputs set pass
            # ``closes=[100.0] * 252`` plus matching highs/lows/volumes via
            # the wrapper below.
            closes = random_walk(n=252, sigma=0.025, seed=11)
        highs, lows, volumes = synthetic_ohlcv(closes)
        return Stage2Inputs(
            ticker="NVDA",
            sector="Technology",
            closes=closes,
            highs=highs,
            lows=lows,
            volumes=volumes,
            nearest_significant_level_pct=nearest_level_pct,
            calendar_entries=list(calendar_entries),
            today_total_options_volume=today_options_volume,
            avg_options_volume_30d=avg_options_volume_30d,
            today_call_options_volume=None,
            today_put_options_volume=None,
            peer_reactions=list(peer_reactions),
        )

    def test_no_catalysts_fails(self):
        # Series where recent volatility *exceeds* historical, so BBW
        # compression and friends do not fire. Also no calendar / UV /
        # peer inputs. Stage 2 must FAIL.
        rng = random.Random(31)
        quiet = [100.0]
        for _ in range(200):
            quiet.append(quiet[-1] * math.exp(rng.gauss(0, 0.005)))
        noisy_tail = [quiet[-1]]
        for _ in range(60):
            noisy_tail.append(noisy_tail[-1] * math.exp(rng.gauss(0, 0.05)))
        closes = quiet + noisy_tail[1:]
        inputs = self._stage2_inputs(closes=closes)
        payload, detections = evaluate_stage2(inputs, "2026-04-26", self.cfg)
        assert payload.result == "FAIL"
        assert payload.strength == 0.0
        assert "no catalyst within window" in payload.summary

    def _bullish_momentum_closes(self) -> list[float]:
        """Generate a close series that ends with a +6% 5-day move."""
        closes = [100.0] * 250
        # Last 5 days ramp +1.2%/day → ~+6% in 5 days.
        for _ in range(5):
            closes.append(closes[-1] * 1.012)
        return closes

    def test_date_known_catalyst_passes(self):
        entry = CatalystCalendarEntry(
            ticker="NVDA",
            event_date="2026-05-14",
            event_type=CatalystEventType.EARNINGS,
            confirmed=True,
        )
        # Direction must resolve — provide bullish 5d momentum.
        inputs = self._stage2_inputs(
            calendar_entries=[entry],
            closes=self._bullish_momentum_closes(),
        )
        payload, _ = evaluate_stage2(inputs, "2026-04-26", self.cfg)
        assert payload.result == "PASS"
        assert payload.extras["selected_catalyst_type"] == "date_known"
        assert payload.extras["direction"] == "bullish"

    def test_max_strength_picked_across_detectors(self):
        # Date-known is strongest (1.0 within 14 days); compression weaker.
        entry = CatalystCalendarEntry(
            ticker="NVDA",
            event_date="2026-05-04",  # 8 days
            event_type=CatalystEventType.EARNINGS,
            confirmed=True,
        )
        inputs = self._stage2_inputs(
            calendar_entries=[entry],
            closes=self._bullish_momentum_closes(),
            today_options_volume=600,
            avg_options_volume_30d=100,  # 6× → UV detected with strength ~0.8
        )
        payload, _ = evaluate_stage2(inputs, "2026-04-26", self.cfg)
        assert payload.result == "PASS"
        # Date-known at 8 days = strength 1.0 — should be selected.
        assert payload.extras["selected_catalyst_type"] == "date_known"
        # Composite strength is max(catalyst_strength, momentum_strength) and
        # the date-known catalyst dominates at 1.0.
        assert payload.strength == pytest.approx(1.0)

    def test_uv_alone_no_longer_admits_stage2_pass(self):
        """UV is evidence, not a catalyst — it cannot admit a Stage 2 PASS
        on its own (no date-known / compression / sympathy)."""
        # Use a noisy close series that won't trigger compression on its own.
        rng = random.Random(31)
        closes = [100.0]
        for _ in range(260):
            closes.append(closes[-1] * math.exp(rng.gauss(0, 0.04)))
        inputs = self._stage2_inputs(
            closes=closes,
            today_options_volume=500,
            avg_options_volume_30d=100,  # 5× — UV detector triggers
        )
        payload, detections = evaluate_stage2(inputs, "2026-04-26", self.cfg)
        # No catalyst signal (no compression / date-known / sympathy) — Stage 2 FAILS
        # despite UV firing.
        assert payload.result == "FAIL"
        assert detections["unusual_volume"].detected is True
        assert payload.criteria["unusual_volume"]["detected"] is True


# ---------------------------------------------------------------------------
# Momentum detector + direction resolution
# ---------------------------------------------------------------------------


class TestDetectMomentumSignal:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def test_returns_empty_when_too_few_closes(self):
        result = detect_momentum_signal([100.0, 101.0], self.cfg)
        assert result.return_5d_pct is None
        assert result.direction == "none"
        assert result.above_threshold is False

    def test_positive_5d_return_is_bullish(self):
        # Last 5 days: 100 → 110 → +10%
        closes = [95.0] * 245 + [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        result = detect_momentum_signal(closes, self.cfg)
        assert result.direction == "bullish"
        assert result.return_5d_pct == pytest.approx(10.0, abs=0.01)
        assert result.above_threshold is True

    def test_negative_5d_return_is_bearish(self):
        closes = [105.0] * 245 + [100.0, 98.0, 96.0, 94.0, 92.0, 90.0]
        result = detect_momentum_signal(closes, self.cfg)
        assert result.direction == "bearish"
        assert result.return_5d_pct == pytest.approx(-10.0, abs=0.01)
        assert result.above_threshold is True

    def test_below_threshold_does_not_align(self):
        # +2% in 5 days — under the default 5% threshold.
        closes = [99.0] * 245 + [100.0, 100.4, 100.8, 101.2, 101.6, 102.0]
        result = detect_momentum_signal(closes, self.cfg)
        assert result.direction == "bullish"
        assert result.above_threshold is False


class TestResolveDirection:

    def _uv(self, detected: bool, skew: str = "balanced") -> UVDetection:
        return UVDetection(
            detected=detected, magnitude=5.0 if detected else 0.0,
            directional_skew=skew, strength=0.6 if detected else 0.0,
        )

    def _momentum(
        self, direction: str, above: bool = True
    ) -> MomentumDetection:
        return MomentumDetection(
            return_5d_pct=6.0 if direction == "bullish" else -6.0,
            direction=direction,
            magnitude_pct=6.0,
            above_threshold=above,
        )

    def test_momentum_alone_resolves(self):
        assert resolve_direction(
            self._momentum("bullish"), self._uv(False)
        ) == "bullish"

    def test_uv_alone_resolves(self):
        assert resolve_direction(
            MomentumDetection(), self._uv(True, "call_heavy")
        ) == "bullish"

    def test_agreement_resolves(self):
        assert resolve_direction(
            self._momentum("bullish"), self._uv(True, "call_heavy")
        ) == "bullish"

    def test_disagreement_yields_ambiguous(self):
        assert resolve_direction(
            self._momentum("bullish"), self._uv(True, "put_heavy")
        ) == "ambiguous"

    def test_neither_yields_ambiguous(self):
        assert resolve_direction(
            MomentumDetection(), self._uv(False)
        ) == "ambiguous"

    def test_below_threshold_momentum_alone_yields_ambiguous(self):
        # Direction-bearing momentum exists but magnitude < threshold,
        # and UV doesn't fire → ambiguous (no actionable direction).
        assert resolve_direction(
            self._momentum("bullish", above=False), self._uv(False)
        ) == "ambiguous"
