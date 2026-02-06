"""Unit tests for LLM module (Phase 10).

Tests cover:
- ThesisInput/ThesisOutput models
- Prompt building and parsing
- Rate limiter
- Generator integration
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.schemas import (
    Decision,
    DTEBucket,
    Evaluation,
    ExitPlanThesis,
    LLMProvider,
    LLMUsage,
    OptionType,
    PillarContributor,
    PillarId,
    PillarScore,
    QualityTier,
    ScannerTrigger,
    ScannerType,
    ThesisConfig,
    ThesisStatus,
    TradeThesis,
    Verdict,
)
from app.llm.models import (
    ContractData,
    PillarContributorData,
    ScannerTriggerData,
    ScoresData,
    ThesisInput,
    ThesisOutput,
    UnderlyingData,
    ExitPlanOutput,
)
from app.llm.prompt import build_thesis_prompt, parse_thesis_response
from app.llm.rate_limiter import RateLimiter
from app.llm.generator import ThesisGenerator
from app.llm.provider import LLMResponse


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def sample_underlying():
    return UnderlyingData(
        ticker="AAPL",
        price=175.50,
        sma20=172.30,
        sma50=168.45,
        return_5d=2.5,
        return_20d=5.8,
    )


@pytest.fixture
def sample_contract():
    return ContractData(
        option_type="CALL",
        strike=180.0,
        expiration="2025-02-21",
        dte=21,
        mid=4.50,
        iv=0.32,
        delta=0.45,
        theta=-0.08,
        gamma=0.025,
        vega=0.15,
        open_interest=1500,
        volume=250,
        spread_pct=3.5,
    )


@pytest.fixture
def sample_scores():
    return ScoresData(
        final=82.5,
        directional=85.0,
        volatility=78.0,
        structure=84.0,
    )


@pytest.fixture
def sample_pillar_contributors():
    return {
        "directional": [
            PillarContributorData(
                feature_name="trend_alignment",
                subscore=90,
                weight=0.30,
                weighted_contribution=27.0,
                raw_value="STRONG_UPTREND",
            ),
            PillarContributorData(
                feature_name="momentum",
                subscore=80,
                weight=0.25,
                weighted_contribution=20.0,
                raw_value=2.5,
            ),
        ],
        "volatility": [
            PillarContributorData(
                feature_name="iv_percentile",
                subscore=75,
                weight=0.25,
                weighted_contribution=18.75,
                raw_value=35,
            ),
        ],
        "structure": [
            PillarContributorData(
                feature_name="spread_score",
                subscore=88,
                weight=0.30,
                weighted_contribution=26.4,
                raw_value=3.5,
            ),
        ],
    }


@pytest.fixture
def sample_scanner_triggers():
    return [
        ScannerTriggerData(
            scanner_type="BREAKOUT",
            reason_codes=["ABOVE_20D_HIGH", "VOLUME_SPIKE"],
            metrics={"breakout_pct": 1.5, "volume_ratio": 2.3},
        ),
    ]


@pytest.fixture
def sample_thesis_input(
    sample_underlying,
    sample_contract,
    sample_scores,
    sample_pillar_contributors,
    sample_scanner_triggers,
):
    return ThesisInput(
        underlying=sample_underlying,
        contract=sample_contract,
        scores=sample_scores,
        pillar_contributors=sample_pillar_contributors,
        scanner_triggers=sample_scanner_triggers,
        policy_version="v2.1.0",
        quality_tier="TIER_1",
        evaluation_id="eval-123",
    )


@pytest.fixture
def sample_evaluation():
    return Evaluation(
        evaluation_id="eval-123",
        opportunity_id="opp-456",
        underlying_ticker="AAPL",
        option_ticker="O:AAPL250221C00180000",
        option_type=OptionType.CALL,
        expiration_date="2025-02-21",
        dte=21,
        strike=180.0,
        underlying_price=175.50,
        moneyness_pct=2.57,
        bid=4.30,
        ask=4.70,
        mid=4.50,
        spread_abs=0.40,
        spread_pct=8.9,
        iv=0.32,
        delta=0.45,
        gamma=0.025,
        theta=-0.08,
        vega=0.15,
        open_interest=1500,
        volume=250,
        breakeven_price=184.50,
        required_move_pct=5.13,
        expected_move_pct=6.8,
        feasibility_ratio=0.75,
        time_adjusted_feasibility=0.72,
        dte_bucket=DTEBucket.A,
        rank_score=85.0,
        policy_version="v2.1.0",
        policy_hash="abc123",
    )


@pytest.fixture
def sample_decision():
    return Decision(
        evaluation_id="eval-123",
        verdict=Verdict.APPROVE,
        quality_tier=QualityTier.TIER_1,
        final_score=82.5,
        directional_score=85.0,
        volatility_score=78.0,
        structure_score=84.0,
        primary_reason_code="APPROVED_TIER_1",
        supporting_reason_codes=["STRONG_TREND", "LOW_IV"],
        failed_gates=[],
        concentration_warnings=[],
        policy_version="v2.1.0",
    )


@pytest.fixture
def sample_pillar_scores():
    return [
        PillarScore(
            evaluation_id="eval-123",
            pillar_id=PillarId.DIRECTIONAL,
            score=85,
            contributors=[
                PillarContributor(
                    feature_name="trend_alignment",
                    subscore=90,
                    weight=0.30,
                    weighted_contribution=27.0,
                    raw_value="STRONG_UPTREND",
                    distance_from_neutral=40,
                ),
            ],
            tags=["STRONG_TREND"],
        ),
        PillarScore(
            evaluation_id="eval-123",
            pillar_id=PillarId.VOLATILITY,
            score=78,
            contributors=[
                PillarContributor(
                    feature_name="iv_percentile",
                    subscore=75,
                    weight=0.25,
                    weighted_contribution=18.75,
                    raw_value=35,
                    distance_from_neutral=15,
                ),
            ],
            tags=["LOW_IV"],
        ),
        PillarScore(
            evaluation_id="eval-123",
            pillar_id=PillarId.STRUCTURE,
            score=84,
            contributors=[
                PillarContributor(
                    feature_name="spread_score",
                    subscore=88,
                    weight=0.30,
                    weighted_contribution=26.4,
                    raw_value=3.5,
                    distance_from_neutral=38,
                ),
            ],
            tags=[],
        ),
    ]


@pytest.fixture
def sample_llm_response():
    return {
        "setup_summary": "AAPL is breaking out above its 20-day high with strong momentum.",
        "thesis": "This bullish call option on AAPL presents an attractive risk/reward setup. The stock is demonstrating strong directional momentum with price above both 20-day and 50-day SMAs. The breakout is supported by above-average volume, suggesting institutional participation.",
        "supporting_evidence": [
            "Price breaking above 20-day high with 2.3x normal volume",
            "Strong 5-day return of 2.5% indicates positive momentum",
            "IV percentile at 35% suggests relatively cheap options",
        ],
        "risks": [
            "Broader market weakness could drag down AAPL",
            "Earnings report in 3 weeks could introduce volatility",
        ],
        "invalidation_conditions": [
            "Price closes below the 20-day SMA at $172.30",
            "Volume dries up significantly below average",
        ],
        "exit_plan": {
            "profit_target": "Exit at 50% gain or when AAPL reaches $185",
            "stop_loss": "Exit if position loses 50% or AAPL breaks below $170",
            "time_exit": "Close position when DTE reaches 5 regardless of P&L",
        },
    }


# ============================================================================
# Test ThesisInput Model
# ============================================================================


class TestThesisInputModel:
    """Tests for ThesisInput dataclass."""

    def test_to_dict(self, sample_thesis_input):
        """Test conversion to dictionary."""
        data = sample_thesis_input.to_dict()
        
        assert data["underlying"]["ticker"] == "AAPL"
        assert data["underlying"]["price"] == 175.50
        assert data["contract"]["type"] == "CALL"
        assert data["contract"]["strike"] == 180.0
        assert data["scores"]["final"] == 82.5
        assert len(data["pillar_contributors"]["directional"]) == 2
        assert len(data["scanner_triggers"]) == 1
        assert data["policy_version"] == "v2.1.0"
        assert data["quality_tier"] == "TIER_1"

    def test_pillar_contributors_format(self, sample_thesis_input):
        """Test pillar contributors are formatted correctly."""
        data = sample_thesis_input.to_dict()
        
        directional_contrib = data["pillar_contributors"]["directional"][0]
        assert directional_contrib["feature"] == "trend_alignment"
        assert directional_contrib["subscore"] == 90
        assert directional_contrib["weight"] == 0.30
        assert directional_contrib["contribution"] == 27.0
        assert directional_contrib["value"] == "STRONG_UPTREND"

    def test_scanner_triggers_format(self, sample_thesis_input):
        """Test scanner triggers are formatted correctly."""
        data = sample_thesis_input.to_dict()
        
        trigger = data["scanner_triggers"][0]
        assert trigger["type"] == "BREAKOUT"
        assert "ABOVE_20D_HIGH" in trigger["reasons"]
        assert trigger["metrics"]["breakout_pct"] == 1.5


# ============================================================================
# Test ThesisOutput Model
# ============================================================================


class TestThesisOutputModel:
    """Tests for ThesisOutput dataclass."""

    def test_from_dict(self, sample_llm_response):
        """Test parsing from dictionary."""
        output = ThesisOutput.from_dict(sample_llm_response)
        
        assert "AAPL is breaking out" in output.setup_summary
        assert "bullish call option" in output.thesis
        assert len(output.supporting_evidence) == 3
        assert len(output.risks) == 2
        assert len(output.invalidation_conditions) == 2
        assert output.exit_plan.profit_target == "Exit at 50% gain or when AAPL reaches $185"
        assert output.exit_plan.stop_loss == "Exit if position loses 50% or AAPL breaks below $170"
        assert output.exit_plan.time_exit == "Close position when DTE reaches 5 regardless of P&L"

    def test_to_dict(self, sample_llm_response):
        """Test conversion back to dictionary."""
        output = ThesisOutput.from_dict(sample_llm_response)
        result = output.to_dict()
        
        assert result["setup_summary"] == sample_llm_response["setup_summary"]
        assert result["thesis"] == sample_llm_response["thesis"]
        assert result["exit_plan"]["profit_target"] == sample_llm_response["exit_plan"]["profit_target"]

    def test_from_dict_with_empty_lists(self):
        """Test parsing handles empty lists."""
        data = {
            "setup_summary": "Summary",
            "thesis": "Thesis text",
            "supporting_evidence": [],
            "risks": [],
            "invalidation_conditions": [],
            "exit_plan": {
                "profit_target": "50%",
                "stop_loss": "50%",
                "time_exit": "5 DTE",
            },
        }
        
        output = ThesisOutput.from_dict(data)
        assert output.supporting_evidence == []
        assert output.risks == []


# ============================================================================
# Test Prompt Building
# ============================================================================


class TestPromptBuilding:
    """Tests for prompt building functions."""

    def test_build_thesis_prompt_contains_data(self, sample_thesis_input):
        """Test prompt contains all required data."""
        prompt = build_thesis_prompt(sample_thesis_input)
        
        assert "AAPL" in prompt
        assert "175.50" in prompt  # Price
        assert "CALL" in prompt
        assert "180" in prompt  # Strike
        assert "82.5" in prompt  # Final score
        assert "trend_alignment" in prompt
        assert "BREAKOUT" in prompt
        assert "v2.1.0" in prompt

    def test_build_thesis_prompt_contains_instructions(self, sample_thesis_input):
        """Test prompt contains proper instructions."""
        prompt = build_thesis_prompt(sample_thesis_input)
        
        assert "expert options trading analyst" in prompt
        assert "trade thesis" in prompt.lower()
        assert "JSON" in prompt
        assert "setup_summary" in prompt
        assert "exit_plan" in prompt


# ============================================================================
# Test Response Parsing
# ============================================================================


class TestResponseParsing:
    """Tests for LLM response parsing."""

    def test_parse_valid_json(self, sample_llm_response):
        """Test parsing valid JSON response."""
        response = json.dumps(sample_llm_response)
        result = parse_thesis_response(response)
        
        assert result["setup_summary"] == sample_llm_response["setup_summary"]
        assert result["thesis"] == sample_llm_response["thesis"]

    def test_parse_json_with_markdown_code_block(self, sample_llm_response):
        """Test parsing JSON wrapped in markdown code block."""
        response = f"```json\n{json.dumps(sample_llm_response)}\n```"
        result = parse_thesis_response(response)
        
        assert result["setup_summary"] == sample_llm_response["setup_summary"]

    def test_parse_invalid_json_raises(self):
        """Test invalid JSON raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_thesis_response("not valid json")

    def test_parse_missing_field_raises(self):
        """Test missing required field raises ValueError."""
        incomplete = {"setup_summary": "Summary"}  # Missing other fields
        
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_thesis_response(json.dumps(incomplete))

    def test_parse_missing_exit_plan_field_raises(self):
        """Test missing exit_plan field raises ValueError."""
        data = {
            "setup_summary": "Summary",
            "thesis": "Thesis",
            "supporting_evidence": [],
            "risks": [],
            "invalidation_conditions": [],
            "exit_plan": {"profit_target": "50%"},  # Missing stop_loss and time_exit
        }
        
        with pytest.raises(ValueError, match="Missing exit_plan fields"):
            parse_thesis_response(json.dumps(data))


