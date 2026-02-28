"""
Agent Configuration — Centralized, immutable config for the God Mode Agent.

Loads sensitive values from environment variables.
Enforces the Non-Determinism Budget (Architecture §2.C).
"""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMConfig:
    """
    LLM sampling and model configuration.

    Architecture Constraint (§2.C — Non-Determinism Budget):
        Default: temperature=0, top_p=1.0 (deterministic).
        Override at your own risk — any override is logged via sampling_policy_hash.
    """

    # Model
    model: str = field(default_factory=lambda: os.environ.get("TOGETHER_MODEL", "Qwen/Qwen3-Coder-Next-FP8"))

    # Sampling — Architecture defaults (deterministic)
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 1          # greedy
    min_p: float = 0.0

    # Penalties
    repetition_penalty: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0

    # Limits
    max_tokens: int = 18472

    # Reasoning (set to "high" for o1/o3/DeepSeek-R1)
    reasoning_effort: str = "medium"

    # Tool calling
    tool_choice: str = "auto"  # "auto", "required", "none"

    @property
    def sampling_policy_hash(self) -> str:
        """
        Unique hash of the sampling policy for audit trail.
        Architecture §2.C: Log sampling_policy_hash.
        """
        policy_str = (
            f"model={self.model}|"
            f"temp={self.temperature}|"
            f"top_p={self.top_p}|"
            f"top_k={self.top_k}|"
            f"min_p={self.min_p}|"
            f"rep_pen={self.repetition_penalty}|"
            f"pres_pen={self.presence_penalty}|"
            f"freq_pen={self.frequency_penalty}|"
            f"max_tokens={self.max_tokens}"
        )
        return hashlib.sha256(policy_str.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for a single LLM provider endpoint."""
    name: str
    base_url: str
    env_key: str           # Name of the env variable holding the API key
    default_model: str = ""

    @property
    def api_key(self) -> str:
        return os.environ.get(self.env_key, "")


# ── Provider Registry ─────────────────────────────────────────────

PROVIDER_CONFIGS: dict[str, ProviderConfig] = {
    "together": ProviderConfig(
        name="together",
        base_url="https://api.together.xyz/v1",
        env_key="TOGETHER_API_KEY",
        default_model="Qwen/Qwen3-Coder-Next-FP8",
    ),
    "openai": ProviderConfig(
        name="openai",
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        env_key="OPENAI_API_KEY",
        default_model="gpt-4o",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        env_key="OPENROUTER_API_KEY",
        default_model="meta-llama/llama-3.3-70b-instruct",
    ),
    "ollama": ProviderConfig(
        name="ollama",
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        env_key="OLLAMA_API_KEY",   # Usually empty for local Ollama
        default_model="llama3.2",
    ),
}


@dataclass(frozen=True)
class AgentConfig:
    """Top-level agent configuration."""

    # LLM
    llm: LLMConfig = field(default_factory=LLMConfig)

    # API Keys (from environment) — legacy direct access
    together_api_key: str = field(default_factory=lambda: os.environ.get("TOGETHER_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))

    # Provider selection
    provider: str = field(default_factory=lambda: os.environ.get("AGENT_PROVIDER", "together"))

    # Sandbox mode
    sandbox: str = field(default_factory=lambda: os.environ.get("AGENT_SANDBOX", "full-access"))

    # Feature flags
    use_llm_intent: bool = True       # False = keyword heuristics fallback
    enable_session_save: bool = True   # Save conversation to ~/.godmode/sessions/
    enable_streaming: bool = True      # Stream tokens to terminal

    @property
    def has_api_key(self) -> bool:
        """Check if we have a valid API key for the selected provider."""
        if self.provider == "together":
            return bool(self.together_api_key)
        if self.provider == "openai":
            return bool(self.openai_api_key)
        if self.provider == "openrouter":
            pconf = PROVIDER_CONFIGS.get("openrouter")
            return bool(pconf.api_key) if pconf else False
        if self.provider == "ollama":
            return True
        return bool(self.together_api_key) or bool(self.openai_api_key)

    @property
    def active_provider_config(self) -> ProviderConfig:
        """Get the ProviderConfig for the currently selected provider."""
        return PROVIDER_CONFIGS.get(self.provider, PROVIDER_CONFIGS["together"])
