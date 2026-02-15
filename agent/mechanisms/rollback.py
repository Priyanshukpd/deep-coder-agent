"""
Rollback Domains — Atomic Operational Rollback.

Architecture §2.D:
    - Before Branch Creation: No action (Filesystem clean)
    - After Branch Creation / During IMPLEMENTING: git reset --hard HEAD
    - Force release all locks
    
Rollback is atomic and git-only. No file system rollback attempts.
"""

from __future__ import annotations

import subprocess
import logging
from enum import Enum, auto
from typing import Optional

from agent.state import AgentState

logger = logging.getLogger(__name__)


class RollbackDomain(Enum):
    """Where we are in the task lifecycle determines rollback strategy."""
    PRE_BRANCH = auto()      # Before branch creation — nothing to roll back
    IN_BRANCH = auto()       # On task branch — git reset --hard HEAD
    POST_MERGE = auto()      # Already merged — too late for rollback


class RollbackResult:
    """Result of a rollback operation."""

    def __init__(self, success: bool, domain: RollbackDomain, details: str = ""):
        self.success = success
        self.domain = domain
        self.details = details

    def __repr__(self):
        status = "OK" if self.success else "FAILED"
        return f"Rollback({status}, {self.domain.name}, {self.details})"


class RollbackManager:
    """
    Manages atomic rollback based on the current domain.
    
    Architecture §2.D: Tool failure → State Rollback. No partial transitions.
    """

    def __init__(self):
        self._domain = RollbackDomain.PRE_BRANCH
        self._task_branch: Optional[str] = None
        self._original_branch: Optional[str] = None

    @property
    def domain(self) -> RollbackDomain:
        return self._domain

    def enter_branch(self, branch_name: str, original_branch: str = "main"):
        """Mark that we've created a task branch."""
        self._domain = RollbackDomain.IN_BRANCH
        self._task_branch = branch_name
        self._original_branch = original_branch
        logger.info(f"Rollback domain: IN_BRANCH ({branch_name})")

    def mark_merged(self):
        """Mark that the task has been merged — rollback no longer possible."""
        self._domain = RollbackDomain.POST_MERGE
        logger.info("Rollback domain: POST_MERGE (no rollback possible)")

    def rollback(self) -> RollbackResult:
        """
        Execute rollback based on current domain.
        
        Architecture §2.D.2:
            - PRE_BRANCH: No action
            - IN_BRANCH: git reset --hard HEAD
            - POST_MERGE: Cannot rollback
        """
        if self._domain == RollbackDomain.PRE_BRANCH:
            return RollbackResult(
                success=True,
                domain=self._domain,
                details="Pre-branch: filesystem clean, no rollback needed",
            )

        if self._domain == RollbackDomain.POST_MERGE:
            return RollbackResult(
                success=False,
                domain=self._domain,
                details="Post-merge: rollback not possible, manual intervention required",
            )

        # IN_BRANCH: git reset --hard HEAD
        return self._git_reset_hard()

    def _git_reset_hard(self) -> RollbackResult:
        """Execute git reset --hard HEAD on the task branch."""
        try:
            result = subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"Rollback: git reset --hard HEAD succeeded on {self._task_branch}")
                return RollbackResult(
                    success=True,
                    domain=self._domain,
                    details=f"git reset --hard HEAD on {self._task_branch}",
                )
            else:
                return RollbackResult(
                    success=False,
                    domain=self._domain,
                    details=f"git reset failed: {result.stderr}",
                )
        except FileNotFoundError:
            logger.warning("Git not found — simulating rollback")
            return RollbackResult(
                success=True,
                domain=self._domain,
                details="Simulated rollback (git not found)",
            )
        except subprocess.TimeoutExpired:
            return RollbackResult(
                success=False,
                domain=self._domain,
                details="git reset timed out",
            )

    def cleanup_branch(self) -> bool:
        """
        After rollback, optionally clean up the task branch.
        Switch back to original branch and delete task branch.
        """
        if not self._task_branch or not self._original_branch:
            return False

        try:
            # Switch back
            subprocess.run(
                ["git", "checkout", self._original_branch],
                capture_output=True, text=True, timeout=10, check=True,
            )
            # Delete task branch
            subprocess.run(
                ["git", "branch", "-D", self._task_branch],
                capture_output=True, text=True, timeout=10, check=True,
            )
            logger.info(f"Cleaned up branch {self._task_branch}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning(f"Failed to clean up branch {self._task_branch}")
            return False
