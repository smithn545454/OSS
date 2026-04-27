"""Shared lightweight types for the Convex Mode pipeline.

Lives outside ``pipeline.py`` so downstream modules (``tier.py``,
``stage4_contract.py``, etc.) can import these without inducing a
circular import via ``ConvexCandidate``.
"""

from __future__ import annotations

from enum import Enum


class Tier(str, Enum):
    """Convex tier assignment."""

    A = "A"
    B = "B"
    C = "C"
