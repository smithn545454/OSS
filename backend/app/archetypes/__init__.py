"""Archetype matcher + anti-archetype gates (Policy v4.1.0)."""

from app.archetypes.gates import AntiArchetypeResult, check_anti_archetypes
from app.archetypes.matcher import (
    ArchetypeFit,
    ArchetypeMatchResult,
    ConditionFit,
    compute_archetype_match,
)

__all__ = [
    "AntiArchetypeResult",
    "ArchetypeFit",
    "ArchetypeMatchResult",
    "ConditionFit",
    "check_anti_archetypes",
    "compute_archetype_match",
]
