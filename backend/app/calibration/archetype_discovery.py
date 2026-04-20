"""v5 archetype auto-discovery + drift detection.

Two responsibilities, kept in one module because they share the same
bucketing and statistical machinery:

1. **Discovery** — mine subgroups of closed paper positions to surface
   archetype candidates that aren't in the current library. Filters:
     * n ≥ min_n (default 30)
     * Wilson lower of target rate ≥ min_lift × scanner baseline
     * Novelty: Jaccard similarity to existing archetype condition sets < 0.7

2. **Drift detection** — for each active archetype, compare its recent
   realized rate against its historical seed. If the recent rate falls
   below the historical Wilson lower bound for a sustained window,
   the archetype is drifting — flag for retirement.

Both are pure-data functions (take position dicts + library, return
candidates / drift assessments). The :mod:`backend.scripts.v5_weekly_discovery`
script wraps them with DynamoDB loading and report generation.

Phase 8 scope: compute-only. Promotion/retirement proposals are
generated as draft Policy JSONs for Nick to approve with an explicit
click — no silent production changes.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.calibration.archetype_rates import normalize_pnl_pct
from app.calibration.wilson import wilson_ci
from app.core.schemas import (
    ArchetypeCondition,
    ArchetypeConfig,
    ArchetypeDefinition,
)

HR200_THRESHOLD = 200.0


# ============================================================================
# Candidate + drift types
# ============================================================================


@dataclass(frozen=True)
class CandidateArchetype:
    """A proposed archetype discovered by mining. Not yet in the library."""

    scanner: str
    conditions: tuple[tuple[str, str], ...]  # (feature_name, bucket_label) pairs
    depth: int
    n: int
    hr200_count: int
    wins_count: int
    point_rate: float          # Point estimate of target rate (HR200 or win) in [0, 1]
    lower_rate: float          # Wilson lower
    upper_rate: float          # Wilson upper
    lift_lower: float          # lower_rate / scanner_baseline (for the same target)
    mean_pnl_pct: float
    target: str                # "hr200" or "profit"

    @property
    def stability_score(self) -> float:
        """Rank combinator: lift_lower × log(n) × sqrt(mean_pnl + 100).

        Lift at the LOWER Wilson bound penalizes small-sample claims.
        log(n) rewards larger cohorts without overweighting huge ones.
        The pnl shim keeps negative-EV candidates low even if their
        rate is high (unlikely but possible for in-sample outliers).
        """
        return (
            self.lift_lower
            * math.log(max(2, self.n))
            * math.sqrt(max(1.0, self.mean_pnl_pct + 100.0))
        )


@dataclass(frozen=True)
class DriftAssessment:
    """Result of comparing an active archetype's recent rate vs historical."""

    archetype_id: str
    target: str                # "hr200" or "profit"
    historical_point: float
    historical_lower: float
    historical_n: int
    recent_point: float
    recent_lower: float
    recent_n: int
    drift_severity: str        # "ok" | "watch" | "retire"
    note: str


# ============================================================================
# Default feature buckets (coarse — matches the Phase 3 discovery script)
# ============================================================================


def _bucket_numeric(v, breaks, labels):
    if v is None:
        return None
    for i, br in enumerate(breaks):
        if v < br:
            return labels[i]
    return labels[-1]


def _rs_direction(r: dict) -> Optional[str]:
    rs = r.get("rs_20d")
    opt = r.get("option_type")
    if rs is None or opt is None:
        return None
    if opt == "CALL":
        return "RS_AGAINST" if rs < 0.95 else "RS_WITH"
    return "RS_AGAINST" if rs > 1.05 else "RS_WITH"


FEATURE_BUCKETS: dict[str, Callable[[dict], Optional[str]]] = {
    "dte": lambda r: _bucket_numeric(
        r.get("dte"), [14, 21, 45],
        ["ULTRA(<14)", "SHORT(14-21)", "MID(21-45)", "LONG(>=45)"],
    ),
    "delta": lambda r: _bucket_numeric(
        abs(r["delta"]) if r.get("delta") is not None else None, [0.25, 0.40, 0.55],
        ["DEEP_OTM(<0.25)", "OTM(0.25-0.40)", "NEAR(0.40-0.55)", "ITM(>=0.55)"],
    ),
    "ivp": lambda r: _bucket_numeric(
        r.get("iv_percentile"), [30, 70], ["IVP_LO", "IVP_MID", "IVP_HI"],
    ),
    "ivrv": lambda r: _bucket_numeric(
        r.get("iv_rv_ratio"), [1.0, 1.3], ["IVRV_CHEAP", "IVRV_FAIR", "IVRV_RICH"],
    ),
    "dc": lambda r: _bucket_numeric(
        r.get("dc_score"), [40, 60, 75], ["DC_LO", "DC_MID", "DC_HIGH", "DC_ELITE"],
    ),
    "mp": lambda r: _bucket_numeric(
        r.get("mp_score"), [40, 60, 75], ["MP_LO", "MP_MID", "MP_HIGH", "MP_ELITE"],
    ),
    "ts": lambda r: _bucket_numeric(
        r.get("ts_score"), [60, 75], ["TS_LO", "TS_MID", "TS_HI"],
    ),
    "adx": lambda r: _bucket_numeric(
        r.get("adx_14"), [20, 30], ["ADX_LO", "ADX_MID", "ADX_HI"],
    ),
    "atr": lambda r: _bucket_numeric(
        r.get("atr14_pct"), [2, 4, 6], ["ATR_LO", "ATR_MID", "ATR_HI", "ATR_VOL"],
    ),
    "option_type": lambda r: r.get("option_type"),
    "rs": _rs_direction,
}


