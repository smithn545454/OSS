"""Pattern Discovery engine for paper trading.

Analyzes closed trade data to identify statistically significant
trade archetypes using an LLM. Stores results in DynamoDB for
display on the Pattern Discovery tab.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.core.schemas import PaperPosition
from app.db.tables import PaperPositionTable
from app.llm.provider import get_provider
from app.paper_trading.pattern_discovery_prompt import (
    build_discovery_prompt,
    parse_discovery_response,
)

logger = logging.getLogger(__name__)

# DynamoDB key patterns for analysis results
ANALYSIS_PK_PREFIX = "ANALYSIS#"
SETUP_RULE_PK = "SETUP_RULE"

# Scoring regime version. Bumped when pillar/gate/conviction logic changes materially.
# v1: pre-Entry Quality pillar (before April 2026)
# v2: Entry Quality added but before directional enhancement (April 1-2)
# v3: Entry Quality structure, enhanced directional (EMA/RSI/ADX/OBV),
#     interaction bonus, premium leverage subscore (April 3+)
# v4: Sharpshooter regime (DIRECTIONAL_CONVICTION × MOVE_POTENTIAL × TRADE_STRUCTURE,
#     weighted-geometric-mean composite). Activated 2026-04-17 with policy v4.0.0.
CURRENT_SCORING_REGIME = "v4"

# Abbreviations for token-efficient CSV encoding
SCANNER_ABBREV = {
    "BREAKOUT_SCANNER": "BRK",
    "BREAKOUT": "BRK",
    "COMPRESSION_SCANNER": "CMP",
    "COMPRESSION": "CMP",
    "CHEAP_OPTIONS_SCANNER": "CHP",
    "CHEAP_OPTIONS": "CHP",
    "UNUSUAL_VOLUME_SCANNER": "UV",
    "UNUSUAL_VOLUME": "UV",
}
VERDICT_ABBREV = {"APPROVE": "A", "WATCH": "W", "REJECT": "R"}
TYPE_ABBREV = {"CALL": "C", "PUT": "P"}
SECTOR_ABBREV = {
    "Technology": "Tech", "Healthcare": "HC", "Energy": "Enrg",
    "Materials": "Matl", "Financials": "Fin",
    "Consumer Discretionary": "ConD", "Consumer Staples": "ConS",
    "Industrials": "Ind", "Utilities": "Util", "Real Estate": "RE",
    "Communication Services": "Comm",
}

# CSV columns sent to the LLM (short names to save tokens)
# Dropped: bucket (redundant with dte), mfe/mae (outcome tracking, not criteria),
# ev (niche), delta (correlated with moneyness+type)
# Pillar columns span both regimes; each position only populates one trio
# (p_pl/p_ub/p_sq for v3, p_dc/p_mp/p_ts for v4). Missing cells stay empty.
CSV_COLUMNS = [
    "tkr", "sec", "scn", "conv", "cscore",
    "p_pl", "p_ub", "p_sq", "p_dc", "p_mp", "p_ts",
    "type", "dte", "iv", "iv_pct", "ivrv", "theta_edge",
    "gate_m", "money_pct", "spread", "oi", "vol",
    "dte_earn", "atr", "rs", "feas", "ret", "days", "verdict",
]

# Estimated tokens per CSV row for dynamic limit calculation
# Empirically measured: ~33 tokens/row with 26 columns
TOKENS_PER_ROW_ESTIMATE = 33
PROMPT_OVERHEAD_TOKENS = 5_000
MAX_PROMPT_TOKENS = 195_000


def _fmt(val: Any, decimals: int = 2) -> str:
    """Format a value for CSV: round floats, empty string for None."""
    if val is None:
        return ""
    if isinstance(val, float):
        return str(round(val, decimals))
    return str(val)


def build_trade_csv(
    positions: list[PaperPosition],
    sector_map: dict[str, str] | None = None,
) -> str:
    """Convert positions to a compact CSV string for LLM analysis.

    Uses abbreviated column names, enum values, and sector names to
    minimize token count. ~28 tokens per row vs ~205 tokens per row with JSON.
    """
    buf = io.StringIO()
    buf.write(",".join(CSV_COLUMNS))
    buf.write("\n")

    sm = sector_map or {}
    for p in positions:
        ticker = p.underlying_ticker or p.option_ticker or ""
        scanner_raw = p.scanner_source or "UNKNOWN"
        verdict_raw = str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry))
        sector_full = sm.get(ticker, "")
        row = [
            ticker,
            SECTOR_ABBREV.get(sector_full, sector_full),
            SCANNER_ABBREV.get(scanner_raw, scanner_raw),
            str(p.convergence_count or 1),
            _fmt(p.conviction_score, 0),
            _fmt(p.pillar_premium_leverage, 0),
            _fmt(p.pillar_underlying_behavior, 0),
            _fmt(p.pillar_setup_quality, 0),
            _fmt(p.pillar_directional_conviction, 0),
            _fmt(p.pillar_move_potential, 0),
            _fmt(p.pillar_trade_structure, 0),
            TYPE_ABBREV.get(str(p.option_type or ""), str(p.option_type or "")),
            _fmt(p.dte_at_entry, 0),
            _fmt(p.entry_iv, 2),
            _fmt(p.entry_iv_percentile, 0),
            _fmt(p.entry_iv_rv_ratio, 2),
            _fmt(p.entry_theta_adjusted_edge, 2),
            _fmt(p.gate_margin, 2),
            _fmt(p.entry_moneyness_pct, 1),
            _fmt(p.entry_spread_pct, 2),
            _fmt(p.entry_open_interest, 0),
            _fmt(p.entry_volume, 0),
            _fmt(p.entry_days_to_earnings, 0),
            _fmt(p.entry_atr14_pct, 1),
            _fmt(p.entry_rs_20d, 1),
            _fmt(p.entry_feasibility_ratio, 1),
            str(round(p.current_pnl_pct, 1)),
            _fmt(p.days_held, 0),
            VERDICT_ABBREV.get(verdict_raw, verdict_raw),
        ]
        buf.write(",".join(row))
        buf.write("\n")

    return buf.getvalue()


async def create_analysis_stub(
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_sample: int = 5,
    min_win_rate: float = 0.55,
) -> dict[str, Any]:
    """Create a 'running' analysis stub and return immediately.

    Checks that enough closed trades exist, then writes a stub record
    to DynamoDB so the frontend can poll for results.

    Returns:
        Dict with analysis_id and status ("running" or "insufficient_data")
    """
    # Quick check: gather positions and count closed trades
    open_pos = await PaperPositionTable.list_open()
    closed_pos = await PaperPositionTable.list_closed()
    all_positions = open_pos + closed_pos

    if verdict and verdict.upper() != "ALL":
        all_positions = [
            p for p in all_positions
            if str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry)) == verdict
        ]
    if scanner and scanner.lower() != "all":
        all_positions = [
            p for p in all_positions
            if p.scanner_source == scanner or p.scanner_source == scanner + "_SCANNER"
        ]
    if period and period != "all":
        from datetime import timedelta
        days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
        days = days_map.get(period)
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            all_positions = [p for p in all_positions if p.entry_date >= cutoff]

    closed = [
        p for p in all_positions
        if str(getattr(p.status, "value", p.status)) == "CLOSED"
    ]

    if len(closed) < min_sample:
        return {
            "analysis_id": None,
            "status": "insufficient_data",
            "message": (
                f"Not enough closed trades for reliable pattern analysis. "
                f"Found {len(closed)} closed trades, need at least {min_sample}. "
                f"Continue accumulating data."
            ),
            "positions_analyzed": len(closed),
            "archetypes": [],
        }

    # Create a "running" stub in DynamoDB
    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    meta_item = {
        "PK": f"{ANALYSIS_PK_PREFIX}{analysis_id}",
        "SK": "META",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
        "period": period or "all",
        "verdict_filter": verdict,
        "scanner_filter": scanner,
        "archetype_count": 0,
    }
    await db.put_item(PaperPositionTable.TABLE, meta_item)

    index_item = {
        "PK": "ANALYSIS_INDEX",
        "SK": f"{now}#{analysis_id}",
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
        "archetype_count": 0,
        "period": period or "all",
    }
    await db.put_item(PaperPositionTable.TABLE, index_item)

    return {
        "analysis_id": analysis_id,
        "status": "running",
        "created_at": now,
        "positions_analyzed": len(closed),
        "archetypes": [],
    }


async def run_pattern_analysis(
    analysis_id: str,
    period: Optional[str] = None,
    verdict: Optional[str] = None,
    scanner: Optional[str] = None,
    min_sample: int = 5,
    min_win_rate: float = 0.55,
) -> dict[str, Any]:
    """Run pattern discovery analysis (worker — no timeout pressure).

    Called asynchronously by the Lambda worker handler. Queries positions,
    runs the LLM call, and updates the analysis stub in DynamoDB.

    Args:
        analysis_id: Pre-created analysis ID to update
        period: Filter by entry_date (7d, 14d, 30d, 90d, all)
        verdict: Filter by verdict (APPROVE, WATCH)
        scanner: Filter by scanner_source
        min_sample: Minimum trades per archetype (default 5)
        min_win_rate: Minimum win rate for archetype (default 55%)

    Returns:
        Analysis results with archetypes
    """
    logger.info("run_pattern_analysis v8 (CSV+trimming, chars_per_token=1.4)")
    from app.db.dynamodb import get_dynamodb

    now = datetime.now(timezone.utc).isoformat()
    db = get_dynamodb()

    try:
        # Gather all positions, applying filters
        open_pos = await PaperPositionTable.list_open()
        closed_pos = await PaperPositionTable.list_closed()
        all_positions = open_pos + closed_pos

        if verdict and verdict.upper() != "ALL":
            all_positions = [
                p for p in all_positions
                if str(getattr(p.verdict_at_entry, "value", p.verdict_at_entry)) == verdict
            ]
        if scanner and scanner.lower() != "all":
            all_positions = [
                p for p in all_positions
                if p.scanner_source == scanner or p.scanner_source == scanner + "_SCANNER"
            ]
        if period and period != "all":
            from datetime import timedelta
            days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
            days = days_map.get(period)
            if days:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
                all_positions = [p for p in all_positions if p.entry_date >= cutoff]

        closed = [
            p for p in all_positions
            if str(getattr(p.status, "value", p.status)) == "CLOSED"
        ]

        total_closed = len(closed)
        wins = sum(1 for p in closed if p.current_pnl_pct > 0)
        avg_return = sum(p.current_pnl_pct for p in closed) / total_closed if total_closed else 0

        context = {
            "total_trades": total_closed,
            "win_rate": round(wins / total_closed * 100, 1) if total_closed else 0,
            "avg_return": round(avg_return, 2),
            "min_sample_size": min_sample,
            "min_win_rate_pct": round(min_win_rate * 100, 1),
        }

        # Fetch sector map for sector-aware pattern discovery
        try:
            from app.db.tables import SP500TickerTable
            sector_map = await SP500TickerTable.get_sector_map()
        except Exception:
            sector_map = {}

        # Sort by most recent, build CSV, and trim to fit token budget
        closed_sorted = sorted(closed, key=lambda p: p.entry_date, reverse=True)
        trade_csv = build_trade_csv(closed_sorted, sector_map)

        # Estimate tokens from character count. Empirical measurement:
        # 308,458 prompt chars = 207,668 API tokens = 1.49 chars/token.
        # CSV numbers/commas/decimals each become separate tokens.
        # Use 1.4 for safety margin.
        chars_per_token = 1.4
        csv_chars = len(trade_csv)
        estimated_tokens = csv_chars / chars_per_token + PROMPT_OVERHEAD_TOKENS
        logger.info(
            f"CSV built: {len(closed_sorted)} rows, {csv_chars} chars, "
            f"est_tokens={int(estimated_tokens)}, limit={MAX_PROMPT_TOKENS}"
        )
        sampled = estimated_tokens > MAX_PROMPT_TOKENS

        if sampled:
            # Calculate how many chars of CSV data we can fit
            csv_lines = trade_csv.split("\n")
            header = csv_lines[0]
            data_lines = [line for line in csv_lines[1:] if line.strip()]
            target_chars = int((MAX_PROMPT_TOKENS - PROMPT_OVERHEAD_TOKENS) * chars_per_token)
            # Trim rows from the end until we fit
            trimmed = []
            char_count = len(header) + 1  # +1 for newline
            for line in data_lines:
                char_count += len(line) + 1
                if char_count > target_chars:
                    break
                trimmed.append(line)
            trade_csv = header + "\n" + "\n".join(trimmed) + "\n"
            closed_sorted = closed_sorted[:len(trimmed)]
            logger.info(
                f"Trimmed to {len(trimmed)} of {total_closed} trades "
                f"(csv_chars={len(trade_csv)}, target_chars={target_chars})"
            )

        context["sampled"] = sampled
        context["sample_size"] = len(closed_sorted)

        # Build prompt
        prompt = build_discovery_prompt(trade_csv, context)
        logger.info(
            f"Prompt built: {len(prompt)} chars, csv_in_prompt={len(trade_csv)} chars"
        )

        provider = get_provider("anthropic")
        llm_response = await provider.generate(prompt, max_tokens=4000)

        if not llm_response.success:
            raise RuntimeError(f"LLM returned error: {llm_response.error}")

        # Parse response
        archetypes = parse_discovery_response(llm_response.content)

        # Update stub with results
        meta_updates = {
            "status": "complete",
            "positions_analyzed": total_closed,
            "archetype_count": len(archetypes),
            "context": context,
        }
        await db.update_item(
            PaperPositionTable.TABLE,
            f"{ANALYSIS_PK_PREFIX}{analysis_id}",
            "META",
            meta_updates,
        )

        # Update index item status
        # Query to find the index SK for this analysis
        index_items = await db.query(PaperPositionTable.TABLE, "ANALYSIS_INDEX")
        for item in index_items:
            if item.get("analysis_id") == analysis_id:
                await db.update_item(
                    PaperPositionTable.TABLE,
                    "ANALYSIS_INDEX",
                    item["SK"],
                    {"status": "complete", "archetype_count": len(archetypes)},
                )
                break

        # Store each archetype
        for i, archetype in enumerate(archetypes):
            arch_item = {
                "PK": f"{ANALYSIS_PK_PREFIX}{analysis_id}",
                "SK": f"ARCHETYPE#{i:03d}",
                **archetype,
            }
            await db.put_item(PaperPositionTable.TABLE, arch_item)

        logger.info(
            f"Pattern analysis {analysis_id} complete: "
            f"{len(archetypes)} archetypes from {total_closed} trades"
        )

        return {
            "analysis_id": analysis_id,
            "status": "complete",
            "created_at": now,
            "positions_analyzed": total_closed,
            "context": context,
            "archetypes": archetypes,
        }

    except Exception as e:
        logger.error(f"Pattern analysis {analysis_id} failed: {e}")
        # Update stub and index to error status
        try:
            await db.update_item(
                PaperPositionTable.TABLE,
                f"{ANALYSIS_PK_PREFIX}{analysis_id}",
                "META",
                {"status": "error", "error_message": str(e)},
            )
            # Also update the index item so listing shows "error" not "running"
            index_items = await db.query(PaperPositionTable.TABLE, "ANALYSIS_INDEX")
            for item in index_items:
                if item.get("analysis_id") == analysis_id:
                    await db.update_item(
                        PaperPositionTable.TABLE,
                        "ANALYSIS_INDEX",
                        item["SK"],
                        {"status": "error"},
                    )
                    break
        except Exception:
            logger.error(f"Failed to update analysis {analysis_id} error status")

        return {
            "analysis_id": analysis_id,
            "status": "error",
            "message": f"AI analysis failed: {str(e)}",
            "positions_analyzed": 0,
            "archetypes": [],
        }


async def list_analyses(limit: int = 10) -> list[dict[str, Any]]:
    """List recent pattern analysis runs.

    Uses the ANALYSIS_INDEX partition for efficient querying.

    Returns:
        List of analysis metadata dicts, most recent first
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        "ANALYSIS_INDEX",
        limit=limit,
        scan_forward=False,  # Most recent first (SK is timestamp-based)
    )

    analyses = []
    for item in items:
        item.pop("PK", None)
        item.pop("SK", None)
        analyses.append(item)

    return analyses


