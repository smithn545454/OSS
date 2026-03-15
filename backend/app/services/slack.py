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

        # Check score threshold
        threshold = config.get("score_threshold", 75)
        if conviction_score < threshold:
            return False, f"Score {conviction_score:.1f} below threshold {threshold}"

        # Check urgency/convergence requirement
        if config.get("require_urgency_or_convergence", True):
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

        return True, None

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

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"\U0001f3af High Conviction: {ticker} ${strike} {option_type}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Conviction:*\n{conviction_score:.0f}/100"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Urgency:*\n{urgency_emoji} {urgency.replace('_', ' ').title()}",
                    },
                    {"type": "mrkdwn", "text": f"*Expiration:*\n{expiration}"},
                    {"type": "mrkdwn", "text": f"*Premium:*\n${premium:.2f}"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Delta:*\n{delta:.2f}"},
                    {"type": "mrkdwn", "text": f"*\u03b8-Adj EV:*\n${theta_adj_ev:.0f}"},
                    {"type": "mrkdwn", "text": f"*Scanners:*\n{', '.join(scanners)}"},
                ],
            },
        ]

        # Trade thesis (truncate to ~200 chars)
        thesis_text = trade_thesis or headline
        if thesis_text:
            if len(thesis_text) > 200:
                thesis_text = thesis_text[:197] + "..."
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"\U0001f4dd _{thesis_text}_"},
                }
            )

        # Matched rules
        if matched_rules:
            rules_text = ", ".join(matched_rules[:5])
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"\U0001f3f7\ufe0f *Rules:* {rules_text}"}
                    ],
                }
            )

        blocks.append({"type": "divider"})

        # View Details button + timestamp
        detail_url = None
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
        )

        if not should_send:
            logger.debug(f"Skipping alert for {contract_id}: {reason}")
            return False, reason

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
        """Send a test alert to verify webhook configuration."""
        config = await self._ensure_config()
        webhooks = self._get_webhook_urls()

        if not webhooks:
            return False, "No webhook URLs configured"

        targets = webhooks
        if channel_index is not None and 0 <= channel_index < len(webhooks):
            targets = [webhooks[channel_index]]

        message = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "\u2705 OSS Alert Test",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "This is a test alert from the Option Scanner System.\n"
                            f"Score threshold: {config.get('score_threshold', 75)}\n"
                            f"Daily cap: {config.get('daily_cap', 10)}\n"
                            f"Quiet hours: {config.get('quiet_hours_start', '22:00')}"
                            f" - {config.get('quiet_hours_end', '08:00')} UTC"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                "\u23f0 "
                                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                            ),
                        }
                    ],
                },
            ]
        }

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


# Singleton instance
_slack_service: SlackAlertService | None = None


def get_slack_service() -> SlackAlertService:
    """Get the singleton Slack alert service."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackAlertService()
    return _slack_service
