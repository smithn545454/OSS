"""Stage 3: Contract Selection.

Selects multiple contracts per ticker for full evaluation.

Implements the 5-step selection pipeline from Section 12.3 of OSS_Complete_Requirements.md:
1. DTE Filter - bucket classification
2. Delta Band Filter - CALL 0.20-0.75, PUT -0.75 to -0.20
3. Liquidity Baseline Filters - OI, volume, spread, mid price
4. Moneyness Filter - restrict ITM/OTM range
5. Ranking + Top-K Selection - select best contracts per bucket/side
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.schemas import (
    ContractSelectionConfig,
    DTEBucket,
    Opportunity,
    OptionType,
)
from app.selection.ranking import RankingCalculator, RankingScores
from app.selection.telemetry import (
    BucketStats,
    SelectedContract,
    SelectionTelemetry,
)
from app.services.polygon import PolygonClient

logger = logging.getLogger(__name__)


@dataclass
class ContractCandidate:
    """A candidate contract for selection."""

    option_ticker: str
    underlying_ticker: str
    option_type: OptionType
    expiration_date: str
    dte: int
    strike: float
    underlying_price: float
    bid: float
    ask: float
    mid: float
    spread_abs: float
    spread_pct: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    open_interest: int
    volume: int
    moneyness_pct: float
    dte_bucket: DTEBucket
    ranking_scores: Optional[RankingScores] = None

    @property
    def rank_score(self) -> float:
        """Get the ranking score."""
        return self.ranking_scores.rank_score if self.ranking_scores else 0.0


@dataclass
class SelectionResult:
    """Result of contract selection for all opportunities."""

    opportunities_processed: int
    total_contracts_evaluated: int
    total_contracts_selected: int
    selected_candidates: list[ContractCandidate]
    telemetry: SelectionTelemetry
    errors: list[str] = field(default_factory=list)


class ContractSelector:
    """Selects option contracts for evaluation.

    Per Section 12.3, for each ticker, for each DTE bucket (A/B/C/D),
    for BOTH sides (CALL and PUT):
    1. Filter by DTE range
    2. Filter by delta band
    3. Filter by liquidity baselines
    4. Filter by moneyness
    5. Rank and select top K
    """

    # DTE Bucket definitions per Section 12.2
    DTE_BUCKETS = {
        DTEBucket.A: (7, 21),    # Short-term
        DTEBucket.B: (22, 45),   # Medium-term
        DTEBucket.C: (46, 75),   # Intermediate
        DTEBucket.D: (76, 120),  # Long-term
    }

    def __init__(
        self,
        config: ContractSelectionConfig,
        polygon_client: Optional[PolygonClient] = None,
        data_provider: Optional[Any] = None,
        as_of_date: Optional[date] = None,
    ) -> None:
        """Initialize the contract selector.

        Args:
            config: Contract selection configuration from policy
            polygon_client: Optional Polygon client
            data_provider: Optional DataProvider for unified data access (backtest mode)
            as_of_date: Target date for backtesting (defaults to today)
        """
        self._config = config
        self._polygon: Optional[PolygonClient] = polygon_client
        self._data_provider = data_provider
        self._as_of_date = as_of_date
        self._ranking = RankingCalculator(
            target_delta_call=config.target_delta_call,
            target_delta_put=config.target_delta_put,
            weight_liquidity=config.rank_weight_liquidity,
            weight_delta=config.rank_weight_delta,
            weight_spread=config.rank_weight_spread,
        )
        self._telemetry = SelectionTelemetry()
        # Greeks source tracking
        self._polygon_greeks_count = 0
        self._bs_fallback_count = 0

    def set_polygon_client(self, client: PolygonClient) -> None:
        """Set the Polygon client for data fetching."""
        self._polygon = client

    def set_data_provider(self, provider: Any, as_of_date: Optional[date] = None) -> None:
        """Set the DataProvider for unified data access."""
        self._data_provider = provider
        if as_of_date is not None:
            self._as_of_date = as_of_date

    def _dte_bucket_for(self, dte: int) -> Optional[DTEBucket]:
        """Determine the DTE bucket for a given DTE value."""
        for bucket in DTEBucket:
            bucket_range = self._get_bucket_range(bucket)
            if bucket_range and bucket_range[0] <= dte <= bucket_range[1]:
                return bucket
        return None

    def _force_include_from_chain(
        self,
        ticker: str,
        underlying_price: float,
        chain: list[dict[str, Any]],
        force_tickers: set[str],
        already_selected: set[str],
    ) -> list[ContractCandidate]:
        """Find force-include contracts in the chain and build candidates.

        These are contracts with existing APPROVE evaluations that must be
        re-evaluated with fresh prices even if they wouldn't normally be
        selected by the ranking pipeline.
        """
        today = datetime.now(timezone.utc)
        forced: list[ContractCandidate] = []
        for contract_data in chain:
            option_ticker = contract_data.get("details", {}).get("ticker", "")
            if option_ticker not in force_tickers or option_ticker in already_selected:
                continue
            candidate = self._parse_contract(
                contract_data, ticker, underlying_price, today
            )
            if candidate is None:
                continue
            bucket = self._dte_bucket_for(candidate.dte)
            if bucket is None:
                continue
            candidate = ContractCandidate(**{**candidate.__dict__, "dte_bucket": bucket})
            forced.append(candidate)
            logger.info(
                f"[SELECT] {ticker}: force-included {option_ticker} "
                f"(mid=${candidate.mid:.2f}, dte={candidate.dte})"
            )
        return forced

    async def select_contracts(
        self,
        opportunities: list[Opportunity],
        force_include_contracts: Optional[set[str]] = None,
    ) -> SelectionResult:
        """Select contracts for all opportunities.

        Args:
            opportunities: List of filtered opportunities from Stage 2
            force_include_contracts: Optional set of option_ticker symbols to
                force-include in the output (for re-evaluating existing APPROVEs
                with fresh prices). These are looked up in the already-fetched
                options chain — no extra API calls.

        Returns:
            SelectionResult with selected candidates and telemetry
        """
        if not self._data_provider and not self._polygon:
            raise RuntimeError("No data source available (set DataProvider or Polygon client)")

        self._telemetry = SelectionTelemetry()
        all_candidates: list[ContractCandidate] = []
        errors: list[str] = []

        # Get unique tickers
        tickers = list(set(opp.underlying_ticker for opp in opportunities))
        logger.info(
            f"Selecting contracts for {len(opportunities)} opportunities "
            f"across {len(tickers)} tickers"
        )

        # Calculate date range for options chain fetch
        min_dte = min(
            self._config.dte_buckets.get("A", self._config.dte_buckets["A"]).min_dte
            if "A" in self._config.dte_buckets
            else 7,
            7,
        )
        max_dte = max(
            self._config.dte_buckets.get("D", self._config.dte_buckets["D"]).max_dte
            if "D" in self._config.dte_buckets
            else 120,
            120,
        )

        effective_date = self._as_of_date or datetime.now(timezone.utc).date()
        today = effective_date
        exp_gte = (today + timedelta(days=min_dte)).strftime("%Y-%m-%d")
        exp_lte = (today + timedelta(days=max_dte)).strftime("%Y-%m-%d")

        # Get previous closes for underlying prices
        if self._data_provider:
            snapshots = await self._data_provider.get_stock_snapshots_batch(
                tickers, as_of=effective_date,
            )
            # Convert StockSnapshot to dict format expected by process_ticker
            prev_closes: dict[str, Any] = {
                t: {"c": s.close} for t, s in snapshots.items()
            }
        else:
            prev_closes = await self._polygon.get_previous_close_batch(tickers)

        # Process tickers in PARALLEL for better performance
        # This changes from O(n * pages) sequential to O(pages) with n concurrent
        async def process_ticker(ticker: str) -> tuple[str, list[ContractCandidate], list[str]]:
            """Process a single ticker - fetch chain and select contracts."""
            ticker_candidates: list[ContractCandidate] = []
            ticker_errors: list[str] = []

            try:
                # Get underlying price
                prev_close = prev_closes.get(ticker)
                if not prev_close:
                    ticker_errors.append(f"No price data for {ticker}")
                    return ticker, ticker_candidates, ticker_errors

                underlying_price = prev_close.get("c", 0.0)
                if underlying_price <= 0:
                    ticker_errors.append(f"Invalid price for {ticker}: {underlying_price}")
                    return ticker, ticker_candidates, ticker_errors

                # Fetch options chain: DataProvider or Polygon
                if self._data_provider:
                    chain = await self._data_provider.get_options_chain(
                        ticker, as_of=effective_date,
                        min_dte=min_dte, max_dte=max_dte,
                    )
                else:
                    chain = await self._polygon.get_options_chain(
                        ticker,
                        expiration_date_gte=exp_gte,
                        expiration_date_lte=exp_lte,
                    )

                if not chain:
                    ticker_errors.append(f"No options chain for {ticker}")
                    return ticker, ticker_candidates, ticker_errors

                # Select contracts for this ticker
                candidates = await self._select_for_ticker(
                    ticker,
                    underlying_price,
                    chain,
                )

                ticker_candidates.extend(candidates)

                # Force-include existing APPROVE contracts not already selected
                if force_include_contracts:
                    selected_tickers = {c.option_ticker for c in ticker_candidates}
                    force_for_ticker = {
                        ot for ot in force_include_contracts
                        if ot.startswith(f"O:{ticker}") and ot not in selected_tickers
                    }
                    if force_for_ticker:
                        forced = self._force_include_from_chain(
                            ticker, underlying_price, chain,
                            force_for_ticker, selected_tickers,
                        )
                        ticker_candidates.extend(forced)

            except Exception as e:
                error_msg = f"Error selecting contracts for {ticker}: {e}"
                logger.error(error_msg)
                ticker_errors.append(error_msg)

            return ticker, ticker_candidates, ticker_errors

        # Execute all ticker processing in parallel
        tasks = [process_ticker(ticker) for ticker in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results from all tickers
        for result in results:
            if isinstance(result, Exception):
                errors.append(f"Ticker processing failed: {result}")
            else:
                ticker, candidates, ticker_errors = result
                all_candidates.extend(candidates)
                errors.extend(ticker_errors)

        total_selected = len(all_candidates)
        logger.info(
            f"Contract selection complete: {len(tickers)} tickers, "
            f"{total_selected} contracts selected, "
            f"greeks_source: polygon={self._polygon_greeks_count} "
            f"bs_fallback={self._bs_fallback_count}"
        )

        return SelectionResult(
            opportunities_processed=len(opportunities),
            total_contracts_evaluated=self._telemetry._total_contracts_evaluated,
            total_contracts_selected=total_selected,
            selected_candidates=all_candidates,
            telemetry=self._telemetry,
            errors=errors,
        )

    async def _select_for_ticker(
        self,
        ticker: str,
        underlying_price: float,
        chain: list[dict[str, Any]],
    ) -> list[ContractCandidate]:
        """Select contracts for a single ticker.

        Args:
            ticker: Underlying ticker symbol
            underlying_price: Current underlying price
            chain: Options chain data from Polygon

        Returns:
            List of selected ContractCandidate objects
        """
        # Start telemetry for this ticker
        self._telemetry.start_ticker(ticker, underlying_price, len(chain))
        logger.info(
            f"[SELECT] {ticker}: chain={len(chain)} contracts, price=${underlying_price:.2f}"
        )

        # Log first contract structure for diagnostics
        if chain:
            c = chain[0]
            d = c.get("day", {})
            q = c.get("last_quote", {})
            g = c.get("greeks", {})
            det = c.get("details", {})
            logger.info(
                f"[SELECT] {ticker} sample contract: "
                f"ticker={det.get('ticker')}, type={det.get('contract_type')}, "
                f"strike={det.get('strike_price')}, exp={det.get('expiration_date')}, "
                f"day_keys={list(d.keys())}, "
                f"bid={d.get('last_bid', 'MISSING')}/{q.get('bid', 'MISSING')}, "
                f"ask={d.get('last_ask', 'MISSING')}/{q.get('ask', 'MISSING')}, "
                f"greeks_delta={g.get('delta', 'MISSING')}, "
                f"oi={c.get('open_interest', 'MISSING')}, "
                f"iv={g.get('implied_volatility', 'MISSING')}/{c.get('implied_volatility', 'MISSING')}"
            )

        all_selected: list[ContractCandidate] = []

        # Process each DTE bucket
        for bucket in DTEBucket:
            bucket_range = self._get_bucket_range(bucket)
            if not bucket_range:
                continue

            min_dte, max_dte = bucket_range

            # Process both sides
            for side in [OptionType.CALL, OptionType.PUT]:
                candidates = self._select_for_bucket_side(
                    ticker=ticker,
                    underlying_price=underlying_price,
                    chain=chain,
                    bucket=bucket,
                    min_dte=min_dte,
                    max_dte=max_dte,
                    side=side,
                )

                all_selected.extend(candidates)

        return all_selected

    def _get_bucket_range(self, bucket: DTEBucket) -> Optional[tuple[int, int]]:
        """Get the DTE range for a bucket from config.

        Args:
            bucket: The DTE bucket

        Returns:
            Tuple of (min_dte, max_dte) or None if not configured
        """
        bucket_key = bucket.value
        if bucket_key in self._config.dte_buckets:
            range_config = self._config.dte_buckets[bucket_key]
            return (range_config.min_dte, range_config.max_dte)
        return self.DTE_BUCKETS.get(bucket)

    def _select_for_bucket_side(
        self,
        ticker: str,
        underlying_price: float,
        chain: list[dict[str, Any]],
        bucket: DTEBucket,
        min_dte: int,
        max_dte: int,
        side: OptionType,
    ) -> list[ContractCandidate]:
        """Select contracts for a specific bucket and side.

        Implements the 5-step selection pipeline:
        1. DTE Filter
        2. Delta Band Filter
        3. Liquidity Baseline Filters
        4. Moneyness Filter
        5. Ranking + Top-K Selection

        Args:
            ticker: Underlying ticker symbol
            underlying_price: Current underlying price
            chain: Options chain data
            bucket: DTE bucket
            min_dte: Minimum DTE for bucket
            max_dte: Maximum DTE for bucket
            side: Option type (CALL or PUT)

        Returns:
            List of selected ContractCandidate objects
        """
        today = self._as_of_date or datetime.now(timezone.utc).date()
        stats = BucketStats(bucket=bucket.value, side=side.value)

        # Parse contracts and apply Step 1: DTE Filter
        dte_filtered: list[ContractCandidate] = []
        parse_fail_count = 0
        side_mismatch_count = 0
        dte_mismatch_count = 0

        for contract_data in chain:
            candidate = self._parse_contract(
                contract_data,
                ticker,
                underlying_price,
                today,
            )

            if candidate is None:
                parse_fail_count += 1
                continue

            # Filter by side
            if candidate.option_type != side:
                side_mismatch_count += 1
                continue

            # Step 1: DTE Filter
            dte_mismatch_count += 1
            if min_dte <= candidate.dte <= max_dte:
                dte_mismatch_count -= 1  # Undo: it passed
                candidate = ContractCandidate(
                    **{**candidate.__dict__, "dte_bucket": bucket}
                )
                dte_filtered.append(candidate)

        stats.contracts_in_dte_range = len(dte_filtered)

        # Step 2: Delta Band Filter
        delta_filtered = self._filter_delta_band(dte_filtered, side)
        stats.survived_delta_filter = len(delta_filtered)

        # Step 3: Liquidity Baseline Filters
        liquidity_filtered = self._filter_liquidity(delta_filtered)
        stats.survived_liquidity_filter = len(liquidity_filtered)

        # Step 4: Moneyness Filter
        moneyness_filtered = self._filter_moneyness(liquidity_filtered, side)
        stats.survived_moneyness_filter = len(moneyness_filtered)

        # Step 5: Ranking + Top-K Selection
        selected = self._rank_and_select(moneyness_filtered, side)
        stats.selected_count = len(selected)

        # Diagnostic logging for selection pipeline
        if len(dte_filtered) > 0 or bucket == DTEBucket.B:
            logger.info(
                f"[SELECT] {ticker} {bucket.value}/{side.value}: "
                f"dte={len(dte_filtered)} -> delta={len(delta_filtered)} -> "
                f"liq={len(liquidity_filtered)} -> money={len(moneyness_filtered)} -> "
                f"top_k={len(selected)}"
            )

        # Record selected contracts in telemetry
        for candidate in selected:
            stats.selected_contracts.append(
                SelectedContract(
                    option_ticker=candidate.option_ticker,
                    strike=candidate.strike,
                    dte=candidate.dte,
                    delta=candidate.delta,
                    rank_score=candidate.rank_score,
                    open_interest=candidate.open_interest,
                    volume=candidate.volume,
                    spread_pct=candidate.spread_pct,
                    mid=candidate.mid,
                )
            )

        # Record bucket stats
        self._telemetry.record_bucket_stats(ticker, stats)

        return selected

    def _parse_contract(
        self,
        contract_data: dict[str, Any],
        ticker: str,
        underlying_price: float,
        today: datetime,
    ) -> Optional[ContractCandidate]:
        """Parse a contract from Polygon chain data.

        Args:
            contract_data: Contract data from Polygon
            ticker: Underlying ticker
            underlying_price: Current underlying price
            today: Current date

        Returns:
            ContractCandidate or None if invalid/incomplete data
        """
        try:
            details = contract_data.get("details", {})
            day = contract_data.get("day", {})
            greeks = contract_data.get("greeks") or {}

            # Extract required fields
            option_ticker = details.get("ticker")
            contract_type = details.get("contract_type", "").upper()
            strike = details.get("strike_price", 0)
            expiration = details.get("expiration_date", "")

            if not all([option_ticker, contract_type, strike, expiration]):
                return None

            # Parse option type
            if contract_type == "CALL":
                option_type = OptionType.CALL
            elif contract_type == "PUT":
                option_type = OptionType.PUT
            else:
                return None

            # Calculate DTE
            try:
                exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
                dte = (exp_date - today.date() if hasattr(today, "date") else exp_date - today).days
            except (ValueError, TypeError):
                return None

            if dte < 0:
                return None

            # Extract price data — try multiple sources
            quote = contract_data.get("last_quote", {})
            bid = (
                day.get("last_bid", 0)
                or quote.get("bid", 0)
                or 0
            )
            ask = (
                day.get("last_ask", 0)
                or quote.get("ask", 0)
                or 0
            )

            # Fallback for contracts without quote data (illiquid options)
            # Most contracts will have real bid/ask from last_quote after plan upgrade
            if bid <= 0 or ask <= 0:
                day_close = day.get("close", 0) or 0
                if day_close > 0:
                    half_spread = day_close * 0.025
                    bid = day_close - half_spread
                    ask = day_close + half_spread
                else:
                    return None

            mid = (bid + ask) / 2
            spread_abs = ask - bid
            spread_pct = (spread_abs / mid * 100) if mid > 0 else 999

            # Extract Greeks — prefer Polygon, fallback to Black-Scholes.
            # With Advanced Options plan, Polygon provides greeks directly.
            # IV lives at the top level (not inside greeks).
            iv = (
                greeks.get("implied_volatility", 0)
                or contract_data.get("implied_volatility", 0)
                or 0
            )
            delta = greeks.get("delta", 0) or 0
            gamma = greeks.get("gamma", 0) or 0
            theta = greeks.get("theta", 0) or 0
            vega = greeks.get("vega", 0) or 0

            # Fallback: compute greeks via Black-Scholes when Polygon
            # doesn't provide complete greeks.  With the Advanced Options
            # plan, most contracts have native Polygon greeks; fallback
            # only triggers for very low-liquidity or newly listed contracts.
            greeks_incomplete = (
                delta == 0 or gamma == 0 or theta == 0 or vega == 0 or iv == 0
            )
            if greeks_incomplete and mid > 0 and dte > 0:
                from app.selection.greeks import compute_greeks

                self._bs_fallback_count += 1
                polygon_iv = iv  # Preserve Polygon IV before fallback
                computed = compute_greeks(
                    S=underlying_price,
                    K=strike,
                    T=dte / 365.0,
                    option_type=(
                        "call" if option_type == OptionType.CALL else "put"
                    ),
                    market_price=mid,
                    iv=polygon_iv if polygon_iv > 0 else None,
                )
                if computed:
                    delta = computed["delta"]
                    gamma = computed["gamma"]
                    theta = computed["theta"]
                    vega = computed["vega"]
                    # Keep Polygon IV when available; only use BS IV as last resort
                    iv = polygon_iv if polygon_iv > 0 else computed["iv"]
            else:
                self._polygon_greeks_count += 1

            # Skip contracts with no greeks even after fallback
            if delta == 0 and iv == 0:
                return None

            # Extract liquidity
            open_interest = contract_data.get("open_interest", 0) or 0
            volume = day.get("volume", 0) or 0

            # Calculate moneyness
            # For CALL: (strike - underlying) / underlying * 100
            # For PUT: (underlying - strike) / underlying * 100
            if option_type == OptionType.CALL:
                moneyness_pct = (strike - underlying_price) / underlying_price * 100
            else:
                moneyness_pct = (underlying_price - strike) / underlying_price * 100

            return ContractCandidate(
                option_ticker=option_ticker,
                underlying_ticker=ticker,
                option_type=option_type,
                expiration_date=expiration,
                dte=dte,
                strike=strike,
                underlying_price=underlying_price,
                bid=bid,
                ask=ask,
                mid=mid,
                spread_abs=spread_abs,
                spread_pct=spread_pct,
                iv=iv,
                delta=delta,
                gamma=gamma,
                theta=theta,
                vega=vega,
                open_interest=open_interest,
                volume=volume,
                moneyness_pct=moneyness_pct,
                dte_bucket=DTEBucket.A,  # Will be set properly later
            )

        except Exception as e:
            logger.debug(f"Error parsing contract: {e}")
            return None

    def _filter_delta_band(
        self,
        candidates: list[ContractCandidate],
        side: OptionType,
    ) -> list[ContractCandidate]:
        """Apply delta band filter.

        Per Section 12.3:
        CALL: 0.20 <= delta <= 0.75
        PUT: -0.75 <= delta <= -0.20

        Args:
            candidates: Contracts to filter
            side: Option type

        Returns:
            Filtered list of candidates
        """
        side_key = side.value
        if side_key in self._config.delta_bands:
            delta_band = self._config.delta_bands[side_key]
            min_delta = delta_band.min_delta
            max_delta = delta_band.max_delta
        else:
            # Default values
            if side == OptionType.CALL:
                min_delta, max_delta = 0.20, 0.75
            else:
                min_delta, max_delta = -0.75, -0.20

        filtered = []
        for candidate in candidates:
            if min_delta <= candidate.delta <= max_delta:
                filtered.append(candidate)

        return filtered

    def _filter_liquidity(
        self,
        candidates: list[ContractCandidate],
    ) -> list[ContractCandidate]:
        """Apply liquidity baseline filters.

        Per Section 12.3:
        - Min Open Interest: 200
        - Min Daily Volume: 50
        - Max Spread Percent: 10%
        - Min Mid Price: $0.20

        Args:
            candidates: Contracts to filter

        Returns:
            Filtered list of candidates
        """
        min_oi = self._config.min_open_interest
        min_vol = self._config.min_volume
        max_spread = self._config.max_spread_pct
        min_mid = self._config.min_mid_price

        filtered = []
        for candidate in candidates:
            if candidate.open_interest < min_oi:
                continue
            if candidate.volume < min_vol:
                continue
            if candidate.spread_pct > max_spread:
                continue
            if candidate.mid < min_mid:
                continue

            filtered.append(candidate)

        return filtered

    def _filter_moneyness(
        self,
        candidates: list[ContractCandidate],
        side: OptionType,
    ) -> list[ContractCandidate]:
        """Apply moneyness filter using configurable ranges.

        Args:
            candidates: Contracts to filter
            side: Option type

        Returns:
            Filtered list of candidates
        """
        if side == OptionType.CALL:
            min_moneyness = self._config.moneyness_call_min
            max_moneyness = self._config.moneyness_call_max
        else:
            min_moneyness = self._config.moneyness_put_min
            max_moneyness = self._config.moneyness_put_max

        filtered = []
        for candidate in candidates:
            if min_moneyness <= candidate.moneyness_pct <= max_moneyness:
                filtered.append(candidate)

        return filtered

    def _rank_and_select(
        self,
        candidates: list[ContractCandidate],
        side: OptionType,
    ) -> list[ContractCandidate]:
        """Rank candidates and select top K with optional delta diversity.

        When diversity_mode is "delta_spread", reserves slots for OTM contracts
        (abs(delta) below threshold) to ensure low-delta options get representation
        even when ATM contracts dominate on liquidity/spread.

        Args:
            candidates: Contracts to rank and select from
            side: Option type

        Returns:
            Top K candidates by rank score, with diversity slots if configured
        """
        if not candidates:
            return []

        is_call = side == OptionType.CALL

        # Calculate ranking scores for all candidates
        scored_candidates = []
        for candidate in candidates:
            scores = self._ranking.calculate_rank_score(
                open_interest=candidate.open_interest,
                volume=candidate.volume,
                delta=candidate.delta,
                spread_pct=candidate.spread_pct,
                is_call=is_call,
            )

            scored_candidate = ContractCandidate(
                **{**candidate.__dict__, "ranking_scores": scores}
            )
            scored_candidates.append(scored_candidate)

        # Sort by rank score descending
        scored_candidates.sort(key=lambda c: c.rank_score, reverse=True)

        top_k = self._config.top_k
        mode = self._config.diversity_mode
        reserved = self._config.diversity_reserved_slots

        # Original behavior when diversity is disabled
        if mode != "delta_spread" or reserved <= 0 or top_k <= reserved:
            return scored_candidates[:top_k]

        # Delta diversity: reserve slots for OTM contracts
        threshold = (
            self._config.diversity_delta_threshold_call
            if is_call
            else self._config.diversity_delta_threshold_put
        )

        def is_otm(c: ContractCandidate) -> bool:
            return abs(c.delta) < abs(threshold)

        # Take top (K - reserved) from full ranked list
        main_slots = top_k - reserved
        main_picks = scored_candidates[:main_slots]
        main_tickers = {c.option_ticker for c in main_picks}

        # Take top 'reserved' OTM contracts not already picked
        otm_pool = [
            c for c in scored_candidates
            if is_otm(c) and c.option_ticker not in main_tickers
        ]
        otm_picks = otm_pool[:reserved]

        # If not enough OTM candidates, fill from remaining ranked list
        if len(otm_picks) < reserved:
            remaining = [
                c for c in scored_candidates[main_slots:]
                if c.option_ticker not in main_tickers
                and c.option_ticker not in {p.option_ticker for p in otm_picks}
            ]
            otm_picks.extend(remaining[:reserved - len(otm_picks)])

        result = main_picks + otm_picks
        result.sort(key=lambda c: c.rank_score, reverse=True)
        return result

    def get_telemetry(self) -> SelectionTelemetry:
        """Get the telemetry tracker.

        Returns:
            SelectionTelemetry instance
        """
        return self._telemetry
