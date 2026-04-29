"""Observability module for OSS.

Provides representative trace sampling per Section 18.3 of requirements.
"""

from app.observability.trace_sampler import (
    TraceSampler,
    get_representative_traces,
)

__all__ = [
    "TraceSampler",
    "get_representative_traces",
]
