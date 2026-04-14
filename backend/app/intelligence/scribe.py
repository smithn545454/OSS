"""Nightly Scribe — Phase 1 intelligence agent.

Runs after market close on weekdays. Gathers the day's pipeline activity,
asks Claude Sonnet 4.5 to write a journal entry, and persists it as a
DynamoDB index row + S3 markdown artifact.

Entrypoint: ``run_nightly_scribe(target_date=None)``.
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.schemas import DiaryEntry, Verdict
from app.db.tables import (
    DiaryTable,
    EvaluationTable,
    PaperPositionTable,
    PipelineRunTable,
    PolicyTable,
    StageEventTable,
)
from app.intelligence.storage import put_markdown
from app.llm.provider import AnthropicProvider, LLMResponse

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "nightly_scribe.md"
_SUMMARY_MAX_LEN = 200


def _load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _resolve_target_date(target_date: Optional[date_cls]) -> date_cls:
    if target_date is not None:
        return target_date
    return datetime.now(timezone.utc).date()


def _iso_day_bounds(day: date_cls) -> tuple[str, str]:
    """Return (start_iso, end_iso) covering the UTC day."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


async def _gather_input_packet(day: date_cls) -> dict[str, Any]:
    """Gather everything Claude needs to write the entry.

    Returns a JSON-safe dict; any surprising missing pieces are logged and
    represented as empty lists / zero counts rather than raised.
    """
    start_iso, end_iso = _iso_day_bounds(day)
    day_str = day.isoformat()

    # Pipeline runs completed today
    runs: list[Any]
    try:
        runs = await PipelineRunTable.list_by_date_range(start_iso, end_iso, limit=50)
    except Exception:
        logger.exception("Failed to list pipeline runs")
        runs = []

    # Verdict counts — evaluations with SK >= start_iso (approximation; Dynamo
    # has no native upper bound via this helper, but same-day is dominant).
    verdict_counts: dict[str, int] = {}
    approve_samples: list[dict[str, Any]] = []
    for verdict in (Verdict.APPROVE, Verdict.WATCH, Verdict.REJECT):
        try:
            evals = await EvaluationTable.list_by_verdict_since(
                verdict.value, start_iso, limit=500
            )
        except Exception:
            logger.exception(f"Failed to list {verdict.value} evaluations")
            evals = []
        # Keep only those whose evaluated_at falls within today.
        todays = [e for e in evals if _eval_is_on_day(e, day_str)]
        verdict_counts[verdict.value] = len(todays)
        if verdict is Verdict.APPROVE:
            approve_samples = [
                {
                    "ticker": e.get("ticker"),
                    "conviction": e.get("conviction_score"),
                    "quality_tier": e.get("quality_tier"),
                    "scanners": e.get("scanner_source") or e.get("scanner_list"),
                }
                for e in todays[:10]
            ]

    # Paper positions closed today (filter list_closed in Python — small set).
    try:
        closed = await PaperPositionTable.list_closed(limit=200)
    except Exception:
        logger.exception("Failed to list closed paper positions")
        closed = []
    closed_today = [p for p in closed if getattr(p, "exit_date", None) == day_str]
    wins = [p for p in closed_today if (p.dollar_pnl or 0) > 0]
    losses = [p for p in closed_today if (p.dollar_pnl or 0) < 0]

    def _pos_summary(pos: Any) -> dict[str, Any]:
        return {
            "ticker": pos.underlying_ticker or pos.option_ticker,
            "option_ticker": pos.option_ticker,
            "entry_date": pos.entry_date,
            "exit_date": pos.exit_date,
            "exit_reason": pos.exit_reason,
            "pnl_dollars": pos.dollar_pnl,
            "pnl_pct": pos.current_pnl_pct,
            "days_held": pos.days_held,
            "scanner": pos.scanner_source,
        }

    closed_summary = {
        "total": len(closed_today),
        "wins": len(wins),
        "losses": len(losses),
        "detail": [_pos_summary(p) for p in closed_today[:20]],
    }

    # Active policy
    try:
        policy = await PolicyTable.get_active()
        policy_version = policy.version if policy else None
    except Exception:
        logger.exception("Failed to load active policy")
        policy_version = None

    # Anomalies — any stage event in today's runs with items_in>0 and items_out==0,
    # or drop_reasons dominated by a single bucket.
    anomalies: list[dict[str, Any]] = []
    for run in runs[:10]:  # cap the I/O even on noisy days
        try:
            events = await StageEventTable.list_by_run(run.run_id)
        except Exception:
            continue
        for ev in events:
            if ev.items_in > 0 and ev.items_out == 0:
                anomalies.append(
                    {
                        "run_id": run.run_id,
                        "stage": ev.stage,
                        "items_in": ev.items_in,
                        "items_out": ev.items_out,
                        "top_drop_reasons": _top_drop_reasons(ev.drop_reasons),
                    }
                )

    return {
        "date": day_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_runs": {
            "total": len(runs),
            "statuses": _count_statuses(runs),
        },
        "decisions": verdict_counts,
        "approve_samples": approve_samples,
        "closed_positions": closed_summary,
        "active_policy_version": policy_version,
        "anomalies": anomalies[:20],
        "notes": {
            "shadow_tracking": "not persisted in Phase 1",
            "regime_tag": "not populated in Phase 1",
        },
    }


