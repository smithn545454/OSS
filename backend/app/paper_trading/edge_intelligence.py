"""Edge Intelligence — rolling-window performance analytics for pre-trade context.

Pure computation module. Takes lists of PaperPosition objects and returns
analytics dataclasses. No database imports, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DimensionStats:
    """Performance stats for a single slice (e.g., CALL, Breakout, score 80+)."""

    label: str
    total: int = 0
    closed: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: Optional[float] = None
    avg_return: Optional[float] = None
    avg_days_held: Optional[float] = None
    total_pnl_dollars: float = 0.0
    expectancy: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "total": self.total,
            "closed": self.closed,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 1) if self.win_rate is not None else None,
            "avg_return": round(self.avg_return, 2) if self.avg_return is not None else None,
            "avg_days_held": (
                round(self.avg_days_held, 1) if self.avg_days_held is not None else None
            ),
            "total_pnl_dollars": round(self.total_pnl_dollars, 2),
            "expectancy": round(self.expectancy, 2) if self.expectancy is not None else None,
        }


@dataclass
class EdgeInsight:
    """A single deterministic, actionable insight."""

    category: str  # "option_type", "scanner", "score", "tier", "convergence"
    headline: str
    detail: Optional[str] = None
    strength: str = "moderate"  # "strong" (>=15pp edge) or "moderate" (5-15pp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "headline": self.headline,
            "detail": self.detail,
            "strength": self.strength,
        }


@dataclass
class EdgeBriefing:
    """Complete pre-trade intelligence snapshot."""

    period_start: str
    period_end: str
    trading_days: int
    total_positions: int
    total_closed: int
    overall_win_rate: Optional[float] = None
    overall_avg_return: Optional[float] = None
    overall_expectancy: Optional[float] = None
    by_option_type: dict[str, DimensionStats] = field(default_factory=dict)
    option_type_edge: Optional[str] = None
    by_scanner: dict[str, DimensionStats] = field(default_factory=dict)
    hot_scanner: Optional[str] = None
    by_score_bucket: dict[str, DimensionStats] = field(default_factory=dict)
    by_dte_bucket: dict[str, DimensionStats] = field(default_factory=dict)
    by_quality_tier: dict[str, DimensionStats] = field(default_factory=dict)
    by_convergence: dict[str, DimensionStats] = field(default_factory=dict)
    insights: list[EdgeInsight] = field(default_factory=list)
    vix_level: Optional[float] = None
    spy_direction: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "trading_days": self.trading_days,
            "total_positions": self.total_positions,
            "total_closed": self.total_closed,
            "overall_win_rate": (
                round(self.overall_win_rate, 1) if self.overall_win_rate is not None else None
            ),
            "overall_avg_return": (
                round(self.overall_avg_return, 2) if self.overall_avg_return is not None else None
            ),
            "overall_expectancy": (
                round(self.overall_expectancy, 2)
                if self.overall_expectancy is not None
                else None
            ),
            "by_option_type": {k: v.to_dict() for k, v in self.by_option_type.items()},
            "option_type_edge": self.option_type_edge,
            "by_scanner": {k: v.to_dict() for k, v in self.by_scanner.items()},
            "hot_scanner": self.hot_scanner,
            "by_score_bucket": {k: v.to_dict() for k, v in self.by_score_bucket.items()},
            "by_dte_bucket": {k: v.to_dict() for k, v in self.by_dte_bucket.items()},
            "by_quality_tier": {k: v.to_dict() for k, v in self.by_quality_tier.items()},
            "by_convergence": {k: v.to_dict() for k, v in self.by_convergence.items()},
            "insights": [i.to_dict() for i in self.insights],
            "vix_level": self.vix_level,
            "spy_direction": self.spy_direction,
        }


@dataclass
class TradeContext:
    """Historical context for trades matching a specific evaluation."""

    exact_match: Optional[DimensionStats] = None
    exact_match_description: str = ""
    sample_size: int = 0
    by_option_type: Optional[DimensionStats] = None
    by_scanner: Optional[DimensionStats] = None
    by_score_range: Optional[DimensionStats] = None
    summary: str = ""
    confidence_flag: str = "insufficient"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_match": self.exact_match.to_dict() if self.exact_match else None,
            "exact_match_description": self.exact_match_description,
            "sample_size": self.sample_size,
            "by_option_type": self.by_option_type.to_dict() if self.by_option_type else None,
            "by_scanner": self.by_scanner.to_dict() if self.by_scanner else None,
            "by_score_range": self.by_score_range.to_dict() if self.by_score_range else None,
            "summary": self.summary,
            "confidence_flag": self.confidence_flag,
        }


# ---------------------------------------------------------------------------
# Helper: enum-safe value extraction
# ---------------------------------------------------------------------------

def _val(v: Any) -> Any:
    """Extract .value from an enum, or return as-is."""
    return v.value if hasattr(v, "value") else v


# ---------------------------------------------------------------------------
# Slice computation
# ---------------------------------------------------------------------------

def _compute_stats(label: str, positions: list) -> DimensionStats:
    """Compute DimensionStats for a group of positions."""
    closed = [p for p in positions if _val(p.status) == "CLOSED"]
    wins = [p for p in closed if p.current_pnl_pct > 0]
    losses = [p for p in closed if p.current_pnl_pct <= 0]

    closed_count = len(closed)
    win_count = len(wins)
    loss_count = len(losses)

    win_rate = (win_count / closed_count * 100) if closed_count > 0 else None
    avg_return = (
        sum(p.current_pnl_pct for p in closed) / closed_count if closed_count > 0 else None
    )
    avg_days_held = (
        sum(p.days_held for p in closed if p.days_held is not None) / closed_count
        if closed_count > 0
        else None
    )
    total_pnl_dollars = sum(p.dollar_pnl or 0.0 for p in closed)

    # Expectancy: (WR * avg_win) - (LR * avg_loss)
    expectancy = None
    if closed_count > 0 and win_rate is not None:
        avg_win = (
            sum(p.current_pnl_pct for p in wins) / win_count if win_count > 0 else 0.0
        )
        avg_loss = (
            abs(sum(p.current_pnl_pct for p in losses) / loss_count) if loss_count > 0 else 0.0
        )
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    return DimensionStats(
        label=label,
        total=len(positions),
        closed=closed_count,
        wins=win_count,
        losses=loss_count,
        win_rate=win_rate,
        avg_return=avg_return,
        avg_days_held=avg_days_held,
        total_pnl_dollars=total_pnl_dollars,
        expectancy=expectancy,
    )


def _score_bucket(score: Any) -> str:
    if score is None:
        return "UNKNOWN"
    s = float(score)
    if s < 65:
        return "<65"
    elif s < 70:
        return "65-69"
    elif s < 75:
        return "70-74"
    elif s < 80:
        return "75-79"
    else:
        return "80+"


def _convergence_label(count: Any) -> str:
    if count is None:
        return "1"
    c = int(count)
    if c >= 3:
        return "3+"
    return str(c)


def _group_by(positions: list, key_fn) -> dict[str, list]:
    groups: dict[str, list] = {}
    for p in positions:
        k = key_fn(p)
        groups.setdefault(k, []).append(p)
    return groups


# ---------------------------------------------------------------------------
# Edge Briefing computation
# ---------------------------------------------------------------------------

def compute_edge_briefing(
    positions: list,
    period_start: str,
    period_end: str,
    trading_days: int,
    market_context: Optional[dict[str, Any]] = None,
) -> EdgeBriefing:
    """Compute a complete edge briefing from a list of PaperPosition objects."""
    overall = _compute_stats("Overall", positions)

    # By option type
    by_opt = _group_by(positions, lambda p: p.option_type or "UNKNOWN")
    by_option_type = {k: _compute_stats(k, v) for k, v in sorted(by_opt.items())}

    # Determine option type edge
    option_type_edge = None
    call_stats = by_option_type.get("CALL")
    put_stats = by_option_type.get("PUT")
    if (
        call_stats
        and put_stats
        and call_stats.win_rate is not None
        and put_stats.win_rate is not None
        and call_stats.closed >= 3
        and put_stats.closed >= 3
    ):
        if put_stats.win_rate > call_stats.win_rate:
            option_type_edge = "PUT"
        elif call_stats.win_rate > put_stats.win_rate:
            option_type_edge = "CALL"

    # By scanner
    by_scn = _group_by(positions, lambda p: _normalize_scanner(p.scanner_source))
    by_scanner = {k: _compute_stats(k, v) for k, v in sorted(by_scn.items())}

    # Hot scanner: best WR with >= 3 closed and >= 10pp above overall
    hot_scanner = None
    if overall.win_rate is not None:
        candidates = [
            (k, v)
            for k, v in by_scanner.items()
            if v.closed >= 3
            and v.win_rate is not None
            and v.win_rate >= 60
            and v.win_rate - overall.win_rate >= 10
        ]
        if candidates:
            hot_scanner = max(candidates, key=lambda x: x[1].win_rate or 0)[0]

    # By score bucket
    by_sc = _group_by(positions, lambda p: _score_bucket(p.conviction_score))
    by_score_bucket = {k: _compute_stats(k, v) for k, v in sorted(by_sc.items())}

    # By DTE bucket
    by_dte = _group_by(positions, lambda p: p.dte_bucket or "UNKNOWN")
    by_dte_bucket = {k: _compute_stats(k, v) for k, v in sorted(by_dte.items())}

    # By quality tier
    by_tier = _group_by(positions, lambda p: _val(p.quality_tier_at_entry) or "NONE")
    by_quality_tier = {k: _compute_stats(k, v) for k, v in sorted(by_tier.items())}

    # By convergence
    by_conv = _group_by(positions, lambda p: _convergence_label(p.convergence_count))
    by_convergence = {k: _compute_stats(k, v) for k, v in sorted(by_conv.items())}

    # Market context
    vix_level = None
    spy_direction = None
    if market_context:
        vix = market_context.get("vix")
        if isinstance(vix, dict):
            vix_level = vix.get("price")
        spy = market_context.get("spy")
        if isinstance(spy, dict):
            change = spy.get("change_percent", 0)
            if change > 0.3:
                spy_direction = "up"
            elif change < -0.3:
                spy_direction = "down"
            else:
                spy_direction = "flat"

    # Generate insights
    insights = _generate_insights(
        overall=overall,
        by_option_type=by_option_type,
        by_scanner=by_scanner,
        by_score_bucket=by_score_bucket,
        by_quality_tier=by_quality_tier,
        by_convergence=by_convergence,
        vix_level=vix_level,
    )

    return EdgeBriefing(
        period_start=period_start,
        period_end=period_end,
        trading_days=trading_days,
        total_positions=overall.total,
        total_closed=overall.closed,
        overall_win_rate=overall.win_rate,
        overall_avg_return=overall.avg_return,
        overall_expectancy=overall.expectancy,
        by_option_type=by_option_type,
        option_type_edge=option_type_edge,
        by_scanner=by_scanner,
        hot_scanner=hot_scanner,
        by_score_bucket=by_score_bucket,
        by_dte_bucket=by_dte_bucket,
        by_quality_tier=by_quality_tier,
        by_convergence=by_convergence,
        insights=insights,
        vix_level=vix_level,
        spy_direction=spy_direction,
    )


def _normalize_scanner(scanner_source: Optional[str]) -> str:
    if not scanner_source:
        return "UNKNOWN"
    s = scanner_source.upper()
    if s.endswith("_SCANNER"):
        s = s[: -len("_SCANNER")]
    return s


# ---------------------------------------------------------------------------
# Insight generation (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _generate_insights(
    overall: DimensionStats,
    by_option_type: dict[str, DimensionStats],
    by_scanner: dict[str, DimensionStats],
    by_score_bucket: dict[str, DimensionStats],
    by_quality_tier: dict[str, DimensionStats],
    by_convergence: dict[str, DimensionStats],
    vix_level: Optional[float],
) -> list[EdgeInsight]:
    """Generate up to 3 ranked insights from the data."""
    candidates: list[tuple[int, EdgeInsight]] = []

    # 1. Option Type Divergence
    call_s = by_option_type.get("CALL")
    put_s = by_option_type.get("PUT")
    if (
        call_s
        and put_s
        and call_s.win_rate is not None
        and put_s.win_rate is not None
        and call_s.closed >= 5
        and put_s.closed >= 5
    ):
        diff = abs(call_s.win_rate - put_s.win_rate)
        if diff >= 10:
            leader = "Puts" if put_s.win_rate > call_s.win_rate else "Calls"
            trailer = "Calls" if leader == "Puts" else "Puts"
            leader_wr = put_s.win_rate if leader == "Puts" else call_s.win_rate
            trailer_wr = call_s.win_rate if leader == "Puts" else put_s.win_rate
            strength = "strong" if diff >= 20 else "moderate"
            priority = 100 + int(diff)
            candidates.append((
                priority,
                EdgeInsight(
                    category="option_type",
                    headline=(
                        f"{leader} winning {leader_wr:.0f}% vs {trailer} at {trailer_wr:.0f}%"
                    ),
                    detail=f"{leader}: {max(call_s.closed, put_s.closed)} trades closed",
                    strength=strength,
                ),
            ))

    # 2. Hot Scanner
    if overall.win_rate is not None:
        for name, stats in by_scanner.items():
            if name == "UNKNOWN":
                continue
            if (
                stats.closed >= 3
                and stats.win_rate is not None
                and stats.win_rate >= 60
                and stats.win_rate - overall.win_rate >= 10
            ):
                diff = stats.win_rate - overall.win_rate
                strength = "strong" if diff >= 20 else "moderate"
                display_name = name.replace("_", " ").title()
                candidates.append((
                    90 + int(diff),
                    EdgeInsight(
                        category="scanner",
                        headline=(
                            f"{display_name} scanner running hot: "
                            f"{stats.win_rate:.0f}% WR ({stats.closed} trades)"
                        ),
                        strength=strength,
                    ),
                ))

    # 3. Score Threshold
    high_score = by_score_bucket.get("80+") or by_score_bucket.get("75-79")
    if (
        high_score
        and overall.win_rate is not None
        and high_score.win_rate is not None
        and high_score.closed >= 3
    ):
        diff = high_score.win_rate - overall.win_rate
        if diff >= 10:
            strength = "strong" if diff >= 20 else "moderate"
            candidates.append((
                80 + int(diff),
                EdgeInsight(
                    category="score",
                    headline=(
                        f"Score {high_score.label} winning at {high_score.win_rate:.0f}% "
                        f"({high_score.closed} trades)"
                    ),
                    detail="Favor high conviction opportunities",
                    strength=strength,
                ),
            ))

    # 4. Tier Validation
    t1 = by_quality_tier.get("TIER_1")
    t3 = by_quality_tier.get("TIER_3")
    if (
        t1
        and t3
        and t1.win_rate is not None
        and t3.win_rate is not None
        and t1.closed >= 3
        and t3.closed >= 3
    ):
        diff = t1.win_rate - t3.win_rate
        if diff >= 15:
            candidates.append((
                70 + int(diff),
                EdgeInsight(
                    category="tier",
                    headline=(
                        f"Tier 1 outperforming Tier 3: "
                        f"{t1.win_rate:.0f}% vs {t3.win_rate:.0f}% WR"
                    ),
                    detail="Quality tier system is validating",
                    strength="strong" if diff >= 25 else "moderate",
                ),
            ))

    # 5. Convergence Edge
    multi = by_convergence.get("3+")
    single = by_convergence.get("1")
    if (
        multi
        and single
        and multi.win_rate is not None
        and single.win_rate is not None
        and multi.closed >= 3
    ):
        diff = multi.win_rate - single.win_rate
        if diff >= 15:
            candidates.append((
                60 + int(diff),
                EdgeInsight(
                    category="convergence",
                    headline=(
                        f"Multi-scanner convergence (3+) at {multi.win_rate:.0f}% WR "
                        f"vs single at {single.win_rate:.0f}%"
                    ),
                    detail=f"{multi.closed} converged trades",
                    strength="strong" if diff >= 25 else "moderate",
                ),
            ))

    # Sort by priority descending, return top 3
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in candidates[:3]]


# ---------------------------------------------------------------------------
# Trade Context computation
# ---------------------------------------------------------------------------

def compute_trade_context(
    option_type: str,
    scanner: Optional[str],
    score: Optional[float],
    dte_bucket: Optional[str],
    positions: list,
) -> TradeContext:
    """Compute historical context for a trade with specific characteristics."""
    closed = [p for p in positions if _val(p.status) == "CLOSED"]

    if len(closed) < 3:
        return TradeContext(
            summary="Not enough closed trades for historical context",
            confidence_flag="insufficient",
        )

    # Broader slices
    type_matches = [p for p in closed if p.option_type == option_type]
    by_opt_stats = _compute_stats(option_type, type_matches) if type_matches else None

    scanner_norm = _normalize_scanner(scanner) if scanner else None
    scanner_matches = (
        [p for p in closed if _normalize_scanner(p.scanner_source) == scanner_norm]
        if scanner_norm
        else []
    )
    by_scanner_stats = (
        _compute_stats(scanner_norm or "UNKNOWN", scanner_matches) if scanner_matches else None
    )

    score_bucket = _score_bucket(score) if score is not None else None
    score_matches = (
        [p for p in closed if _score_bucket(p.conviction_score) == score_bucket]
        if score_bucket and score_bucket != "UNKNOWN"
        else []
    )
    by_score_stats = (
        _compute_stats(f"Score {score_bucket}", score_matches) if score_matches else None
    )

    # Exact match: option_type + scanner + score bucket
    exact_matches = [
        p
        for p in closed
        if p.option_type == option_type
        and (scanner_norm is None or _normalize_scanner(p.scanner_source) == scanner_norm)
        and (score_bucket is None or _score_bucket(p.conviction_score) == score_bucket)
    ]

    # Build description
    parts = [option_type]
    if scanner_norm and scanner_norm != "UNKNOWN":
        parts.append(scanner_norm.replace("_", " ").title())
    if score_bucket and score_bucket != "UNKNOWN":
        parts.append(f"Score {score_bucket}")
    description = " + ".join(parts)

    exact_stats = _compute_stats(description, exact_matches) if exact_matches else None

    # Confidence flag
    exact_closed = exact_stats.closed if exact_stats else 0
    if exact_closed >= 10:
        confidence = "high_confidence"
    elif exact_closed >= 5:
        confidence = "moderate"
    elif exact_closed >= 1:
        confidence = "low_sample"
    else:
        confidence = "insufficient"

    # Summary sentence
    if exact_stats and exact_stats.closed >= 3 and exact_stats.win_rate is not None:
        summary = (
            f"For {description} trades: "
            f"{exact_stats.wins} of {exact_stats.closed} were winners "
            f"({exact_stats.win_rate:.0f}%)"
        )
    elif by_opt_stats and by_opt_stats.closed >= 3 and by_opt_stats.win_rate is not None:
        summary = (
            f"All {option_type} trades: "
            f"{by_opt_stats.wins} of {by_opt_stats.closed} were winners "
            f"({by_opt_stats.win_rate:.0f}%)"
        )
    else:
        summary = "Limited historical data for this trade profile"

    return TradeContext(
        exact_match=exact_stats,
        exact_match_description=description,
        sample_size=exact_closed,
        by_option_type=by_opt_stats,
        by_scanner=by_scanner_stats,
        by_score_range=by_score_stats,
        summary=summary,
        confidence_flag=confidence,
    )
