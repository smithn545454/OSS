"""Production data providers for the Convex Mode daily pipeline.

Concrete implementations of ``Stage2InputsProvider``,
``Stage3InputsProvider``, and ``Stage4InputsProvider`` that wire the
existing OSS data sources (PriceHistoryTable, IVHistoryTable,
CatalystCalendarTable, EarningsHistoryTable, Polygon options chain) into
the Stage modules' input dataclasses.

All providers are dependency-injected for testability — they accept a
Polygon client and rely on the standard table-class APIs. Tests can
substitute stubs, while production wires them via the Lambda handler in
``app.main._run_convex_daily_pipeline``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta
from typing import Any, Optional

from app.convex.iv_extraction import (
    ContractRow,
    extract_iv_metrics,
)
from app.convex.stage1_universe import calculate_realized_volatility
from app.convex.stage2_catalyst import (
    PeerEarningsReaction,
    Stage2Inputs,
)
from app.convex.stage3_volatility import Stage3Inputs
from app.convex.stage4_contract import (
    ConvexContractCandidate,
    Stage4Inputs,
)
from app.core.schemas import (
    ConvexUniverseSnapshot,
)
from app.db.tables import (
    CatalystCalendarTable,
    EarningsHistoryTable,
    IVHistoryTable,
    PriceHistoryTable,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared chain-fetch helper
# ---------------------------------------------------------------------------


async def fetch_options_chain(
    polygon_client: Any, ticker: str
) -> list[dict]:
    """Pull a single chain snapshot from Polygon (best-effort)."""
    try:
        return await polygon_client.get_options_chain_minimal(ticker)
    except Exception as e:
        logger.warning("Polygon chain fetch failed for %s: %s", ticker, e)
        return []


def _split_chain_volume(
    contracts: list[dict],
) -> tuple[float, float, float]:
    """Aggregate total / call / put day volumes from a chain snapshot."""
    total = call = put = 0.0
    for c in contracts:
        day = c.get("day", {}) or {}
        try:
            vol = float(day.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        details = c.get("details", {}) or {}
        contract_type = details.get("contract_type")
        total += vol
        if contract_type == "call":
            call += vol
        elif contract_type == "put":
            put += vol
    return total, call, put


def _underlying_price_from_chain(contracts: list[dict]) -> Optional[float]:
    """Extract the underlying spot from a chain snapshot's first row."""
    for c in contracts:
        ua = c.get("underlying_asset", {}) or {}
        price = ua.get("price")
        if isinstance(price, (int, float)) and price > 0:
            return float(price)
    return None


def _chain_to_iv_metrics(contracts: list[dict], today_iso: str) -> Optional[dict]:
    """Run the Phase 0.5 extractor against today's chain snapshot.

    Returns a dict with ``iv_30d`` / ``iv_60d`` / ``iv_25d_put`` /
    ``iv_25d_call`` keys (None when extraction fails).
    """
    rows: list[ContractRow] = []
    for c in contracts:
        details = c.get("details", {}) or {}
        greeks = c.get("greeks", {}) or {}
        delta = greeks.get("delta")
        iv = c.get("implied_volatility")
        expiry = details.get("expiration_date")
        ticker = (c.get("underlying_asset") or {}).get("ticker") or ""
        if not ticker or not expiry:
            continue
        rows.append(
            ContractRow(
                ticker=ticker,
                expiry_date=expiry,
                delta=delta,
                bid_iv=iv,  # Polygon Advanced ships top-level IV; reuse for both legs
                ask_iv=iv,
            )
        )
    metrics = extract_iv_metrics(rows, today_iso)
    if not metrics:
        return None
    m = metrics[0]
    return {
        "iv_30d": m.iv_30d,
        "iv_60d": m.iv_60d,
        "iv_25d_put": m.iv_25d_put,
        "iv_25d_call": m.iv_25d_call,
    }


