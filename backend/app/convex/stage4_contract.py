"""Convex Mode — Stage 4: Contract Selection.

Translates "this name has a setup" into "buy this specific contract".
Computes the expected-move terminus, picks the strike closest to that
terminus within the configured delta + DTE bands, validates liquidity,
and sets the Smart Money Confirmation flag when Stage 2 UV directional
skew aligns with the chosen thesis.

Stage 4 PASSES when a single tradeable contract meeting all parameter
requirements exists. FAILS when no contract clears liquidity (e.g.,
chain too thin at the required delta / DTE).

For ``ambiguous`` candidates (straddle), Stage 4 selects both a 50Δ
call and a 50Δ put at the same expiry; both must clear liquidity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence

from app.core.schemas import (
    ConvexConfig,
    ConvexStagePayload,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class ConvexContractCandidate:
    """A single option contract evaluated for Stage 4 selection."""

    option_ticker: str
    option_type: str  # "CALL" or "PUT"
    strike: float
    expiry: str       # YYYY-MM-DD
    dte: int
    delta: float      # signed (calls positive, puts negative)
    bid: float
    ask: float
    open_interest: int
    volume: int

    @property
    def mid(self) -> Optional[float]:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return None
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> Optional[float]:
        m = self.mid
        if m is None or m <= 0:
            return None
        return ((self.ask - self.bid) / m) * 100.0


@dataclass
class Stage4Inputs:
    """Per-ticker pre-computed inputs for Stage 4 evaluation."""

    ticker: str
    underlying_price: float
    direction: str  # "bullish" | "bearish" | "ambiguous"
    catalyst_type: Optional[str]
    catalyst_date_iso: Optional[str]  # YYYY-MM-DD if date-known catalyst
    measured_move_pct: Optional[float]      # State-based catalyst (consolidation)
    historical_event_move_pct: Optional[float]  # Date-known catalyst (mean of last 4)
    available_contracts: Sequence[ConvexContractCandidate]
    uv_directional_skew: Optional[str] = None  # "call_heavy" | "put_heavy" | "balanced"
    today_iso: Optional[str] = None


# ---------------------------------------------------------------------------
# Expected-move terminus
# ---------------------------------------------------------------------------


def compute_expected_terminus(
    underlying_price: float,
    direction: str,
    measured_move_pct: Optional[float],
    historical_event_move_pct: Optional[float],
) -> Optional[float]:
    """Combine measured move + historical event move and project from price.

    For ambiguous (straddle) candidates the terminus is unsigned — caller
    handles call/put leg selection separately.

    Returns None when neither move estimate is available.
    """
    candidates = [m for m in (measured_move_pct, historical_event_move_pct) if m]
    if not candidates:
        return None
    move_pct = max(candidates)
    sign = 1 if direction == "bullish" else (-1 if direction == "bearish" else 0)
    if direction == "ambiguous" or sign == 0:
        # Caller computes both legs; return the magnitude as +.
        return underlying_price * (move_pct / 100.0)
    return underlying_price * (1.0 + sign * (move_pct / 100.0))


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _earliest_acceptable_expiry(
    inputs: Stage4Inputs, config: ConvexConfig
) -> Optional[_date]:
    """Compute the post-catalyst-cushion expiry floor when applicable."""
    if not inputs.today_iso:
        return None
    try:
        _date.fromisoformat(inputs.today_iso)  # validate format
    except ValueError:
        return None

    if inputs.catalyst_type == "date_known" and inputs.catalyst_date_iso:
        try:
            event = _date.fromisoformat(inputs.catalyst_date_iso)
        except ValueError:
            return None
        # Per source plan: "If date-known catalyst, DTE = catalyst date + 14
        # days minimum" — so the contract's expiry must sit at least 14 days
        # past the event.
        from datetime import timedelta
        return event + timedelta(days=config.contract_dte_post_event_buffer)
    return None


def _filter_by_dte(
    candidates: Sequence[ConvexContractCandidate],
    config: ConvexConfig,
    min_expiry: Optional[_date],
) -> list[ConvexContractCandidate]:
    out: list[ConvexContractCandidate] = []
    for c in candidates:
        if c.dte < config.contract_dte_min or c.dte > config.contract_dte_max:
            continue
        if min_expiry is not None:
            try:
                exp = _date.fromisoformat(c.expiry)
            except ValueError:
                continue
            if exp < min_expiry:
                continue
        out.append(c)
    return out


def _filter_by_direction(
    candidates: Sequence[ConvexContractCandidate], direction: str
) -> list[ConvexContractCandidate]:
    if direction == "bullish":
        return [c for c in candidates if c.option_type == "CALL"]
    if direction == "bearish":
        return [c for c in candidates if c.option_type == "PUT"]
    return list(candidates)  # ambiguous handled by caller (both legs)


def _filter_by_delta(
    candidates: Sequence[ConvexContractCandidate],
    delta_min: float,
    delta_max: float,
) -> list[ConvexContractCandidate]:
    return [c for c in candidates if delta_min <= abs(c.delta) <= delta_max]


def _passes_liquidity(c: ConvexContractCandidate, config: ConvexConfig) -> bool:
    if c.open_interest < config.contract_min_open_interest:
        return False
    spread = c.spread_pct
    if spread is None or spread > config.contract_max_spread_pct:
        return False
    return True


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass
class RejectedAlternative:
    """A contract that was evaluated and rejected, with the reason."""

    option_ticker: str
    strike: float
    expiry: str
    dte: int
    delta: float
    rejected_reason: str


@dataclass
class StrikeSelection:
    """Outcome of strike selection for one direction (or one straddle leg)."""

    selected: Optional[ConvexContractCandidate]
    alternatives_considered: list[RejectedAlternative] = field(default_factory=list)
    strike_rationale: str = ""


def _select_directional_contract(
    in_band_candidates: Sequence[ConvexContractCandidate],
    out_of_band_candidates: Sequence[ConvexContractCandidate],
    expected_terminus: Optional[float],
    direction: str,
    config: ConvexConfig,
) -> StrikeSelection:
    """Pick the strike closest to ``expected_terminus`` that clears liquidity.

    Ranks in-band candidates by (distance to terminus, |delta - 0.30| as
    tie-break). Walks down the ranked list until liquidity passes.
    Out-of-band candidates (too far OTM / too deep ITM) are surfaced as
    rejected alternatives so the UI can show why-this-and-not-the-next.
    """
    if not in_band_candidates:
        return StrikeSelection(
            selected=None,
            strike_rationale="No contracts in delta/DTE band.",
        )

    target_delta = (config.contract_delta_min + config.contract_delta_max) / 2

    def _score(c: ConvexContractCandidate) -> tuple[float, float]:
        dist = (
            abs(c.strike - expected_terminus)
            if expected_terminus is not None
            else 0.0
        )
        return (dist, abs(abs(c.delta) - target_delta))

    ranked = sorted(in_band_candidates, key=_score)
    rejected: list[RejectedAlternative] = []
    chosen: Optional[ConvexContractCandidate] = None

    for c in ranked:
        if _passes_liquidity(c, config):
            chosen = c
            break
        spread = c.spread_pct
        spread_str = (
            f"spread {spread:.1f}% > {config.contract_max_spread_pct:.0f}% cap"
            if spread is not None
            else "no quote"
        )
        oi_str = (
            f"OI {c.open_interest} < {config.contract_min_open_interest} floor"
            if c.open_interest < config.contract_min_open_interest
            else None
        )
        reason = " and ".join(s for s in (oi_str, spread_str) if s)
        rejected.append(
            RejectedAlternative(
                option_ticker=c.option_ticker,
                strike=c.strike,
                expiry=c.expiry,
                dte=c.dte,
                delta=c.delta,
                rejected_reason=f"failed liquidity: {reason}",
            )
        )

    if chosen is None:
        return StrikeSelection(
            selected=None,
            alternatives_considered=rejected,
            strike_rationale=(
                "All in-band contracts failed liquidity validation; "
                "chain too thin at the required delta/DTE."
            ),
        )

    # Capture nearby out-of-band alternatives so the UI can show
    # why-this-and-not-the-next. Picks at most two: one too-OTM and
    # one too-ITM.
    nearby = _nearby_alternatives(chosen, out_of_band_candidates, config)
    rejected.extend(nearby)

    rationale = _build_strike_rationale(chosen, expected_terminus, direction)
    return StrikeSelection(
        selected=chosen,
        alternatives_considered=rejected,
        strike_rationale=rationale,
    )


def _nearby_alternatives(
    chosen: ConvexContractCandidate,
    out_of_band: Sequence[ConvexContractCandidate],
    config: ConvexConfig,
) -> list[RejectedAlternative]:
    """Surface 1-2 out-of-band strikes that were considered and rejected.

    Helps the UI explain "why this strike, not the one next door."
    """
    alternatives: list[RejectedAlternative] = []
    seen_tickers = {chosen.option_ticker}
    for c in out_of_band:
        if c.option_ticker in seen_tickers:
            continue
        if abs(c.delta) < config.contract_delta_min:
            reason = (
                f"below {config.contract_delta_min:.2f}Δ target — too OTM, "
                "gamma payoff insufficient if move underwhelms"
            )
            alternatives.append(
                RejectedAlternative(
                    option_ticker=c.option_ticker,
                    strike=c.strike,
                    expiry=c.expiry,
                    dte=c.dte,
                    delta=c.delta,
                    rejected_reason=reason,
                )
            )
            seen_tickers.add(c.option_ticker)
        elif abs(c.delta) > config.contract_delta_max:
            reason = (
                f"above {config.contract_delta_max:.2f}Δ target — paying too "
                "much for intrinsic, vega/gamma kicker reduced"
            )
            alternatives.append(
                RejectedAlternative(
                    option_ticker=c.option_ticker,
                    strike=c.strike,
                    expiry=c.expiry,
                    dte=c.dte,
                    delta=c.delta,
                    rejected_reason=reason,
                )
            )
            seen_tickers.add(c.option_ticker)
        if len(alternatives) >= 2:
            break
    return alternatives


def _build_strike_rationale(
    chosen: ConvexContractCandidate,
    expected_terminus: Optional[float],
    direction: str,
) -> str:
    if expected_terminus is None:
        return (
            f"Selected {chosen.option_ticker} (delta {chosen.delta:+.2f}, "
            f"{chosen.dte} DTE)."
        )
    return (
        f"Strike {chosen.strike:g} chosen as the closest contract to the "
        f"expected-move terminus (~{expected_terminus:.2f}); delta "
        f"{chosen.delta:+.2f} optimises gamma kicker if {direction} "
        "thesis plays out."
    )


# ---------------------------------------------------------------------------
# Smart Money Confirmation
# ---------------------------------------------------------------------------


def smart_money_confirmation(
    direction: str, uv_directional_skew: Optional[str]
) -> bool:
    """True when Stage 2 UV skew aligns with the Stage 3 directional thesis.

    This is a flag, not a gate — the Evaluation Detail page surfaces it
    prominently but the tier assignment ignores it (per Nick's launch
    decision: visibility-only).
    """
    if uv_directional_skew is None:
        return False
    if direction == "bullish" and uv_directional_skew == "call_heavy":
        return True
    if direction == "bearish" and uv_directional_skew == "put_heavy":
        return True
    return False


# ---------------------------------------------------------------------------
# Integrator
# ---------------------------------------------------------------------------


@dataclass
class Stage4Result:
    payload: ConvexStagePayload
    selected_call: Optional[ConvexContractCandidate] = None
    selected_put: Optional[ConvexContractCandidate] = None
    smart_money_confirmation: bool = False


def evaluate_stage4(inputs: Stage4Inputs, config: ConvexConfig) -> Stage4Result:
    """Pick a tradeable contract (or straddle pair) and PASS/FAIL the gate."""
    expected_terminus = compute_expected_terminus(
        inputs.underlying_price,
        inputs.direction,
        inputs.measured_move_pct,
        inputs.historical_event_move_pct,
    )

    min_expiry = _earliest_acceptable_expiry(inputs, config)
    by_dte = _filter_by_dte(inputs.available_contracts, config, min_expiry)

    if inputs.direction == "ambiguous":
        return _evaluate_straddle(inputs, by_dte, config)

    by_direction = _filter_by_direction(by_dte, inputs.direction)
    by_delta = _filter_by_delta(
        by_direction,
        config.contract_delta_min,
        config.contract_delta_max,
    )
    in_band_tickers = {c.option_ticker for c in by_delta}
    out_of_band = [
        c for c in by_direction if c.option_ticker not in in_band_tickers
    ]

    if inputs.direction == "bullish":
        signed_terminus = (
            inputs.underlying_price + (expected_terminus - inputs.underlying_price)
            if expected_terminus is not None
            else None
        )
    else:
        signed_terminus = expected_terminus  # already signed by compute_expected_terminus

    selection = _select_directional_contract(
        by_delta, out_of_band, signed_terminus, inputs.direction, config
    )
    smart_money = smart_money_confirmation(inputs.direction, inputs.uv_directional_skew)

    if selection.selected is None:
        return Stage4Result(
            payload=ConvexStagePayload(
                stage=4,
                stage_name="Contract Selection",
                result="FAIL",
                summary=(
                    f"{inputs.ticker}: no tradeable contract — "
                    f"{selection.strike_rationale}"
                ),
                criteria={
                    "filtered_chain_size": len(by_delta),
                    "alternatives_considered": [
                        _alt_dict(a) for a in selection.alternatives_considered
                    ],
                },
                strength=0.0,
            ),
        )

    payload = _build_directional_payload(
        inputs, selection, expected_terminus, smart_money, config
    )
    return Stage4Result(
        payload=payload,
        selected_call=selection.selected if inputs.direction == "bullish" else None,
        selected_put=selection.selected if inputs.direction == "bearish" else None,
        smart_money_confirmation=smart_money,
    )


def _evaluate_straddle(
    inputs: Stage4Inputs,
    by_dte: Sequence[ConvexContractCandidate],
    config: ConvexConfig,
) -> Stage4Result:
    """Pick a 50Δ call + 50Δ put at the same expiry (straddle candidate)."""
    delta_target = config.contract_delta_straddle
    delta_min = delta_target - 0.10
    delta_max = delta_target + 0.10
    calls = _filter_by_delta(
        [c for c in by_dte if c.option_type == "CALL"], delta_min, delta_max
    )
    puts = _filter_by_delta(
        [c for c in by_dte if c.option_type == "PUT"], delta_min, delta_max
    )
    if not calls or not puts:
        return Stage4Result(
            payload=ConvexStagePayload(
                stage=4,
                stage_name="Contract Selection",
                result="FAIL",
                summary=(
                    f"{inputs.ticker}: no straddle candidate — chain lacks "
                    "near-50Δ call/put pair in the DTE band."
                ),
            ),
        )

    # Pair calls and puts on shared expiry; rank by combined liquidity.
    call_by_exp = {c.expiry: c for c in calls}
    put_by_exp = {p.expiry: p for p in puts}
    shared_expiries = sorted(set(call_by_exp) & set(put_by_exp))

    chosen_call: Optional[ConvexContractCandidate] = None
    chosen_put: Optional[ConvexContractCandidate] = None
    for exp in shared_expiries:
        call = call_by_exp[exp]
        put = put_by_exp[exp]
        if _passes_liquidity(call, config) and _passes_liquidity(put, config):
            chosen_call = call
            chosen_put = put
            break

    if chosen_call is None or chosen_put is None:
        return Stage4Result(
            payload=ConvexStagePayload(
                stage=4,
                stage_name="Contract Selection",
                result="FAIL",
                summary=(
                    f"{inputs.ticker}: straddle pair failed liquidity "
                    "validation across all eligible expiries."
                ),
            ),
        )

    summary = (
        f"{inputs.ticker}: selected long straddle at {chosen_call.expiry} "
        f"(call {chosen_call.strike:g} {chosen_call.delta:+.2f}Δ, "
        f"put {chosen_put.strike:g} {chosen_put.delta:+.2f}Δ)."
    )
    payload = ConvexStagePayload(
        stage=4,
        stage_name="Contract Selection",
        result="PASS",
        summary=summary,
        criteria={
            "structure": "long_straddle",
            "selected_call": _contract_dict(chosen_call),
            "selected_put": _contract_dict(chosen_put),
        },
        strength=_directional_strength(chosen_call, config),
        extras={"smart_money_confirmation": False},
    )
    return Stage4Result(
        payload=payload,
        selected_call=chosen_call,
        selected_put=chosen_put,
        smart_money_confirmation=False,
    )


def _build_directional_payload(
    inputs: Stage4Inputs,
    selection: StrikeSelection,
    expected_terminus: Optional[float],
    smart_money: bool,
    config: ConvexConfig,
) -> ConvexStagePayload:
    chosen = selection.selected
    assert chosen is not None  # narrowing for type-checker

    summary = (
        f"{inputs.ticker}: selected {chosen.option_type.lower()} "
        f"{chosen.strike:g} expiring {chosen.expiry} "
        f"(delta {chosen.delta:+.2f}, {chosen.dte} DTE)."
    )

    return ConvexStagePayload(
        stage=4,
        stage_name="Contract Selection",
        result="PASS",
        summary=summary,
        criteria={
            "structure": "directional",
            "selected_contract": _contract_dict(chosen),
            "alternatives_considered": [
                _alt_dict(a) for a in selection.alternatives_considered
            ],
            "strike_rationale": selection.strike_rationale,
            "expected_move_terminus": expected_terminus,
            "liquidity": {
                "open_interest": chosen.open_interest,
                "spread_pct": chosen.spread_pct,
                "volume_today": chosen.volume,
            },
        },
        strength=_directional_strength(chosen, config),
        extras={"smart_money_confirmation": smart_money},
    )


def _directional_strength(
    chosen: object, config: ConvexConfig
) -> float:
    """Composite strength for within-tier ranking only.

    Inputs: delta proximity to target band centre, DTE proximity to band
    centre, spread tightness. Liquidity quality folds in via spread_pct.
    """
    c = chosen  # type: ignore[assignment]
    target_delta = (config.contract_delta_min + config.contract_delta_max) / 2
    delta_score = max(
        0.0,
        1.0 - abs(abs(c.delta) - target_delta) / target_delta,  # type: ignore[attr-defined]
    )

    target_dte = (config.contract_dte_min + config.contract_dte_max) / 2
    dte_score = max(
        0.0,
        1.0 - abs(c.dte - target_dte) / target_dte,  # type: ignore[attr-defined]
    )

    spread = c.spread_pct  # type: ignore[attr-defined]
    spread_score = (
        max(0.0, 1.0 - (spread / config.contract_max_spread_pct))
        if spread is not None
        else 0.5
    )

    return round((delta_score + dte_score + spread_score) / 3, 4)


def _contract_dict(c: ConvexContractCandidate) -> dict[str, object]:
    return {
        "option_ticker": c.option_ticker,
        "type": c.option_type,
        "strike": c.strike,
        "expiry": c.expiry,
        "dte": c.dte,
        "delta": round(c.delta, 4),
        "bid": c.bid,
        "ask": c.ask,
        "mid": c.mid,
        "spread_pct": c.spread_pct,
        "open_interest": c.open_interest,
        "volume": c.volume,
    }


def _alt_dict(a: RejectedAlternative) -> dict[str, object]:
    return {
        "option_ticker": a.option_ticker,
        "strike": a.strike,
        "expiry": a.expiry,
        "dte": a.dte,
        "delta": round(a.delta, 4),
        "rejected_reason": a.rejected_reason,
    }