# ============================================================================
# Scanner baselines
# ============================================================================


def scanner_baselines(
    positions: list[dict],
    target: str = "hr200",
) -> dict[str, dict[str, float]]:
    """Compute per-scanner baseline rate (Wilson lower) for the target.

    Returns ``{scanner: {n, point, lower, upper, mean_pnl}}`` keyed by
    ``scanner_source``. Used by :func:`discover_candidates` to filter
    candidates by lift vs the scanner's own baseline (not the global
    baseline — lift is most meaningful within-scanner).
    """
    by_scanner: dict[str, list[dict]] = defaultdict(list)
    for r in positions:
        by_scanner[str(r.get("scanner_source") or "UNKNOWN")].append(r)

    baselines: dict[str, dict[str, float]] = {}
    for scanner, cohort in by_scanner.items():
        baselines[scanner] = _cohort_rates(cohort, target=target)
    return baselines


def _cohort_rates(cohort: list[dict], target: str = "hr200") -> dict[str, float]:
    """Return rate stats for a cohort: n, point, lower, upper, mean_pnl."""
    n = len(cohort)
    if n == 0:
        return {"n": 0, "point": 0.0, "lower": 0.0, "upper": 0.0,
                "mean_pnl": 0.0, "wins": 0, "hr200": 0}

    hr200 = sum(
        1 for r in cohort if (r.get("max_favorable_excursion") or 0) >= HR200_THRESHOLD
    )
    wins = sum(1 for r in cohort if (r.get("current_pnl_pct") or 0) > 0)
    pnls = [r["current_pnl_pct"] for r in cohort if r.get("current_pnl_pct") is not None]
    mean_pnl = statistics.mean(pnls) if pnls else 0.0

    if target == "hr200":
        successes = hr200
    elif target == "profit":
        successes = wins
    else:
        raise ValueError(f"Unknown target: {target}")
    point, lower, upper = wilson_ci(successes, n)

    return {
        "n": n, "point": point, "lower": lower, "upper": upper,
        "mean_pnl": mean_pnl, "wins": wins, "hr200": hr200,
    }


# ============================================================================
# Discovery
# ============================================================================


