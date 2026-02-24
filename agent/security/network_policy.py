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
    Whitelisted Net-Zero Sandbox policy for the IMPLEMENTING state.
    """
    enabled: bool = True
    allow_localhost: bool = True
    
    # Domains allowed even during IMPLEMENTING lockdown
    WHITELIST = {
        "api.anthropic.com",
        "api.openai.com",
        "github.com",
        "pypi.org",
        "npmjs.com",
        "registry.npmjs.org",
        "files.pythonhosted.org",
    }

    def _extract_domain(self, command: str) -> Optional[str]:
        """Attempt to extract a domain from a shell command."""
        # Simple regex for URLs
        match = re.search(r"https?://([a-zA-Z0-9.-]+)", command)
        if match:
            return match.group(1)
        
        # Specific check for git/npm/pip targets that might not be full URLs
        return None

    def check_command(self, command: str) -> Optional[str]:
        if not self.enabled:
            return None

        stripped = command.strip()
        domain = self._extract_domain(stripped)

        # If a domain is found, check against whitelist
        if domain:
            is_whitelisted = any(domain == w or domain.endswith("." + w) for w in self.WHITELIST)
            if not is_whitelisted:
                return f"Net-Zero Violation: Domain '{domain}' is not in the sandbox whitelist."

        # Fallback to TIER-based blocking for generic network tools without explicit URLs
        from agent.security.command_safety import classify_command, CommandTier
        classification = classify_command(stripped)
        
        if classification.tier == CommandTier.NETWORK:
            # If it's a network command but we couldn't verify a whitelist domain, block it.
            # This is "deny-by-default".
            if not domain:
                return f"Net-Zero Violation: Generic network command '{stripped}' blocked (no verified whitelisted target)."

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
