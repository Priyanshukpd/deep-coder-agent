"""
Command Policy Config — Project-specific command allowlists/blocklists.

Inspired by Blackbox CLI's coreTools/excludeTools model.

Reads .agent/config.json from the project repo. If not present, defaults
to permissive mode (all non-catastrophic commands allowed).

Example .agent/config.json:
{
  "command_policy": {
    "allow_prefixes": ["git", "python", "node", "docker", "npm"],
    "block_prefixes": ["rm -rf", "sudo"],
    "require_approval_prefixes": ["git push", "docker push"]
  }
}

If allow_prefixes is provided, it acts as a strict allowlist — only commands
matching those prefixes are permitted (after the catastrophic block check).

If allow_prefixes is absent, all commands are permissive (the RuleEngine
catastrophic checks still apply).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CommandPolicyConfig:
    """Loaded configuration for a specific project repo."""
    allow_prefixes: list[str] = field(default_factory=list)   # allowlist (empty = permissive)
    block_prefixes: list[str] = field(default_factory=list)   # project-level instant block
    require_approval_prefixes: list[str] = field(default_factory=list)  # prompt user

    @property
    def is_permissive(self) -> bool:
        """True if no allowlist is defined — all safe commands pass."""
        return len(self.allow_prefixes) == 0


def load_policy(repo_path: str) -> CommandPolicyConfig:
    """
    Load command policy from <repo_path>/.agent/config.json.
    Falls back to permissive defaults if file doesn't exist or has no policy.
    """
    config_path = os.path.join(repo_path, ".agent", "config.json")
    if not os.path.exists(config_path):
        return CommandPolicyConfig()

    try:
        with open(config_path, "r") as f:
            data = json.load(f)
        policy_data = data.get("command_policy", {})
        return CommandPolicyConfig(
            allow_prefixes=policy_data.get("allow_prefixes", []),
            block_prefixes=policy_data.get("block_prefixes", []),
            require_approval_prefixes=policy_data.get("require_approval_prefixes", []),
        )
    except (json.JSONDecodeError, IOError):
        # Malformed config — fall back to permissive to not break the agent
        return CommandPolicyConfig()


def _matches_any_prefix(command: str, prefixes: list[str]) -> Optional[str]:
    """Return the first matching prefix, or None."""
    cmd = command.strip().lower()
    for prefix in prefixes:
        if cmd.startswith(prefix.lower()):
            return prefix
    return None


class CommandPolicyEnforcer:
    """
    Applies project-level command policy on top of the RuleEngine's
    catastrophic checks.

    Policy evaluation order (matches Blackbox CLI's logic):
    1. block_prefixes (project blocklist) — takes priority over everything
    2. require_approval_prefixes — prompt user
    3. allow_prefixes — if defined and command doesn't match, block
    4. Default: ALLOW (permissive)
    """

    def __init__(self, repo_path: str):
        self._config = load_policy(repo_path)

    def evaluate(self, command: str) -> tuple[str, Optional[str]]:
        """
        Returns (decision, reason) where decision is one of:
          'allow', 'block', 'require_approval'
        """
        cfg = self._config

        # 1. Project-level block prefixes
        matched = _matches_any_prefix(command, cfg.block_prefixes)
        if matched:
            return "block", f"Blocked by project policy prefix: '{matched}'"

        # 2. Require-approval prefixes
        matched = _matches_any_prefix(command, cfg.require_approval_prefixes)
        if matched:
            return "require_approval", f"Requires approval per project policy: '{matched}'"

        # 3. Allowlist (if defined)
        if not cfg.is_permissive:
            matched = _matches_any_prefix(command, cfg.allow_prefixes)
            if matched:
                return "allow", f"Allowed by project policy prefix: '{matched}'"
            return "block", f"Command not in project allowlist. Allowed prefixes: {cfg.allow_prefixes}"

        # 4. Permissive default
        return "allow", "No project policy restrictions — permissive default"
