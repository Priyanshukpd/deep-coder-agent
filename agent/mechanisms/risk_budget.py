"""
Risk Budget — Multi-dimensional safety limits.

Prevents runaway behavior by tracking retries, diff sizes,
shell commands, time, and files modified. Any dimension exceeding
its budget transitions the agent to FAILED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
import time


class BudgetDimension(Enum):
    """Which dimension was exceeded."""

    RETRIES_PER_STATE = auto()
    TOTAL_RETRIES = auto()
    DIFF_LINES = auto()
    SHELL_COMMANDS = auto()
    EXECUTION_TIME = auto()
    FILES_MODIFIED = auto()
    CUMULATIVE_DIFF = auto()


@dataclass
class BudgetViolation:
    """Record of a budget limit being exceeded."""

    dimension: BudgetDimension
    current_value: int | float
    limit: int | float
    message: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RiskBudget:
    """
    Multi-dimensional safety budget.

    Tracks consumption across all dimensions and raises violations
    when limits are exceeded. All limits are configurable.
    """

    # -- Configurable Limits --
    max_retries_per_state: int = 3
    max_total_retries: int = 10
    max_diff_lines: int = 200           # single diff — confirm if exceeded
    max_cumulative_diff_lines: int = 500 # total across task — hard stop
    max_shell_commands: int = 20
    max_execution_time_sec: int = 1200    # 20 minutes
    max_files_modified: int = 5          # confirm if exceeded
    require_confirmation_on_large_diff: bool = True

    # -- Runtime Tracking --
    _retry_count: dict[str, int] = field(default_factory=dict)
    _total_retries: int = 0
    _cumulative_diff_lines: int = 0
    _shell_commands_used: int = 0
    _files_modified: set[str] = field(default_factory=set)
    _start_time: Optional[float] = None
    _violations: list[BudgetViolation] = field(default_factory=list)

    def start(self) -> None:
        """Mark task start for time tracking."""
        self._start_time = time.time()

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    # -- Recording Methods --

    def record_retry(self, state_name: str) -> Optional[BudgetViolation]:
        """Record a retry in a given state. Returns violation if exceeded."""
        self._retry_count[state_name] = self._retry_count.get(state_name, 0) + 1
        self._total_retries += 1

        # Check per-state limit
        if self._retry_count[state_name] > self.max_retries_per_state:
            v = BudgetViolation(
                dimension=BudgetDimension.RETRIES_PER_STATE,
                current_value=self._retry_count[state_name],
                limit=self.max_retries_per_state,
                message=f"Exceeded {self.max_retries_per_state} retries in state {state_name}",
            )
            self._violations.append(v)
            return v

        # Check total limit
        if self._total_retries > self.max_total_retries:
            v = BudgetViolation(
                dimension=BudgetDimension.TOTAL_RETRIES,
                current_value=self._total_retries,
                limit=self.max_total_retries,
                message=f"Exceeded {self.max_total_retries} total retries across all states",
            )
            self._violations.append(v)
            return v

        return None

    def record_diff(self, lines: int, filepath: str) -> Optional[BudgetViolation]:
        """Record a diff being applied. Returns violation if exceeded."""
        self._cumulative_diff_lines += lines
        self._files_modified.add(filepath)

        # Single-diff check (warning, may need confirmation)
        if lines > self.max_diff_lines and self.require_confirmation_on_large_diff:
            v = BudgetViolation(
                dimension=BudgetDimension.DIFF_LINES,
                current_value=lines,
                limit=self.max_diff_lines,
                message=f"Diff of {lines} lines exceeds {self.max_diff_lines}-line limit. Requires confirmation.",
            )
            self._violations.append(v)
            return v

        # Cumulative diff hard stop
        if self._cumulative_diff_lines > self.max_cumulative_diff_lines:
            v = BudgetViolation(
                dimension=BudgetDimension.CUMULATIVE_DIFF,
                current_value=self._cumulative_diff_lines,
                limit=self.max_cumulative_diff_lines,
                message=f"Cumulative diff ({self._cumulative_diff_lines} lines) exceeds hard limit",
            )
            self._violations.append(v)
            return v

        # Files-modified check
        if len(self._files_modified) > self.max_files_modified:
            v = BudgetViolation(
                dimension=BudgetDimension.FILES_MODIFIED,
                current_value=len(self._files_modified),
                limit=self.max_files_modified,
                message=f"Modified {len(self._files_modified)} files, exceeds {self.max_files_modified} limit",
            )
            self._violations.append(v)
            return v

        return None

    def record_shell_command(self) -> Optional[BudgetViolation]:
        """Record a shell command execution."""
        self._shell_commands_used += 1
        if self._shell_commands_used > self.max_shell_commands:
            v = BudgetViolation(
                dimension=BudgetDimension.SHELL_COMMANDS,
                current_value=self._shell_commands_used,
                limit=self.max_shell_commands,
                message=f"Exceeded {self.max_shell_commands} shell commands",
            )
            self._violations.append(v)
            return v
        return None

    def check_time(self) -> Optional[BudgetViolation]:
        """Check if execution time budget is exceeded."""
        if self.elapsed_seconds > self.max_execution_time_sec:
            v = BudgetViolation(
                dimension=BudgetDimension.EXECUTION_TIME,
                current_value=self.elapsed_seconds,
                limit=self.max_execution_time_sec,
                message=f"Execution time ({self.elapsed_seconds:.0f}s) exceeds {self.max_execution_time_sec}s limit",
            )
            self._violations.append(v)
            return v
        return None

    # -- Query Methods --

    @property
    def is_exhausted(self) -> bool:
        """True if any hard-stop dimension is exceeded."""
        return any(
            v.dimension
            in {
                BudgetDimension.TOTAL_RETRIES,
                BudgetDimension.CUMULATIVE_DIFF,
                BudgetDimension.EXECUTION_TIME,
                BudgetDimension.SHELL_COMMANDS,
            }
            for v in self._violations
        )

    @property
    def needs_confirmation(self) -> bool:
        """True if a soft limit was hit (large diff, many files)."""
        return any(
            v.dimension
            in {
                BudgetDimension.DIFF_LINES,
                BudgetDimension.FILES_MODIFIED,
                BudgetDimension.RETRIES_PER_STATE,
            }
            for v in self._violations
        )

    @property
    def violations(self) -> list[BudgetViolation]:
        return list(self._violations)

    def summary(self) -> dict:
        """Return current budget consumption as a dict (for logging)."""
        return {
            "retries_by_state": dict(self._retry_count),
            "total_retries": self._total_retries,
            "cumulative_diff_lines": self._cumulative_diff_lines,
            "shell_commands": self._shell_commands_used,
            "files_modified": len(self._files_modified),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "is_exhausted": self.is_exhausted,
            "violation_count": len(self._violations),
        }
