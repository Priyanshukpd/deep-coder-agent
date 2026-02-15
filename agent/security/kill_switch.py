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


# Default timeout: 30 minutes (adaptive, can be extended)
DEFAULT_TIMEOUT_SECONDS = 30 * 60

# Stack-specific timeouts (seconds)
STACK_TIMEOUTS = {
    'python': 30 * 60,      # 30 min — pip installs, ML model downloads
    'java': 45 * 60,        # 45 min — Maven/Gradle builds are slow
    'node': 30 * 60,        # 30 min — npm install + build
    'go': 30 * 60,          # 30 min
    'rust': 45 * 60,        # 45 min — cargo build can be very slow
    'dart': 30 * 60,        # 30 min
    'docker': 60 * 60,      # 60 min — Docker builds + pulls can take very long
    'generic': 30 * 60,     # 30 min
}

# Commands that deserve extra time when detected
SLOW_COMMAND_EXTENSIONS = {
    'docker build': 20 * 60,
    'docker-compose up': 20 * 60,
    'docker compose up': 20 * 60,
    'docker pull': 15 * 60,
    'mvn install': 15 * 60,
    'mvn package': 15 * 60,
    'gradle build': 15 * 60,
    'cargo build': 15 * 60,
    'flutter build': 10 * 60,
    'pip install torch': 10 * 60,
    'pip install tensorflow': 10 * 60,
    'npm install': 5 * 60,
    'apt-get install': 10 * 60,
    'brew install': 10 * 60,
}


class KillSwitch:
    """
    Adaptive kill switch with stack-aware timeouts.

    Features:
        - Signal-based interrupts (SIGINT, SIGTERM)
        - Stack-aware default timeouts (Docker=60m, Java/Rust=45m, etc.)
        - extend(): adds time when slow commands start (Docker build, model download)
        - heartbeat(): resets the inactivity timer — keeps alive as long as progress
        - Hard cap: 90 minutes absolute maximum (safety net)
    """

    HARD_CAP_SECONDS = 90 * 60  # 90 min absolute max — no extension beyond this

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        on_interrupt: Optional[Callable] = None,
        on_timeout: Optional[Callable] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self._start_time: Optional[float] = None
        self._deadline: Optional[float] = None     # absolute deadline
        self._timer: Optional[threading.Timer] = None
        self._interrupted = False
        self._timed_out = False
        self._original_sigint = None
        self._original_sigterm = None
        self._on_interrupt = on_interrupt
        self._on_timeout = on_timeout
        self._extensions: list[str] = []  # log of extensions applied

    @classmethod
    def for_stack(cls, stack: str, **kwargs) -> 'KillSwitch':
        """Create a kill switch with stack-appropriate timeout."""
        timeout = STACK_TIMEOUTS.get(stack, DEFAULT_TIMEOUT_SECONDS)
        logger.info(f"Kill switch: using {timeout // 60}m timeout for stack '{stack}'")
        return cls(timeout_seconds=timeout, **kwargs)

    def arm(self):
        """
        Arm the kill switch. Call at task start.

        - Installs signal handlers for SIGINT/SIGTERM
        - Starts the timeout timer
        """
        self._start_time = time.time()
        self._deadline = self._start_time + self.timeout_seconds
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

        if self._extensions:
            logger.info(f"Kill switch disarmed. Extensions applied: {self._extensions}")
        else:
            logger.info("Kill switch disarmed")

    def extend(self, reason: str, extra_seconds: Optional[int] = None):
        """
        Extend the deadline for a known slow operation.

        Call this BEFORE starting a slow command like 'docker build' or 'mvn install'.
        If extra_seconds is None, auto-detects from SLOW_COMMAND_EXTENSIONS.
        """
        if extra_seconds is None:
            # Auto-detect from command
            extra_seconds = 0
            reason_lower = reason.lower()
            for cmd_pattern, extension in SLOW_COMMAND_EXTENSIONS.items():
                if cmd_pattern in reason_lower:
                    extra_seconds = extension
                    break
            if extra_seconds == 0:
                extra_seconds = 5 * 60  # Default 5 min extension

        if self._deadline is None:
            return

        hard_cap = self._start_time + self.HARD_CAP_SECONDS if self._start_time else None
        new_deadline = self._deadline + extra_seconds

        # Respect hard cap
        if hard_cap and new_deadline > hard_cap:
            new_deadline = hard_cap
            actual_ext = int(new_deadline - self._deadline)
            if actual_ext <= 0:
                logger.warning(f"Kill switch: at hard cap ({self.HARD_CAP_SECONDS // 60}m), cannot extend for '{reason}'")
                return
            logger.info(f"Kill switch: extending +{actual_ext // 60}m for '{reason}' (capped at {self.HARD_CAP_SECONDS // 60}m total)")
        else:
            logger.info(f"Kill switch: extending +{extra_seconds // 60}m for '{reason}'")

        self._deadline = new_deadline
        self._extensions.append(f"+{extra_seconds // 60}m ({reason})")

        # Reset the timer
        if self._timer:
            self._timer.cancel()
        remaining = max(1, self._deadline - time.time())
        self._timer = threading.Timer(remaining, self._handle_timeout)
        self._timer.daemon = True
        self._timer.start()

    def heartbeat(self):
        """
        Signal that the agent is still making progress.

        Call this periodically during long operations (e.g. after each
        file write, each build step, each test run). Ensures the agent
        won't be killed while actively working.

        Extends deadline by 5 minutes from now, up to the hard cap.
        """
        if self._deadline is None or self._start_time is None:
            return

        min_deadline = time.time() + 5 * 60  # At least 5 min from now
        hard_cap = self._start_time + self.HARD_CAP_SECONDS

        if self._deadline < min_deadline and min_deadline <= hard_cap:
            self._deadline = min_deadline
            # Reset timer
            if self._timer:
                self._timer.cancel()
            remaining = max(1, self._deadline - time.time())
            self._timer = threading.Timer(remaining, self._handle_timeout)
            self._timer.daemon = True
            self._timer.start()

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
    def remaining_seconds(self) -> float:
        if self._deadline is None:
            return 0.0
        return max(0, self._deadline - time.time())

    @property
    def is_armed(self) -> bool:
        return self._start_time is not None

    def _is_over_time(self) -> bool:
        if self._deadline is None:
            return False
        return time.time() > self._deadline

    def _handle_interrupt(self, signum, frame):
        """Handle SIGINT/SIGTERM — transition to FAILED_BY_INTERRUPT."""
        logger.critical(f"KILL SWITCH: Received signal {signum}. Stopping agent.")
        self._interrupted = True
        if self._on_interrupt:
            self._on_interrupt()

    def _handle_timeout(self):
        """Handle timeout — transition to FAILED_BY_TIMEOUT."""
        elapsed = self.elapsed_seconds
        logger.critical(
            f"KILL SWITCH: Timeout after {elapsed:.0f}s ({elapsed / 60:.1f}m). Hard stop."
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