async def get_analysis(analysis_id: str) -> Optional[dict[str, Any]]:
    """Get a specific analysis with its archetypes.

    Args:
        analysis_id: The analysis UUID

    Returns:
        Analysis with metadata and archetypes, or None if not found
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        f"{ANALYSIS_PK_PREFIX}{analysis_id}",
    )

    if not items:
        return None

    meta = None
    archetypes = []
    for item in items:
        sk = item.pop("SK", "")
        item.pop("PK", None)
        if sk == "META":
            meta = item
        elif sk.startswith("ARCHETYPE#"):
            archetypes.append(item)

    if not meta:
        return None

    # Sort archetypes by index
    archetypes.sort(key=lambda x: x.get("SK", ""))
    meta["archetypes"] = archetypes
    return meta


# ============================================================================
# Setup Rules
# ============================================================================


async def list_setup_rules() -> list[dict[str, Any]]:
    """List all setup rules."""
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    items = await db.query(
        PaperPositionTable.TABLE,
        SETUP_RULE_PK,
    )

    rules = []
    for item in items:
        item.pop("PK", None)
        item.pop("SK", None)
        item["is_stale"] = item.get("regime", "v1") != CURRENT_SCORING_REGIME
        rules.append(item)

    return rules


async def create_setup_rule(rule_data: dict[str, Any]) -> dict[str, Any]:
    """Create a setup rule from an archetype.

    Args:
        rule_data: Dict with name, criteria, source_analysis_id, performance_at_creation

    Returns:
        The created rule with generated rule_id
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    rule_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "PK": SETUP_RULE_PK,
        "SK": f"RULE#{rule_id}",
        "rule_id": rule_id,
        "name": rule_data.get("name", "Unnamed Rule"),
        "criteria": rule_data.get("criteria", {}),
        "is_active": True,
        "mode": rule_data.get("mode", "production"),
        "created_at": now,
        "source": rule_data.get("source", "ai"),
        "source_analysis_id": rule_data.get("source_analysis_id"),
        "performance_at_creation": rule_data.get("performance_at_creation"),
        "regime": rule_data.get("regime", CURRENT_SCORING_REGIME),
    }

    await db.put_item(PaperPositionTable.TABLE, item)
    item.pop("PK", None)
    item.pop("SK", None)
    return item


async def update_setup_rule(rule_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Update a setup rule (e.g., toggle is_active).

    Args:
        rule_id: The rule UUID
        updates: Fields to update

    Returns:
        Updated rule or None if not found
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    item = await db.update_item(
        PaperPositionTable.TABLE,
        SETUP_RULE_PK,
        f"RULE#{rule_id}",
        updates,
    )

    if item:
        item.pop("PK", None)
        item.pop("SK", None)
    return item


async def delete_setup_rule(rule_id: str) -> bool:
    """Delete a setup rule.

    Args:
        rule_id: The rule UUID

    Returns:
        True if deleted successfully
    """
    from app.db.dynamodb import get_dynamodb
    db = get_dynamodb()

    try:
        await db.delete_item(
            PaperPositionTable.TABLE,
            SETUP_RULE_PK,
            f"RULE#{rule_id}",
        )
        return True
    except Exception as e:
        logger.error(f"Failed to delete setup rule {rule_id}: {e}")
        return False
