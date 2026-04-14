"""Tests for the Nightly Scribe (Phase 1 intelligence agent)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.schemas import (
    Decision,
    DTEBucket,
    Evaluation,
    ExitReason,
    OptionType,
    PaperPosition,
    PipelineRun,
    PositionStatus,
    RunStatus,
    Verdict,
)
from app.db.tables import (
    DiaryTable,
    EvaluationTable,
    PaperPositionTable,
    PipelineRunTable,
    PolicyTable,
)
from app.intelligence.scribe import run_nightly_scribe
from app.llm.provider import LLMResponse


TARGET_DAY = date(2026, 4, 14)
TARGET_DAY_STR = TARGET_DAY.isoformat()
TARGET_TS = "2026-04-14T18:00:00+00:00"


SAMPLE_MARKDOWN = """## 1. **Headline**

AAPL and MSFT carried the day; three anomalies in the gate stage.

## 2. By the numbers
- Approves: 3
- Watches: 10
- Rejects: 200
- Closed: 2 (1 win, 1 loss)

## 3. What went right
AAPL closed +$45 on the TP1 target. Gate rejections held firm on TSLA.

## 4. What went wrong
NVDA hit the time exit with a $12 loss.

## 5. Questions for tomorrow
- Why did Stage 6 reject 91% of candidates?
- Are IV-percentile inputs stale for semis?
"""


def _make_evaluation(ticker: str, verdict: Verdict, evaluated_at: str) -> tuple[Evaluation, Decision]:
    ev = Evaluation(
        opportunity_id="opp-1",
        underlying_ticker=ticker,
        option_ticker=f"O:{ticker}260417C00150000",
        option_type=OptionType.CALL,
        expiration_date="2026-04-17",
        dte=3,
        strike=150.0,
        underlying_price=148.0,
        moneyness_pct=-1.33,
        bid=0.5,
        ask=0.6,
        mid=0.55,
        spread_abs=0.1,
        spread_pct=18.2,
        iv=0.45,
        delta=0.35,
        gamma=0.01,
        theta=-0.02,
        vega=0.05,
        open_interest=1000,
        volume=500,
        breakeven_price=150.55,
        required_move_pct=1.7,
        expected_move_pct=2.5,
        feasibility_ratio=1.47,
        time_adjusted_feasibility=1.2,
        dte_bucket=DTEBucket.A,
        rank_score=75.0,
        policy_version="v3.1.1",
        policy_hash="deadbeef",
        evaluated_at=evaluated_at,
    )
    dec = Decision(
        evaluation_id=ev.evaluation_id,
        verdict=verdict,
        final_score=75.0,
        premium_leverage_score=70.0,
        underlying_behavior_score=80.0,
        setup_quality_score=75.0,
        primary_reason_code="STRONG_SETUP",
        supporting_reason_codes=[],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v3.1.1",
    )
    return ev, dec


def _make_closed_position(
    ticker: str, pnl_dollars: float, exit_date: str
) -> PaperPosition:
    return PaperPosition(
        position_id=f"pos-{ticker}",
        evaluation_id=f"eval-{ticker}",
        option_ticker=f"O:{ticker}",
        entry_price=0.50,
        entry_date="2026-04-10",
        verdict_at_entry=Verdict.APPROVE,
        exit_price=0.95 if pnl_dollars > 0 else 0.10,
        exit_date=exit_date,
        exit_reason=ExitReason.PROFIT_TARGET if pnl_dollars > 0 else ExitReason.STOP_LOSS,
        current_price=0.95 if pnl_dollars > 0 else 0.10,
        current_pnl_pct=(pnl_dollars / 50.0) * 100,
        dollar_pnl=pnl_dollars,
        days_held=4,
        status=PositionStatus.CLOSED,
        underlying_ticker=ticker,
        scanner_source="BREAKOUT",
    )


async def _seed(day_str: str = TARGET_DAY_STR) -> None:
    # Pipeline run completed today
    run = PipelineRun(
        run_id="run-1",
        policy_version="v3.1.1",
        status=RunStatus.COMPLETED,
        started_at=f"{day_str}T18:00:00+00:00",
        completed_at=f"{day_str}T18:05:00+00:00",
        total_approves=3,
        total_watches=10,
        total_rejects=200,
    )
    await PipelineRunTable.put(run)

    # 3 APPROVEs, 1 WATCH, 2 REJECTs today
    for ticker in ("AAPL", "MSFT", "NVDA"):
        ev, dec = _make_evaluation(ticker, Verdict.APPROVE, TARGET_TS)
        await EvaluationTable.put(ev, dec)
    ev, dec = _make_evaluation("TSLA", Verdict.WATCH, TARGET_TS)
    await EvaluationTable.put(ev, dec)
    for ticker in ("META", "AMZN"):
        ev, dec = _make_evaluation(ticker, Verdict.REJECT, TARGET_TS)
        await EvaluationTable.put(ev, dec)

    # One win, one loss closed today
    await PaperPositionTable.put(_make_closed_position("AAPL", 45.0, day_str))
    await PaperPositionTable.put(_make_closed_position("NVDA", -12.0, day_str))


class TestRunNightlyScribe:

    @pytest.mark.asyncio
    async def test_writes_s3_and_dynamo_on_success(self, fresh_dynamodb_client):
        await _seed()

        fake_provider = type(
            "FakeProvider",
            (),
            {
                "generate": AsyncMock(
                    return_value=LLMResponse(
                        content=SAMPLE_MARKDOWN,
                        tokens_used=500,
                        model="claude-sonnet-4-5-20250929",
                        success=True,
                    )
                )
            },
        )()

        with patch("app.intelligence.scribe.put_markdown") as mock_put:
            mock_put.return_value = f"diary/{TARGET_DAY_STR}/nightly.md"
            entry = await run_nightly_scribe(
                target_date=TARGET_DAY, provider=fake_provider
            )

        # S3 write: called once with the expected key, and the body is the full markdown
        mock_put.assert_called_once()
        args, _kwargs = mock_put.call_args
        assert args[0] == f"diary/{TARGET_DAY_STR}/nightly.md"
        assert "Headline" in args[1]
        assert len(args[1]) > 100

        # Entry persisted to DynamoDB
        fetched = await DiaryTable.get(TARGET_DAY_STR)
        assert fetched is not None
        assert fetched.date == TARGET_DAY_STR
        assert fetched.s3_key == f"diary/{TARGET_DAY_STR}/nightly.md"
        assert fetched.summary  # non-empty
        assert len(fetched.summary) <= 200

        # Metrics contain the verdict/position counts we seeded
        assert fetched.metrics["approves"] == 3
        assert fetched.metrics["watches"] == 1
        assert fetched.metrics["rejects"] == 2
        assert fetched.metrics["closed_total"] == 2
        assert fetched.metrics["closed_wins"] == 1
        assert fetched.metrics["closed_losses"] == 1
        assert fetched.metrics["pipeline_runs"] == 1
        # Equality to entry returned from the function
        assert entry.s3_key == fetched.s3_key
        assert entry.metrics == fetched.metrics

    @pytest.mark.asyncio
    async def test_raises_when_llm_fails(self, fresh_dynamodb_client):
        fake_provider = type(
            "FakeProvider",
            (),
            {
                "generate": AsyncMock(
                    return_value=LLMResponse(
                        content="",
                        tokens_used=0,
                        model="claude-sonnet-4-5-20250929",
                        success=False,
                        error="API rate limit",
                    )
                )
            },
        )()

        with patch("app.intelligence.scribe.put_markdown") as mock_put:
            with pytest.raises(RuntimeError, match="API rate limit"):
                await run_nightly_scribe(
                    target_date=TARGET_DAY, provider=fake_provider
                )
        mock_put.assert_not_called()

        # Nothing persisted
        assert await DiaryTable.get(TARGET_DAY_STR) is None

    @pytest.mark.asyncio
    async def test_raises_when_llm_returns_empty_content(self, fresh_dynamodb_client):
        fake_provider = type(
            "FakeProvider",
            (),
            {
                "generate": AsyncMock(
                    return_value=LLMResponse(
                        content="   \n   ",
                        tokens_used=0,
                        model="claude-sonnet-4-5-20250929",
                        success=True,
                    )
                )
            },
        )()

        with patch("app.intelligence.scribe.put_markdown"):
            with pytest.raises(RuntimeError, match="empty content"):
                await run_nightly_scribe(
                    target_date=TARGET_DAY, provider=fake_provider
                )

    @pytest.mark.asyncio
    async def test_defaults_to_today_utc_when_target_date_not_given(
        self, fresh_dynamodb_client
    ):
        fake_provider = type(
            "FakeProvider",
            (),
            {
                "generate": AsyncMock(
                    return_value=LLMResponse(
                        content=SAMPLE_MARKDOWN,
                        tokens_used=500,
                        model="claude-sonnet-4-5-20250929",
                        success=True,
                    )
                )
            },
        )()

        with patch("app.intelligence.scribe.put_markdown") as mock_put:
            mock_put.return_value = "diary/x/nightly.md"
            entry = await run_nightly_scribe(provider=fake_provider)

        today = datetime.now(timezone.utc).date().isoformat()
        assert entry.date == today
