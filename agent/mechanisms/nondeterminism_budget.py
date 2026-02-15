"""
Non-Determinism Budget Enforcement.

Architecture §2.C:
    1. Policy: temperature=0, top_p fixed
    2. Audit: Log sampling_policy_hash
    3. Enforcement: System prompt mutation mid-run is FORBIDDEN

This module enforces the non-determinism budget by:
    - Validating LLM config at startup
    - Detecting system prompt mutations
    - Providing audit hooks for compliance
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class NonDeterminismViolation(Exception):
    """Raised when the non-determinism budget is violated."""
    pass


class SystemPromptMutationError(NonDeterminismViolation):
    """System prompt was modified mid-run."""
    pass


class TemperaturePolicyError(NonDeterminismViolation):
    """Temperature is not 0."""
    pass


class NonDeterminismBudget:
    """
    Enforces deterministic LLM behavior.
    
    Architecture §2.C:
        - temperature must be 0
        - top_p must be fixed
        - System prompt must not change mid-run
        - sampling_policy_hash must be logged
    """

    def __init__(
        self,
        allowed_temperature: float = 0.0,
        allowed_top_p: float = 1.0,
        strict: bool = True,
    ):
        self.allowed_temperature = allowed_temperature
        self.allowed_top_p = allowed_top_p
        self.strict = strict
        self._system_prompt_hash: Optional[str] = None
        self._validated = False

    @staticmethod
    def hash_prompt(prompt: str) -> str:
        """Hash a system prompt for mutation detection."""
        return hashlib.sha256(prompt.encode()).hexdigest()

    def register_system_prompt(self, prompt: str):
        """
        Register the system prompt at session start.
        
        Architecture §2.C.3: System prompt mutation mid-run is FORBIDDEN.
        """
        self._system_prompt_hash = self.hash_prompt(prompt)
        logger.info(f"Non-determinism budget: system prompt registered (hash: {self._system_prompt_hash[:12]})")

    def assert_prompt_unchanged(self, current_prompt: str):
        """
        Verify system prompt has not been mutated mid-run.
        
        Raises SystemPromptMutationError if prompt changed.
        """
        if self._system_prompt_hash is None:
            # First call — register it
            self.register_system_prompt(current_prompt)
            return

        current_hash = self.hash_prompt(current_prompt)
        if current_hash != self._system_prompt_hash:
            raise SystemPromptMutationError(
                f"System prompt mutated mid-run! "
                f"Original: {self._system_prompt_hash[:12]}, "
                f"Current: {current_hash[:12]}"
            )

    def validate_config(self, temperature: float, top_p: float):
        """
        Validate LLM sampling configuration against budget.
        
        Raises NonDeterminismViolation if budget is exceeded.
        """
        violations = []

        if temperature != self.allowed_temperature:
            violations.append(
                f"temperature={temperature} (allowed: {self.allowed_temperature})"
            )

        if top_p != self.allowed_top_p:
            violations.append(
                f"top_p={top_p} (allowed: {self.allowed_top_p})"
            )

        if violations and self.strict:
            raise TemperaturePolicyError(
                f"Non-determinism budget violation: {'; '.join(violations)}"
            )
        elif violations:
            for v in violations:
                logger.warning(f"Non-determinism budget warning: {v}")

        self._validated = True
        logger.info("Non-determinism budget: config validated ✅")

    @property
    def is_validated(self) -> bool:
        return self._validated

    def compute_policy_hash(self, temperature: float, top_p: float) -> str:
        """
        Compute sampling policy hash for audit logging.
        
        Architecture §2.C.2: Log sampling_policy_hash.
        """
        data = f"temp={temperature}|top_p={top_p}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
