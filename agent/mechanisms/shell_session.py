"""
Shell Session Persistence — Maintains shell state across commands.

Provides a persistent shell session that remembers:
    - Environment variables set during the session
    - Working directory changes
    - Shell function definitions
    
Uses a subprocess with persistent stdin/stdout for session continuity.
"""

from __future__ import annotations

import subprocess
import os
import time
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ShellOutput:
    """Result of a shell command within a persistent session."""
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float


class PersistentShell:
    """
    A persistent shell session that maintains state across commands.
    
    Unlike subprocess.run() which creates a new process for each command,
    this maintains a single shell process, preserving:
        - Environment variable changes (export FOO=bar)
        - Current directory (cd /path)
        - Shell functions and aliases
    """

    def __init__(self, shell: str = "/bin/bash", cwd: str = None):
        self._shell = shell
        self._cwd = cwd or os.getcwd()
        self._process: Optional[subprocess.Popen] = None
        self._history: list[ShellOutput] = []
        self._env_snapshot: dict[str, str] = {}

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def history(self) -> list[ShellOutput]:
        return list(self._history)

    def start(self):
        """Start the persistent shell session."""
        if self.is_alive:
            return

        self._process = subprocess.Popen(
            [self._shell],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self._cwd,
            env=os.environ.copy(),
        )
        logger.info(f"Shell session started (PID: {self._process.pid})")

    def run(self, command: str, timeout: int = 30) -> ShellOutput:
        """
        Run a command in the persistent session.
        
        Uses a sentinel marker to detect when command output ends.
        """
        if not self.is_alive:
            self.start()

        sentinel = f"__CMD_DONE_{time.time_ns()}__"
        exit_sentinel = f"__EXIT_{time.time_ns()}__"

        # Write command with sentinel markers
        full_cmd = (
            f"{command}\n"
            f"echo {exit_sentinel}$?\n"
            f"echo {sentinel}\n"
        )

        start = time.time()

        try:
            self._process.stdin.write(full_cmd)
            self._process.stdin.flush()

            # Read output until sentinel
            stdout_lines = []
            exit_code = 0

            deadline = time.time() + timeout
            while time.time() < deadline:
                line = self._process.stdout.readline()
                if not line:
                    break

                if sentinel in line:
                    break

                if exit_sentinel in line:
                    try:
                        exit_code = int(line.replace(exit_sentinel, "").strip())
                    except ValueError:
                        pass
                    continue

                stdout_lines.append(line)

            duration = (time.time() - start) * 1000
            stdout = "".join(stdout_lines)

            output = ShellOutput(
                command=command,
                stdout=stdout,
                stderr="",
                exit_code=exit_code,
                duration_ms=duration,
            )
            self._history.append(output)
            return output

        except Exception as e:
            duration = (time.time() - start) * 1000
            output = ShellOutput(
                command=command,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration_ms=duration,
            )
            self._history.append(output)
            return output

    def stop(self):
        """Stop the persistent shell session."""
        if self._process:
            try:
                self._process.stdin.write("exit\n")
                self._process.stdin.flush()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            finally:
                logger.info("Shell session stopped")
                self._process = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


class ShellSessionManager:
    """
    Manages multiple named shell sessions.
    
    Usage:
        manager = ShellSessionManager()
        session = manager.get_or_create("task_123")
        result = session.run("echo hello")
        manager.cleanup()
    """

    def __init__(self):
        self._sessions: dict[str, PersistentShell] = {}

    def get_or_create(
        self,
        name: str,
        cwd: str = None,
    ) -> PersistentShell:
        """Get an existing session or create a new one."""
        if name not in self._sessions:
            shell = PersistentShell(cwd=cwd)
            shell.start()
            self._sessions[name] = shell
            logger.info(f"Created shell session: {name}")
        return self._sessions[name]

    def close(self, name: str):
        """Close a specific session."""
        if name in self._sessions:
            self._sessions[name].stop()
            del self._sessions[name]

    def cleanup(self):
        """Close all sessions."""
        for name in list(self._sessions):
            self.close(name)
        logger.info("All shell sessions cleaned up")

    @property
    def active_sessions(self) -> list[str]:
        return [n for n, s in self._sessions.items() if s.is_alive]
