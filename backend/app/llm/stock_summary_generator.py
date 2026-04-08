"""Stock summary generator — orchestrates data fetching, prompt building, and LLM calls."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from app.core.schemas import LLMProvider as LLMProviderEnum
from app.core.schemas import MaterialEvent, StockSummary, StockSummaryStatus, ThesisConfig
from app.llm.provider import get_provider
from app.llm.rate_limiter import RateLimiter
from app.llm.stock_summary import (
    STOCK_SUMMARY_SYSTEM_PROMPT,
    StockSummaryInput,
    build_stock_summary_prompt,
    parse_stock_summary_response,
)

logger = logging.getLogger(__name__)


async def fetch_news_context(
    ticker: str,
    polygon_client: Any,
    finnhub_client: Optional[Any] = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Fetch and deduplicate news from Polygon and Finnhub.

    Args:
        ticker: Stock ticker symbol
        polygon_client: Initialized PolygonClient
        finnhub_client: Optional initialized FinnhubClient
        days: Number of days to look back

    Returns:
        Deduplicated list of news items sorted by recency
    """
    news_items: list[dict[str, Any]] = []

    # Fetch from Polygon
    try:
        polygon_news = await polygon_client.get_ticker_news(ticker, limit=10)
        for article in polygon_news:
            published = article.get("published_utc", "")[:10]
            news_items.append({
                "date": published,
                "title": article.get("title", ""),
                "source": article.get("publisher", {}).get("name", ""),
                "summary": article.get("description", ""),
            })
    except Exception as e:
        logger.warning(f"Failed to fetch Polygon news for {ticker}: {e}")

    # Fetch from Finnhub
    if finnhub_client:
        try:
            to_date = date.today()
            from_date = to_date - timedelta(days=days)
            finnhub_news = await finnhub_client.get_company_news(
                ticker, from_date, to_date
            )
            for article in finnhub_news[:10]:
                # Finnhub datetime is unix timestamp
                dt = article.get("datetime", 0)
                if isinstance(dt, (int, float)) and dt > 0:
                    from datetime import datetime

                    date_str = datetime.utcfromtimestamp(dt).strftime("%Y-%m-%d")
                else:
                    date_str = ""
                news_items.append({
                    "date": date_str,
                    "title": article.get("headline", ""),
                    "source": article.get("source", ""),
                    "summary": article.get("summary", ""),
                })
        except Exception as e:
            logger.warning(f"Failed to fetch Finnhub news for {ticker}: {e}")

    # Deduplicate by title similarity (exact match after lowering)
    seen_titles: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in news_items:
        title_key = item["title"].lower().strip()
        if title_key and title_key not in seen_titles:
            seen_titles.add(title_key)
            unique.append(item)

    # Sort by date descending, take top 10
    unique.sort(key=lambda x: x.get("date", ""), reverse=True)
    return unique[:10]


class StockSummaryGenerator:
    """Generates AI stock summaries using LLM providers."""

    def __init__(
        self,
        config: Optional[ThesisConfig] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        self._config = config or ThesisConfig()
        self._rate_limiter = rate_limiter or RateLimiter(
            max_daily_calls=self._config.max_daily_calls
        )
        self._provider: Optional[Any] = None
        if self._config.enabled:
            self._provider = get_provider("anthropic")

    async def generate(
        self,
        input_data: StockSummaryInput,
    ) -> StockSummary:
        """Generate a stock summary.

        Args:
            input_data: Assembled StockSummaryInput with all context

        Returns:
            StockSummary with COMPLETED, FAILED, or RATE_LIMITED status
        """
        ticker = input_data.ticker

        if not self._config.enabled:
            return self._create_failed(
                ticker, "Stock summary generation is disabled",
                status=StockSummaryStatus.GENERATING,
            )

        if not await self._rate_limiter.can_make_call():
            return self._create_failed(
                ticker,
                f"Daily rate limit reached ({self._config.max_daily_calls} calls/day)",
                status=StockSummaryStatus.RATE_LIMITED,
            )

        provider = self._provider
        if not provider or not provider.is_available():
            return self._create_failed(
                ticker, "No LLM provider available (check API keys)"
            )

        try:
            user_prompt = build_stock_summary_prompt(input_data)
            # Combine system + user prompt for provider compatibility
            prompt = STOCK_SUMMARY_SYSTEM_PROMPT + "\n\n---\n\n" + user_prompt

            logger.info(f"Generating stock summary for {ticker} using {provider.name}")
            response = await provider.generate(
                prompt=prompt,
                max_tokens=2000,
            )

            if not response.success:
                return self._create_failed(
                    ticker, f"LLM call failed: {response.error}"
                )

            try:
                data = parse_stock_summary_response(response.content)
            except ValueError as e:
                return self._create_failed(
                    ticker, f"Failed to parse LLM response: {e}"
                )

            await self._rate_limiter.record_call(response.tokens_used)

            # Build material events
            events = []
            for ev in data.get("material_events", []):
                events.append(MaterialEvent(
                    event=ev.get("event", ""),
                    date=ev.get("date", ""),
                    impact=ev.get("impact", ""),
                    severity=ev.get("severity", "MEDIUM"),
                ))

            return StockSummary(
                ticker=ticker,
                company_snapshot=data.get("company_snapshot", ""),
                sector_context=data.get("sector_context", ""),
                material_events=events,
                trading_considerations=data.get("trading_considerations", []),
                trade_impact_assessment=data.get("trade_impact_assessment", ""),
                risk_level=data.get("risk_level", ""),
                risk_level_rationale=data.get("risk_level_rationale", ""),
                llm_provider=LLMProviderEnum.ANTHROPIC,
                model_used=response.model,
                tokens_used=response.tokens_used,
                status=StockSummaryStatus.COMPLETED,
            )

        except Exception as e:
            logger.error(f"Stock summary generation failed for {ticker}: {e}")
            return self._create_failed(ticker, str(e))

    @staticmethod
    def _create_failed(
        ticker: str,
        error: str,
        status: StockSummaryStatus = StockSummaryStatus.FAILED,
    ) -> StockSummary:
        return StockSummary(
            ticker=ticker,
            status=status,
            error_message=error,
        )
