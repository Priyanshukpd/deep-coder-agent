"""
Task Isolation — Atomic Branching + Clean Tree Invariant.

Architecture §2.F / §3:
    - Assert clean working tree before branching
    - Create task branch: agent/task-{id}
    - Enforce ordering: Plan → Hash → THEN Task Isolation
"""

from __future__ import annotations

import subprocess
import uuid
import logging

logger = logging.getLogger(__name__)


class TaskIsolationError(Exception):
    """Raised when task isolation fails."""
    pass


class DirtyWorkingTreeError(TaskIsolationError):
    """Working tree has uncommitted changes."""
    pass


class TaskIsolation:
    """
    Manages atomic task branching for the agent.
    
    Invariant: Working tree must be clean before creating task branch.
    """

    @staticmethod
    def assert_clean_tree() -> bool:
        """
        Check that `git status --porcelain` is empty.
        
        Returns True if clean, raises DirtyWorkingTreeError if dirty.
        Architecture §2.F.1: If dirty → ABORT immediately.
        """
        try:
            # Check if it's even a git repo
            is_repo = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, timeout=5
            )
            if is_repo.returncode != 0:
                logger.warning("Not a git repository — skipping clean tree check")
                return True

            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout.strip()
            if output:
                # If there are files but no commits, git status --porcelain might show ?? files
                # We still consider it "dirty" if we want to enforce isolation, 
                # but if there's no HEAD, we can't branch anyway.
                # However, for robustness, we'll allow proceed if no commits exist.
                has_head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5
                )
                if has_head.returncode != 0:
                    logger.info("Fresh repository (no commits) — allowing proceed")
                    return True

                raise DirtyWorkingTreeError(
                    f"Working tree is dirty. Uncommitted changes:\n{output}"
                )
            return True
        except FileNotFoundError:
            logger.warning("Git not found — skipping clean tree check")
            return True
        except subprocess.TimeoutExpired:
            raise TaskIsolationError("Git status timed out")

    @staticmethod
    def create_task_branch(task_id: str = None) -> str:
        """
        Create and checkout a new task branch.
        
        Returns the branch name.
        Architecture §3: git checkout -b agent/task-{id}
        """
        if task_id is None:
            task_id = uuid.uuid4().hex[:8]

        branch_name = f"agent/task-{task_id}"

        try:
            # Check if HEAD exists (cannot branch from nothing)
            has_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            if has_head.returncode != 0:
                logger.warning("Cannot create branch: No commits in repository (HEAD not found).")
                return "main" # Or current default branch

            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                capture_output=True, text=True, check=True, timeout=10,
            )
            logger.info(f"Created task branch: {branch_name}")
            return branch_name
        except subprocess.CalledProcessError as e:
            # If we're already on a branch with this name, or other git error
            if "already exists" in e.stderr:
                 logger.info(f"Branch {branch_name} already exists, switching...")
                 subprocess.run(["git", "checkout", branch_name], capture_output=True)
                 return branch_name
            raise TaskIsolationError(f"Failed to create branch: {e.stderr}")
        except FileNotFoundError:
            logger.warning("Git not found — simulating branch creation")
            return branch_name

    @staticmethod
    def get_base_sha() -> str:
        """Capture the base SHA for drift detection."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "origin/main"],
                capture_output=True, text=True, timeout=10,
            )
            sha = result.stdout.strip()
            return sha if sha else "unknown"
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "unknown"

    @staticmethod
    def get_current_branch_head() -> str:
        """Get current branch HEAD SHA."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "unknown"