# ============================================================================
# Test Rate Limiter
# ============================================================================


class TestRateLimiter:
    """Tests for rate limiter."""

    @pytest.mark.asyncio
    async def test_can_make_call_under_limit(self):
        """Test can_make_call returns True when under limit."""
        limiter = RateLimiter(max_daily_calls=50)
        assert await limiter.can_make_call() is True

    @pytest.mark.asyncio
    async def test_can_make_call_at_limit(self):
        """Test can_make_call returns False when at limit."""
        limiter = RateLimiter(max_daily_calls=2)
        
        # Make 2 calls
        await limiter.record_call(100)
        await limiter.record_call(100)
        
        # Third call should be denied
        assert await limiter.can_make_call() is False

    @pytest.mark.asyncio
    async def test_record_call_increments_count(self):
        """Test record_call increments the usage count."""
        limiter = RateLimiter(max_daily_calls=50)
        
        stats_before = await limiter.get_usage_stats()
        assert stats_before["calls_made"] == 0
        
        await limiter.record_call(100)
        
        stats_after = await limiter.get_usage_stats()
        assert stats_after["calls_made"] == 1
        assert stats_after["tokens_used"] == 100

    @pytest.mark.asyncio
    async def test_get_remaining_calls(self):
        """Test get_remaining_calls returns correct count."""
        limiter = RateLimiter(max_daily_calls=10)
        
        assert await limiter.get_remaining_calls() == 10
        
        await limiter.record_call(50)
        assert await limiter.get_remaining_calls() == 9

    @pytest.mark.asyncio
    async def test_usage_stats(self):
        """Test get_usage_stats returns complete stats."""
        limiter = RateLimiter(max_daily_calls=50)
        
        await limiter.record_call(100)
        await limiter.record_call(200)
        
        stats = await limiter.get_usage_stats()
        
        assert stats["calls_made"] == 2
        assert stats["tokens_used"] == 300
        assert stats["remaining"] == 48
        assert stats["limit"] == 50


