"""
Merge Guard — Optimistic Merge Security + CI Binding.

Architecture §2.E:
    1. Optimistic Guard: origin/main SHA must match branch_base_sha
    2. CI Guard: ci_validated_sha must match branch_head_sha
    3. Status Guard: CI Status must be SUCCESS
    4. Freshness: CI run must be the LATEST execution for that SHA
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class MergeBlockReason(Enum):
    """Why a merge was blocked."""
    STALE_BASE = auto()          # origin/main moved
    CI_SHA_MISMATCH = auto()     # CI ran on different SHA
    CI_NOT_SUCCESS = auto()      # CI failed or pending
    CI_NOT_LATEST = auto()       # Stale CI run
    LOCKFILE_MISMATCH = auto()   # Dependencies changed
    TEST_MODIFIED = auto()       # Test code was modified after writing


@dataclass
class MergeCheckResult:
    """Result of merge guard validation."""
    can_merge: bool
    block_reason: Optional[MergeBlockReason] = None
    details: str = ""


class MergeGuard:
    """
    Validates all preconditions before merging a task branch.
    
    Architecture §2.E — All four guards must pass:
        1. branch_base_sha == origin/main (no drift)
        2. ci_validated_sha == branch_head_sha (CI ran on our code)
        3. CI status == SUCCESS
        4. CI run is the latest for that SHA (no stale cache)
    """

    @staticmethod
    def check_base_freshness(
        branch_base_sha: str,
        origin_main_sha: str,
    ) -> MergeCheckResult:
        """Guard 1: origin/main hasn't moved since we branched."""
        if branch_base_sha != origin_main_sha:
            return MergeCheckResult(
                can_merge=False,
                block_reason=MergeBlockReason.STALE_BASE,
                details=(
                    f"Base drift: branch base={branch_base_sha[:12]}, "
                    f"origin/main={origin_main_sha[:12]}"
                ),
            )
        return MergeCheckResult(can_merge=True, details="Base is fresh")

    @staticmethod
    def check_ci_sha(
        ci_validated_sha: str,
        branch_head_sha: str,
    ) -> MergeCheckResult:
        """Guard 2: CI validated the exact SHA we're trying to merge."""
        if ci_validated_sha != branch_head_sha:
            return MergeCheckResult(
                can_merge=False,
                block_reason=MergeBlockReason.CI_SHA_MISMATCH,
                details=(
                    f"CI SHA mismatch: ci={ci_validated_sha[:12]}, "
                    f"head={branch_head_sha[:12]}"
                ),
            )
        return MergeCheckResult(can_merge=True, details="CI SHA matches HEAD")

    @staticmethod
    def check_ci_status(status: str) -> MergeCheckResult:
        """Guard 3: CI status must be SUCCESS."""
        if status.upper() != "SUCCESS":
            return MergeCheckResult(
                can_merge=False,
                block_reason=MergeBlockReason.CI_NOT_SUCCESS,
                details=f"CI status: {status} (expected: SUCCESS)",
            )
        return MergeCheckResult(can_merge=True, details="CI status is SUCCESS")

    @staticmethod
    def check_ci_freshness(is_latest_run: bool) -> MergeCheckResult:
        """Guard 4: CI run must be the latest for that SHA."""
        if not is_latest_run:
            return MergeCheckResult(
                can_merge=False,
                block_reason=MergeBlockReason.CI_NOT_LATEST,
                details="Stale CI run — a newer push invalidated this result",
            )
        return MergeCheckResult(can_merge=True, details="CI run is the latest")

    @staticmethod
    def full_check(
        branch_base_sha: str,
        origin_main_sha: str,
        ci_validated_sha: str,
        branch_head_sha: str,
        ci_status: str,
        is_latest_run: bool,
    ) -> MergeCheckResult:
        """Run all four merge guards. Returns first failure or success."""
        checks = [
            MergeGuard.check_base_freshness(branch_base_sha, origin_main_sha),
            MergeGuard.check_ci_sha(ci_validated_sha, branch_head_sha),
            MergeGuard.check_ci_status(ci_status),
            MergeGuard.check_ci_freshness(is_latest_run),
        ]

        for result in checks:
            if not result.can_merge:
                logger.critical(f"MERGE BLOCKED: {result.details}")
                return result

        logger.info("All merge guards passed — safe to merge")
        return MergeCheckResult(can_merge=True, details="All guards passed")
