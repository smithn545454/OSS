"""Archetype matcher — v4.1.0 secondary scoring axis.

Evaluates a ScoringContext against all configured archetypes. Each
archetype is an AND of conditions; condition fit scores (0-100) are
combined via MIN; the best archetype (highest min-fit) becomes the
matched archetype if its fit ≥ min_fit_to_match.

Anti-archetypes live in ``gates.py`` — they have hard-reject semantics.
This module only computes positive matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.schemas import ArchetypeCondition, ArchetypeConfig
from app.pillars.models import ScoringContext

logger = logging.getLogger(__name__)


@dataclass
class ConditionFit:
    condition_id: str
    display_name: str
    raw_value: Any
    fit: float  # 0-100


@dataclass
class ArchetypeFit:
    archetype_id: str
    display_name: str
    fit: float  # min of condition fits
    condition_fits: list[ConditionFit]
    matched: bool  # fit >= min_fit_to_match


@dataclass
class ArchetypeMatchResult:
    best: Optional[ArchetypeFit]
    best_match_score: Optional[float]
    all_fits: dict[str, float] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def compute_archetype_match(
    ctx: ScoringContext,
    config: ArchetypeConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
) -> ArchetypeMatchResult:
    """Evaluate all archetypes, return the best match + per-archetype fits.

    ``pillar_scores`` lets archetype conditions reference pillar scores
    (e.g., ``ts_score`` for Archetype B's "TS≥75" condition).
    """
    pillar_scores = pillar_scores or {}
    all_fits: dict[str, float] = {}
    archetype_fits: list[ArchetypeFit] = []

    for archetype in config.archetypes:
        cond_fits = [
            _evaluate_condition(ctx, cond, pillar_scores)
            for cond in archetype.conditions
        ]
        min_fit = min((c.fit for c in cond_fits), default=0.0)
        matched = min_fit >= archetype.min_fit_to_match
        archetype_fits.append(
            ArchetypeFit(
                archetype_id=archetype.archetype_id,
                display_name=archetype.display_name,
                fit=min_fit,
                condition_fits=cond_fits,
                matched=matched,
            )
        )
        all_fits[archetype.archetype_id] = round(min_fit, 2)

    matched_list = [af for af in archetype_fits if af.matched]
    best: Optional[ArchetypeFit] = (
        max(matched_list, key=lambda af: af.fit) if matched_list else None
    )

    best_score: Optional[float] = None
    tags: list[str] = []
    if best is not None:
        multiplier = next(
            (
                a.match_score_multiplier
                for a in config.archetypes
                if a.archetype_id == best.archetype_id
            ),
            1.0,
        )
        best_score = min(100.0, best.fit * multiplier)
        tags.append(f"ARCHETYPE_{best.archetype_id}")

    return ArchetypeMatchResult(
        best=best,
        best_match_score=best_score,
        all_fits=all_fits,
        tags=tags,
    )


def _evaluate_condition(
    ctx: ScoringContext,
    cond: ArchetypeCondition,
    pillar_scores: dict[str, float],
) -> ConditionFit:
    """Compute 0-100 fit for a single condition."""
    raw = _resolve_feature(ctx, cond.feature_field, pillar_scores)

    if raw is None:
        fit = 0.0 if cond.required else 50.0
        return ConditionFit(cond.condition_id, cond.display_name, None, fit)

    try:
        fit = _condition_fit(raw, cond)
    except Exception:
        logger.exception("archetype condition eval failed: %s", cond.condition_id)
        fit = 0.0

    return ConditionFit(cond.condition_id, cond.display_name, raw, fit)


def _resolve_feature(
    ctx: ScoringContext,
    field_name: str,
    pillar_scores: dict[str, float],
) -> Any:
    """Look up a feature on ctx OR a pillar score by name.

    Pillar score aliases: ``ts_score``, ``mp_score``, ``dc_score`` —
    resolved against ``pillar_scores`` keyed by ``TS`` / ``MP`` / ``DC``
    or full pillar-id values. Derived features: ``abs_delta``,
    ``rs_contrarian``.
    """
    alias = {"ts_score": "TS", "mp_score": "MP", "dc_score": "DC"}
    if field_name in alias:
        key = alias[field_name]
        if key in pillar_scores:
            return pillar_scores[key]
        # Fallback to full PillarId value if keyed that way.
        full = {
            "TS": "TRADE_STRUCTURE",
            "MP": "MOVE_POTENTIAL",
            "DC": "DIRECTIONAL_CONVICTION",
        }[key]
        return pillar_scores.get(full)
    if field_name == "abs_delta":
        d = ctx.delta
        if d is None:
            return None
        try:
            return abs(float(d))
        except (TypeError, ValueError):
            return None
    if field_name == "rs_contrarian":
        rs = ctx.rs_20d
        if rs is None:
            return None
        if ctx.option_type == "CALL":
            return 1.0 if rs < 0 else 0.0
        return 1.0 if rs > 0 else 0.0
    return getattr(ctx, field_name, None)


def _condition_fit(value: Any, cond: ArchetypeCondition) -> float:
    """Return fit score for a resolved feature value against a condition."""
    if cond.eq is not None:
        return 100.0 if value == cond.eq else 0.0
    if cond.in_values is not None:
        return 100.0 if value in cond.in_values else 0.0

    v = float(value)
    feather = cond.feather or 0.0

    if cond.between is not None:
        lo, hi = cond.between[0], cond.between[1]
        if lo <= v <= hi:
            return 100.0
        if feather > 0:
            if lo - feather <= v < lo:
                return 100.0 * (v - (lo - feather)) / feather
            if hi < v <= hi + feather:
                return 100.0 * ((hi + feather) - v) / feather
        return 0.0

    if cond.lte is not None:
        if v <= cond.lte:
            return 100.0
        if feather > 0 and v <= cond.lte + feather:
            return 100.0 * ((cond.lte + feather) - v) / feather
        return 0.0

    if cond.gte is not None:
        if v >= cond.gte:
            return 100.0
        if feather > 0 and v >= cond.gte - feather:
            return 100.0 * (v - (cond.gte - feather)) / feather
        return 0.0

    return 50.0
