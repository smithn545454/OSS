"""Slack alert service — v5-native dual-conviction alerts.

Under the v5 regime, alerts are gated on HR Conviction (grand-slam track,
0-20 scale) and P Conviction (profitability track, 0-100 scale) rather than
the legacy blended conviction_score. The message leads with the verdict
driver (HR vs P), the matched archetype(s) with Wilson-lower rates, the
regime multiplier, and a home-run structural check — matching the
sharpshooter / 200%+ MFE north star of the system.

Config persists in DynamoDB (paper-positions table) so it survives Lambda
cold starts.
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

# v5 scoring thresholds (mirror GateConfig defaults in schemas.py)
V5_HR_FLOOR = 7.0
V5_P_FLOOR = 50.0
V5_HR_TIER1 = 14.0  # hr_conviction ≥ 14 → Sharpshooter / TIER_1

# v5-native default configuration. Prior schema (score_threshold,
# require_urgency_or_convergence, cheap_gem_*, per_scanner_thresholds,
# excluded_scanners, setup_rule_filter_ids) is retired — none of those
# knobs map cleanly onto HR/P conviction. If DynamoDB still holds old keys
# they are ignored on load.
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    # v5 eligibility (soft filters; Tier 1 bypasses these)
    "hr_only_mode": True,            # Pure Sharpshooter: short-circuit the P track entirely
    "hr_conviction_min": 10.0,       # ≥10/20 = solid HR bet (above 7.0 APPROVE floor)
    "p_conviction_min": 70.0,        # ≥70/100 = high-base-rate grinder (above 50 floor)
    "require_hr_archetype": False,   # Sharpshooter-only mode: filter pure P-driven trades
    "min_archetype_fit": 60.0,       # Discard weak-fit even when conviction clears
    "min_regime_alignment": 0.0,     # 0 = accept any regime; raise to require tailwind
    "max_premium": None,             # Optional $ ceiling on option mid (None = off)
    # Hard / regime-independent knobs
    "tier_1_bypass": True,           # Sharpshooter (TIER_1) always alerts
    "cooldown_minutes": 30,          # Per-contract cooldown
    "ticker_cooldown_minutes": 240,  # Per-underlying cooldown (collapses multi-strike dupes)
    "daily_cap": 10,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00",
    "webhook_channels": [],
    "verdicts": ["APPROVE"],
    # Convex pipeline cutover knobs. ``convex_min_tier`` filters which
    # tiers fire Slack alerts: "A" = Tier A only (most selective),
    # "B" = A+B (default — drops Tier C borderline candidates),
    # "C" = all Convex APPROVES through (noisy).
    "convex_min_tier": "B",
}

# Config keys carried forward. Anything else loaded from DynamoDB (including
# retired legacy keys) is filtered out so the old schema doesn't leak into
# new code paths.
_CONFIG_KEYS = set(DEFAULT_CONFIG.keys()) | {"updated_at"}


async def load_alert_config() -> dict[str, Any]:
    """Load alert config from DynamoDB, falling back to defaults.

    Filters out any legacy keys that are no longer part of the v5 schema so
    stale DynamoDB state doesn't silently re-enable retired behaviors.
    """
    try:
        from app.db.dynamodb import get_dynamodb
        from app.db.tables import PAPER_POSITIONS_TABLE

        db = get_dynamodb()
        item = await db.get_item(PAPER_POSITIONS_TABLE, ALERT_CONFIG_PK, ALERT_CONFIG_SK)
        if item:
            item.pop("PK", None)
            item.pop("SK", None)
            cleaned = {k: v for k, v in item.items() if k in _CONFIG_KEYS}
            return {**DEFAULT_CONFIG, **cleaned}
    except Exception as e:
        logger.warning(f"Failed to load alert config from DynamoDB: {e}")
    return {**DEFAULT_CONFIG}


async def save_alert_config(config: dict[str, Any]) -> dict[str, Any]:
    """Save alert config to DynamoDB (only v5 keys — legacy keys dropped)."""
    from app.db.dynamodb import get_dynamodb
    from app.db.tables import PAPER_POSITIONS_TABLE

    db = get_dynamodb()
    now = datetime.now(timezone.utc).isoformat()
    cleaned = {k: v for k, v in config.items() if k in _CONFIG_KEYS}
    item = {
        "PK": ALERT_CONFIG_PK,
        "SK": ALERT_CONFIG_SK,
        **cleaned,
        "updated_at": now,
    }
    await db.put_item(PAPER_POSITIONS_TABLE, item)
    return {**cleaned, "updated_at": now}


async def log_alert(
    contract_id: str,
    ticker: str,
    hr_conviction: float | None,
    p_conviction: float | None,
    driver: str | None,
    channel_name: str,
    status: str,
    quality_tier: str | None = None,
) -> None:
    """Write an alert log entry for audit/volume preview.

    ``conviction_score`` retained as a composite field for the history UI —
    populated with whichever track's conviction drove the alert (normalized
    to the 0-100 scale for legacy display).
    """
    try:
        from app.db.dynamodb import get_dynamodb
        from app.db.tables import PAPER_POSITIONS_TABLE

        db = get_dynamodb()
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        ts = now.isoformat()

        # Normalize a single display score — HR on the 0-100 scale (×5) when
        # HR-driven, else P as-is. Keeps the history table column meaningful.
        display_score: float = 0.0
        if driver == "HR" and hr_conviction is not None:
            display_score = hr_conviction * 5.0
        elif p_conviction is not None:
            display_score = p_conviction

        item = {
            "PK": f"{ALERT_LOG_PK_PREFIX}#{date_str}",
            "SK": f"{ts}#{contract_id}",
            "contract_id": contract_id,
            "ticker": ticker,
            "hr_conviction": hr_conviction,
            "p_conviction": p_conviction,
            "driver": driver,
            "conviction_score": display_score,
            "channel": channel_name,
            "status": status,
            "quality_tier": quality_tier,
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
            "url": ch.get("url", ""),  # Full URL for the config page
        }
        for ch in channels
    ]
    return result


def infer_verdict_driver(
    hr_conviction: float | None,
    p_conviction: float | None,
    hr_floor: float = V5_HR_FLOOR,
    p_floor: float = V5_P_FLOOR,
) -> str | None:
    """Infer which conviction track drove the APPROVE.

    Mirrors the logic in ``ThesisGenerator.build_input``: whichever track
    cleared its floor with the larger relative margin is the driver.
    Returns "HR", "P", or None (neither cleared — caller shouldn't be
    alerting anyway, but we surface None rather than guessing).
    """
    hr = hr_conviction or 0.0
    p = p_conviction or 0.0
    hr_cleared = hr >= hr_floor
    p_cleared = p >= p_floor
    if hr_cleared and not p_cleared:
        return "HR"
    if p_cleared and not hr_cleared:
        return "P"
    if hr_cleared and p_cleared:
        hr_margin = (hr - hr_floor) / hr_floor if hr_floor > 0 else 0.0
        p_margin = (p - p_floor) / p_floor if p_floor > 0 else 0.0
        return "HR" if hr_margin >= p_margin else "P"
    return None


class SlackAlertService:
    """v5-native Slack alert service — gates on HR/P conviction, not a blended score."""

    def __init__(self) -> None:
        settings = get_settings()

        # Legacy single webhook from env var (fallback)
        self._legacy_webhook_url = settings.slack_webhook_url

        # Config — loaded from DynamoDB on first use
        self._config: dict[str, Any] | None = None
        self._config_loaded = False

    async def _ensure_config(self) -> dict[str, Any]:
        if not self._config_loaded:
            self._config = await load_alert_config()
            self._config_loaded = True
        return self._config or DEFAULT_CONFIG

    def configure(self, config: dict[str, Any]) -> None:
        """Apply config dict directly (used after PUT /api/alerts/config).

        Filters out legacy keys so a stale payload can't reintroduce retired
        behavior mid-process.
        """
        cleaned = {k: v for k, v in config.items() if k in _CONFIG_KEYS}
        self._config = {**DEFAULT_CONFIG, **cleaned}
        self._config_loaded = True

    def _get_webhook_urls(self) -> list[dict[str, str]]:
        config = self._config or DEFAULT_CONFIG
        channels = config.get("webhook_channels", [])
        if channels:
            return channels
        if self._legacy_webhook_url:
            return [{"channel_name": "#default", "url": self._legacy_webhook_url}]
        return []

    def _parse_time(self, time_str: str) -> time:
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]))

    def _is_within_quiet_hours(self) -> bool:
        config = self._config or DEFAULT_CONFIG
        quiet_start = self._parse_time(config.get("quiet_hours_start", "22:00"))
        quiet_end = self._parse_time(config.get("quiet_hours_end", "08:00"))
        now = datetime.now(timezone.utc).time()
        if quiet_start > quiet_end:
            return now >= quiet_start or now <= quiet_end
        return quiet_start <= now <= quiet_end

    async def _count_sent_today(self) -> int:
        """Count ALERT_LOG entries with status='sent' for today (UTC).

        Backed by DynamoDB so the daily cap survives Lambda cold starts
        and multi-worker fan-out. Fails open (returns 0) on DB error — we
        log a warning rather than silence the whole alert stream.
        """
        try:
            from app.db.dynamodb import get_dynamodb
            from app.db.tables import PAPER_POSITIONS_TABLE

            db = get_dynamodb()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            items = await db.query(
                PAPER_POSITIONS_TABLE,
                f"{ALERT_LOG_PK_PREFIX}#{today}",
                limit=500,
                scan_forward=False,
            )
            return sum(1 for item in items if item.get("status") == "sent")
        except Exception as e:
            logger.warning(f"Failed to count today's alerts (failing open): {e}")
            return 0

    async def _last_alert_for_contract(self, contract_id: str) -> datetime | None:
        """Return the most recent ALERT_LOG timestamp for a contract.

        Looks at today's and yesterday's partitions to handle UTC midnight
        boundary. Fails open (returns None) on DB error.
        """
        try:
            from app.db.dynamodb import get_dynamodb
            from app.db.tables import PAPER_POSITIONS_TABLE

            db = get_dynamodb()
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

            latest: datetime | None = None
            for date_str in (today, yesterday):
                items = await db.query(
                    PAPER_POSITIONS_TABLE,
                    f"{ALERT_LOG_PK_PREFIX}#{date_str}",
                    limit=500,
                    scan_forward=False,
                )
                for item in items:
                    if item.get("contract_id") != contract_id:
                        continue
                    ts = item.get("timestamp")
                    if not ts:
                        continue
                    try:
                        parsed = datetime.fromisoformat(ts)
                    except ValueError:
                        continue
                    if latest is None or parsed > latest:
                        latest = parsed
            return latest
        except Exception as e:
            logger.warning(
                f"Failed to look up contract cooldown for {contract_id} "
                f"(failing open): {e}"
            )
            return None

    async def _last_alert_for_ticker(self, ticker: str) -> dict[str, Any] | None:
        """Return the most recent ALERT_LOG entry for a ticker (any contract).

        Used by the underlying-ticker cooldown so multiple strikes on the
        same symbol don't generate separate alerts. Returns the full item
        (for the upgrade-path conviction comparison). Fails open on DB error.
        """
        try:
            from app.db.dynamodb import get_dynamodb
            from app.db.tables import PAPER_POSITIONS_TABLE

            db = get_dynamodb()
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

            latest: dict[str, Any] | None = None
            latest_ts: datetime | None = None
            for date_str in (today, yesterday):
                items = await db.query(
                    PAPER_POSITIONS_TABLE,
                    f"{ALERT_LOG_PK_PREFIX}#{date_str}",
                    limit=500,
                    scan_forward=False,
                )
                for item in items:
                    if item.get("ticker") != ticker:
                        continue
                    if item.get("status") != "sent":
                        continue
                    ts = item.get("timestamp")
                    if not ts:
                        continue
                    try:
                        parsed = datetime.fromisoformat(ts)
                    except ValueError:
                        continue
                    if latest_ts is None or parsed > latest_ts:
                        latest_ts = parsed
                        latest = item
            return latest
        except Exception as e:
            logger.warning(
                f"Failed to look up ticker cooldown for {ticker} "
                f"(failing open): {e}"
            )
            return None

    async def should_alert(
        self,
        *,
        hr_conviction: float | None,
        p_conviction: float | None,
        hr_archetype_matched: str | None,
        hr_archetype_fit: float | None,
        p_archetype_fit: float | None,
        regime_alignment: float | None,
        contract_id: str,
        ticker: str | None = None,
        verdict: str = "APPROVE",
        premium: float = 0.0,
        quality_tier: str | None = None,
    ) -> tuple[bool, str | None]:
        """Decide whether to alert on a v5 decision.

        Returns ``(True, driver)`` where driver is ``"tier_1"``, ``"HR"``,
        or ``"P"`` to let the formatter pick a header lane. Returns
        ``(False, reason)`` otherwise.

        Gate order:
        1. Hard checks (enabled / webhooks / verdict / daily cap / cooldowns).
           Caps and cooldowns are DB-backed so they survive Lambda cold starts
           and fan-out across workers.
        2. Tier 1 bypass — Sharpshooter always alerts (modulo hard checks)
        3. Soft v5 checks (archetype presence, regime, conviction + fit,
           HR-only short-circuit)
        4. Premium ceiling (optional convenience filter)
        5. Quiet hours
        """
        config = await self._ensure_config()

        # --- Hard checks ---
        if not config.get("enabled", False):
            return False, "Alerts disabled"

        webhooks = self._get_webhook_urls()
        if not webhooks:
            return False, "No webhook URLs configured"

        allowed_verdicts = config.get("verdicts", ["APPROVE"])
        if verdict not in allowed_verdicts:
            return False, f"Verdict {verdict} not in allowed list {allowed_verdicts}"

        daily_cap = config.get("daily_cap", 10)
        sent_today = await self._count_sent_today()
        if sent_today >= daily_cap:
            return False, f"Daily alert cap ({daily_cap}) reached"

        contract_cooldown = config.get("cooldown_minutes", 30)
        last_contract_ts = await self._last_alert_for_contract(contract_id)
        if last_contract_ts is not None:
            elapsed = datetime.now(timezone.utc) - last_contract_ts
            if elapsed < timedelta(minutes=contract_cooldown):
                return False, f"Contract in {contract_cooldown}min cooldown"

        # Underlying-ticker cooldown: collapse multi-strike/multi-expiration
        # duplicates. Upgrade path allows a stronger HR-conviction candidate
        # to replace an earlier weaker alert within the same window.
        ticker_cooldown = config.get("ticker_cooldown_minutes", 240)
        if ticker and ticker_cooldown:
            last_ticker_alert = await self._last_alert_for_ticker(ticker)
            if last_ticker_alert is not None:
                ts = last_ticker_alert.get("timestamp")
                try:
                    last_ts = datetime.fromisoformat(ts) if ts else None
                except ValueError:
                    last_ts = None
                if last_ts is not None and (
                    datetime.now(timezone.utc) - last_ts
                    < timedelta(minutes=ticker_cooldown)
                ):
                    prior_hr = last_ticker_alert.get("hr_conviction") or 0.0
                    current_hr = hr_conviction or 0.0
                    if current_hr <= prior_hr:
                        return (
                            False,
                            f"Ticker {ticker} in {ticker_cooldown}min cooldown "
                            f"(prior HR {prior_hr:.1f} ≥ current {current_hr:.1f})",
                        )
                    # Upgrade path: HR conviction improved — allow the alert through.
                    logger.info(
                        f"Ticker {ticker} cooldown upgrade: HR "
                        f"{prior_hr:.1f} → {current_hr:.1f}"
                    )

        # --- Tier 1 fast path ---
        if quality_tier == "TIER_1" and config.get("tier_1_bypass", True):
            logger.info(
                f"Tier 1 fast path: alerting for {contract_id} "
                f"(hr={hr_conviction}, p={p_conviction})"
            )
            return True, "tier_1"

        # --- Soft v5 checks ---
        # Sharpshooter-only mode: require an HR archetype match
        if config.get("require_hr_archetype", False) and not hr_archetype_matched:
            return False, "No HR archetype matched (sharpshooter-only mode)"

        # Regime headwind filter
        min_regime = config.get("min_regime_alignment", 0.0) or 0.0
        if min_regime > 0 and (regime_alignment is None or regime_alignment < min_regime):
            return (
                False,
                f"Regime alignment {regime_alignment} below min {min_regime}",
            )

        # Conviction gate — in HR-only mode the P track is short-circuited
        # entirely (pure Sharpshooter); otherwise either track can clear.
        hr_min = config.get("hr_conviction_min", 10.0) or 0.0
        p_min = config.get("p_conviction_min", 70.0) or 0.0
        fit_min = config.get("min_archetype_fit", 60.0) or 0.0

        hr_passes = (
            (hr_conviction or 0.0) >= hr_min
            and (hr_archetype_fit or 0.0) >= fit_min
        )
        p_passes = (
            (p_conviction or 0.0) >= p_min
            and (p_archetype_fit or 0.0) >= fit_min
        )

        if config.get("hr_only_mode", False):
            if not hr_passes:
                return (
                    False,
                    (
                        f"HR-only mode: HR track failed "
                        f"({hr_conviction}/{hr_min}, fit "
                        f"{hr_archetype_fit}/{fit_min})"
                    ),
                )
        elif not (hr_passes or p_passes):
            return (
                False,
                (
                    f"Neither track passed: HR {hr_conviction}/{hr_min} "
                    f"fit {hr_archetype_fit}/{fit_min}, "
                    f"P {p_conviction}/{p_min} fit {p_archetype_fit}/{fit_min}"
                ),
            )

        # Premium ceiling (optional)
        max_premium = config.get("max_premium")
        if max_premium is not None and premium > max_premium:
            return False, f"Premium ${premium:.2f} above max ${max_premium:.2f}"

        # Quiet hours
        if self._is_within_quiet_hours():
            return False, "Within quiet hours"

        # Driver: in HR-only mode it's always HR (by construction); otherwise
        # whichever track cleared more decisively (HR preferred on ties —
        # sharpshooter-biased by design).
        if config.get("hr_only_mode", False):
            return True, "HR"
        driver = infer_verdict_driver(hr_conviction, p_conviction)
        if driver is None:
            # Both passed the user's floors but neither cleared the v5
            # system APPROVE floor — unusual but possible if user has set
            # min below 7.0/50.0. Prefer HR since we're hunting grand slams.
            driver = "HR" if hr_passes else "P"
        return True, driver

    def _format_message(
        self,
        *,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        driver: str,
        hr_conviction: float | None,
        p_conviction: float | None,
        hr_archetype_matched: str | None,
        hr_archetype_fit: float | None,
        hr_p_lower: float | None,
        hr_n_trades: int | None,
        p_archetype_matched: str | None,
        p_archetype_fit: float | None,
        p_win_lower: float | None,
        regime_alignment: float | None,
        delta: float,
        dte: int,
        premium: float,
        feasibility_ratio: float | None,
        expected_move_pct: float | None,
        underlying_price: float,
        scanners: list[str],
        setup_summary: str | None = None,
        tp2_pct: float | None = None,
        tp2_underlying: float | None = None,
        evaluation_id: str | None = None,
        quality_tier: str | None = None,
        is_test: bool = False,
    ) -> dict[str, Any]:
        """Format a v5 dual-conviction Slack message.

        Header lane is chosen by driver + tier so a reader can scan the
        channel at a glance:
        - ⭐ SHARPSHOOTER — TIER_1 (hr_conviction ≥ 14)
        - 🎯 HR GRAND SLAM — HR-driven, sub-TIER_1
        - 💰 P GRINDER — P-driven (no HR archetype or HR too weak)
        """
        test_prefix = "[TEST] " if is_test else ""

        if quality_tier == "TIER_1" or driver == "tier_1":
            header_emoji = "\u2b50"  # ⭐
            header_label = "SHARPSHOOTER"
        elif driver == "HR":
            header_emoji = "\U0001f3af"  # 🎯
            header_label = "HR GRAND SLAM"
        elif driver == "P":
            header_emoji = "\U0001f4b0"  # 💰
            header_label = "P GRINDER"
        else:
            header_emoji = "\U0001f50d"  # 🔍
            header_label = "Opportunity"

        header_text = (
            f"{header_emoji} {test_prefix}{header_label}: "
            f"{ticker} ${strike:g} {option_type}"
        )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text, "emoji": True},
            },
        ]

        # --- Contract + structural check ---
        feas_str = "N/A"
        feas_note = ""
        if feasibility_ratio is not None:
            feas_str = f"{feasibility_ratio:.2f}"
            feas_note = (
                " (achievable)" if feasibility_ratio <= 1.0 else " (stretch)"
            )
        em_str = (
            f"{expected_move_pct:.1f}%" if expected_move_pct is not None else "N/A"
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4c4 *Contract*\n"
                        f"Exp {expiration} \u00b7 Premium ${premium:.2f} \u00b7 "
                        f"\u0394 {delta:.2f} \u00b7 DTE {dte}\n"
                        f"Expected move {em_str} \u00b7 "
                        f"Feasibility {feas_str}{feas_note}"
                    ),
                },
            }
        )

        # --- Underlying + scanners ---
        scanner_list = ", ".join(scanners) if scanners else "—"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4ca *Underlying: ${underlying_price:.2f}*\n"
                        f"Scanners: {scanner_list}"
                    ),
                },
            }
        )

        # --- Dual-conviction block ---
        hr_val = f"{hr_conviction:.1f}" if hr_conviction is not None else "—"
        p_val = f"{p_conviction:.1f}" if p_conviction is not None else "—"
        hr_flag = (
            " \u2705" if hr_conviction is not None and hr_conviction >= V5_HR_FLOOR
            else " \u274c" if hr_conviction is not None
            else ""
        )
        p_flag = (
            " \u2705" if p_conviction is not None and p_conviction >= V5_P_FLOOR
            else " \u274c" if p_conviction is not None
            else ""
        )
        tier_label = {"TIER_1": "\u2b50 Tier 1", "TIER_2": "Tier 2", "TIER_3": "Tier 3"}.get(
            quality_tier or "", quality_tier or "—"
        )
        driver_label = (
            "\u2b50 TIER_1" if driver == "tier_1" else f"*{driver}*"
            if driver in ("HR", "P") else "—"
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4c8 *Convictions*\n"
                        f"HR {hr_val}/20{hr_flag} (floor {V5_HR_FLOOR:g}) \u00b7 "
                        f"P {p_val}/100{p_flag} (floor {V5_P_FLOOR:g})\n"
                        f"Driver: {driver_label} \u00b7 Quality: {tier_label}"
                    ),
                },
            }
        )

        # --- Archetype evidence ---
        archetype_lines = []
        if hr_archetype_matched:
            fit_s = f"{hr_archetype_fit:.0f}" if hr_archetype_fit is not None else "—"
            wl_s = (
                f"Wilson-lower {hr_p_lower * 100:.1f}%"
                if hr_p_lower is not None else "Wilson-lower —"
            )
            n_s = f"n={hr_n_trades}" if hr_n_trades else "n=—"
            archetype_lines.append(
                f"HR: *{hr_archetype_matched}* (fit {fit_s}, {wl_s} HR200, {n_s})"
            )
        else:
            archetype_lines.append("HR: _no archetype match_")
        if p_archetype_matched:
            fit_s = f"{p_archetype_fit:.0f}" if p_archetype_fit is not None else "—"
            wl_s = (
                f"Wilson-lower {p_win_lower * 100:.1f}%"
                if p_win_lower is not None else "Wilson-lower —"
            )
            archetype_lines.append(
                f"P: *{p_archetype_matched}* (fit {fit_s}, {wl_s} win)"
            )
        else:
            archetype_lines.append("P: _no archetype match_")
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\U0001f3f7\ufe0f *Archetypes*\n" + "\n".join(archetype_lines),
                },
            }
        )

        # --- Regime alignment ---
        if regime_alignment is not None:
            if regime_alignment > 1.05:
                regime_emoji = "\U0001f7e2"  # 🟢
                regime_label = "tailwind"
            elif regime_alignment < 0.95:
                regime_emoji = "\U0001f534"  # 🔴
                regime_label = "headwind"
            else:
                regime_emoji = "\u26aa"  # ⚪
                regime_label = "neutral"
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"{regime_emoji} Regime {regime_alignment:.2f}\u00d7 "
                                f"({regime_label})"
                            ),
                        }
                    ],
                }
            )

        # --- Home-run target preview (TP2 from thesis) ---
        if tp2_pct is not None:
            tp2_text = f"TP2 (home-run base case): +{tp2_pct:.0f}%"
            if tp2_underlying is not None:
                tp2_text += f" at ${tp2_underlying:.2f} underlying"
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"\U0001f3af *Target*\n{tp2_text}",
                    },
                }
            )

        # --- Setup summary (single paragraph — full thesis lives on the web page) ---
        if setup_summary:
            summary_text = setup_summary
            if len(summary_text) > 500:
                summary_text = summary_text[:497] + "..."
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"\U0001f4dd *Setup Summary*\n{summary_text}",
                    },
                }
            )

        blocks.append({"type": "divider"})

        # --- View Details button ---
        if evaluation_id:
            detail_url = f"{FRONTEND_URL}/evaluation/{ticker}/{evaluation_id}"
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View Details",
                                "emoji": True,
                            },
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
                            f"\u23f0 "
                            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                        ),
                    }
                ],
            }
        )

        return {"blocks": blocks}

    async def send_alert(
        self,
        *,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        contract_id: str,
        verdict: str = "APPROVE",
        quality_tier: str | None = None,
        # v5 score fields
        hr_conviction: float | None = None,
        p_conviction: float | None = None,
        hr_archetype_matched: str | None = None,
        hr_archetype_fit: float | None = None,
        hr_p_lower: float | None = None,
        hr_n_trades: int | None = None,
        p_archetype_matched: str | None = None,
        p_archetype_fit: float | None = None,
        p_win_lower: float | None = None,
        regime_alignment: float | None = None,
        # Contract + structural
        delta: float = 0.0,
        dte: int = 0,
        premium: float = 0.0,
        feasibility_ratio: float | None = None,
        expected_move_pct: float | None = None,
        underlying_price: float = 0.0,
        scanners: list[str] | None = None,
        # Thesis-derived context (from the completed TradeThesis, if any)
        setup_summary: str | None = None,
        tp2_pct: float | None = None,
        tp2_underlying: float | None = None,
        evaluation_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Send a v5 dual-conviction alert. Returns (sent, error_or_reason)."""
        scanners = scanners or []

        should_send, driver_or_reason = await self.should_alert(
            hr_conviction=hr_conviction,
            p_conviction=p_conviction,
            hr_archetype_matched=hr_archetype_matched,
            hr_archetype_fit=hr_archetype_fit,
            p_archetype_fit=p_archetype_fit,
            regime_alignment=regime_alignment,
            contract_id=contract_id,
            ticker=ticker,
            verdict=verdict,
            premium=premium,
            quality_tier=quality_tier,
        )

        if not should_send:
            logger.info(f"Skipping alert for {contract_id}: {driver_or_reason}")
            return False, driver_or_reason

        driver = driver_or_reason or "HR"

        message = self._format_message(
            ticker=ticker,
            strike=strike,
            option_type=option_type,
            expiration=expiration,
            driver=driver,
            hr_conviction=hr_conviction,
            p_conviction=p_conviction,
            hr_archetype_matched=hr_archetype_matched,
            hr_archetype_fit=hr_archetype_fit,
            hr_p_lower=hr_p_lower,
            hr_n_trades=hr_n_trades,
            p_archetype_matched=p_archetype_matched,
            p_archetype_fit=p_archetype_fit,
            p_win_lower=p_win_lower,
            regime_alignment=regime_alignment,
            delta=delta,
            dte=dte,
            premium=premium,
            feasibility_ratio=feasibility_ratio,
            expected_move_pct=expected_move_pct,
            underlying_price=underlying_price,
            scanners=scanners,
            setup_summary=setup_summary,
            tp2_pct=tp2_pct,
            tp2_underlying=tp2_underlying,
            evaluation_id=evaluation_id,
            quality_tier=quality_tier,
        )

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
                    f"(driver={driver}, hr={hr_conviction}, p={p_conviction})"
                )
                await log_alert(
                    contract_id=contract_id,
                    ticker=ticker,
                    hr_conviction=hr_conviction,
                    p_conviction=p_conviction,
                    driver=driver,
                    channel_name=channel,
                    status="sent",
                    quality_tier=quality_tier,
                )
            except httpx.HTTPError as e:
                logger.error(f"Failed to send Slack alert to {channel}: {e}")
                await log_alert(
                    contract_id=contract_id,
                    ticker=ticker,
                    hr_conviction=hr_conviction,
                    p_conviction=p_conviction,
                    driver=driver,
                    channel_name=channel,
                    status="failed",
                    quality_tier=quality_tier,
                )

        if sent_count == 0:
            return False, "All webhook sends failed"

        # Cap + cooldown state is derived from ALERT_LOG entries written by
        # log_alert() above, so there's no in-memory bookkeeping to update.
        return True, None

    async def send_convex_alert(
        self,
        finalised: Any,
    ) -> tuple[bool, str | None]:
        """Send a Convex-shaped alert for a finalised Convex candidate.

        Convex Decisions don't carry HR/P conviction — gating uses tier
        (A/B/C), composite strength, and smart-money confirmation
        instead. The cap + cooldown infrastructure is reused so a flood
        of Convex APPROVES doesn't blow past the daily cap.

        Returns ``(sent, error_or_reason)``.
        """
        config = await self._ensure_config()

        if not config.get("enabled", False):
            return False, "Alerts disabled"

        webhooks = self._get_webhook_urls()
        if not webhooks:
            return False, "No webhook URLs configured"

        candidate = finalised.candidate
        decision = finalised.decision

        tier_label = (
            finalised.tier.value
            if hasattr(finalised.tier, "value")
            else str(finalised.tier)
        )
        min_tier = str(config.get("convex_min_tier", "B")).upper()
        # Tier ordering: A (best) > B > C. Drop tiers worse than min.
        tier_rank = {"A": 0, "B": 1, "C": 2}
        if tier_rank.get(tier_label, 99) > tier_rank.get(min_tier, 99):
            return (
                False,
                f"Convex tier {tier_label} below min_tier {min_tier}",
            )

        # Pick the contract that matches direction.
        direction = (candidate.direction or "ambiguous").lower()
        if direction == "bearish" and candidate.selected_put is not None:
            selected = candidate.selected_put
        elif candidate.selected_call is not None:
            selected = candidate.selected_call
        else:
            selected = candidate.selected_put

        if selected is None:
            return False, "No selected contract on candidate"

        contract_id = selected.option_ticker

        # Cap + cooldown share state with the legacy alert path.
        daily_cap = config.get("daily_cap", 10)
        sent_today = await self._count_sent_today()
        if sent_today >= daily_cap:
            return False, f"Daily alert cap ({daily_cap}) reached"

        contract_cooldown = config.get("cooldown_minutes", 30)
        last_contract_ts = await self._last_alert_for_contract(contract_id)
        if last_contract_ts is not None:
            elapsed = datetime.now(timezone.utc) - last_contract_ts
            if elapsed < timedelta(minutes=contract_cooldown):
                return False, f"Contract in {contract_cooldown}min cooldown"

        ticker_cooldown = config.get("ticker_cooldown_minutes", 240)
        if ticker_cooldown:
            last_ticker_alert = await self._last_alert_for_ticker(candidate.ticker)
            if last_ticker_alert is not None:
                ts = last_ticker_alert.get("timestamp")
                try:
                    last_ts = datetime.fromisoformat(ts) if ts else None
                except ValueError:
                    last_ts = None
                if last_ts is not None and (
                    datetime.now(timezone.utc) - last_ts
                    < timedelta(minutes=ticker_cooldown)
                ):
                    return (
                        False,
                        f"Ticker {candidate.ticker} in {ticker_cooldown}min cooldown",
                    )

        if self._is_within_quiet_hours():
            return False, "Within quiet hours"

        message = self._format_convex_message(
            ticker=candidate.ticker,
            tier=tier_label,
            composite_strength=float(finalised.composite),
            smart_money=bool(candidate.smart_money_confirmation),
            direction=direction,
            selected=selected,
            stages=candidate.stages,
            evaluation_id=decision.evaluation_id,
        )

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
                    f"Sent Convex alert to {channel} for {contract_id} "
                    f"(tier={tier_label}, composite={finalised.composite:.2f})"
                )
                await log_alert(
                    contract_id=contract_id,
                    ticker=candidate.ticker,
                    hr_conviction=None,
                    p_conviction=None,
                    driver=f"convex_tier_{tier_label.lower()}",
                    channel_name=channel,
                    status="sent",
                    quality_tier=None,
                )
            except httpx.HTTPError as e:
                logger.error(f"Failed to send Convex alert to {channel}: {e}")
                await log_alert(
                    contract_id=contract_id,
                    ticker=candidate.ticker,
                    hr_conviction=None,
                    p_conviction=None,
                    driver=f"convex_tier_{tier_label.lower()}",
                    channel_name=channel,
                    status="failed",
                    quality_tier=None,
                )

        if sent_count == 0:
            return False, "All webhook sends failed"
        return True, None

    def _format_convex_message(
        self,
        *,
        ticker: str,
        tier: str,
        composite_strength: float,
        smart_money: bool,
        direction: str,
        selected: Any,
        stages: Any,
        evaluation_id: str,
    ) -> dict[str, Any]:
        """Format a Convex-shaped Slack message.

        Headers lead with tier (A/B/C) plus a smart-money flag so the
        reader can scan the channel without parsing pillar fields that
        no longer exist.
        """
        if tier == "A":
            header_emoji = "⭐"  # ⭐
            header_label = "CONVEX TIER A"
        elif tier == "B":
            header_emoji = "\U0001f3af"  # 🎯
            header_label = "CONVEX TIER B"
        else:
            header_emoji = "\U0001f50d"  # 🔍
            header_label = "CONVEX TIER C"

        sm_suffix = " ✨" if smart_money else ""  # ✨ when UV-confirmed
        opt_type = str(getattr(selected, "option_type", "?")).upper()
        strike = getattr(selected, "strike", 0) or 0
        expiry = getattr(selected, "expiry", "?")
        dte = getattr(selected, "dte", "?")
        delta = getattr(selected, "delta", None)
        bid = getattr(selected, "bid", None)
        ask = getattr(selected, "ask", None)

        header_text = (
            f"{header_emoji} {header_label}{sm_suffix}: "
            f"{ticker} ${strike:g} {opt_type}"
        )

        delta_str = f"{delta:.2f}" if delta is not None else "—"
        spread_str = "—"
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            spread_str = f"${bid:.2f} / ${ask:.2f} (mid ${mid:.2f})"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text, "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4c4 *Contract*\n"
                        f"Exp {expiry} · DTE {dte} · Δ {delta_str}\n"
                        f"Bid/Ask: {spread_str}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f4ca *Convex Profile*\n"
                        f"Direction: *{direction}* · "
                        f"Composite Strength: {composite_strength:.2f}\n"
                        f"Smart Money: "
                        f"{'✅ confirmed' if smart_money else '❌ not confirmed'}"
                    ),
                },
            },
        ]

        # Per-stage summary line.
        stage_lines: list[str] = []
        for n in (1, 2, 3, 4):
            payload = getattr(stages, f"stage_{n}", None)
            if payload is None:
                continue
            stage_name = getattr(payload, "stage_name", f"Stage {n}")
            strength = getattr(payload, "strength", None)
            strength_str = f"{strength:.2f}" if strength is not None else "—"
            summary = getattr(payload, "summary", "")
            stage_lines.append(
                f"*{stage_name}* (str {strength_str}): {summary}"
            )
        if stage_lines:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\U0001f9ed *Stages*\n" + "\n".join(stage_lines),
                    },
                }
            )

        if evaluation_id:
            link = f"{FRONTEND_URL}/evaluation/{ticker}/{evaluation_id}"
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View Details"},
                            "url": link,
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
                            f"⏰ "
                            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                        ),
                    }
                ],
            }
        )

        return {"blocks": blocks}

    async def send_test_alert(
        self, channel_index: int | None = None
    ) -> tuple[bool, str | None]:
        """Send a test alert using the top v5 APPROVE evaluation.

        Falls back to a generic message if no evaluations are available.
        """
        config = await self._ensure_config()
        webhooks = self._get_webhook_urls()

        if not webhooks:
            return False, "No webhook URLs configured"

        targets = webhooks
        if channel_index is not None and 0 <= channel_index < len(webhooks):
            targets = [webhooks[channel_index]]

        message = await self._build_test_message_from_eval()
        if message is None:
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
        """Build a realistic v5 test alert from the top HR-conviction APPROVE evaluation."""
        try:
            from app.db.tables import EvaluationTable, TradeThesisTable

            items = await EvaluationTable.list_by_verdict("APPROVE", limit=30)
            if not items:
                return None

            # Pick the eval with the highest HR conviction (most sharpshooter-like);
            # fall back to highest P conviction, then to first item.
            def _hr(item: dict[str, Any]) -> float:
                d = item.get("decision") or {}
                return d.get("hr_conviction") or 0.0

            def _p(item: dict[str, Any]) -> float:
                d = item.get("decision") or {}
                return d.get("p_conviction") or 0.0

            item = max(items, key=lambda i: (_hr(i), _p(i)))
            decision = item.get("decision") or {}

            hr = decision.get("hr_conviction")
            p = decision.get("p_conviction")
            driver = infer_verdict_driver(hr, p) or "HR"

            # Try to pick up the thesis's setup_summary + TP2
            setup_summary = None
            tp2_pct: float | None = None
            tp2_underlying: float | None = None
            try:
                thesis = await TradeThesisTable.get_by_evaluation_id(
                    item.get("evaluation_id", "")
                )
                if thesis and getattr(thesis, "status", None):
                    status = str(getattr(thesis.status, "value", thesis.status))
                    if status == "COMPLETED":
                        setup_summary = thesis.setup_summary or None
                        tps = getattr(thesis.exit_plan, "take_profits", []) or []
                        if len(tps) >= 2:
                            tp2_pct = tps[1].option_pnl_pct
                            tp2_underlying = tps[1].underlying_price
            except Exception:
                pass

            scanner_source = item.get("scanner_source")
            scanners = [scanner_source] if scanner_source else []

            return self._format_message(
                ticker=item.get("underlying_ticker", "???"),
                strike=item.get("strike", 0),
                option_type=str(item.get("option_type", "CALL")),
                expiration=item.get("expiration_date", "N/A"),
                driver=driver,
                hr_conviction=hr,
                p_conviction=p,
                hr_archetype_matched=decision.get("hr_archetype_matched"),
                hr_archetype_fit=decision.get("hr_archetype_fit"),
                hr_p_lower=decision.get("hr_p_lower"),
                hr_n_trades=decision.get("hr_n_trades"),
                p_archetype_matched=decision.get("p_archetype_matched"),
                p_archetype_fit=decision.get("p_archetype_fit"),
                p_win_lower=decision.get("p_win_lower"),
                regime_alignment=decision.get("regime_alignment"),
                delta=item.get("delta") or 0,
                dte=item.get("dte") or 0,
                premium=item.get("mid") or 0,
                feasibility_ratio=item.get("feasibility_ratio"),
                expected_move_pct=item.get("expected_move_pct"),
                underlying_price=item.get("underlying_price") or 0,
                scanners=scanners,
                setup_summary=setup_summary,
                tp2_pct=tp2_pct,
                tp2_underlying=tp2_underlying,
                evaluation_id=item.get("evaluation_id"),
                quality_tier=item.get("quality_tier"),
                is_test=True,
            )
        except Exception as e:
            logger.warning(f"Failed to build realistic v5 test alert: {e}")
            return None

    def _build_generic_test_message(self, config: dict[str, Any]) -> dict[str, Any]:
        """Fallback test alert when no evaluations are available."""
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
                            "Once the v5 pipeline produces a sharpshooter opportunity, "
                            "alerts will carry the full dual-conviction block: HR/P "
                            "scores, matched archetypes with Wilson-lower rates, "
                            "regime alignment, a home-run structural check, and the "
                            "TP2 home-run target price.\n\n"
                            f"*Config:* HR min "
                            f"{config.get('hr_conviction_min', 10):g}/20 \u00b7 "
                            f"P min {config.get('p_conviction_min', 70):g}/100 \u00b7 "
                            f"min archetype fit "
                            f"{config.get('min_archetype_fit', 60):g} \u00b7 "
                            f"daily cap {config.get('daily_cap', 10)}"
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


_slack_service: SlackAlertService | None = None


def get_slack_service() -> SlackAlertService:
    """Get the singleton Slack alert service."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackAlertService()
    return _slack_service
