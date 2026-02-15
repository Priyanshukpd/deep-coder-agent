"""
Kill Switch Cascade — INTERRUPT / TIMEOUT / STALE state handlers.

Architecture §1 (terminal states):
    - FAILED_BY_INTERRUPT: SIGINT / User Stop → Audit Distinct, stops auto-retry
    - FAILED_BY_TIMEOUT: Runtime > 15m → Hard Stop, SIGTERM → Log → Exit
    - FAILED_BY_STALE: main moved during task → No Retry, new invocation required
    - FAILED_BY_SCOPE: RepoMap > MAX_FILE_CAP → Hard Stop, require narrower prompt
"""

from __future__ import annotations

import signal
import time
import logging
import threading
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.controller import StateMachineController

from agent.state import AgentState

logger = logging.getLogger(__name__)


# Default timeout: 15 minutes (Architecture §1)
DEFAULT_TIMEOUT_SECONDS = 15 * 60


class KillSwitch:
    """
    Manages the kill switch cascade for the agent.
    
    Handles:
        - Signal-based interrupts (SIGINT, SIGTERM)
        - Timeout enforcement (configurable, default 15m)
        - Stale branch detection callbacks
    """

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        on_interrupt: Optional[Callable] = None,
        on_timeout: Optional[Callable] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self._start_time: Optional[float] = None
        self._timer: Optional[threading.Timer] = None
        self._interrupted = False
        self._timed_out = False
        self._original_sigint = None
        self._original_sigterm = None
        self._on_interrupt = on_interrupt
        self._on_timeout = on_timeout

    def arm(self):
        """
        Arm the kill switch. Call at task start.
        
        - Installs signal handlers for SIGINT/SIGTERM
        - Starts the timeout timer
        """
        self._start_time = time.time()
        self._interrupted = False
        self._timed_out = False

        # Install signal handlers (only if in main thread)
        if threading.current_thread() is threading.main_thread():
            try:
                self._original_sigint = signal.getsignal(signal.SIGINT)
                self._original_sigterm = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGINT, self._handle_interrupt)
                signal.signal(signal.SIGTERM, self._handle_interrupt)
            except ValueError as e:
                logger.warning(f"Could not attach signal handlers: {e}")
        else:
            logger.info("Running in background thread; skipping signal handlers.")

        # Start timeout timer
        self._timer = threading.Timer(self.timeout_seconds, self._handle_timeout)
        self._timer.daemon = True
        self._timer.start()

        logger.info(
            f"Kill switch armed: timeout={self.timeout_seconds}s "
            f"({self.timeout_seconds / 60:.1f}m)"
        )

    def disarm(self):
        """Disarm the kill switch. Call at task end."""
        # Cancel timer
        if self._timer:
            self._timer.cancel()
            self._timer = None

        # Restore original signal handlers
        if self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)
            self._original_sigint = None
        if self._original_sigterm:
            signal.signal(signal.SIGTERM, self._original_sigterm)
            self._original_sigterm = None

        logger.info("Kill switch disarmed")

    def check(self) -> Optional[AgentState]:
        """
        Check if kill switch has been triggered.
        
        Returns the failure state if triggered, None if OK.
        Call this before every major operation.
        """
        if self._interrupted:
            return AgentState.FAILED_BY_INTERRUPT
        if self._timed_out:
            return AgentState.FAILED_BY_TIMEOUT
        if self._is_over_time():
            self._timed_out = True
            return AgentState.FAILED_BY_TIMEOUT
        return None

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    @property
    def is_armed(self) -> bool:
        return self._start_time is not None

    def _is_over_time(self) -> bool:
        if self._start_time is None:
            return False
        return (time.time() - self._start_time) > self.timeout_seconds

    def _handle_interrupt(self, signum, frame):
        """Handle SIGINT/SIGTERM — transition to FAILED_BY_INTERRUPT."""
        logger.critical(f"KILL SWITCH: Received signal {signum}. Stopping agent.")
        self._interrupted = True
        if self._on_interrupt:
            self._on_interrupt()

    def _handle_timeout(self):
        """Handle timeout — transition to FAILED_BY_TIMEOUT."""
        logger.critical(
            f"KILL SWITCH: Timeout after {self.timeout_seconds}s. Hard stop."
        )
        self._timed_out = True
        if self._on_timeout:
            self._on_timeout()


class StaleDetector:
    """
    Detects if origin/main has moved during task execution.
    
    Architecture §1: FAILED_BY_STALE — main moved during task → No Retry.
    """

    def __init__(self):
        self._base_sha: Optional[str] = None

    def capture_base(self, sha: str):
        """Capture the base SHA at task start."""
        self._base_sha = sha
        logger.info(f"Stale detector: captured base SHA {sha[:12]}")

    def check(self) -> bool:
        """
        Check if origin/main has moved.
        
        Returns True if stale (main moved), False if fresh.
        """
        if not self._base_sha:
            return False

        import subprocess
        try:
            result = subprocess.run(
                ["git", "rev-parse", "origin/main"],
                capture_output=True, text=True, timeout=10,
            )
            current_sha = result.stdout.strip()
            if current_sha and current_sha != self._base_sha:
                logger.critical(
                    f"STALE DETECTED: base={self._base_sha[:12]}, "
                    f"current={current_sha[:12]}"
                )
                return True
            return False
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False  # Can't check → assume fresh
