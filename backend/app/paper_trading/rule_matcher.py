"""Setup rule matching engine.

Matches evaluations against saved setup rules. Each criterion in a rule
must be satisfied for the rule to match. Pure functions with no side effects.
"""

import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


def _check_criterion(
    key: str,
    value: Any,
    evaluation: dict[str, Any],
    decision: dict[str, Any],
    scanners: list[str],
) -> bool:
    """Check a single criterion against evaluation + decision data.

    Returns True if the criterion is satisfied, False otherwise.
    Unknown criteria are ignored (return True) to be forward-compatible.
    """
    try:
        if key == "scanners":
            # At least one of the rule's scanners must appear in the evaluation's scanner list
            if not isinstance(value, list) or not value:
                return True
            return any(s in scanners for s in value)

        if key == "scanner_confluence":
            # Boolean: requires 2+ scanners
            if value:
                return len(scanners) >= 2
            return True

        if key == "option_type":
            eval_type = str(evaluation.get("option_type", "")).upper()
            return eval_type == str(value).upper()

        if key == "conviction_score_min":
            score = decision.get("final_score")
            return score is not None and score >= float(value)

        if key == "conviction_score_max":
            score = decision.get("final_score")
            return score is not None and score <= float(value)

        if key == "pillar_directional_min":
            score = decision.get("directional_score")
            return score is not None and score >= float(value)

        if key == "pillar_volatility_min":
            score = decision.get("volatility_score")
            return score is not None and score >= float(value)

        if key == "pillar_structure_min":
            score = decision.get("structure_score")
            return score is not None and score >= float(value)

        if key == "dte_min":
            dte = evaluation.get("dte")
            return dte is not None and dte >= int(value)

        if key == "dte_max":
            dte = evaluation.get("dte")
            return dte is not None and dte <= int(value)

        if key == "entry_iv_min":
            iv = evaluation.get("iv")
            return iv is not None and iv >= float(value)

        if key == "entry_iv_max":
            iv = evaluation.get("iv")
            return iv is not None and iv <= float(value)

        # Volatility feature criteria (from FeatureValueTable, merged into evaluation dict)
        if key == "iv_percentile_max":
            pct = evaluation.get("iv_percentile")
            return pct is not None and pct <= float(value)

        if key == "iv_percentile_min":
            pct = evaluation.get("iv_percentile")
            return pct is not None and pct >= float(value)

        if key == "iv_rv_ratio_max":
            ratio = evaluation.get("iv_rv_ratio")
            return ratio is not None and ratio <= float(value)

        if key == "iv_rv_ratio_min":
            ratio = evaluation.get("iv_rv_ratio")
            return ratio is not None and ratio >= float(value)

        if key == "theta_adjusted_edge_min":
            edge = evaluation.get("theta_adjusted_edge")
            return edge is not None and edge >= float(value)

        # Gate margin (from decision dict or evaluation enrichment)
        if key == "gate_margin_min":
            val = decision.get("gate_margin") or evaluation.get("gate_margin")
            return val is not None and float(val) >= float(value)

        # Underlying price
        if key == "underlying_price_min":
            val = evaluation.get("underlying_price")
            return val is not None and float(val) >= float(value)

        if key == "underlying_price_max":
            val = evaluation.get("underlying_price")
            return val is not None and float(val) <= float(value)

        # Moneyness
        if key == "moneyness_pct_min":
            val = evaluation.get("moneyness_pct")
            return val is not None and float(val) >= float(value)

        if key == "moneyness_pct_max":
            val = evaluation.get("moneyness_pct")
            return val is not None and float(val) <= float(value)

        # Liquidity
        if key == "spread_pct_max":
            val = evaluation.get("spread_pct")
            return val is not None and float(val) <= float(value)

        if key == "open_interest_min":
            val = evaluation.get("open_interest")
            return val is not None and int(val) >= int(value)

        if key == "volume_min":
            val = evaluation.get("volume")
            return val is not None and int(val) >= int(value)

        # Catalyst
        if key == "days_to_earnings_min":
            val = evaluation.get("days_to_earnings")
            return val is not None and int(val) >= int(value)

        if key == "days_to_earnings_max":
            val = evaluation.get("days_to_earnings")
            return val is not None and int(val) <= int(value)

        # Technical
        if key == "atr14_pct_min":
            val = evaluation.get("atr14_pct")
            return val is not None and float(val) >= float(value)

        if key == "atr14_pct_max":
            val = evaluation.get("atr14_pct")
            return val is not None and float(val) <= float(value)

        if key == "rs_20d_min":
            val = evaluation.get("rs_20d")
            return val is not None and float(val) >= float(value)

        if key == "feasibility_ratio_max":
            val = evaluation.get("feasibility_ratio")
            return val is not None and float(val) <= float(value)

        # Unknown criteria are ignored for forward compatibility
        return True

    except (TypeError, ValueError):
        return True


def matches_rule(
    criteria: dict[str, Any],
    evaluation: dict[str, Any],
    decision: dict[str, Any],
    scanners: list[str],
) -> bool:
    """Check if an evaluation + decision matches a rule's criteria.

    All criteria must be satisfied for a match (AND logic).
    """
    for key, value in criteria.items():
        if value is None:
            continue
        if not _check_criterion(key, value, evaluation, decision, scanners):
            return False
    return True


def match_rules(
    rules: Sequence[dict[str, Any]],
    evaluation: dict[str, Any],
    decision: dict[str, Any],
    scanners: list[str],
    *,
    active_only: bool = True,
    mode_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return all rules that match this evaluation.

    Args:
        rules: List of setup rule dicts
        evaluation: Evaluation data (option_type, dte, iv, etc.)
        decision: Decision data (final_score, directional_score, etc.)
        scanners: List of scanner names that fired for this evaluation
        active_only: Only consider active rules (default True)
        mode_filter: If set, only consider rules with this mode ("production" or "test")

    Returns:
        List of matching rule dicts
    """
    matched = []
    for rule in rules:
        if active_only and not rule.get("is_active", False):
            continue
        if mode_filter and rule.get("mode", "production") != mode_filter:
            continue
        if matches_rule(rule.get("criteria", {}), evaluation, decision, scanners):
            matched.append(rule)
    return matched


def format_matched_rules(
    matched: list[dict[str, Any]],
    include_criteria: bool = False,
) -> list[dict[str, Any]]:
    """Format matched rules for API response."""
    result = []
    for rule in matched:
        entry: dict[str, Any] = {
            "rule_id": rule["rule_id"],
            "name": rule["name"],
            "mode": rule.get("mode", "production"),
            "performance_at_creation": rule.get("performance_at_creation", {}),
        }
        if include_criteria:
            entry["criteria"] = rule.get("criteria", {})
        result.append(entry)
    return result
