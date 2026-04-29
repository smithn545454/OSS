"""Tests for Convex downstream wiring: paper trading, LLM thesis, Slack alerts.

Phase 1 of the Convex cutover plan adds three new entry points so the
Convex daily runner produces every artifact the legacy pipeline produces
today. These tests exercise each entry point in isolation against a
hand-built ``FinalisedConvexCandidate`` fixture so we don't need to drive
the full four-stage pipeline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.convex._types import Tier
from app.convex.daily_runner import _emit_downstream_artifacts
from app.convex.pipeline import ConvexCandidate
from app.convex.stage4_contract import ConvexContractCandidate
from app.convex.tier import FinalisedConvexCandidate
from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
    ConvexStagesPayload,
    Decision,
    Verdict,
)
from app.db.tables import PaperPositionTable
from app.paper_trading.position_manager import (
    create_position_from_convex_candidate,
)
from app.services.slack import SlackAlertService


# ---------------------------------------------------------------------------
# Fixture: a fully-finalised Tier B Convex candidate ready for downstream wiring.
# ---------------------------------------------------------------------------


def _make_stage_payload(stage: int, name: str, strength: float) -> ConvexStagePayload:
    return ConvexStagePayload(
        stage=stage,
        stage_name=name,
        result="PASS",
        summary=f"{name} cleared at strength {strength:.2f}",
        criteria={"value": strength},
        strength=strength,
    )


def _make_finalised(
    *,
    ticker: str = "AAPL",
    tier: Tier = Tier.B,
    direction: str = "bullish",
    smart_money: bool = True,
    composite: float = 0.62,
) -> FinalisedConvexCandidate:
    stages = ConvexStagesPayload(
        stage_1=_make_stage_payload(1, "Kinetic Universe", 0.80),
        stage_2=_make_stage_payload(2, "Catalyst", 0.55),
        stage_3=_make_stage_payload(3, "Volatility Mispricing", 0.62),
        stage_4=_make_stage_payload(4, "Contract Selection", 0.70),
    )
    selected_call = ConvexContractCandidate(
        option_ticker=f"O:{ticker}260515C00200000",
        option_type="CALL",
        strike=200.0,
        expiry="2026-05-15",
        dte=45,
        delta=0.32,
        bid=4.50,
        ask=4.70,
        open_interest=8000,
        volume=1500,
    )
    candidate = ConvexCandidate(
        ticker=ticker,
        stages=stages,
        direction=direction,
        smart_money_confirmation=smart_money,
        tier=tier,
        composite_strength=composite,
        selected_call=selected_call,
    )
    decision = Decision(
        evaluation_id=f"convex-test-{ticker}",
        verdict=Verdict.CONVEX_APPROVE,
        final_score=0.0,
        primary_reason_code="CONVEX_APPROVED_BY_TIER",
        supporting_reason_codes=[f"convex_tier_{tier.value.lower()}"],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v4.1.1",
        decided_at="2026-04-29T00:00:00+00:00",
        convex_tier=tier.value,
        convex_stages=stages,
        convex_strength_composite=composite,
        smart_money_confirmation=smart_money,
        position_sizing_recommendation=f"Tier {tier.value} → 35% of standard sizing",
    )
    return FinalisedConvexCandidate(
        candidate=candidate,
        tier=tier,
        composite=composite,
        decision=decision,
    )


# ---------------------------------------------------------------------------
# Paper trading: create_position_from_convex_candidate
# ---------------------------------------------------------------------------


class TestConvexPaperPosition:

    @pytest.mark.asyncio
    async def test_creates_position_with_convex_shape(self, fresh_dynamodb_client):
        finalised = _make_finalised(ticker="AAPL", tier=Tier.A, composite=0.78)
        position = await create_position_from_convex_candidate(finalised)

        assert position is not None
        assert position.evaluation_id == "convex-test-AAPL"
        assert position.option_ticker == "O:AAPL260515C00200000"
        assert position.entry_price == pytest.approx(4.70)
        assert position.verdict_at_entry == Verdict.CONVEX_APPROVE
        assert position.scanner_source == "CONVEX_TIER_A"
        assert position.scanner_list == ["CONVEX"]
        # Conviction projected from composite onto legacy 0–100 scale.
        assert position.conviction_score == pytest.approx(78.0)
        # Pillar fields stay None — Convex carries no pillar data.
        assert position.pillar_premium_leverage is None
        assert position.pillar_directional_conviction is None
        assert position.entry_delta == pytest.approx(0.32)
        assert position.dte_at_entry == 45
        assert position.quality_tier_at_entry is None

    @pytest.mark.asyncio
    async def test_dedupes_open_position_for_same_contract(self, fresh_dynamodb_client):
        first = _make_finalised(ticker="MSFT")
        # Build a second finalised candidate with a fresh evaluation_id but
        # the same option_ticker so the dedup path under test is
        # has_open_position, not the eval_id check.
        second_decision = first.decision.model_copy(
            update={"evaluation_id": "convex-test-MSFT-2"}
        )
        second = FinalisedConvexCandidate(
            candidate=first.candidate,
            tier=first.tier,
            composite=first.composite,
            decision=second_decision,
        )

        first_position = await create_position_from_convex_candidate(first)
        second_position = await create_position_from_convex_candidate(second)

        assert first_position is not None
        assert second_position is None  # blocked by has_open_position dedup

    @pytest.mark.asyncio
    async def test_picks_put_for_bearish_direction(self, fresh_dynamodb_client):
        finalised = _make_finalised(direction="bearish")
        # Add a put on the candidate so the bearish branch has something to pick.
        finalised.candidate.selected_put = ConvexContractCandidate(
            option_ticker="O:AAPL260515P00180000",
            option_type="PUT",
            strike=180.0,
            expiry="2026-05-15",
            dte=45,
            delta=-0.30,
            bid=3.20,
            ask=3.40,
            open_interest=4000,
            volume=900,
        )
        position = await create_position_from_convex_candidate(finalised)
        assert position is not None
        assert position.option_ticker == "O:AAPL260515P00180000"
        assert position.option_type == "PUT"

    @pytest.mark.asyncio
    async def test_skips_when_no_contract_selected(self, fresh_dynamodb_client):
        finalised = _make_finalised()
        finalised.candidate.selected_call = None
        finalised.candidate.selected_put = None

        position = await create_position_from_convex_candidate(finalised)
        assert position is None


# ---------------------------------------------------------------------------
# Slack: send_convex_alert
# ---------------------------------------------------------------------------


class TestConvexSlackAlert:

    @pytest.mark.asyncio
    async def test_returns_disabled_when_alerts_off(self, fresh_dynamodb_client):
        service = SlackAlertService()
        service.configure({"enabled": False})

        finalised = _make_finalised()
        sent, reason = await service.send_convex_alert(finalised)
        assert sent is False
        assert "disabled" in (reason or "").lower()

    @pytest.mark.asyncio
    async def test_blocks_tier_below_min(self, fresh_dynamodb_client):
        service = SlackAlertService()
        # Tier C should be blocked by default (min is "B").
        service.configure({
            "enabled": True,
            "convex_min_tier": "B",
            "webhook_channels": [
                {"channel_name": "#test", "url": "https://hooks.slack.com/x"}
            ],
        })

        finalised = _make_finalised(tier=Tier.C)
        sent, reason = await service.send_convex_alert(finalised)
        assert sent is False
        assert "below min_tier" in (reason or "")

    @pytest.mark.asyncio
    async def test_sends_for_tier_a_with_smart_money(self, fresh_dynamodb_client):
        service = SlackAlertService()
        service.configure({
            "enabled": True,
            "convex_min_tier": "B",
            "daily_cap": 100,
            "cooldown_minutes": 0,
            "ticker_cooldown_minutes": 0,
            "quiet_hours_start": "00:00",
            "quiet_hours_end": "00:01",  # effectively no quiet hours
            "webhook_channels": [
                {"channel_name": "#test", "url": "https://hooks.slack.com/x"}
            ],
        })

        finalised = _make_finalised(tier=Tier.A, smart_money=True)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        async def _fake_post(*args, **kwargs):
            return mock_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            sent, reason = await service.send_convex_alert(finalised)

        assert sent is True
        assert reason is None

    def test_format_convex_message_has_tier_and_smart_money(self):
        service = SlackAlertService()
        finalised = _make_finalised(tier=Tier.A, smart_money=True)
        msg = service._format_convex_message(
            ticker=finalised.candidate.ticker,
            tier=finalised.tier.value,
            composite_strength=finalised.composite,
            smart_money=finalised.candidate.smart_money_confirmation,
            direction=finalised.candidate.direction or "bullish",
            selected=finalised.candidate.selected_call,
            stages=finalised.candidate.stages,
            evaluation_id=finalised.decision.evaluation_id,
        )
        # Header carries the tier label and the smart-money sparkle.
        header = msg["blocks"][0]["text"]["text"]
        assert "TIER A" in header
        # Stage block lists all four stages.
        body_text = "\n".join(b["text"]["text"] for b in msg["blocks"] if "text" in b)
        for name in ("Kinetic Universe", "Catalyst", "Volatility", "Contract Selection"):
            assert name in body_text


# ---------------------------------------------------------------------------
# Daily runner: _emit_downstream_artifacts
# ---------------------------------------------------------------------------


class TestEmitDownstreamArtifacts:

    @pytest.mark.asyncio
    async def test_creates_position_and_writes_thesis(self, fresh_dynamodb_client):
        finalised = [_make_finalised(ticker="NVDA", tier=Tier.A)]
        # alerts_enabled=False so Slack is skipped.
        config = ConvexConfig(enabled=True, alerts_enabled=False)

        # Patch the LLM thesis generator to return a stub COMPLETED thesis
        # without making a real API call.
        from app.core.schemas import (
            ExitPlanThesis,
            LLMProvider as LLMProviderEnum,
            ThesisStatus,
            TradeThesis,
        )

        stub_thesis = TradeThesis(
            thesis_id="t-stub-1",
            evaluation_id="convex-test-NVDA",
            setup_summary="stub",
            thesis="stub",
            supporting_evidence=[],
            risks=[],
            invalidation_conditions=[],
            exit_plan=ExitPlanThesis(
                profit_target="", stop_loss="", time_exit=""
            ),
            llm_provider=LLMProviderEnum.ANTHROPIC,
            model_used="stub",
            tokens_used=0,
            status=ThesisStatus.COMPLETED,
        )

        with patch(
            "app.llm.generator.ThesisGenerator.generate_convex",
            new=AsyncMock(return_value=stub_thesis),
        ):
            await _emit_downstream_artifacts(finalised, config)

        # Position was created.
        position = await PaperPositionTable.get_by_evaluation_id("convex-test-NVDA")
        assert position is not None
        assert position.scanner_source == "CONVEX_TIER_A"

        # Thesis was persisted.
        from app.db.tables import TradeThesisTable
        thesis = await TradeThesisTable.get_by_evaluation_id("convex-test-NVDA")
        assert thesis is not None
        assert thesis.thesis_id == "t-stub-1"

    @pytest.mark.asyncio
    async def test_does_not_send_slack_when_alerts_disabled(self, fresh_dynamodb_client):
        finalised = [_make_finalised(ticker="TSLA", tier=Tier.A)]
        config = ConvexConfig(enabled=True, alerts_enabled=False)

        slack_calls: list[Any] = []

        async def _capturing_send(self, candidate):
            slack_calls.append(candidate)
            return True, None

        from app.core.schemas import (
            ExitPlanThesis,
            LLMProvider as LLMProviderEnum,
            ThesisStatus,
            TradeThesis,
        )
        stub_thesis = TradeThesis(
            thesis_id="t-stub-2",
            evaluation_id="convex-test-TSLA",
            setup_summary="",
            thesis="",
            supporting_evidence=[],
            risks=[],
            invalidation_conditions=[],
            exit_plan=ExitPlanThesis(
                profit_target="", stop_loss="", time_exit=""
            ),
            llm_provider=LLMProviderEnum.ANTHROPIC,
            model_used="",
            tokens_used=0,
            status=ThesisStatus.COMPLETED,
        )

        with patch(
            "app.llm.generator.ThesisGenerator.generate_convex",
            new=AsyncMock(return_value=stub_thesis),
        ), patch(
            "app.services.slack.SlackAlertService.send_convex_alert",
            new=_capturing_send,
        ):
            await _emit_downstream_artifacts(finalised, config)

        assert slack_calls == [], "Slack was called despite alerts_enabled=False"

    @pytest.mark.asyncio
    async def test_calls_slack_when_alerts_enabled(self, fresh_dynamodb_client):
        finalised = [_make_finalised(ticker="META", tier=Tier.A)]
        config = ConvexConfig(enabled=True, alerts_enabled=True)

        slack_calls: list[Any] = []

        async def _capturing_send(self, candidate):
            slack_calls.append(candidate)
            return True, None

        from app.core.schemas import (
            ExitPlanThesis,
            LLMProvider as LLMProviderEnum,
            ThesisStatus,
            TradeThesis,
        )
        stub_thesis = TradeThesis(
            thesis_id="t-stub-3",
            evaluation_id="convex-test-META",
            setup_summary="",
            thesis="",
            supporting_evidence=[],
            risks=[],
            invalidation_conditions=[],
            exit_plan=ExitPlanThesis(
                profit_target="", stop_loss="", time_exit=""
            ),
            llm_provider=LLMProviderEnum.ANTHROPIC,
            model_used="",
            tokens_used=0,
            status=ThesisStatus.COMPLETED,
        )

        with patch(
            "app.llm.generator.ThesisGenerator.generate_convex",
            new=AsyncMock(return_value=stub_thesis),
        ), patch(
            "app.services.slack.SlackAlertService.send_convex_alert",
            new=_capturing_send,
        ):
            await _emit_downstream_artifacts(finalised, config)

        assert len(slack_calls) == 1
        assert slack_calls[0].candidate.ticker == "META"

    @pytest.mark.asyncio
    async def test_one_failure_does_not_block_others(self, fresh_dynamodb_client):
        finalised = [
            _make_finalised(ticker="AMD", tier=Tier.A),
            _make_finalised(ticker="INTC", tier=Tier.A),
        ]
        config = ConvexConfig(enabled=True, alerts_enabled=False)

        async def _flaky_thesis(self, candidate):
            if candidate.candidate.ticker == "AMD":
                raise RuntimeError("simulated LLM failure")
            from app.core.schemas import (
                ExitPlanThesis,
                LLMProvider as LLMProviderEnum,
                ThesisStatus,
                TradeThesis,
            )
            return TradeThesis(
                thesis_id="t-stub-flaky",
                evaluation_id=candidate.decision.evaluation_id,
                setup_summary="",
                thesis="",
                supporting_evidence=[],
                risks=[],
                invalidation_conditions=[],
                exit_plan=ExitPlanThesis(
                    profit_target="", stop_loss="", time_exit=""
                ),
                llm_provider=LLMProviderEnum.ANTHROPIC,
                model_used="",
                tokens_used=0,
                status=ThesisStatus.COMPLETED,
            )

        with patch(
            "app.llm.generator.ThesisGenerator.generate_convex",
            new=_flaky_thesis,
        ):
            await _emit_downstream_artifacts(finalised, config)

        # Both positions should still be written despite the AMD thesis failure.
        amd = await PaperPositionTable.get_by_evaluation_id("convex-test-AMD")
        intc = await PaperPositionTable.get_by_evaluation_id("convex-test-INTC")
        assert amd is not None
        assert intc is not None
