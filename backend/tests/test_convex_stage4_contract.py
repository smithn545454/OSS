"""Tests for Convex Mode Stage 4 (Contract Selection).

Pure-function selection logic: expected-move terminus, strike picking,
liquidity validation, Smart Money Confirmation, and the integrator.
Pipeline wiring is covered separately in test_convex_pipeline.py.
"""

from __future__ import annotations

import pytest

from app.convex import (
    ConvexContractCandidate,
    Stage4Inputs,
    compute_expected_terminus,
    evaluate_stage4,
    smart_money_confirmation,
)
from app.core.schemas import ConvexConfig


def _contract(
    strike: float,
    delta: float,
    dte: int = 42,
    expiry: str = "2026-06-26",
    option_type: str = "CALL",
    bid: float = 4.75,
    ask: float = 4.95,
    open_interest: int = 8000,
    volume: int = 1500,
) -> ConvexContractCandidate:
    return ConvexContractCandidate(
        option_ticker=f"O:NVDA{expiry.replace('-','')[2:]}{option_type[0]}{int(strike*1000):08d}",
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        dte=dte,
        delta=delta,
        bid=bid,
        ask=ask,
        open_interest=open_interest,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# Expected-move terminus
# ---------------------------------------------------------------------------


class TestComputeExpectedTerminus:

    def test_bullish_projects_above(self):
        result = compute_expected_terminus(
            underlying_price=140.0,
            direction="bullish",
            measured_move_pct=4.0,
            historical_event_move_pct=5.0,
        )
        # Uses larger of 4 / 5 = 5%; 140 * 1.05 = 147
        assert result == pytest.approx(147.0)

    def test_bearish_projects_below(self):
        result = compute_expected_terminus(
            underlying_price=140.0,
            direction="bearish",
            measured_move_pct=4.0,
            historical_event_move_pct=None,
        )
        # 140 * (1 - 0.04) = 134.4
        assert result == pytest.approx(134.4)

    def test_returns_none_when_no_move_estimate(self):
        result = compute_expected_terminus(
            underlying_price=140.0,
            direction="bullish",
            measured_move_pct=None,
            historical_event_move_pct=None,
        )
        assert result is None

    def test_ambiguous_returns_unsigned_magnitude(self):
        result = compute_expected_terminus(
            underlying_price=140.0,
            direction="ambiguous",
            measured_move_pct=4.0,
            historical_event_move_pct=None,
        )
        # Magnitude only, not signed: 140 * 0.04 = 5.6
        assert result == pytest.approx(5.6)


# ---------------------------------------------------------------------------
# Smart Money Confirmation
# ---------------------------------------------------------------------------


class TestSmartMoneyConfirmation:

    def test_bullish_call_heavy(self):
        assert smart_money_confirmation("bullish", "call_heavy") is True

    def test_bearish_put_heavy(self):
        assert smart_money_confirmation("bearish", "put_heavy") is True

    def test_bullish_put_heavy_does_not_confirm(self):
        # Skew opposes thesis — no confirmation
        assert smart_money_confirmation("bullish", "put_heavy") is False

    def test_balanced_does_not_confirm(self):
        assert smart_money_confirmation("bullish", "balanced") is False

    def test_no_uv_skew_does_not_confirm(self):
        assert smart_money_confirmation("bullish", None) is False

    def test_ambiguous_never_confirms(self):
        assert smart_money_confirmation("ambiguous", "call_heavy") is False


# ---------------------------------------------------------------------------
# evaluate_stage4 — directional path
# ---------------------------------------------------------------------------


class TestEvaluateStage4Directional:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def _bullish_inputs(
        self,
        contracts=None,
        catalyst_type="state_based",
        catalyst_date_iso=None,
        uv_skew=None,
    ):
        if contracts is None:
            # 3 calls in delta band, varying strikes near terminus 147
            contracts = [
                _contract(strike=150, delta=0.24, dte=42),  # below 0.25 floor
                _contract(strike=145, delta=0.32, dte=42),  # ideal
                _contract(strike=140, delta=0.45, dte=42),  # above 0.35 ceiling
            ]
        return Stage4Inputs(
            ticker="NVDA",
            underlying_price=140.0,
            direction="bullish",
            catalyst_type=catalyst_type,
            catalyst_date_iso=catalyst_date_iso,
            measured_move_pct=4.0,
            historical_event_move_pct=5.0,
            available_contracts=contracts,
            uv_directional_skew=uv_skew,
            today_iso="2026-04-26",
        )

    def test_picks_strike_in_delta_band_closest_to_terminus(self):
        result = evaluate_stage4(self._bullish_inputs(), self.cfg)
        assert result.payload.result == "PASS"
        assert result.selected_call is not None
        assert result.selected_call.strike == 145
        assert result.selected_call.delta == pytest.approx(0.32)

    def test_records_alternatives_outside_delta_band(self):
        result = evaluate_stage4(self._bullish_inputs(), self.cfg)
        alts = result.payload.criteria["alternatives_considered"]
        # Strikes 150 (below 0.25Δ) and 140 (above 0.35Δ) should be flagged
        rejected_strikes = {a["strike"] for a in alts}
        assert 150 in rejected_strikes
        assert 140 in rejected_strikes

    def test_fails_when_no_contracts_in_band(self):
        # All contracts outside the 0.25-0.35 delta band
        contracts = [
            _contract(strike=150, delta=0.10),
            _contract(strike=140, delta=0.55),
        ]
        result = evaluate_stage4(
            self._bullish_inputs(contracts=contracts), self.cfg
        )
        assert result.payload.result == "FAIL"
        assert result.selected_call is None

    def test_fails_when_only_candidate_fails_liquidity(self):
        # In band but spread too wide
        contracts = [
            _contract(
                strike=145, delta=0.32,
                bid=2.0, ask=4.0,  # 67% spread
                open_interest=100,
            ),
        ]
        result = evaluate_stage4(
            self._bullish_inputs(contracts=contracts), self.cfg
        )
        assert result.payload.result == "FAIL"
        summary_lc = result.payload.summary.lower()
        assert "tradeable" in summary_lc or "thin" in summary_lc

    def test_falls_back_to_secondary_strike_when_primary_fails_liquidity(self):
        # Primary 145 fails liquidity; 148 still in band passes
        contracts = [
            _contract(  # closest to terminus, fails OI
                strike=147, delta=0.30,
                open_interest=100,
            ),
            _contract(  # next-closest, passes
                strike=145, delta=0.32,
                open_interest=8000,
            ),
        ]
        result = evaluate_stage4(
            self._bullish_inputs(contracts=contracts), self.cfg
        )
        assert result.payload.result == "PASS"
        assert result.selected_call is not None
        assert result.selected_call.strike == 145

    def test_post_event_buffer_excludes_pre_event_expiries(self):
        # Catalyst on 2026-05-14, today 2026-04-26. Buffer = 14 → expiry
        # must be ≥ 2026-05-28. Contract at 2026-05-15 is excluded.
        contracts = [
            _contract(
                strike=145, delta=0.32, dte=19,
                expiry="2026-05-15",  # falls inside buffer
            ),
            _contract(
                strike=145, delta=0.32, dte=42,
                expiry="2026-06-07",  # past buffer
            ),
        ]
        result = evaluate_stage4(
            self._bullish_inputs(
                contracts=contracts,
                catalyst_type="date_known",
                catalyst_date_iso="2026-05-14",
            ),
            self.cfg,
        )
        assert result.payload.result == "PASS"
        assert result.selected_call.expiry == "2026-06-07"

    def test_smart_money_confirmation_propagated(self):
        contracts = [_contract(strike=145, delta=0.32, dte=42)]
        result = evaluate_stage4(
            self._bullish_inputs(contracts=contracts, uv_skew="call_heavy"),
            self.cfg,
        )
        assert result.smart_money_confirmation is True
        assert result.payload.extras["smart_money_confirmation"] is True

    def test_smart_money_off_when_skew_misaligned(self):
        contracts = [_contract(strike=145, delta=0.32, dte=42)]
        result = evaluate_stage4(
            self._bullish_inputs(contracts=contracts, uv_skew="put_heavy"),
            self.cfg,
        )
        assert result.smart_money_confirmation is False
        assert result.payload.extras["smart_money_confirmation"] is False


# ---------------------------------------------------------------------------
# evaluate_stage4 — straddle path
# ---------------------------------------------------------------------------


class TestEvaluateStage4Straddle:

    def setup_method(self):
        self.cfg = ConvexConfig()

    def _straddle_inputs(self, contracts) -> Stage4Inputs:
        return Stage4Inputs(
            ticker="NVDA",
            underlying_price=140.0,
            direction="ambiguous",
            catalyst_type="state_based",
            catalyst_date_iso=None,
            measured_move_pct=4.0,
            historical_event_move_pct=None,
            available_contracts=contracts,
            uv_directional_skew=None,
            today_iso="2026-04-26",
        )

    def test_picks_50d_call_and_put_at_shared_expiry(self):
        contracts = [
            _contract(strike=140, delta=0.50, option_type="CALL", expiry="2026-06-07"),
            _contract(strike=140, delta=-0.50, option_type="PUT", expiry="2026-06-07"),
        ]
        result = evaluate_stage4(self._straddle_inputs(contracts), self.cfg)
        assert result.payload.result == "PASS"
        assert result.selected_call is not None
        assert result.selected_put is not None
        assert result.selected_call.expiry == result.selected_put.expiry
        assert result.payload.criteria["structure"] == "long_straddle"

    def test_fails_when_call_or_put_missing(self):
        # Only a call available — no put pair to form straddle
        contracts = [
            _contract(strike=140, delta=0.50, option_type="CALL", expiry="2026-06-07"),
        ]
        result = evaluate_stage4(self._straddle_inputs(contracts), self.cfg)
        assert result.payload.result == "FAIL"

    def test_fails_when_no_shared_expiry(self):
        contracts = [
            _contract(strike=140, delta=0.50, option_type="CALL", expiry="2026-06-07"),
            _contract(strike=140, delta=-0.50, option_type="PUT", expiry="2026-07-07"),
        ]
        result = evaluate_stage4(self._straddle_inputs(contracts), self.cfg)
        assert result.payload.result == "FAIL"

    def test_fails_when_pair_fails_liquidity(self):
        contracts = [
            _contract(
                strike=140, delta=0.50, option_type="CALL", expiry="2026-06-07",
                open_interest=100,
            ),
            _contract(
                strike=140, delta=-0.50, option_type="PUT", expiry="2026-06-07",
                open_interest=8000,
            ),
        ]
        result = evaluate_stage4(self._straddle_inputs(contracts), self.cfg)
        assert result.payload.result == "FAIL"


# ---------------------------------------------------------------------------
# Contract menu (Phase post-cutover refinement)
# ---------------------------------------------------------------------------


class TestContractMenu:
    """The Stage 4 contract menu surfaces 1-3 recommended alternatives."""

    cfg = ConvexConfig(enabled=True)

    def _bullish_inputs(
        self, contracts: list[ConvexContractCandidate]
    ) -> Stage4Inputs:
        return Stage4Inputs(
            ticker="NVDA",
            underlying_price=140.0,
            direction="bullish",
            catalyst_type="state_based",
            catalyst_date_iso=None,
            measured_move_pct=10.0,
            historical_event_move_pct=None,
            available_contracts=contracts,
            today_iso="2026-04-28",
        )

    def test_menu_includes_primary(self):
        contracts = [
            _contract(strike=145, delta=0.30, option_type="CALL", expiry="2026-06-26"),
        ]
        result = evaluate_stage4(self._bullish_inputs(contracts), self.cfg)
        assert result.payload.result == "PASS"
        assert len(result.contract_menu) >= 1
        assert result.contract_menu[0].label == "primary"
        assert result.selected_call.strike == result.contract_menu[0].contract.strike

    def test_menu_adds_stretch_when_lower_delta_strike_available(self):
        # Primary at 0.30Δ + a 0.20Δ stretch (further OTM).
        contracts = [
            _contract(strike=145, delta=0.30, option_type="CALL", expiry="2026-06-26"),
            _contract(strike=155, delta=0.18, option_type="CALL", expiry="2026-06-26"),
        ]
        result = evaluate_stage4(self._bullish_inputs(contracts), self.cfg)
        assert result.payload.result == "PASS"
        labels = [rc.label for rc in result.contract_menu]
        assert "primary" in labels
        assert "stretch" in labels
        stretch = next(rc for rc in result.contract_menu if rc.label == "stretch")
        assert abs(stretch.contract.delta) < abs(result.selected_call.delta)

    def test_menu_adds_defensive_when_longer_dte_available(self):
        # Primary at 42 DTE + a 70 DTE same-strike defensive variant.
        contracts = [
            _contract(strike=145, delta=0.30, option_type="CALL", expiry="2026-06-26", dte=42),
            _contract(strike=145, delta=0.32, option_type="CALL", expiry="2026-07-24", dte=70),
        ]
        result = evaluate_stage4(self._bullish_inputs(contracts), self.cfg)
        assert result.payload.result == "PASS"
        labels = [rc.label for rc in result.contract_menu]
        assert "defensive" in labels
        defensive = next(rc for rc in result.contract_menu if rc.label == "defensive")
        assert defensive.contract.dte > result.selected_call.dte

    def test_menu_skips_slot_when_no_qualifying_contract(self):
        # Only the primary qualifies — no stretch, no defensive variants.
        contracts = [
            _contract(strike=145, delta=0.30, option_type="CALL", expiry="2026-06-26"),
        ]
        result = evaluate_stage4(self._bullish_inputs(contracts), self.cfg)
        assert len(result.contract_menu) == 1
        assert result.contract_menu[0].label == "primary"

    def test_payload_criteria_includes_menu(self):
        contracts = [
            _contract(strike=145, delta=0.30, option_type="CALL", expiry="2026-06-26"),
            _contract(strike=155, delta=0.18, option_type="CALL", expiry="2026-06-26"),
        ]
        result = evaluate_stage4(self._bullish_inputs(contracts), self.cfg)
        menu_in_criteria = result.payload.criteria.get("contract_menu", [])
        assert len(menu_in_criteria) >= 1
        primary_entry = menu_in_criteria[0]
        assert primary_entry["label"] == "primary"
        assert "rationale" in primary_entry
        assert "contract" in primary_entry
