"""
Command Safety Layer — Classify and gate shell commands.

Every command must be classified before execution.
Destructive and unknown commands require human approval.
"""

from __future__ import annotations

import re
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional


class CommandTier(Enum):
    """Safety classification for shell commands."""

    SAFE = auto()              # npm test, pytest, cat, ls, grep
    NETWORK = auto()           # curl, npm install, pip install
    FILE_DESTRUCTIVE = auto()  # rm, mv, truncate, chmod
    GIT_REWRITE = auto()       # git reset, git push --force, git clean
    UNKNOWN = auto()           # anything not recognized


class CommandPolicy(Enum):
    """What to do with commands at each tier."""

    ALLOW = auto()             # auto-execute
    RATE_LIMIT = auto()        # execute with rate limit
    REQUIRE_APPROVAL = auto()  # show to user, wait for approval
    BLOCK = auto()             # reject entirely


# -- Policy mapping --
TIER_POLICY: dict[CommandTier, CommandPolicy] = {
    CommandTier.SAFE: CommandPolicy.ALLOW,
    CommandTier.NETWORK: CommandPolicy.RATE_LIMIT,
    CommandTier.FILE_DESTRUCTIVE: CommandPolicy.REQUIRE_APPROVAL,
    CommandTier.GIT_REWRITE: CommandPolicy.REQUIRE_APPROVAL,
    CommandTier.UNKNOWN: CommandPolicy.BLOCK,
}


# -- Pattern-based classification --
# Order matters: first match wins. More specific patterns first.

_PATTERNS: list[tuple[re.Pattern, CommandTier]] = [
    # Git destructive (must check before generic git)
    (re.compile(r"\bgit\s+(reset|push\s+.*--force|clean|rebase|filter-branch)"), CommandTier.GIT_REWRITE),

    # File destructive
    (re.compile(r"\b(rm\s|rmdir|shred|truncate|dd\s|mkfs)"), CommandTier.FILE_DESTRUCTIVE),
    (re.compile(r"\bmv\s.*(?:\s|/)\.\./"), CommandTier.FILE_DESTRUCTIVE),  # mv to parent
    (re.compile(r"\bchmod\s+0?0?0\b"), CommandTier.FILE_DESTRUCTIVE),      # chmod 000

    # Network
    (re.compile(r"\b(curl|wget|npm\s+install|pip\s+install|yarn\s+add|apt\s+install|brew\s+install)"), CommandTier.NETWORK),
    (re.compile(r"\b(docker\s+pull|docker\s+push)"), CommandTier.NETWORK),

    # Safe: read-only or test commands
    (re.compile(r"\b(cat|head|tail|less|more|wc|file|stat)\b"), CommandTier.SAFE),
    (re.compile(r"\b(ls|find|tree|du|df)\b"), CommandTier.SAFE),
    (re.compile(r"\b(grep|rg|ag|ack|sed\s+-n)\b"), CommandTier.SAFE),
    (re.compile(r"\b(echo|printf|true|false|test)\b"), CommandTier.SAFE),
    (re.compile(r"\b(pwd|whoami|uname|date|env|printenv)\b"), CommandTier.SAFE),
    (re.compile(r"\b(git\s+(status|log|diff|show|branch|stash\s+list))"), CommandTier.SAFE),
    (re.compile(r"\b(git\s+(add|commit|stash\s+(save|push|pop)))"), CommandTier.SAFE),
    (re.compile(r"\b(npm\s+(test|run|start|build)|npx)\b"), CommandTier.SAFE),
    (re.compile(r"\b(pytest|python\s+-m\s+pytest|jest|mocha|mvn\s+(test|compile))\b"), CommandTier.SAFE),
    (re.compile(r"\b(python|node|java|javac|tsc|eslint|pylint|flake8|black|mypy)\b"), CommandTier.SAFE),
    (re.compile(r"\b(cargo\s+(test|build|check|clippy))\b"), CommandTier.SAFE),
    (re.compile(r"\b(make|cmake)\b"), CommandTier.SAFE),
    (re.compile(r"\b(mkdir|touch|cp)\b"), CommandTier.SAFE),
]

# Explicitly blocked patterns (even if they match a safe pattern somehow)
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+-rf\s+(/|~|\$HOME|\.\.)"),   # rm -rf / or ~ or ..
    re.compile(r"\b>\s*/dev/sd"),                      # writing to raw devices
    re.compile(r"\bsudo\s+rm"),                        # sudo rm anything
    re.compile(r":(){ :\|:& };:"),                     # fork bomb
]


@dataclass
class CommandClassification:
    """Result of classifying a shell command."""

    command: str
    tier: CommandTier
    policy: CommandPolicy
    matched_pattern: Optional[str] = None
    is_explicitly_blocked: bool = False
    reasoning: str = ""


from agent.security.rule_engine import rule_engine, RuleTier
from agent.security.governance import get_governance_manager

def classify_command(command: str, repo_path: str = ".") -> CommandClassification:
    """
    Classify a shell command into a safety tier using RuleEngine.
    """
    res = rule_engine.check(command)
    governance = get_governance_manager(repo_path)
    
    # Map RuleTier to CommandTier
    mapping = {
        RuleTier.SAFE: CommandTier.SAFE,
        RuleTier.NETWORK: CommandTier.NETWORK,
        RuleTier.DESTRUCTIVE: CommandTier.FILE_DESTRUCTIVE,
        RuleTier.GIT_REWRITE: CommandTier.GIT_REWRITE,
        RuleTier.BLOCKED: CommandTier.UNKNOWN,
        RuleTier.EXFILTRATION: CommandTier.UNKNOWN,
    }
    
    tier = mapping.get(res.tier, CommandTier.UNKNOWN)
    policy = CommandPolicy.ALLOW if res.tier == RuleTier.SAFE else CommandPolicy.BLOCK if res.is_blocked else TIER_POLICY.get(tier, CommandPolicy.REQUIRE_APPROVAL)

    # Phase 68: Session-Aware Governance Override
    if policy == CommandPolicy.REQUIRE_APPROVAL and governance.is_approved(command):
        policy = CommandPolicy.ALLOW

    return CommandClassification(
        command=command,
        tier=tier,
        policy=policy,
        matched_pattern=res.matched_pattern,
        is_explicitly_blocked=res.is_blocked,
        reasoning=res.reason,
    )


def is_command_allowed(command: str) -> tuple[bool, CommandClassification]:
    """ Quick check. """
    result = classify_command(command)
    auto_allowed = result.policy in {CommandPolicy.ALLOW, CommandPolicy.RATE_LIMIT}
    return auto_allowed, result
