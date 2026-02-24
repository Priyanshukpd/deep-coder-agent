"""
Provider Factory — Create the correct LLM provider based on configuration.

Supports: together | openai | openrouter | ollama

Usage:
    from agent.core.provider_factory import create_provider
    provider = create_provider(config)
    result = provider.complete(messages)

OpenRouter and Ollama both use the OpenAI-compatible API, just with
different base_urls and API keys.
"""
from __future__ import annotations

import os
import logging
from typing import Union

from agent.config import AgentConfig
from agent.core.llm_provider import TogetherProvider
from agent.core.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# OpenRouter base URL
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Default Ollama base URL
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")


Provider = Union[TogetherProvider, OpenAIProvider]


def create_provider(config: AgentConfig) -> Provider:
    """
    Instantiate the LLM provider named in config.provider.

    Supported values:
        "together"   → TogetherProvider (default)
        "openai"     → OpenAIProvider with api.openai.com
        "openrouter" → OpenAIProvider with openrouter.ai (uses OPENROUTER_API_KEY)
        "ollama"     → OpenAIProvider with localhost Ollama (no auth needed)

    Args:
        config: AgentConfig with provider + model settings.

    Returns:
        An initialized provider ready to call .complete().
    """
    provider_name = (config.provider or "together").lower().strip()

    logger.info(f"Creating provider: {provider_name} / model: {config.llm.model}")

    if provider_name == "together":
        return TogetherProvider(config)

    if provider_name == "openai":
        return OpenAIProvider(config)

    if provider_name == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set. Set it with: export OPENROUTER_API_KEY=sk-..."
            )
        # Patch config with openrouter credentials
        patched_config = _patch_config(
            config,
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key or "no-key",
        )
        return OpenAIProvider(patched_config)

    if provider_name == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
        patched_config = _patch_config(
            config,
            base_url=base_url,
            api_key="ollama",  # Ollama ignores the key, but OpenAI client requires a non-empty value
        )
        return OpenAIProvider(patched_config)

    raise ValueError(
        f"Unknown provider '{provider_name}'. "
        f"Valid providers: together, openai, openrouter, ollama"
    )


def _patch_config(config: AgentConfig, base_url: str, api_key: str) -> AgentConfig:
    """
    Return a new AgentConfig with patched openai_base_url and openai_api_key.
    Uses object.__setattr__ because AgentConfig is frozen.
    """
    import copy
    new_config = copy.copy(config)
    # AgentConfig is frozen=True, so we need to use object.__setattr__
    object.__setattr__(new_config, "openai_base_url", base_url)
    object.__setattr__(new_config, "openai_api_key", api_key)
    return new_config