def discover_candidates(
    positions: list[dict],
    *,
    existing_library: Optional[ArchetypeConfig] = None,
    target: str = "hr200",
    min_n: int = 30,
    min_lift_lower: float = 2.5,
    min_hr_or_wins: int = 3,
    min_mean_pnl: float = 0.0,
    min_scanner_n: int = 100,
    max_depth: int = 3,
    novelty_threshold: float = 0.7,
) -> list[CandidateArchetype]:
    """Mine subgroup archetypes targeting HR200 or profit.

    Args:
        positions: Closed paper-position dicts with scanner_source + feature
            columns + max_favorable_excursion + current_pnl_pct.
        existing_library: Optional archetype config to check novelty against.
        target: "hr200" (v5 HR candidates) or "profit" (v5 P candidates).
        min_n: Minimum cohort size.
        min_lift_lower: Wilson-lower rate of candidate must be ≥ this × scanner baseline lower.
        min_hr_or_wins: Minimum success count (target-specific).
        min_mean_pnl: Minimum mean P&L % (for P target — filter out break-even patterns).
        min_scanner_n: Scanner cohort must be this large to run mining.
        max_depth: Maximum number of conditions per archetype (depth 1-3).
        novelty_threshold: Jaccard similarity threshold — candidates above
            this are considered duplicates of existing archetypes.

    Returns:
        Sorted list of CandidateArchetype (strongest first by stability_score).
    """
    baselines = scanner_baselines(positions, target=target)
    existing_sets = _existing_condition_sets(existing_library) if existing_library else []

    candidates: list[CandidateArchetype] = []
    by_scanner: dict[str, list[dict]] = defaultdict(list)
    for r in positions:
        by_scanner[str(r.get("scanner_source") or "UNKNOWN")].append(r)

    for scanner, cohort in by_scanner.items():
        if len(cohort) < min_scanner_n:
            continue
        base = baselines.get(scanner, {})
        base_lower = float(base.get("lower", 0.0))
        if base_lower <= 0:
            continue

        feature_names = list(FEATURE_BUCKETS.keys())
        # Pre-bucket each row once to avoid repeated function calls
        bucketed = [
            {f: FEATURE_BUCKETS[f](r) for f in feature_names}
            for r in cohort
        ]

        # Iterate over all combinations up to max_depth
        for depth in range(1, max_depth + 1):
            for combo in _feature_combinations(feature_names, depth):
                groups: dict[tuple, list[dict]] = defaultdict(list)
                for idx, r in enumerate(cohort):
                    key = tuple(bucketed[idx][f] for f in combo)
                    if any(v is None for v in key):
                        continue
                    groups[key].append(r)
                for key, sub in groups.items():
                    if len(sub) < min_n:
                        continue
                    stats = _cohort_rates(sub, target=target)
                    successes = stats["hr200"] if target == "hr200" else stats["wins"]
                    if successes < min_hr_or_wins:
                        continue
                    if stats["mean_pnl"] < min_mean_pnl:
                        continue
                    lift_lower = stats["lower"] / base_lower if base_lower else 0.0
                    if lift_lower < min_lift_lower:
                        continue

                    # Novelty filter — compare candidate FEATURE SET to
                    # existing archetypes' feature sets (bucket labels ignored).
                    cand_conds = tuple(zip(combo, key))
                    cand_feature_set = set(combo)
                    if _max_jaccard(cand_feature_set, existing_sets) >= novelty_threshold:
                        continue

                    candidates.append(CandidateArchetype(
                        scanner=scanner,
                        conditions=cand_conds,
                        depth=depth,
                        n=stats["n"],
                        hr200_count=stats["hr200"],
                        wins_count=stats["wins"],
                        point_rate=stats["point"],
                        lower_rate=stats["lower"],
                        upper_rate=stats["upper"],
                        lift_lower=lift_lower,
                        mean_pnl_pct=stats["mean_pnl"],
                        target=target,
                    ))

    # Dedupe across depths — same condition set different depths is the same candidate
    seen: set = set()
    unique: list[CandidateArchetype] = []
    for c in candidates:
        key = (c.scanner, frozenset(c.conditions))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    unique.sort(key=lambda c: -c.stability_score)
    return unique


# ============================================================================
# Drift detection
# ============================================================================


def detect_drift(
    archetype: ArchetypeDefinition,
    recent_positions: list[dict],
    target: str = "hr200",
    match_fn: Optional[Callable[[dict, ArchetypeDefinition], bool]] = None,
    *,
    retire_threshold_weeks: int = 4,  # Documentation only; caller tracks windows
) -> DriftAssessment:
    """Compare an archetype's historical vs recent realized rate.

    ``match_fn`` should return True when a position matches the archetype.
    Defaults to strict-match on all conditions (no feather).

    Drift severity:
        * ``ok`` — recent point ≥ historical Wilson lower
        * ``watch`` — recent point < historical lower but recent n < 20
        * ``retire`` — recent point < historical lower AND recent n ≥ 20
                      (sufficient sample and drifting low → retire signal)

    Returns:
        :class:`DriftAssessment`.
    """
    hist_rate = (
        archetype.historical_hr200_rate if target == "hr200"
        else archetype.historical_win_rate
    )
    hist_n = archetype.historical_n
    # Historical Wilson — approximate since we don't have the raw success count,
    # but we can derive it from rate × n (rounded).
    hist_successes = round(hist_rate * hist_n)
    _, hist_lower, _ = wilson_ci(hist_successes, hist_n)

    matcher = match_fn or _default_strict_match
    matched = [r for r in recent_positions if matcher(r, archetype)]
    recent = _cohort_rates(matched, target=target)
    recent_n = recent["n"]
    recent_point = recent["point"]
    recent_lower = recent["lower"]

    if recent_n == 0:
        severity = "watch"
        note = f"No recent matches in window (historical n={hist_n})"
    elif recent_point >= hist_lower:
        severity = "ok"
        note = (f"Recent rate {recent_point*100:.2f}% within historical CI "
                f"(lower {hist_lower*100:.2f}%)")
    elif recent_n < 20:
        severity = "watch"
        note = (f"Recent rate {recent_point*100:.2f}% below historical lower "
                f"{hist_lower*100:.2f}% but n={recent_n} < 20 — wait for more data")
    else:
        severity = "retire"
        note = (f"Recent rate {recent_point*100:.2f}% below historical lower "
                f"{hist_lower*100:.2f}% with n={recent_n} — retire candidate")

    return DriftAssessment(
        archetype_id=archetype.archetype_id,
        target=target,
        historical_point=hist_rate,
        historical_lower=hist_lower,
        historical_n=hist_n,
        recent_point=recent_point,
        recent_lower=recent_lower,
        recent_n=recent_n,
        drift_severity=severity,
        note=note,
    )


