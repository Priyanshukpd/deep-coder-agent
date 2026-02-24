"""
Model Metadata Registry — Knowledge base of LLM capabilities per model.

Provides context_window, max_tokens, tool support flags, and reasoning flags.
Used by context manager and provider to select correct limits.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ModelMeta:
    """Capabilities and limits of a specific model."""
    name: str
    context_window: int       # max input+output tokens
    max_output_tokens: int    # max tokens model can generate
    supports_tools: bool = True
    supports_vision: bool = False
    is_reasoning: bool = False   # e.g. o1, DeepSeek-R1 — emits <think> tokens
    provider_hint: str = ""      # together / openai / openrouter / ollama


# ── Registry ─────────────────────────────────────────────────────

_REGISTRY: dict[str, ModelMeta] = {
    # Together AI
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8": ModelMeta(
        name="Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
        context_window=262144, max_output_tokens=32768,
        supports_tools=True, provider_hint="together"
    ),
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": ModelMeta(
        name="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        context_window=131072, max_output_tokens=16384,
        supports_tools=True, provider_hint="together"
    ),
    "meta-llama/Llama-4-Scout-17B-16E-Instruct": ModelMeta(
        name="meta-llama/Llama-4-Scout-17B-16E-Instruct",
        context_window=131072, max_output_tokens=16384,
        supports_tools=True, provider_hint="together"
    ),
    "deepseek-ai/DeepSeek-R1": ModelMeta(
        name="deepseek-ai/DeepSeek-R1",
        context_window=128000, max_output_tokens=32768,
        supports_tools=False, is_reasoning=True, provider_hint="together"
    ),
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": ModelMeta(
        name="deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
        context_window=128000, max_output_tokens=32768,
        supports_tools=False, is_reasoning=True, provider_hint="together"
    ),

    # OpenAI
    "gpt-4o": ModelMeta(
        name="gpt-4o", context_window=128000, max_output_tokens=16384,
        supports_tools=True, supports_vision=True, provider_hint="openai"
    ),
    "gpt-4o-mini": ModelMeta(
        name="gpt-4o-mini", context_window=128000, max_output_tokens=16384,
        supports_tools=True, supports_vision=True, provider_hint="openai"
    ),
    "o1": ModelMeta(
        name="o1", context_window=200000, max_output_tokens=100000,
        supports_tools=True, is_reasoning=True, provider_hint="openai"
    ),
    "o1-mini": ModelMeta(
        name="o1-mini", context_window=128000, max_output_tokens=65536,
        supports_tools=False, is_reasoning=True, provider_hint="openai"
    ),
    "o3": ModelMeta(
        name="o3", context_window=200000, max_output_tokens=100000,
        supports_tools=True, is_reasoning=True, provider_hint="openai"
    ),
    "o3-mini": ModelMeta(
        name="o3-mini", context_window=200000, max_output_tokens=100000,
        supports_tools=True, is_reasoning=True, provider_hint="openai"
    ),
    "gpt-4.1": ModelMeta(
        name="gpt-4.1", context_window=1047576, max_output_tokens=32768,
        supports_tools=True, supports_vision=True, provider_hint="openai"
    ),

    # OpenRouter
    "meta-llama/llama-3.3-70b-instruct": ModelMeta(
        name="meta-llama/llama-3.3-70b-instruct",
        context_window=131072, max_output_tokens=16384,
        supports_tools=True, provider_hint="openrouter"
    ),
    "anthropic/claude-3.5-sonnet": ModelMeta(
        name="anthropic/claude-3.5-sonnet",
        context_window=200000, max_output_tokens=8192,
        supports_tools=True, supports_vision=True, provider_hint="openrouter"
    ),
    "google/gemini-2.0-flash-exp:free": ModelMeta(
        name="google/gemini-2.0-flash-exp:free",
        context_window=1048576, max_output_tokens=8192,
        supports_tools=True, supports_vision=True, provider_hint="openrouter"
    ),
    "nvidia/llama-3.1-nemotron-ultra-253b-v1:free": ModelMeta(
        name="nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
        context_window=131072, max_output_tokens=32768,
        supports_tools=True, is_reasoning=True, provider_hint="openrouter"
    ),

    # Ollama (local)
    "llama3.2": ModelMeta(
        name="llama3.2", context_window=131072, max_output_tokens=8192,
        supports_tools=True, provider_hint="ollama"
    ),
    "llama3.1:70b": ModelMeta(
        name="llama3.1:70b", context_window=131072, max_output_tokens=8192,
        supports_tools=True, provider_hint="ollama"
    ),
    "qwen2.5-coder:7b": ModelMeta(
        name="qwen2.5-coder:7b", context_window=131072, max_output_tokens=8192,
        supports_tools=True, provider_hint="ollama"
    ),
    "deepseek-r1:7b": ModelMeta(
        name="deepseek-r1:7b", context_window=32768, max_output_tokens=8192,
        supports_tools=False, is_reasoning=True, provider_hint="ollama"
    ),
}

# Models with reasoning by keywords
_REASONING_KEYWORDS = ["deepseek-r1", "o1", "o3", "apriel", "thinker", "nemotron"]


def get_model_meta(model_name: str) -> ModelMeta:
    """
    Look up model metadata.

    Falls back to fuzzy matching by substring.
    Returns a permissive default with 128k context if model is unknown.
    """
    # Exact match first
    if model_name in _REGISTRY:
        return _REGISTRY[model_name]

    # Fuzzy match: check if model name contains a known key substring
    model_lower = model_name.lower()
    for key, meta in _REGISTRY.items():
        if key.lower() in model_lower or model_lower in key.lower():
            return meta

    # Detect reasoning by keywords
    is_reasoning = any(kw in model_lower for kw in _REASONING_KEYWORDS)

    logger.debug(f"Unknown model '{model_name}', using default limits")
    return ModelMeta(
        name=model_name,
        context_window=128000,
        max_output_tokens=8192,
        supports_tools=not is_reasoning,
        is_reasoning=is_reasoning,
    )


def warn_if_no_tools(model_name: str) -> bool:
    """Returns False and prints warning if model doesn't support tools."""
    meta = get_model_meta(model_name)
    if not meta.supports_tools:
        logger.warning(
            f"⚠️  Model '{model_name}' does not support tool calling. "
            f"Intent classification will fall back to keyword heuristics."
        )
        return False
    return True
