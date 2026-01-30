"""LLM client for document summarization."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..config import LLMConfig

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        """Generate a completion for the given prompt."""
        pass


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.config.api_key)
        return self._client

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        """Generate a completion using Claude."""
        client = self._get_client()

        try:
            response = client.messages.create(
                model=self.config.model,
                max_tokens=1024,
                system=system_prompt or "Du bist ein hilfreicher Assistent.",
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise


class OpenAIClient(BaseLLMClient):
    """OpenAI GPT client."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(api_key=self.config.api_key)
        return self._client

    async def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        """Generate a completion using GPT."""
        client = self._get_client()

        try:
            response = client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt or "Du bist ein hilfreicher Assistent."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise


def create_llm_client(config: LLMConfig) -> BaseLLMClient | None:
    """Create an LLM client based on configuration.

    Args:
        config: LLM configuration.

    Returns:
        LLM client instance or None if not configured.
    """
    if not config.api_key:
        logger.warning("No LLM API key configured, summaries will be disabled")
        return None

    provider = config.provider.lower()

    if provider == "anthropic":
        return AnthropicClient(config)
    elif provider == "openai":
        return OpenAIClient(config)
    else:
        logger.warning(f"Unknown LLM provider: {provider}")
        return None
