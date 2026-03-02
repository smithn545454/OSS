"""Backtest Phase 3: Finalize Worker.

Runs as a single Lambda after all Phase 2 resolvers complete.
Computes metrics, equity curve, segments, readiness assessment,
and exports structured data to S3 for AI analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.backtest.coordinator import mark_run_completed, mark_run_failed
from app.backtest.metrics import calculate_metrics
from app.core.schemas import BacktestRunConfig, BacktestTrade
from app.db.backtest_pending_table import BacktestPendingTradeTable
from app.db.backtest_tables import BacktestTradeTable

logger = logging.getLogger(__name__)


async def run_phase3_finalize(
    run_id: str,
    config: BacktestRunConfig,
    s3_bucket: str,
) -> dict[str, Any]:
    """Compute final metrics, export results, and mark run COMPLETED.

    Args:
        run_id: Backtest run ID
        config: Run configuration
        s3_bucket: S3 bucket for exporting results

    Returns:
        Summary dict with final metrics.
    """
    logger.info(f"[Phase3] Finalizing backtest run {run_id}")

    try:
        # Load all completed trades
        trade_dicts = await BacktestTradeTable.list_by_run(run_id, limit=50000)
        trades: list[BacktestTrade] = []
        for td in trade_dicts:
            try:
                trades.append(BacktestTrade(**td))
            except Exception as e:
                logger.warning(f"Failed to parse trade: {e}")

        logger.info(f"[Phase3] Loaded {len(trades)} completed trades")

        # Compute metrics
        metrics = calculate_metrics(
            trades, starting_capital=config.starting_capital,
        )

        # Generate equity curve
        from app.backtest.equity_curve import generate_equity_curve, generate_monthly_pnl
        equity_curve = generate_equity_curve(
            trades,
            starting_capital=config.starting_capital,
            start_date=config.start_date,
            end_date=config.end_date,
        )

        monthly_pnl = generate_monthly_pnl(trades)

        # Segmented analysis
        from app.backtest.segments import SEGMENT_TYPES, analyze_segment
        segments: dict[str, list] = {}
        for seg_type in SEGMENT_TYPES:
            try:
                segments[seg_type] = analyze_segment(trades, seg_type)
            except Exception as e:
                logger.warning(f"Segment analysis '{seg_type}' failed: {e}")
                segments[seg_type] = []

        # Readiness assessment
        from app.backtest.readiness import assess_readiness
        readiness = assess_readiness(metrics, trades)

        # Export to S3 for AI analysis
        await _export_to_s3(
            s3_bucket=s3_bucket,
            run_id=run_id,
            config=config,
            trades=trade_dicts,
            metrics=metrics,
            equity_curve=equity_curve,
            monthly_pnl=monthly_pnl,
            segments=segments,
            readiness=readiness,
        )

        # Build summary with all computed data
        metrics["total_days"] = metrics.get("total_trades", 0)
        metrics["equity_curve_points"] = len(equity_curve)
        metrics["segments_computed"] = list(segments.keys())

        # Mark run COMPLETED
        await mark_run_completed(run_id, summary=metrics)

        # Clean up pending trades (no longer needed)
        try:
            cleaned = await BacktestPendingTradeTable.delete_by_run(run_id)
            logger.info(f"[Phase3] Cleaned up {cleaned} pending trades")
        except Exception as e:
            logger.warning(f"[Phase3] Pending trade cleanup failed (non-blocking): {e}")

        logger.info(
            f"[Phase3] Run {run_id} finalized: {len(trades)} trades, "
            f"win_rate={metrics.get('win_rate', 0):.1f}%, "
            f"sharpe={metrics.get('sharpe_ratio', 'N/A')}"
        )

        return {
            "status": "success",
            "phase": "finalize",
            "run_id": run_id,
            "total_trades": len(trades),
            "metrics": metrics,
        }

    except Exception as e:
        logger.error(f"[Phase3] Finalization failed for run {run_id}: {e}", exc_info=True)
        await mark_run_failed(run_id, error=f"Phase 3 finalization failed: {e}")
        return {"status": "error", "run_id": run_id, "error": str(e)}


async def _export_to_s3(
    s3_bucket: str,
    run_id: str,
    config: BacktestRunConfig,
    trades: list[dict],
    metrics: dict,
    equity_curve: list,
    monthly_pnl: list,
    segments: dict,
    readiness: dict,
) -> None:
    """Export comprehensive analysis data to S3 for AI queryability.

    Writes structured JSON files under results/RUN#{run_id}/ prefix.
    """
    import boto3

    s3 = boto3.client("s3")
    prefix = f"results/RUN#{run_id}"

    def _write_json(key: str, data: Any) -> None:
        s3.put_object(
            Bucket=s3_bucket,
            Key=f"{prefix}/{key}",
            Body=json.dumps(data, default=str, indent=2),
            ContentType="application/json",
        )

    try:
        # Trades with full attributes
        _write_json("trades.json", trades)

        # Summary: metrics + equity curve + segments + readiness
        _write_json("summary.json", {
            "metrics": metrics,
            "equity_curve": equity_curve,
            "monthly_pnl": monthly_pnl,
            "segments": segments,
            "readiness": readiness,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        })

        # Config snapshot
        _write_json("config.json", config.model_dump(mode="json"))

        # Pre-computed analysis context for AI chat
        analysis_context = _build_analysis_context(trades, metrics, segments, readiness)
        _write_json("analysis_context.json", analysis_context)

        logger.info(f"[Phase3] Exported results to s3://{s3_bucket}/{prefix}/")

    except Exception as e:
        logger.warning(f"[Phase3] S3 export failed (non-blocking): {e}")


def _build_analysis_context(
    trades: list[dict],
    metrics: dict,
    segments: dict,
    readiness: dict,
) -> dict:
    """Build a structured analysis context for AI chat interactions.

    Pre-computes patterns, anomalies, and analysis hints that help the AI
    advisor provide faster, more targeted insights.
    """
    context: dict[str, Any] = {
        "summary": {
            "total_trades": metrics.get("total_trades", 0),
            "win_rate": metrics.get("win_rate", 0),
            "net_pnl": metrics.get("net_pnl", 0),
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "profit_factor": metrics.get("profit_factor"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct", 0),
        },
        "readiness_verdict": readiness.get("verdict", "UNKNOWN"),
    }

    # Top winners and losers
    sorted_by_pnl = sorted(
        [t for t in trades if t.get("pnl_pct") is not None],
        key=lambda t: float(t.get("pnl_pct", 0)),
    )
    if sorted_by_pnl:
        context["top_winners"] = [
            {
                "ticker": t.get("ticker"),
                "scanner": t.get("scanner_type"),
                "pnl_pct": t.get("pnl_pct"),
                "entry_date": t.get("entry_date"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in sorted_by_pnl[-5:]
        ]
        context["top_losers"] = [
            {
                "ticker": t.get("ticker"),
                "scanner": t.get("scanner_type"),
                "pnl_pct": t.get("pnl_pct"),
                "entry_date": t.get("entry_date"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in sorted_by_pnl[:5]
        ]

    # Scanner performance summary
    scanner_segments = segments.get("scanner", [])
    if scanner_segments:
        context["scanner_performance"] = [
            {
                "scanner": s.get("segment"),
                "trades": s.get("trades"),
                "win_rate": s.get("win_rate"),
                "net_pnl": s.get("net_pnl"),
                "profit_factor": s.get("profit_factor"),
            }
            for s in scanner_segments
        ]

    # Exit reason distribution
    exit_reasons = metrics.get("exit_reasons", {})
    if exit_reasons:
        context["exit_distribution"] = exit_reasons

    # Monthly performance summary
    month_segments = segments.get("month", [])
    if month_segments:
        context["monthly_summary"] = [
            {
                "month": m.get("segment"),
                "trades": m.get("trades"),
                "win_rate": m.get("win_rate"),
                "net_pnl": m.get("net_pnl"),
            }
            for m in month_segments
        ]

    return context