def _eval_is_on_day(eval_item: dict[str, Any], day_str: str) -> bool:
    evaluated_at = eval_item.get("evaluated_at") or ""
    return evaluated_at.startswith(day_str)


def _count_statuses(runs: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in runs:
        status = getattr(r, "status", None)
        status_str = getattr(status, "value", status) or "unknown"
        counts[status_str] = counts.get(status_str, 0) + 1
    return counts


def _top_drop_reasons(reasons: dict[str, int], n: int = 3) -> list[tuple[str, int]]:
    return sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _build_metrics(packet: dict[str, Any]) -> dict[str, int]:
    decisions = packet.get("decisions", {})
    closed = packet.get("closed_positions", {})
    return {
        "approves": int(decisions.get("APPROVE", 0)),
        "watches": int(decisions.get("WATCH", 0)),
        "rejects": int(decisions.get("REJECT", 0)),
        "pipeline_runs": int(packet.get("pipeline_runs", {}).get("total", 0)),
        "closed_total": int(closed.get("total", 0)),
        "closed_wins": int(closed.get("wins", 0)),
        "closed_losses": int(closed.get("losses", 0)),
        "anomalies": len(packet.get("anomalies", [])),
    }


def _extract_summary(markdown: str) -> str:
    """Pull a short headline out of the generated markdown.

    Prefers the first non-empty line under a "Headline" heading; falls back
    to the first non-empty line.
    """
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return ""
    # Look for bold headline pattern after any "Headline" heading
    for i, line in enumerate(lines):
        if line.lower().startswith(("**headline", "# headline", "## headline", "1. **headline")):
            # Same line may contain content after the colon.
            if ":" in line:
                tail = line.split(":", 1)[1].strip().strip("*").strip()
                if tail:
                    return tail[:_SUMMARY_MAX_LEN]
            if i + 1 < len(lines):
                return lines[i + 1].strip("*").strip()[:_SUMMARY_MAX_LEN]
    return lines[0].lstrip("#").strip("*").strip()[:_SUMMARY_MAX_LEN]


async def _call_llm(
    input_packet: dict[str, Any],
    provider: Optional[AnthropicProvider] = None,
) -> LLMResponse:
    prompt_template = _load_prompt_template()
    prompt = prompt_template.replace(
        "{input_packet_json}", json.dumps(input_packet, indent=2, default=str)
    )
    scribe = provider or AnthropicProvider()
    return await scribe.generate(
        prompt=prompt,
        max_tokens=1500,
        system_prompt=(
            "You write clear, specific, non-hedging journal entries about an "
            "automated options-scanning system. Be direct and concise."
        ),
    )


async def run_nightly_scribe(
    target_date: Optional[date_cls] = None,
    provider: Optional[AnthropicProvider] = None,
) -> DiaryEntry:
    """Generate the diary entry for ``target_date`` (default: today UTC).

    Writes the markdown body to S3 and the index row to DynamoDB.
    Returns the persisted ``DiaryEntry``. Raises on LLM failure so callers
    (manual endpoint + EventBridge) can surface the error loudly.
    """
    day = _resolve_target_date(target_date)
    day_str = day.isoformat()
    logger.info(f"Nightly scribe starting for {day_str}")

    packet = await _gather_input_packet(day)
    response = await _call_llm(packet, provider=provider)
    if not response.success:
        raise RuntimeError(f"LLM call failed: {response.error or 'unknown error'}")
    if not response.content.strip():
        raise RuntimeError("LLM returned empty content")

    s3_key = f"diary/{day_str}/nightly.md"
    put_markdown(s3_key, response.content)

    entry = DiaryEntry(
        date=day_str,
        entry_type="nightly",
        summary=_extract_summary(response.content),
        s3_key=s3_key,
        metrics=_build_metrics(packet),
        policy_version=packet.get("active_policy_version"),
    )
    await DiaryTable.put(entry)
    logger.info(
        f"Nightly scribe wrote {s3_key} and index row "
        f"(summary='{entry.summary[:60]}...', metrics={entry.metrics})"
    )
    return entry
