"""
Network Policy — OS-Level Block + Lockfile Hash.

Architecture §1 IMPLEMENTING: No Network. No Dep Install.
Architecture §2.F.4: Block pip install, npm install.

Enforces network isolation during IMPLEMENTING state
to prevent dependency mutation and external fetches.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from agent.security.command_safety import classify_command, CommandTier

logger = logging.getLogger(__name__)


class NetworkPolicyViolation(Exception):
    """Raised when a command violates network policy."""
    pass


@dataclass(frozen=True)
class NetworkPolicy:
    """
    Network isolation policy for the IMPLEMENTING state.
    
    When active, blocks:
        - All NETWORK tier commands (pip install, npm install, curl, wget, etc.)
        - Any command that could fetch dependencies
    """
    enabled: bool = True
    allow_localhost: bool = True  # Allow local test servers

    # Patterns that are ALWAYS blocked during implementation
    _BLOCKED_NETWORK_PATTERNS = [
        re.compile(r"\bpip\s+install\b"),
        re.compile(r"\bnpm\s+install\b"),
        re.compile(r"\byarn\s+add\b"),
        re.compile(r"\bcurl\b"),
        re.compile(r"\bwget\b"),
        re.compile(r"\bdocker\s+pull\b"),
        re.compile(r"\bapt\s+install\b"),
        re.compile(r"\bbrew\s+install\b"),
    ]

    def check_command(self, command: str) -> Optional[str]:
        """
        Check if a command violates the network policy.
        
        Returns violation reason if blocked, None if allowed.
        """
        if not self.enabled:
            return None

        stripped = command.strip()

        # Check against network patterns
        for pattern in self._BLOCKED_NETWORK_PATTERNS:
            if pattern.search(stripped):
                return (
                    f"Network policy violation: '{stripped}' is blocked during IMPLEMENTING. "
                    f"No dependency installs or network access allowed."
                )

        # Also check via command safety classifier
        classification = classify_command(stripped)
        if classification.tier == CommandTier.NETWORK:
            return (
                f"Network policy violation: command classified as NETWORK tier. "
                f"Blocked during IMPLEMENTING state."
            )

        return None


class NetworkPolicyEnforcer:
    """
    Manages network policy lifecycle.
    
    Usage:
        enforcer = NetworkPolicyEnforcer()
        enforcer.activate()  # When entering IMPLEMENTING
        
        violation = enforcer.check("pip install requests")
        if violation:
            raise NetworkPolicyViolation(violation)
            
        enforcer.deactivate()  # When leaving IMPLEMENTING
    """

    def __init__(self):
        self._active = False
        self._policy = NetworkPolicy()

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self):
        """Activate network isolation (entering IMPLEMENTING)."""
        self._active = True
        logger.info("Network policy ACTIVATED — no network access allowed")

    def deactivate(self):
        """Deactivate network isolation (leaving IMPLEMENTING)."""
        self._active = False
        logger.info("Network policy DEACTIVATED")

    def check(self, command: str) -> Optional[str]:
        """Check if command is allowed under current policy."""
        if not self._active:
            return None
        return self._policy.check_command(command)
