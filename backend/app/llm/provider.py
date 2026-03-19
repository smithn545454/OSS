"""LLM Provider implementations for trade thesis generation.

Supports both Anthropic Claude and OpenAI GPT-4.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Response from LLM provider."""

    content: str
    tokens_used: int
    model: str
    success: bool
    error: Optional[str] = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name identifier."""
        pass

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """Generate completion from prompt.

        Args:
            prompt: The formatted prompt string
            max_tokens: Maximum output tokens

        Returns:
            LLMResponse with content and metadata
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is configured and available."""
        pass


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    MODEL = "claude-haiku-4-5-20251001"
    SONNET_MODEL = "claude-sonnet-4-5-20241022"

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key (defaults to config or ANTHROPIC_API_KEY env var)
        """
        if api_key:
            self._api_key = api_key
        else:
            # Try config first (which checks Secrets Manager), then env var
            from app.config import get_settings
            settings = get_settings()
            self._api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client: Optional["anthropic.AsyncAnthropic"] = None

    @property
    def name(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        """Check if Anthropic API key is configured."""
        return bool(self._api_key)

    def _get_client(self) -> "anthropic.AsyncAnthropic":
        """Get or create Anthropic client."""
        if self._client is None:
            try:
                import anthropic

                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
        return self._client

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """Generate completion using Claude.

        Args:
            prompt: The formatted prompt string
            max_tokens: Maximum output tokens
            system_prompt: Optional system prompt
            model: Override model ID (defaults to self.MODEL)
        """
        use_model = model or self.MODEL
        if not self.is_available():
            return LLMResponse(
                content="",
                tokens_used=0,
                model=use_model,
                success=False,
                error="Anthropic API key not configured",
            )

        try:
            client = self._get_client()
            kwargs: dict = {
                "model": use_model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            response = await client.messages.create(**kwargs)

            # Extract content from response
            content = ""
            if response.content:
                content = response.content[0].text

            # Calculate tokens used
            tokens_used = response.usage.output_tokens if response.usage else 0

            return LLMResponse(
                content=content,
                tokens_used=tokens_used,
                model=use_model,
                success=True,
            )

        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            return LLMResponse(
                content="",
                tokens_used=0,
                model=use_model,
                success=False,
                error=str(e),
            )


class OpenAIProvider(LLMProvider):
    """OpenAI GPT-4 provider implementation."""

    MODEL = "gpt-4o-mini"

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key (defaults to config or OPENAI_API_KEY env var)
        """
        if api_key:
            self._api_key = api_key
        else:
            # Try config first (which checks Secrets Manager), then env var
            from app.config import get_settings
            settings = get_settings()
            self._api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
        self._client: Optional["openai.AsyncOpenAI"] = None

    @property
    def name(self) -> str:
        return "openai"

    def is_available(self) -> bool:
        """Check if OpenAI API key is configured."""
        return bool(self._api_key)

    def _get_client(self) -> "openai.AsyncOpenAI":
        """Get or create OpenAI client."""
        if self._client is None:
            try:
                import openai

                self._client = openai.AsyncOpenAI(api_key=self._api_key)
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install openai"
                )
        return self._client

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Generate completion using GPT-4.
        
        Args:
            prompt: The prompt to send
            max_tokens: Maximum tokens in response
            json_mode: If True and prompt contains 'json', use json_object format
        """
        if not self.is_available():
            return LLMResponse(
                content="",
                tokens_used=0,
                model=self.MODEL,
                success=False,
                error="OpenAI API key not configured",
            )

        try:
            client = self._get_client()
            
            # Only use json_object format if prompt mentions JSON
            # (OpenAI requires "json" in prompt when using json_object format)
            use_json_format = json_mode or "json" in prompt.lower()
            
            kwargs = {
                "model": self.MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if use_json_format:
                kwargs["response_format"] = {"type": "json_object"}
            
            response = await client.chat.completions.create(**kwargs)

            # Extract content from response
            content = ""
            if response.choices:
                content = response.choices[0].message.content or ""

            # Calculate tokens used
            tokens_used = (
                response.usage.completion_tokens if response.usage else 0
            )

            return LLMResponse(
                content=content,
                tokens_used=tokens_used,
                model=self.MODEL,
                success=True,
            )

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return LLMResponse(
                content="",
                tokens_used=0,
                model=self.MODEL,
                success=False,
                error=str(e),
            )


def get_provider(provider_name: str) -> LLMProvider:
    """Get LLM provider by name.

    Args:
        provider_name: "anthropic" or "openai"

    Returns:
        Configured LLM provider instance
    """
    providers = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
    }

    if provider_name not in providers:
        raise ValueError(f"Unknown provider: {provider_name}. Use 'anthropic' or 'openai'")

    return providers[provider_name]()