# ============================================================================
# Test Generator
# ============================================================================


class TestThesisGenerator:
    """Tests for ThesisGenerator."""

    def test_build_input(
        self,
        sample_evaluation,
        sample_decision,
        sample_pillar_scores,
    ):
        """Test building ThesisInput from evaluation data."""
        config = ThesisConfig(enabled=False)  # Disable to avoid provider init
        generator = ThesisGenerator(config=config)
        
        triggers = [
            ScannerTrigger(
                scanner_type=ScannerType.BREAKOUT,
                reason_codes=["ABOVE_20D_HIGH"],
                metrics={"breakout_pct": 1.5},
                triggered_at=datetime.now(timezone.utc).isoformat(),
            )
        ]
        
        input_data = generator.build_input(
            evaluation=sample_evaluation,
            decision=sample_decision,
            pillar_scores=sample_pillar_scores,
            scanner_triggers=triggers,
            features={"sma20": 172.30, "sma50": 168.45},
        )
        
        assert input_data.underlying.ticker == "AAPL"
        assert input_data.underlying.price == 175.50
        assert input_data.underlying.sma20 == 172.30
        assert input_data.contract.option_type == "CALL"
        assert input_data.contract.strike == 180.0
        assert input_data.scores.final == 82.5
        assert len(input_data.pillar_contributors) == 3
        assert len(input_data.scanner_triggers) == 1
        assert input_data.quality_tier == "TIER_1"

    @pytest.mark.asyncio
    async def test_generate_non_approve_returns_failed(
        self,
        sample_evaluation,
        sample_pillar_scores,
    ):
        """Test generate returns failed thesis for non-APPROVE verdict."""
        config = ThesisConfig(enabled=True)
        generator = ThesisGenerator(config=config)
        
        # Decision with WATCH verdict
        watch_decision = Decision(
            evaluation_id="eval-123",
            verdict=Verdict.WATCH,
            quality_tier=None,
            final_score=68.0,
            directional_score=70.0,
            volatility_score=65.0,
            structure_score=69.0,
            primary_reason_code="WATCH_SCORE",
            supporting_reason_codes=[],
            failed_gates=[],
            concentration_warnings=[],
            policy_version="v2.1.0",
        )
        
        result = await generator.generate(
            evaluation=sample_evaluation,
            decision=watch_decision,
            pillar_scores=sample_pillar_scores,
            scanner_triggers=[],
        )
        
        assert result.status == ThesisStatus.FAILED
        assert "APPROVE verdicts" in result.error_message

    @pytest.mark.asyncio
    async def test_generate_disabled_returns_pending(
        self,
        sample_evaluation,
        sample_decision,
        sample_pillar_scores,
    ):
        """Test generate returns pending when disabled."""
        config = ThesisConfig(enabled=False)
        generator = ThesisGenerator(config=config)
        
        result = await generator.generate(
            evaluation=sample_evaluation,
            decision=sample_decision,
            pillar_scores=sample_pillar_scores,
            scanner_triggers=[],
        )
        
        assert result.status == ThesisStatus.PENDING
        assert "disabled" in result.error_message

    @pytest.mark.asyncio
    async def test_generate_rate_limited(
        self,
        sample_evaluation,
        sample_decision,
        sample_pillar_scores,
    ):
        """Test generate returns rate limited when quota exhausted."""
        config = ThesisConfig(enabled=True, max_daily_calls=0)
        generator = ThesisGenerator(config=config)
        
        result = await generator.generate(
            evaluation=sample_evaluation,
            decision=sample_decision,
            pillar_scores=sample_pillar_scores,
            scanner_triggers=[],
        )
        
        assert result.status == ThesisStatus.RATE_LIMITED
        assert "rate limit" in result.error_message.lower()


