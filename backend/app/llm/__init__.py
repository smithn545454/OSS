"""LLM Integration Module for Trade Thesis Generation.

Per Section 21 of OSS_Complete_Requirements.md.

This module provides:
- ThesisGenerator: Main orchestrator for generating trade theses
- LLMProvider: Abstract interface with Anthropic and OpenAI implementations
- RateLimiter: Daily call limit tracking (max 50/day)
"""

from app.llm.generator import ThesisGenerator
from app.llm.models import ThesisInput, ThesisOutput
from app.llm.provider import AnthropicProvider, LLMProvider, OpenAIProvider
from app.llm.rate_limiter import RateLimiter

__all__ = [
    "ThesisGenerator",
    "ThesisInput",
    "ThesisOutput",
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "RateLimiter",
]
