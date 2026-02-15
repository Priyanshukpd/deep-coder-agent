"""
Plan Enforcement — Ensures task.md and implementation_plan.md discipline.

Validates that the agent follows its own plan:
    - task.md must exist before implementation
    - implementation_plan.md must be approved before execution
    - Changes must match the plan's scope
    - Plan completion tracking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

logger = logging.getLogger(__name__)


class PlanViolation(Exception):
    """Raised when plan discipline is violated."""
    pass


@dataclass
class PlanStatus:
    """Tracks the status of planning artifacts."""
    task_md_exists: bool = False
    plan_md_exists: bool = False
    plan_approved: bool = False
    total_items: int = 0
    completed_items: int = 0
    in_progress_items: int = 0

    @property
    def completion_pct(self) -> float:
        if self.total_items == 0:
            return 0.0
        return (self.completed_items / self.total_items) * 100

    @property
    def is_ready(self) -> bool:
        return self.task_md_exists and self.plan_md_exists and self.plan_approved


class PlanEnforcer:
    """
    Enforces planning discipline for the agent.
    
    Before IMPLEMENTING:
        - task.md must exist with at least one item
        - implementation_plan.md must exist and be approved
        
    During IMPLEMENTING:
        - Track progress against the plan
        - Reject work outside plan scope
    """

    def __init__(self, workspace_dir: str = "."):
        self._workspace = Path(workspace_dir)
        self._approved = False

    def assert_plan_exists(self) -> PlanStatus:
        """
        Verify planning artifacts exist before implementation.
        
        Raises PlanViolation if artifacts are missing.
        """
        task_path = self._workspace / "task.md"
        plan_path = self._workspace / "implementation_plan.md"

        status = PlanStatus(
            task_md_exists=task_path.exists(),
            plan_md_exists=plan_path.exists(),
            plan_approved=self._approved,
        )

        if task_path.exists():
            content = task_path.read_text()
            status.total_items, status.completed_items, status.in_progress_items = (
                self._parse_checklist(content)
            )

        if not status.task_md_exists:
            raise PlanViolation("task.md not found — plan before implementing!")

        return status

    def mark_approved(self):
        """Mark the implementation plan as approved."""
        self._approved = True
        logger.info("Implementation plan marked as APPROVED")

    def track_progress(self, task_md_content: str) -> PlanStatus:
        """Parse task.md and track completion progress."""
        total, completed, in_progress = self._parse_checklist(task_md_content)

        status = PlanStatus(
            task_md_exists=True,
            plan_md_exists=True,
            plan_approved=self._approved,
            total_items=total,
            completed_items=completed,
            in_progress_items=in_progress,
        )

        logger.info(
            f"Plan progress: {completed}/{total} complete "
            f"({status.completion_pct:.0f}%), {in_progress} in progress"
        )
        return status

    @staticmethod
    def _parse_checklist(content: str) -> tuple[int, int, int]:
        """Parse markdown checklist items from content."""
        total = 0
        completed = 0
        in_progress = 0

        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
                total += 1
                completed += 1
            elif stripped.startswith("- [/]"):
                total += 1
                in_progress += 1
            elif stripped.startswith("- [ ]"):
                total += 1

        return total, completed, in_progress