# ============================================================================
# Helpers
# ============================================================================


def _feature_combinations(features: list[str], depth: int) -> list[tuple[str, ...]]:
    """All size-depth combinations of features (ordered, without replacement)."""
    if depth <= 0:
        return [()]
    if depth == 1:
        return [(f,) for f in features]
    out: list[tuple[str, ...]] = []
    for i, f in enumerate(features):
        for rest in _feature_combinations(features[i + 1:], depth - 1):
            out.append((f,) + rest)
    return out


def _existing_condition_sets(library: ArchetypeConfig) -> list[set]:
    """Extract FEATURE-FIELD sets from an archetype library for novelty checks.

    We compare candidates to existing archetypes by their FEATURE SET only
    (not the specific bucket values). Rationale: discovery scans the same
    feature space as the existing library, so if a candidate uses exactly
    the same features as an existing archetype, it's almost certainly
    re-discovering a slight variation of that pattern. Better to surface
    candidates with genuinely novel feature combinations.

    A future refinement could include bucket overlap if/when we add more
    granular feature bucketing — the feature-only filter is conservative
    and works for the current coarse buckets.
    """
    sets: list[set] = []
    for a in library.archetypes:
        features = {c.feature_field for c in a.conditions}
        # Normalize v5 runtime aliases to discovery bucket names
        normalized = {_normalize_feature_name(f) for f in features}
        if normalized:
            sets.append(normalized)
    return sets


def _normalize_feature_name(feature_field: str) -> str:
    """Map archetype feature_field names to discovery bucket keys.

    Archetype conditions reference features like ``abs_delta``, ``ts_score``,
    ``iv_percentile``. Discovery buckets use shorter keys: ``delta``, ``ts``,
    ``ivp``. This alias table keeps the novelty check accurate.
    """
    return {
        "abs_delta": "delta",
        "iv_percentile": "ivp",
        "iv_rv_ratio": "ivrv",
        "adx_14": "adx",
        "atr14_pct": "atr",
        "ts_score": "ts",
        "mp_score": "mp",
        "dc_score": "dc",
        "scanner_source": "scanner",
        "rs_contrarian": "rs",
    }.get(feature_field, feature_field)


def _condition_signature(cond: ArchetypeCondition) -> Any:
    """Return a hashable signature for a condition."""
    if cond.eq is not None:
        return ("eq", cond.eq)
    if cond.in_values is not None:
        return ("in_values", tuple(sorted(cond.in_values)))
    if cond.between is not None:
        return ("between", tuple(cond.between))
    if cond.lte is not None:
        return ("lte", cond.lte)
    if cond.gte is not None:
        return ("gte", cond.gte)
    return ("noop",)


def _max_jaccard(cand: set, existing: list[set]) -> float:
    """Maximum Jaccard similarity of cand to any existing set."""
    best = 0.0
    for e in existing:
        if not e and not cand:
            continue
        inter = len(cand & e)
        union = len(cand | e)
        if union == 0:
            continue
        best = max(best, inter / union)
    return best


def _default_strict_match(r: dict, archetype: ArchetypeDefinition) -> bool:
    """Strict archetype match (no feather). Mirrors scripts/seed_v5_archetype_rates.py."""
    for cond in archetype.conditions:
        feature = cond.feature_field
        if feature == "abs_delta":
            d = r.get("delta")
            value = abs(d) if d is not None else None
        elif feature == "rs_contrarian":
            rs = r.get("rs_20d")
            opt = r.get("option_type")
            if rs is None or opt is None:
                return False
            if opt == "CALL":
                value = 1 if rs < 0.95 else 0
            else:
                value = 1 if rs > 1.05 else 0
        else:
            value = r.get(feature)

        if value is None:
            return False

        if cond.eq is not None and value != cond.eq:
            return False
        if cond.in_values is not None and value not in cond.in_values:
            return False
        if cond.between is not None:
            lo, hi = cond.between[0], cond.between[1]
            if not (lo <= float(value) <= hi):
                return False
        if cond.lte is not None and float(value) > cond.lte:
            return False
        if cond.gte is not None and float(value) < cond.gte:
            return False
    return True


__all__ = [
    "CandidateArchetype",
    "DriftAssessment",
    "FEATURE_BUCKETS",
    "HR200_THRESHOLD",
    "detect_drift",
    "discover_candidates",
    "normalize_pnl_pct",  # Re-exported for convenience
    "scanner_baselines",
]
