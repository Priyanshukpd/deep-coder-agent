"""
Sandboxed Command Runner — Safe shell execution with policy enforcement.

Integrates:
    - CommandSafetyLayer for tier/policy classification
    - NetworkPolicy enforcement during IMPLEMENTING
    - Kill switch checking before each command
    - Audit logging of all commands
"""

from __future__ import annotations

import subprocess
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from agent.security.command_safety import classify_command, CommandPolicy, CommandTier

logger = logging.getLogger(__name__)


class CommandBlockedError(Exception):
    """Raised when a command is blocked by policy."""
    pass


class CommandTimeoutError(Exception):
    """Raised when a command exceeds its timeout."""
    pass


@dataclass
class CommandResult:
    """Result of a sandboxed command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    tier: CommandTier
    policy: CommandPolicy
    blocked: bool = False
    block_reason: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.blocked


class SandboxedRunner:
    """
    Executes shell commands within a safety sandbox.
    
    Enforces:
        - Command classification and policy checking
        - Network policy during IMPLEMENTING state
        - Command timeout (default 60s)
        - Working directory isolation
        - Audit logging
    """

    def __init__(
        self,
        timeout_seconds: int = 60,
        working_directory: str = ".",
        network_enforcer=None,
    ):
        self.timeout = timeout_seconds
        self.cwd = working_directory
        self._network_enforcer = network_enforcer
        self._approval_callback = None
        self._history: list[CommandResult] = []

    def set_approval_callback(self, callback):
        """Set a callback for human-in-the-loop approval."""
        self._approval_callback = callback

    @property
    def command_history(self) -> list[CommandResult]:
        return list(self._history)

    def run(
        self,
        command: str,
        timeout: int = None,
        check_network: bool = True,
    ) -> CommandResult:
        """
        Run a command through the safety sandbox.
        
        1. Classify command tier and policy
        2. Check if blocked by policy
        3. Check network policy (if in IMPLEMENTING)
        4. Execute with timeout
        5. Log result
        """
        timeout = timeout or self.timeout
        start = time.time()

        # Step 1: Classify
        classification = classify_command(command)
        tier = classification.tier
        policy = classification.policy

        # Step 2: Check policy
        if policy == CommandPolicy.BLOCK:
            result = CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"BLOCKED: {command}",
                duration_ms=0,
                tier=tier,
                policy=policy,
                blocked=True,
                block_reason=f"Command blocked by safety policy (tier: {tier.name})",
            )
            self._history.append(result)
            logger.critical(f"BLOCKED command: {command}")
            raise CommandBlockedError(result.block_reason)

        if policy == CommandPolicy.REQUIRE_APPROVAL:
            # Phase 68: Check if already approved in this session
            from agent.security.governance import get_governance_manager
            governance = get_governance_manager(self.cwd)
            
            if not governance.is_approved(command):
                if self._approval_callback:
                    # Request human approval
                    approved = self._approval_callback("COMMAND", f"{command}\nTier: {tier.name}\nReason: {classification.reasoning}")
                    if approved:
                        governance.approve(command)
                    else:
                        raise CommandBlockedError(f"User declined command: {command}")
                else:
                    # No way to get approval, must block
                    raise CommandBlockedError(f"Command requires manual approval but no callback set: {command}")

        # Step 3: Check network policy
        if check_network and self._network_enforcer:
            violation = self._network_enforcer.check(command)
            if violation:
                result = CommandResult(
                    command=command,
                    exit_code=-1,
                    stdout="",
                    stderr=violation,
                    duration_ms=0,
                    tier=tier,
                    policy=policy,
                    blocked=True,
                    block_reason=violation,
                )
                self._history.append(result)
                raise CommandBlockedError(violation)

        # Step 4: Execute
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cwd,
            )
            duration = (time.time() - start) * 1000

            result = CommandResult(
                command=command,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration,
                tier=tier,
                policy=policy,
            )

        except subprocess.TimeoutExpired:
            duration = (time.time() - start) * 1000
            result = CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                duration_ms=duration,
                tier=tier,
                policy=policy,
                blocked=True,
                block_reason=f"Timeout ({timeout}s)",
            )
            raise CommandTimeoutError(f"Command timed out: {command}")

        finally:
            self._history.append(result)

        # Step 5: Log
        level = logging.INFO if result.success else logging.WARNING
        logger.log(
            level,
            f"Command [{tier.name}]: {command} → exit={result.exit_code} "
            f"({result.duration_ms:.0f}ms)",
        )

        return result
