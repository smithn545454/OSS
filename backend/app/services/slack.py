"""Slack alert service for high-conviction opportunities.

Sends alerts to configured Slack webhooks when pipeline evaluations
exceed the conviction score threshold and match optional setup rule filters.
Config is persisted in DynamoDB (paper-positions table) so it survives
Lambda cold starts.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# DynamoDB key constants (stored in paper-positions table)
ALERT_CONFIG_PK = "ALERT_CONFIG"
ALERT_CONFIG_SK = "CURRENT"
ALERT_LOG_PK_PREFIX = "ALERT_LOG"

# Frontend URL for evaluation detail links
FRONTEND_URL = "https://d3upsbalspxt4n.cloudfront.net"

# Default configuration
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "score_threshold": 75,
    "require_urgency_or_convergence": True,
    "cooldown_minutes": 30,
    "daily_cap": 10,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00",
    "webhook_channels": [],
    "setup_rule_filter_ids": [],
    "verdicts": ["APPROVE"],
    "max_premium": None,
    "cheap_gem_enabled": False,
    "cheap_gem_threshold": 60,
    "cheap_gem_max_premium": 1.50,
}


async def load_alert_config() -> dict[str, Any]:
    """Load alert config from DynamoDB, falling back to defaults."""
    try:
        from app.db.dynamodb import get_dynamodb
        from app.db.tables import PAPER_POSITIONS_TABLE

        db = get_dynamodb()
        item = await db.get_item(PAPER_POSITIONS_TABLE, ALERT_CONFIG_PK, ALERT_CONFIG_SK)
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            return {**DEFAULT_CONFIG, **item}
    except Exception as e:
        logger.warning(f"Failed to load alert config from DynamoDB: {e}")
    return {**DEFAULT_CONFIG}


async def save_alert_config(config: dict[str, Any]) -> dict[str, Any]:
    """Save alert config to DynamoDB."""
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import PAPER_POSITIONS_TABLE

    db = get_dynamodb()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "PK": ALERT_CONFIG_PK,
        "SK": ALERT_CONFIG_SK,
        **config,
        "updated_at": now,
    }
    await db.put_item(PAPER_POSITIONS_TABLE, item)

    result = {**config, "updated_at": now}
    return result


async def log_alert(
    contract_id: str,
    ticker: str,
    conviction_score: float,
    channel_name: str,
    status: str,
) -> None:
    """Write an alert log entry for audit/volume preview."""
    try:
        from app.db.dynamodb import get_dynamodb
        from app.db.tables import PAPER_POSITIONS_TABLE

        db = get_dynamodb()
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        ts = now.isoformat()

        item = {
            "PK": f"{ALERT_LOG_PK_PREFIX}#{date_str}",
            "SK": f"{ts}#{contract_id}",
            "contract_id": contract_id,
            "ticker": ticker,
            "conviction_score": conviction_score,
            "channel": channel_name,
            "status": status,
            "timestamp": ts,
        }
        await db.put_item(PAPER_POSITIONS_TABLE, item)
    except Exception as e:
        logger.warning(f"Failed to write alert log: {e}")


async def get_alert_history(
    date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get alert history entries for a given date."""
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import PAPER_POSITIONS_TABLE

    db = get_dynamodb()
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    items = await db.query(
        PAPER_POSITIONS_TABLE,
        f"{ALERT_LOG_PK_PREFIX}#{date}",
        limit=limit,
        scan_forward=False,
    )
    for item in items:
        item.pop("PK", None)
        item.pop("SK", None)
    return items


def mask_webhook_url(url: str) -> str:
    """Mask a webhook URL for safe display (show last 6 chars)."""
    if not url or len(url) < 10:
        return "***"
    return f"...{url[-6:]}"


