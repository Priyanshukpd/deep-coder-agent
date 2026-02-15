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
    model: str = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"

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

    # Reasoning
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
class AgentConfig:
    """Top-level agent configuration."""

    # LLM
    llm: LLMConfig = field(default_factory=LLMConfig)

    # API Key (from environment)
    together_api_key: str = field(default_factory=lambda: os.environ.get("TOGETHER_API_KEY", ""))

    # Feature flags
    use_llm_intent: bool = True  # False = keyword heuristics fallback

    @property
    def has_api_key(self) -> bool:
        return bool(self.together_api_key)
