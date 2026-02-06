"""Slack alert service for high-conviction opportunities.

Per Section 15 of OSS_Opportunities_Page_Specification.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone, timedelta
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class SlackAlertService:
    """Service for sending Slack alerts on high-conviction opportunities."""

    def __init__(self) -> None:
        """Initialize the Slack alert service."""
        settings = get_settings()
        self._webhook_url = settings.slack_webhook_url
        self._enabled = bool(self._webhook_url)
        
        # Alert tracking
        self._alert_timestamps: dict[str, datetime] = {}
        self._daily_count = 0
        self._last_reset_date: Optional[str] = None
        
        # Default configuration (can be overridden)
        self._score_threshold = 85
        self._require_urgency_or_convergence = True
        self._cooldown_minutes = 30
        self._daily_cap = 10
        self._quiet_hours_start = time(22, 0)  # 10 PM
        self._quiet_hours_end = time(8, 0)     # 8 AM

    def configure(
        self,
        score_threshold: Optional[int] = None,
        require_urgency_or_convergence: Optional[bool] = None,
        cooldown_minutes: Optional[int] = None,
        daily_cap: Optional[int] = None,
        quiet_hours_start: Optional[str] = None,
        quiet_hours_end: Optional[str] = None,
    ) -> None:
        """Update alert configuration.
        
        Args:
            score_threshold: Minimum conviction score to alert
            require_urgency_or_convergence: Require Act Now urgency or 2+ convergence
            cooldown_minutes: Minimum minutes between alerts for same contract
            daily_cap: Maximum alerts per day
            quiet_hours_start: Start of quiet hours (HH:MM)
            quiet_hours_end: End of quiet hours (HH:MM)
        """
        if score_threshold is not None:
            self._score_threshold = score_threshold
        if require_urgency_or_convergence is not None:
            self._require_urgency_or_convergence = require_urgency_or_convergence
        if cooldown_minutes is not None:
            self._cooldown_minutes = cooldown_minutes
        if daily_cap is not None:
            self._daily_cap = daily_cap
        if quiet_hours_start is not None:
            parts = quiet_hours_start.split(':')
            self._quiet_hours_start = time(int(parts[0]), int(parts[1]))
        if quiet_hours_end is not None:
            parts = quiet_hours_end.split(':')
            self._quiet_hours_end = time(int(parts[0]), int(parts[1]))

    def _is_within_quiet_hours(self) -> bool:
        """Check if current time is within quiet hours."""
        now = datetime.now(timezone.utc).time()
        
        # Handle overnight quiet hours
        if self._quiet_hours_start > self._quiet_hours_end:
            # Quiet hours span midnight
            return now >= self._quiet_hours_start or now <= self._quiet_hours_end
        else:
            return self._quiet_hours_start <= now <= self._quiet_hours_end

    def _check_cooldown(self, contract_id: str) -> bool:
        """Check if contract is within cooldown period.
        
        Returns:
            True if can send alert, False if in cooldown
        """
        if contract_id not in self._alert_timestamps:
            return True
        
        last_alert = self._alert_timestamps[contract_id]
        cooldown = timedelta(minutes=self._cooldown_minutes)
        
        return datetime.now(timezone.utc) - last_alert >= cooldown

    def _reset_daily_count_if_needed(self) -> None:
        """Reset daily count if new day."""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        
        if self._last_reset_date != today:
            self._daily_count = 0
            self._last_reset_date = today

    def should_alert(
        self,
        conviction_score: float,
        urgency: str,
        convergence: int,
        contract_id: str,
    ) -> tuple[bool, Optional[str]]:
        """Determine if an alert should be sent.
        
        Per Section 15.2:
        - Score >= threshold
        - Urgency == act_now OR Convergence >= 2
        - Not in cooldown
        - Not in quiet hours
        - Under daily cap
        
        Args:
            conviction_score: Conviction score (0-100)
            urgency: Urgency level (act_now, hours, patient)
            convergence: Number of scanners
            contract_id: Unique contract identifier
            
        Returns:
            Tuple of (should_alert, reason_if_not)
        """
        if not self._enabled:
            return False, "Slack alerts disabled"
        
        # Check score threshold
        if conviction_score < self._score_threshold:
            return False, f"Score {conviction_score:.1f} below threshold {self._score_threshold}"
        
        # Check urgency/convergence requirement
        if self._require_urgency_or_convergence:
            if urgency != 'act_now' and convergence < 2:
                return False, "Requires Act Now urgency or 2+ scanner convergence"
        
        # Check quiet hours
        if self._is_within_quiet_hours():
            return False, "Within quiet hours"
        
        # Reset daily count if new day
        self._reset_daily_count_if_needed()
        
        # Check daily cap
        if self._daily_count >= self._daily_cap:
            return False, f"Daily alert cap ({self._daily_cap}) reached"
        
        # Check cooldown
        if not self._check_cooldown(contract_id):
            return False, f"Contract in {self._cooldown_minutes}min cooldown"
        
        return True, None

    def _format_message(
        self,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        conviction_score: float,
        urgency: str,
        headline: Optional[str],
        theta_adj_ev: float,
        delta: float,
        premium: float,
        scanners: list[str],
    ) -> dict[str, Any]:
        """Format Slack message per spec Section 15.2.
        
        Returns:
            Slack blocks message payload
        """
        urgency_emoji = {'act_now': '🔴', 'hours': '🟡', 'patient': '🟢'}.get(urgency, '⚪')
        
        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🎯 High Conviction Opportunity: {ticker}",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Contract:*\n{ticker} ${strike} {option_type}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Expiration:*\n{expiration}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Conviction Score:*\n{conviction_score:.0f}/100"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Urgency:*\n{urgency_emoji} {urgency.replace('_', ' ').title()}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Delta:*\n{delta:.2f}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Premium:*\n${premium:.2f}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*θ-Adj EV:*\n${theta_adj_ev:.0f}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Scanners:*\n{', '.join(scanners)}"
                        }
                    ]
                },
                *([{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"📝 _{headline}_"
                    }
                }] if headline else []),
                {
                    "type": "divider"
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                        }
                    ]
                }
            ]
        }

    async def send_alert(
        self,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        conviction_score: float,
        urgency: str,
        convergence: int,
        headline: Optional[str],
        theta_adj_ev: float,
        delta: float,
        premium: float,
        scanners: list[str],
        contract_id: str,
    ) -> tuple[bool, Optional[str]]:
        """Send a Slack alert for a high-conviction opportunity.
        
        Args:
            ticker: Underlying ticker
            strike: Option strike price
            option_type: CALL or PUT
            expiration: Expiration date
            conviction_score: Conviction score
            urgency: Urgency level
            convergence: Number of scanners
            headline: LLM headline summary
            theta_adj_ev: Theta-adjusted EV
            delta: Option delta
            premium: Option premium (mid)
            scanners: List of scanner types
            contract_id: Unique contract identifier
            
        Returns:
            Tuple of (success, error_message)
        """
        # Check if should alert
        should_send, reason = self.should_alert(
            conviction_score, urgency, convergence, contract_id
        )
        
        if not should_send:
            logger.debug(f"Skipping alert for {contract_id}: {reason}")
            return False, reason
        
        # Format message
        message = self._format_message(
            ticker, strike, option_type, expiration,
            conviction_score, urgency, headline,
            theta_adj_ev, delta, premium, scanners
        )
        
        # Send to Slack
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._webhook_url,
                    json=message,
                    timeout=10.0,
                )
                response.raise_for_status()
            
            # Update tracking
            self._alert_timestamps[contract_id] = datetime.now(timezone.utc)
            self._daily_count += 1
            
            logger.info(f"Sent Slack alert for {contract_id} (score: {conviction_score:.0f})")
            return True, None
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False, str(e)


# Singleton instance
_slack_service: Optional[SlackAlertService] = None


def get_slack_service() -> SlackAlertService:
    """Get the singleton Slack alert service."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackAlertService()
    return _slack_service
