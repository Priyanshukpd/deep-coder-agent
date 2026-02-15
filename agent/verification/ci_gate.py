"""
CI Gate Integration — Interface for CI/CD status checks.

Architecture §2.E:
    - CI Guard: ci_validated_sha must match branch_head_sha
    - Status Guard: CI Status must be SUCCESS
    - Freshness: CI run must be the LATEST execution for that SHA

This module provides the interface and a mock implementation.
Real implementations would integrate with GitHub Actions, GitLab CI, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Protocol
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class CIStatus(Enum):
    """CI pipeline status values."""
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILURE = auto()
    CANCELLED = auto()
    UNKNOWN = auto()


@dataclass
class CIResult:
    """Result from a CI pipeline run."""
    sha: str                         # SHA the CI ran against
    status: CIStatus                 # Pass/Fail/Pending
    is_latest: bool = True           # Is this the latest run for this SHA?
    run_id: str = ""                 # CI run identifier
    details: str = ""                # Human-readable details
    duration_seconds: float = 0.0    # How long the CI run took


class CIGateInterface(ABC):
    """
    Abstract interface for CI gate integrations.
    
    Implementations should poll the CI system and return status.
    """

    @abstractmethod
    def get_status(self, sha: str) -> CIResult:
        """Get the CI status for a given commit SHA."""
        ...

    @abstractmethod
    def trigger_run(self, sha: str) -> str:
        """Trigger a new CI run. Returns run_id."""
        ...

    @abstractmethod
    def wait_for_completion(self, sha: str, timeout_seconds: int = 600) -> CIResult:
        """Block until CI completes or timeout. Returns final status."""
        ...


class MockCIGate(CIGateInterface):
    """
    Mock CI gate for testing and dry runs.
    
    Always returns SUCCESS by default.
    Can be configured to simulate failures.
    """

    def __init__(self, default_status: CIStatus = CIStatus.SUCCESS):
        self._default_status = default_status
        self._overrides: dict[str, CIStatus] = {}
        self._run_counter = 0

    def set_status_for_sha(self, sha: str, status: CIStatus):
        """Configure a specific status for a SHA (for testing)."""
        self._overrides[sha] = status

    def get_status(self, sha: str) -> CIResult:
        """Get mock CI status."""
        status = self._overrides.get(sha, self._default_status)
        return CIResult(
            sha=sha,
            status=status,
            is_latest=True,
            run_id=f"mock-run-{self._run_counter}",
            details=f"Mock CI: {status.name}",
        )

    def trigger_run(self, sha: str) -> str:
        """Trigger a mock CI run."""
        self._run_counter += 1
        run_id = f"mock-run-{self._run_counter}"
        logger.info(f"Mock CI triggered: run_id={run_id}, sha={sha[:12]}")
        return run_id

    def wait_for_completion(self, sha: str, timeout_seconds: int = 600) -> CIResult:
        """Mock wait — returns immediately."""
        return self.get_status(sha)


class CIGate:
    """
    High-level CI gate that combines CI checks with merge guard validation.
    
    Usage:
        gate = CIGate(ci=MockCIGate())
        result = gate.validate(sha="abc123", branch_base_sha="def456")
    """

    def __init__(self, ci: CIGateInterface = None):
        self.ci = ci or MockCIGate()

    def validate(
        self,
        sha: str,
        branch_base_sha: str = "",
    ) -> CIResult:
        """
        Full CI validation for a commit.
        
        1. Get CI status
        2. Verify SHA matches
        3. Verify status is SUCCESS
        4. Verify it's the latest run
        """
        result = self.ci.get_status(sha)

        if result.status != CIStatus.SUCCESS:
            logger.warning(f"CI gate: status is {result.status.name}, not SUCCESS")

        if result.sha != sha:
            logger.critical(f"CI gate: SHA mismatch! Expected {sha[:12]}, got {result.sha[:12]}")
            result.status = CIStatus.FAILURE
            result.details = "SHA mismatch"

        if not result.is_latest:
            logger.critical(f"CI gate: stale run detected for {sha[:12]}")
            result.status = CIStatus.FAILURE
            result.details = "Stale CI run"

        return result