def mask_config_for_response(config: dict[str, Any]) -> dict[str, Any]:
    """Mask webhook URLs in config before returning to client."""
    result = {**config}
    channels = result.get("webhook_channels", [])
    result["webhook_channels"] = [
        {
            "channel_name": ch.get("channel_name", ""),
            "url_masked": mask_webhook_url(ch.get("url", "")),
            "url": ch.get("url", ""),  # Include full URL for the config page
        }
        for ch in channels
    ]
    return result


class SlackAlertService:
    """Service for sending Slack alerts on high-conviction opportunities."""

    def __init__(self) -> None:
        """Initialize the Slack alert service."""
        settings = get_settings()

        # Legacy single webhook from env var (fallback)
        self._legacy_webhook_url = settings.slack_webhook_url

        # Alert tracking (in-memory, per Lambda instance)
        self._alert_timestamps: dict[str, datetime] = {}
        self._daily_count = 0
        self._last_reset_date: str | None = None

        # Config — loaded from DynamoDB on first use
        self._config: dict[str, Any] | None = None
        self._config_loaded = False

    async def _ensure_config(self) -> dict[str, Any]:
        """Load config from DynamoDB if not already loaded."""
        if not self._config_loaded:
            self._config = await load_alert_config()
            self._config_loaded = True
        return self._config or DEFAULT_CONFIG

    def configure(self, config: dict[str, Any]) -> None:
        """Apply config dict directly (used after PUT /api/alerts/config)."""
        self._config = {**DEFAULT_CONFIG, **config}
        self._config_loaded = True

    def _get_webhook_urls(self) -> list[dict[str, str]]:
        """Get list of webhook channels. Falls back to legacy env var."""
        config = self._config or DEFAULT_CONFIG
        channels = config.get("webhook_channels", [])
        if channels:
            return channels

        # Fallback to legacy single webhook URL from env var
        if self._legacy_webhook_url:
            return [{"channel_name": "#default", "url": self._legacy_webhook_url}]
        return []

    def _parse_time(self, time_str: str) -> time:
        """Parse HH:MM string to time object."""
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))

    def _is_within_quiet_hours(self) -> bool:
        """Check if current time is within quiet hours."""
        config = self._config or DEFAULT_CONFIG
        quiet_start = self._parse_time(config.get("quiet_hours_start", "22:00"))
        quiet_end = self._parse_time(config.get("quiet_hours_end", "08:00"))
        now = datetime.now(timezone.utc).time()

        if quiet_start > quiet_end:
            return now >= quiet_start or now <= quiet_end
        else:
            return quiet_start <= now <= quiet_end

    def _check_cooldown(self, contract_id: str) -> bool:
        """Check if contract is outside cooldown period. True = can send."""
        if contract_id not in self._alert_timestamps:
            return True

        config = self._config or DEFAULT_CONFIG
        cooldown = timedelta(minutes=config.get("cooldown_minutes", 30))
        last_alert = self._alert_timestamps[contract_id]
        return datetime.now(timezone.utc) - last_alert >= cooldown

    def _reset_daily_count_if_needed(self) -> None:
        """Reset daily count if new day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._daily_count = 0
            self._last_reset_date = today

    async def should_alert(
        self,
        conviction_score: float,
        urgency: str,
        convergence: int,
        contract_id: str,
        verdict: str = "APPROVE",
        matched_rule_ids: list[str] | None = None,
        premium: float = 0,
    ) -> tuple[bool, str | None]:
        """Determine if an alert should be sent.

        Returns:
            Tuple of (should_alert, reason_if_not)
        """
        config = await self._ensure_config()

        if not config.get("enabled", False):
            return False, "Alerts disabled"

        webhooks = self._get_webhook_urls()
        if not webhooks:
            return False, "No webhook URLs configured"

        # Check verdict filter
        allowed_verdicts = config.get("verdicts", ["APPROVE"])
        if verdict not in allowed_verdicts:
            return False, f"Verdict {verdict} not in allowed list {allowed_verdicts}"

        # Check score threshold (with cheap gem fallback)
        threshold = config.get("score_threshold", 75)
        is_cheap_gem = False
        if conviction_score < threshold:
            # Check if qualifies as a cheap gem (lower threshold for cheap options)
            cheap_gem_enabled = config.get("cheap_gem_enabled", False)
            cheap_gem_threshold = config.get("cheap_gem_threshold", 60)
            cheap_gem_max_premium = config.get("cheap_gem_max_premium", 1.50)
            if (
                cheap_gem_enabled
                and conviction_score >= cheap_gem_threshold
                and 0 < premium <= cheap_gem_max_premium
            ):
                is_cheap_gem = True
            else:
                return False, f"Score {conviction_score:.1f} below threshold {threshold}"

        # Check max premium (skip for cheap gems — already filtered by cheap_gem_max_premium)
        if not is_cheap_gem:
            max_premium = config.get("max_premium")
            if max_premium is not None and premium > max_premium:
                return False, f"Premium ${premium:.2f} above max ${max_premium:.2f}"

        # Check urgency/convergence requirement (skip for cheap gems)
        if not is_cheap_gem and config.get("require_urgency_or_convergence", True):
            if urgency != "act_now" and convergence < 2:
                return False, "Requires Act Now urgency or 2+ scanner convergence"

        # Check setup rule filter
        filter_ids = config.get("setup_rule_filter_ids", [])
        if filter_ids and matched_rule_ids is not None:
            if not any(rid in filter_ids for rid in matched_rule_ids):
                return False, "No matching setup rules in filter"

        # Check quiet hours
        if self._is_within_quiet_hours():
            return False, "Within quiet hours"

        # Reset daily count if new day
        self._reset_daily_count_if_needed()

        # Check daily cap
        daily_cap = config.get("daily_cap", 10)
        if self._daily_count >= daily_cap:
            return False, f"Daily alert cap ({daily_cap}) reached"

        # Check cooldown
        if not self._check_cooldown(contract_id):
            cooldown = config.get("cooldown_minutes", 30)
            return False, f"Contract in {cooldown}min cooldown"

        return True, "cheap_gem" if is_cheap_gem else None

    def _format_message(
        self,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        conviction_score: float,
        urgency: str,
        headline: str | None,
        theta_adj_ev: float,
        delta: float,
        premium: float,
        scanners: list[str],
        evaluation_id: str | None = None,
        trade_thesis: str | None = None,
        matched_rules: list[str] | None = None,
        pillar_scores: dict[str, float] | None = None,
        underlying_price: float = 0,
        gate_margin: float = 50,
        dte: int = 0,
        is_test: bool = False,
        is_cheap_gem: bool = False,
    ) -> dict[str, Any]:
        """Format Slack message with rich blocks.

        Returns:
            Slack blocks message payload
        """
        urgency_map = {
            "act_now": "\U0001f534",
            "hours": "\U0001f7e1",
            "patient": "\U0001f7e2",
        }
        urgency_emoji = urgency_map.get(urgency, "\u26aa")
        urgency_label = urgency.replace("_", " ").title()
        test_prefix = "[TEST] " if is_test else ""
        gem_prefix = "\U0001f48e Cheap Gem: " if is_cheap_gem else ""

        blocks: list[dict[str, Any]] = [
            # Header
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": (
                        f"\U0001f3af {test_prefix}{gem_prefix}High Conviction: "
                        f"{ticker} ${strike} {option_type}"
                    ),
                    "emoji": True,
                },
            },
            # Contract details
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4c4 *Contract*\n"
                        f"Exp {expiration} \u00b7 Premium ${premium:.2f} \u00b7 "
                        f"Delta {delta:.2f}\n"
                        f"\u03b8-Adj EV: ${theta_adj_ev:.2f} \u00b7 DTE: {dte}"
                    ),
                },
            },
            # Underlying
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4ca *Underlying: ${underlying_price:.2f}*\n"
                        f"{', '.join(scanners)} \u00b7 {urgency_emoji} {urgency_label}"
                    ),
                },
            },
        ]

        # Scores section
        pillars = pillar_scores or {}
        dir_score = pillars.get("DIRECTIONAL", 0)
        vol_score = pillars.get("VOLATILITY", 0)
        str_score = pillars.get("STRUCTURE", 0)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4c8 *Scores*\n"
                        f"Conviction: {conviction_score:.0f}/100\n"
                        f"Directional: {dir_score:.0f} \u00b7 "
                        f"Volatility: {vol_score:.0f} \u00b7 "
                        f"Structure: {str_score:.0f}\n"
                        f"Gate Margin: {gate_margin:.1f}"
                    ),
                },
            }
        )

        # Matched setup rules
        if matched_rules:
            rules_text = ", ".join(matched_rules[:5])
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"\U0001f3f7\ufe0f *Setup Rules Matched*\n{rules_text}",
                    },
                }
            )

        # Trade thesis (truncate to ~500 chars)
        thesis_text = trade_thesis or headline
        if thesis_text:
            if len(thesis_text) > 500:
                thesis_text = thesis_text[:497] + "..."
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"\U0001f4dd *Trade Thesis*\n{thesis_text}"},
                }
            )

        blocks.append({"type": "divider"})

        # View Details button + timestamp
        if evaluation_id:
            detail_url = f"{FRONTEND_URL}/evaluation/{ticker}/{evaluation_id}"
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View Details", "emoji": True},
                            "url": detail_url,
                            "style": "primary",
                        }
                    ],
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"\u23f0 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                        ),
                    }
                ],
            }
        )

        return {"blocks": blocks}

    async def send_alert(
        self,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        conviction_score: float,
        urgency: str,
        convergence: int,
        headline: str | None,
        theta_adj_ev: float,
        delta: float,
        premium: float,
        scanners: list[str],
        contract_id: str,
        verdict: str = "APPROVE",
        evaluation_id: str | None = None,
        trade_thesis: str | None = None,
        matched_rule_ids: list[str] | None = None,
        matched_rule_names: list[str] | None = None,
        pillar_scores: dict[str, float] | None = None,
        underlying_price: float = 0,
        gate_margin: float = 50,
        dte: int = 0,
    ) -> tuple[bool, str | None]:
        """Send a Slack alert for a high-conviction opportunity.

        Returns:
            Tuple of (success, error_message)
        """
        should_send, reason = await self.should_alert(
            conviction_score,
            urgency,
            convergence,
            contract_id,
            verdict=verdict,
            matched_rule_ids=matched_rule_ids,
            premium=premium,
        )

        if not should_send:
            logger.debug(f"Skipping alert for {contract_id}: {reason}")
            return False, reason

        is_cheap_gem = reason == "cheap_gem"

        # Format message
        message = self._format_message(
            ticker,
            strike,
            option_type,
            expiration,
            conviction_score,
            urgency,
            headline,
            theta_adj_ev,
            delta,
            premium,
            scanners,
            evaluation_id=evaluation_id,
            trade_thesis=trade_thesis,
            matched_rules=matched_rule_names,
            pillar_scores=pillar_scores,
            underlying_price=underlying_price,
            gate_margin=gate_margin,
            dte=dte,
            is_cheap_gem=is_cheap_gem,
        )

        # Send to all configured webhooks
        webhooks = self._get_webhook_urls()
        sent_count = 0

        for webhook in webhooks:
            url = webhook.get("url", "")
            channel = webhook.get("channel_name", "unknown")
            if not url:
                continue

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=message, timeout=10.0)
                    response.raise_for_status()
                sent_count += 1
                logger.info(
                    f"Sent Slack alert to {channel} for {contract_id} "
                    f"(score: {conviction_score:.0f})"
                )

                # Log the alert
                await log_alert(
                    contract_id=contract_id,
                    ticker=ticker,
                    conviction_score=conviction_score,
                    channel_name=channel,
                    status="sent",
                )

            except httpx.HTTPError as e:
                logger.error(f"Failed to send Slack alert to {channel}: {e}")
                await log_alert(
                    contract_id=contract_id,
                    ticker=ticker,
                    conviction_score=conviction_score,
                    channel_name=channel,
                    status="failed",
                )

        if sent_count == 0:
            return False, "All webhook sends failed"

        # Update tracking
        self._alert_timestamps[contract_id] = datetime.now(timezone.utc)
        self._daily_count += 1

        return True, None

    async def send_test_alert(self, channel_index: int | None = None) -> tuple[bool, str | None]:
        """Send a test alert using the top conviction APPROVE evaluation.

        Falls back to a generic message if no evaluations are available.
        """
        config = await self._ensure_config()
        webhooks = self._get_webhook_urls()

        if not webhooks:
            return False, "No webhook URLs configured"

        targets = webhooks
        if channel_index is not None and 0 <= channel_index < len(webhooks):
            targets = [webhooks[channel_index]]

        # Try to build a realistic message from the top conviction evaluation
        message = await self._build_test_message_from_eval()
        if message is None:
            # Fallback: generic message if no evaluations available
            message = self._build_generic_test_message(config)

        sent = 0
        for webhook in targets:
            url = webhook.get("url", "")
            if not url:
                continue
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=message, timeout=10.0)
                    response.raise_for_status()
                sent += 1
            except httpx.HTTPError as e:
                logger.error(f"Test alert failed for {webhook.get('channel_name')}: {e}")

        if sent == 0:
            return False, "All test sends failed"
        return True, None

    async def _build_test_message_from_eval(self) -> dict[str, Any] | None:
        """Build a realistic test alert from the top conviction APPROVE evaluation."""
        import asyncio

        try:
            from app.api.routes.evaluations import (
                calculate_gate_margin,
                calculate_theta_adjusted_ev,
            )
            from app.db.tables import (
                EvaluationTable,
                GateResultTable,
                PillarScoreTable,
                TradeThesisTable,
            )
            from app.scoring.conviction import (
                calculate_conviction_score,
                determine_urgency,
            )

            # Fetch recent APPROVE evaluations
            items = await EvaluationTable.list_by_verdict("APPROVE", limit=20)
            if not items:
                return None

            # Enrich and score each evaluation
            best_score = -1.0
            best_message: dict[str, Any] | None = None

            for item in items:
                evaluation_id = item.get("evaluation_id", "")
                if not evaluation_id:
                    continue

                try:
                    pillar_scores_raw, gate_results_raw, thesis = await asyncio.gather(
                        PillarScoreTable.list_by_evaluation(evaluation_id),
                        GateResultTable.list_by_evaluation(evaluation_id),
                        TradeThesisTable.get_by_evaluation_id(evaluation_id),
                    )
                except Exception:
                    continue

                # Build pillar scores dict
                pillar_scores: dict[str, float] = {}
                for ps in pillar_scores_raw:
                    pid = (
                        ps.pillar_id.value
                        if hasattr(ps.pillar_id, "value")
                        else str(ps.pillar_id)
                    )
                    pillar_scores[pid] = ps.score

                # Build gate results for margin calculation
                gate_dicts = [
                    {
                        "enabled": gr.enabled,
                        "passed": gr.passed,
                        "measured_value": gr.measured_value,
                        "threshold_value": gr.threshold_value,
                        "operator": (
                            gr.operator.value
                            if hasattr(gr.operator, "value")
                            else str(gr.operator)
                        ),
                    }
                    for gr in gate_results_raw
                ]
                gate_margin = calculate_gate_margin(gate_dicts)

                # Calculate theta-adjusted EV
                theta_ev = calculate_theta_adjusted_ev(
                    delta=item.get("delta", 0) or 0,
                    theta=item.get("theta", 0) or 0,
                    mid=item.get("mid", 0) or 0,
                    iv=item.get("iv", 0) or 0,
                    underlying_price=item.get("underlying_price", 0) or 0,
                    dte=item.get("dte", 30) or 30,
                )

                # Get scanner types
                scanner_source = item.get("scanner_source")
                scanner_types = [scanner_source] if scanner_source else []

                # Calculate conviction score
                premium = item.get("mid", 0) or 0
                final_score = item.get("final_score") or 0
                evaluated_at = item.get("evaluated_at", "")
                result = calculate_conviction_score(
                    final_score=final_score,
                    evaluated_at=evaluated_at,
                )

                if result.total > best_score:
                    best_score = result.total
                    urgency = determine_urgency(scanner_types, mid=premium)

                    # Get trade thesis text
                    trade_thesis_text = None
                    headline = None
                    if thesis:
                        trade_thesis_text = thesis.thesis if hasattr(thesis, "thesis") else None
                        if hasattr(thesis, "setup_summary") and thesis.setup_summary:
                            headline = thesis.setup_summary

                    # Enrich with sector for sector-aware rule matching
                    try:
                        from app.db.tables import SP500TickerTable
                        sector_map = await SP500TickerTable.get_sector_map()
                        slack_ticker = item.get("underlying_ticker", "")
                        if slack_ticker in sector_map:
                            item["sector"] = sector_map[slack_ticker]
                    except Exception:
                        pass

                    # Get matched rules
                    matched_rule_names: list[str] = []
                    try:
                        from app.paper_trading.pattern_discovery import list_setup_rules
                        from app.paper_trading.rule_matcher import (
                            format_matched_rules,
                            match_rules,
                        )

                        all_rules = await list_setup_rules()
                        active_rules = [r for r in all_rules if r.get("is_active", False)]
                        if active_rules:
                            matched = match_rules(
                                active_rules,
                                item,
                                item.get("decision", {}),
                                scanner_types,
                            )
                            if matched:
                                matched_rule_names = [
                                    r.get("name", "") for r in format_matched_rules(matched)
                                ]
                    except Exception:
                        pass

                    best_message = self._format_message(
                        ticker=item.get("underlying_ticker", "???"),
                        strike=item.get("strike", 0),
                        option_type=item.get("option_type", "CALL"),
                        expiration=item.get("expiration_date", "N/A"),
                        conviction_score=result.total,
                        urgency=urgency,
                        headline=headline,
                        theta_adj_ev=theta_ev,
                        delta=item.get("delta", 0) or 0,
                        premium=item.get("mid", 0) or 0,
                        scanners=scanner_types,
                        evaluation_id=evaluation_id,
                        trade_thesis=trade_thesis_text,
                        matched_rules=matched_rule_names if matched_rule_names else None,
                        pillar_scores=pillar_scores,
                        underlying_price=item.get("underlying_price", 0) or 0,
                        gate_margin=gate_margin,
                        dte=item.get("dte", 0) or 0,
                        is_test=True,
                    )

            return best_message

        except Exception as e:
            logger.warning(f"Failed to build realistic test alert: {e}")
            return None

    def _build_generic_test_message(self, config: dict[str, Any]) -> dict[str, Any]:
        """Build a generic test alert when no evaluations are available."""
        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "\u2705 OSS Alert Test — No Evaluations Available",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "Your webhook is working, but there are no recent APPROVE "
                            "evaluations to preview.\n\n"
                            "When the pipeline produces high-conviction opportunities, "
                            "alerts will appear here with full contract details, "
                            "pillar scores, and trade thesis.\n\n"
                            f"*Config:* Score threshold {config.get('score_threshold', 75)} · "
                            f"Daily cap {config.get('daily_cap', 10)} · "
                            f"Quiet hours {config.get('quiet_hours_start', '22:00')}"
                            f"–{config.get('quiet_hours_end', '08:00')} UTC"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"\u23f0 "
                                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                            ),
                        }
                    ],
                },
            ]
        }


# Singleton instance
_slack_service: SlackAlertService | None = None


def get_slack_service() -> SlackAlertService:
    """Get the singleton Slack alert service."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackAlertService()
    return _slack_service
