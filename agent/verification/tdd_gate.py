"""
TDD Gate — Red/Green Integrity Check.

Architecture §1 PROVING_GROUND:
    - Intent != REFACTOR → must write test first
    - Test must FAIL initially (Red)
    - After IMPLEMENTING, test must PASS (Green)
    - Test code must not be modified after initial writing

Architecture §1 VERIFYING:
    - TDD Check: Test Code Unmodified
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class TDDViolation(Exception):
    """Raised when TDD integrity is violated."""
    pass


class TestNotRedError(TDDViolation):
    """Test did not fail initially (not a valid Red test)."""
    pass


class TestNotGreenError(TDDViolation):
    """Test did not pass after implementation."""
    pass


class TestModifiedError(TDDViolation):
    """Test code was modified after initial writing."""
    pass


@dataclass
class TDDCheckpoint:
    """Snapshot of test file state for integrity verification."""
    test_file: str
    initial_hash: str        # Hash when test was first written
    is_red: bool = False     # Did the test fail initially?
    is_green: bool = False   # Did the test pass after implementation?
    final_hash: Optional[str] = None  # Hash at verification time

    @property
    def integrity_ok(self) -> bool:
        """Test code was not modified between Red and Green phases."""
        if self.final_hash is None:
            return True
        return self.initial_hash == self.final_hash


class TDDGate:
    """
    Enforces TDD discipline: Write Red test → Implement → Verify Green.
    
    Tracks test file hashes to detect unauthorized modifications.
    """

    def __init__(self):
        self._checkpoints: dict[str, TDDCheckpoint] = {}

    @staticmethod
    def hash_content(content: str) -> str:
        """Hash test file content for integrity tracking."""
        return hashlib.sha256(content.encode()).hexdigest()

    def register_test(self, test_file: str, content: str) -> TDDCheckpoint:
        """
        Register a newly written test file in the Red phase.
        
        Called during PROVING_GROUND after writing the test.
        """
        checkpoint = TDDCheckpoint(
            test_file=test_file,
            initial_hash=self.hash_content(content),
        )
        self._checkpoints[test_file] = checkpoint
        logger.info(f"TDD: Registered test {test_file} (hash: {checkpoint.initial_hash[:12]})")
        return checkpoint

    def assert_red(self, test_file: str, test_passed: bool):
        """
        Assert that the test FAILED initially (Red phase).
        
        Architecture §1: PROVING_GROUND → TEST FAILED (AssertionError)
        """
        if test_file not in self._checkpoints:
            raise TDDViolation(f"Test {test_file} not registered with TDD gate")

        if test_passed:
            raise TestNotRedError(
                f"TDD violation: {test_file} passed without implementation! "
                f"This is a tautological test."
            )

        self._checkpoints[test_file].is_red = True
        logger.info(f"TDD: {test_file} correctly FAILED (Red ✅)")

    def assert_green(self, test_file: str, test_passed: bool, current_content: str):
        """
        Assert that the test PASSED after implementation (Green phase).
        Also verify test integrity (code wasn't modified).
        
        Architecture §1: VERIFYING → TEST PASSED + TDD Check: Test Code Unmodified
        """
        if test_file not in self._checkpoints:
            raise TDDViolation(f"Test {test_file} not registered with TDD gate")

        checkpoint = self._checkpoints[test_file]

        # Check integrity — test code should not have been modified
        checkpoint.final_hash = self.hash_content(current_content)
        if not checkpoint.integrity_ok:
            raise TestModifiedError(
                f"TDD violation: {test_file} was modified after initial writing! "
                f"Original: {checkpoint.initial_hash[:12]}, "
                f"Current: {checkpoint.final_hash[:12]}"
            )

        if not test_passed:
            raise TestNotGreenError(
                f"TDD violation: {test_file} still fails after implementation!"
            )

        checkpoint.is_green = True
        logger.info(f"TDD: {test_file} correctly PASSED (Green ✅), integrity OK")

    def get_checkpoint(self, test_file: str) -> Optional[TDDCheckpoint]:
        """Get the TDD checkpoint for a test file."""
        return self._checkpoints.get(test_file)

    @property
    def all_checkpoints(self) -> list[TDDCheckpoint]:
        return list(self._checkpoints.values())