# ============================================================================
# Test Schema Models
# ============================================================================


class TestSchemaModels:
    """Tests for Pydantic schema models."""

    def test_trade_thesis_creation(self):
        """Test TradeThesis model creation."""
        thesis = TradeThesis(
            evaluation_id="eval-123",
            setup_summary="Test summary",
            thesis="Test thesis",
            supporting_evidence=["Evidence 1", "Evidence 2"],
            risks=["Risk 1"],
            invalidation_conditions=["Condition 1"],
            exit_plan=ExitPlanThesis(
                profit_target="50%",
                stop_loss="50%",
                time_exit="5 DTE",
            ),
            llm_provider=LLMProvider.ANTHROPIC,
            model_used="claude-sonnet-4-20250514",
            tokens_used=500,
            status=ThesisStatus.COMPLETED,
        )
        
        assert thesis.evaluation_id == "eval-123"
        assert thesis.status == ThesisStatus.COMPLETED
        assert thesis.llm_provider == LLMProvider.ANTHROPIC
        assert len(thesis.supporting_evidence) == 2

    def test_thesis_config_defaults(self):
        """Test ThesisConfig default values."""
        config = ThesisConfig()
        
        assert config.enabled is True
        assert config.max_daily_calls == 50
        assert config.output_token_limit == 1000
        assert config.preferred_provider == LLMProvider.ANTHROPIC
        assert config.fallback_enabled is True

    def test_llm_usage_creation(self):
        """Test LLMUsage model creation."""
        usage = LLMUsage(
            date="2025-01-30",
            calls_made=25,
            tokens_used=12500,
        )
        
        assert usage.date == "2025-01-30"
        assert usage.calls_made == 25
        assert usage.tokens_used == 12500

    def test_trade_thesis_to_dynamodb(self):
        """Test TradeThesis conversion to DynamoDB item."""
        thesis = TradeThesis(
            evaluation_id="eval-123",
            setup_summary="Test",
            thesis="Test",
            supporting_evidence=[],
            risks=[],
            invalidation_conditions=[],
            exit_plan=ExitPlanThesis(
                profit_target="50%",
                stop_loss="50%",
                time_exit="5 DTE",
            ),
            llm_provider=LLMProvider.ANTHROPIC,
            model_used="test-model",
            tokens_used=100,
            status=ThesisStatus.COMPLETED,
        )
        
        item = thesis.to_dynamodb_item()
        
        assert item["evaluation_id"] == "eval-123"
        assert item["status"] == "COMPLETED"
        assert item["llm_provider"] == "anthropic"