def _chain_to_contract_candidates(
    contracts: list[dict], today_iso: str
) -> list[ConvexContractCandidate]:
    """Transform Polygon chain snapshots into ConvexContractCandidate objects."""
    out: list[ConvexContractCandidate] = []
    try:
        today = _date.fromisoformat(today_iso)
    except ValueError:
        return out

    for c in contracts:
        details = c.get("details", {}) or {}
        greeks = c.get("greeks", {}) or {}
        quote = c.get("last_quote", {}) or {}
        day = c.get("day", {}) or {}

        option_ticker = c.get("ticker") or details.get("ticker") or ""
        contract_type = details.get("contract_type")
        if contract_type not in ("call", "put"):
            continue
        strike = details.get("strike_price")
        expiry = details.get("expiration_date")
        delta = greeks.get("delta")
        iv = c.get("implied_volatility")  # Polygon Advanced top-level field
        bid = quote.get("bid")
        ask = quote.get("ask")
        oi = c.get("open_interest")
        vol = day.get("volume")

        if (
            not option_ticker
            or not expiry
            or strike is None
            or delta is None
            or bid is None
            or ask is None
        ):
            continue

        try:
            exp = _date.fromisoformat(expiry)
        except ValueError:
            continue
        dte = (exp - today).days

        out.append(
            ConvexContractCandidate(
                option_ticker=option_ticker,
                option_type=contract_type.upper(),
                strike=float(strike),
                expiry=expiry,
                dte=dte,
                delta=float(delta),
                bid=float(bid),
                ask=float(ask),
                open_interest=int(oi or 0),
                volume=int(vol or 0),
                iv=float(iv) if iv is not None else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Peer-earnings cache (shared by Stage 2 sympathy detection)
# ---------------------------------------------------------------------------


@dataclass
class PeerReactionsCache:
    """Per-pipeline-run map of sector → list of recent earnings reactions.

    Built once at run start from the universe snapshot + EarningsHistory
    so Stage 2 sympathy detection avoids per-ticker DB scans.
    """

    today_iso: str
    sector_index: dict[str, list[str]] = field(default_factory=dict)
    by_ticker: dict[str, PeerEarningsReaction] = field(default_factory=dict)

    def reactions_for_sector(self, sector: str) -> list[PeerEarningsReaction]:
        if not sector:
            return []
        tickers = self.sector_index.get(sector, [])
        return [self.by_ticker[t] for t in tickers if t in self.by_ticker]


async def build_peer_reactions_cache(
    snapshot: ConvexUniverseSnapshot,
    today_iso: str,
    lookback_days: int = 5,
) -> PeerReactionsCache:
    """Pre-compute sector → recent peer earnings reactions for a run."""
    today = _date.fromisoformat(today_iso)
    floor = today - timedelta(days=lookback_days)
    cache = PeerReactionsCache(today_iso=today_iso)

    for entry in snapshot.tickers:
        sector = entry.sector or "Unknown"
        cache.sector_index.setdefault(sector, []).append(entry.ticker)

        try:
            events = await EarningsHistoryTable.get_recent_with_moves(
                entry.ticker, n=4
            )
        except Exception as e:
            logger.debug("EarningsHistory fetch failed for %s: %s", entry.ticker, e)
            continue
        for ev in events:
            try:
                ev_date = _date.fromisoformat(ev.earnings_date)
            except (TypeError, ValueError):
                continue
            if ev_date < floor or ev_date > today:
                continue
            move = ev.one_day_move_pct
            if move is None:
                continue
            cache.by_ticker[entry.ticker] = PeerEarningsReaction(
                ticker=entry.ticker,
                event_date=ev.earnings_date,
                days_ago=(today - ev_date).days,
                move_pct=float(move),
            )
            break  # Most recent first; one per ticker is enough.

    return cache


# ---------------------------------------------------------------------------
# Stage 2 Provider
# ---------------------------------------------------------------------------


class ProductionStage2InputsProvider:
    """Wires PriceHistory + Catalyst calendar + Polygon chain + peer cache."""

    def __init__(
        self,
        polygon_client: Any,
        peer_cache: PeerReactionsCache,
        compression_window_bars: int = 252,
    ) -> None:
        self._polygon = polygon_client
        self._peer_cache = peer_cache
        self._window = compression_window_bars

    async def fetch(
        self, ticker: str, sector: Optional[str], today_iso: str
    ) -> Optional[Stage2Inputs]:
        try:
            bars = await PriceHistoryTable.list_by_ticker(
                ticker, limit=self._window, scan_forward=True
            )
        except Exception as e:
            logger.warning("PriceHistory fetch failed for %s: %s", ticker, e)
            return None
        if len(bars) < 60:
            return None

        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        volumes = [float(b.volume) for b in bars]

        try:
            calendar = await CatalystCalendarTable.list_for_ticker(
                ticker,
                start_date=today_iso,
                end_date=(_date.fromisoformat(today_iso) + timedelta(days=60)).isoformat(),
            )
        except Exception as e:
            logger.debug("CatalystCalendar fetch failed for %s: %s", ticker, e)
            calendar = []

        chain = await fetch_options_chain(self._polygon, ticker)
        total_vol, call_vol, put_vol = _split_chain_volume(chain)
        avg_vol_30d = await _avg_options_volume_30d(ticker)

        peer_reactions = self._peer_cache.reactions_for_sector(sector or "")

        return Stage2Inputs(
            ticker=ticker,
            sector=sector,
            closes=closes,
            highs=highs,
            lows=lows,
            volumes=volumes,
            nearest_significant_level_pct=None,  # Phase 3+ refinement
            calendar_entries=calendar,
            today_total_options_volume=total_vol if total_vol > 0 else None,
            avg_options_volume_30d=avg_vol_30d,
            today_call_options_volume=call_vol if call_vol > 0 else None,
            today_put_options_volume=put_vol if put_vol > 0 else None,
            peer_reactions=peer_reactions,
        )


async def _avg_options_volume_30d(ticker: str) -> Optional[float]:
    """Best-effort 30-day average options volume.

    Phase 6.5 simplification: returns ``None`` until we wire an
    OIHistory aggregation across all contracts for the underlying.
    Stage 2 UV detection then needs ``today_total_options_volume`` and
    ``avg_options_volume_30d`` both set; until backfilled, UV signals
    won't fire (only date-known + compression catalysts will). The
    impact report flagged this as a fast-follow.
    """
    return None


# ---------------------------------------------------------------------------
# Stage 3 Provider
# ---------------------------------------------------------------------------


class ProductionStage3InputsProvider:
    """Wires IVHistory + PriceHistory (HV20) for Stage 3."""

    def __init__(
        self,
        polygon_client: Any,
        history_window: int = 252,
    ) -> None:
        self._polygon = polygon_client
        self._window = history_window

    async def fetch(
        self,
        ticker: str,
        today_iso: str,
    ) -> Optional[Stage3Inputs]:
        try:
            history = await IVHistoryTable.list_by_ticker(ticker, limit=self._window)
        except Exception as e:
            logger.warning("IVHistory fetch failed for %s: %s", ticker, e)
            history = []

        latest = history[0] if history else None

        # Compute HV20 from price history.
        try:
            bars = await PriceHistoryTable.list_by_ticker(
                ticker, limit=25, scan_forward=True
            )
        except Exception as e:
            logger.debug("PriceHistory fetch failed for %s: %s", ticker, e)
            bars = []
        rv20 = (
            calculate_realized_volatility([b.close for b in bars], 20)
            if len(bars) >= 21
            else None
        )

        # Today's 30-day IV: prefer the latest IVHistory row, fall back to
        # live extraction from a fresh Polygon chain snapshot, then to the
        # legacy atm_iv field.
        iv_30d = getattr(latest, "iv_30d", None) if latest else None
        if iv_30d is None:
            chain = await fetch_options_chain(self._polygon, ticker)
            extracted = _chain_to_iv_metrics(chain, today_iso) if chain else None
            if extracted:
                iv_30d = extracted.get("iv_30d")
        if iv_30d is None and latest is not None:
            iv_30d = latest.atm_iv

        return Stage3Inputs(
            ticker=ticker,
            current_iv_30d=iv_30d,
            iv_history=history,
            rv20=rv20,
        )


async def _compute_price_position_pct(ticker: str) -> Optional[float]:
    """Position of the latest close within trailing 60-day high/low (0-100)."""
    try:
        bars = await PriceHistoryTable.list_by_ticker(
            ticker, limit=60, scan_forward=False
        )
    except Exception:
        return None
    if len(bars) < 20:
        return None
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    high = max(highs)
    low = min(lows)
    if high <= low:
        return None
    current = closes[0]
    return max(0.0, min(100.0, ((current - low) / (high - low)) * 100.0))


# ---------------------------------------------------------------------------
# Stage 4 Provider
# ---------------------------------------------------------------------------


class ProductionStage4InputsProvider:
    """Wires Polygon chain + measured-move estimate + historical event move."""

    def __init__(self, polygon_client: Any) -> None:
        self._polygon = polygon_client

    async def fetch(  # noqa: PLR0913
        self,
        ticker: str,
        direction: str,
        catalyst_type: Optional[str],
        catalyst_date_iso: Optional[str],
        uv_directional_skew: Optional[str],
        today_iso: str,
    ) -> Optional[Stage4Inputs]:
        chain = await fetch_options_chain(self._polygon, ticker)
        if not chain:
            return None

        underlying_price = _underlying_price_from_chain(chain)
        if underlying_price is None:
            return None

        candidates = _chain_to_contract_candidates(chain, today_iso)
        if not candidates:
            return None

        measured_move = await _measured_move_pct(ticker)
        historical_move = (
            await _historical_event_move_pct(ticker)
            if catalyst_type == "date_known"
            else None
        )

        return Stage4Inputs(
            ticker=ticker,
            underlying_price=underlying_price,
            direction=direction,
            catalyst_type=catalyst_type,
            catalyst_date_iso=catalyst_date_iso,
            measured_move_pct=measured_move,
            historical_event_move_pct=historical_move,
            available_contracts=candidates,
            uv_directional_skew=uv_directional_skew,
            today_iso=today_iso,
        )


async def _measured_move_pct(ticker: str) -> Optional[float]:
    """Approx the consolidation height as a percent of price.

    Uses the trailing 20-day high-minus-low / current-price ratio.
    Mirrors Stage 2 range-compression detail; avoids re-querying the
    whole 252-day series during Stage 4 fetch.
    """
    try:
        bars = await PriceHistoryTable.list_by_ticker(
            ticker, limit=20, scan_forward=False
        )
    except Exception:
        return None
    if len(bars) < 5:
        return None
    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    current = bars[0].close
    if current <= 0:
        return None
    return ((high - low) / current) * 100.0


async def _historical_event_move_pct(ticker: str) -> Optional[float]:
    """Mean absolute 1-day post-event move from the trailing 4 events."""
    try:
        events = await EarningsHistoryTable.get_recent_with_moves(ticker, n=4)
    except Exception:
        return None
    moves = [
        abs(e.one_day_move_pct)
        for e in events
        if e.one_day_move_pct is not None
    ]
    if not moves:
        return None
    return sum(moves) / len(moves)
