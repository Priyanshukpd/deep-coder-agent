"""
Sandbox Permission Modes — Control what God Mode Agent is allowed to do.

Three modes (matching Codex terminology):
    read-only       → model can read files but NOT write or run commands
    workspace-write → can write within project dir only, no arbitrary shell commands
    full-access     → current God Mode behavior (default)

Usage:
    sandbox = Sandbox(SandboxMode.WORKSPACE_WRITE, repo_path="/path/to/repo")
    sandbox.check_write("/path/to/repo/src/main.py")  # OK
    sandbox.check_run("rm -rf /")                      # raises SandboxViolation
"""
from __future__ import annotations

import os
from enum import Enum


class SandboxMode(Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"


class SandboxViolation(Exception):
    """Raised when an action violates the current sandbox policy."""
    pass


class Sandbox:
    """Enforces sandbox permissions for file writes and shell commands."""

    def __init__(self, mode: SandboxMode, repo_path: str = "."):
        self.mode = mode
        self.repo_path = os.path.abspath(repo_path)

    def check_write(self, file_path: str) -> None:
        """
        Raise SandboxViolation if writing to file_path is not allowed.

        Args:
            file_path: Absolute or relative path to the file to write.

        Raises:
            SandboxViolation: If the mode prohibits this write.
        """
        if self.mode == SandboxMode.FULL_ACCESS:
            return  # No restrictions

        abs_path = os.path.abspath(file_path)

        if self.mode == SandboxMode.READ_ONLY:
            raise SandboxViolation(
                f"🔒 Sandbox: write blocked (read-only mode) → {abs_path}"
            )

        if self.mode == SandboxMode.WORKSPACE_WRITE:
            # Must be inside the project directory
            if not abs_path.startswith(self.repo_path):
                raise SandboxViolation(
                    f"🔒 Sandbox: write outside project dir blocked → {abs_path}\n"
                    f"   Project root: {self.repo_path}"
                )

    def check_run(self, command: str) -> None:
        """
        Raise SandboxViolation if running a shell command is not allowed.

        Args:
            command: Shell command string.

        Raises:
            SandboxViolation: If the mode prohibits shell execution.
        """
        if self.mode == SandboxMode.FULL_ACCESS:
            return

        if self.mode == SandboxMode.READ_ONLY:
            raise SandboxViolation(
                f"🔒 Sandbox: shell command blocked (read-only mode) → {command}"
            )

        if self.mode == SandboxMode.WORKSPACE_WRITE:
            # Block obviously dangerous commands
            danger = ["rm -rf", "sudo", "curl | sh", "wget | sh", "mkfs", "dd if=", "> /dev/"]
            cmd_lower = command.lower()
            for d in danger:
                if d in cmd_lower:
                    raise SandboxViolation(
                        f"🔒 Sandbox: dangerous command blocked (workspace-write mode) → {command}"
                    )

    @classmethod
    def from_string(cls, mode_str: str, repo_path: str = ".") -> "Sandbox":
        """Create a Sandbox from a CLI string like 'read-only'."""
        try:
            mode = SandboxMode(mode_str)
        except ValueError:
            valid = [m.value for m in SandboxMode]
            raise ValueError(f"Unknown sandbox mode '{mode_str}'. Valid: {valid}")
        return cls(mode=mode, repo_path=repo_path)

    def __repr__(self) -> str:
        return f"Sandbox(mode={self.mode.value}, repo={self.repo_path})"
