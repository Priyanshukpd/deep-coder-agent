"""
Rule Engine — Deterministic, sub-millisecond command safety.
"""

import re
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List

class RuleTier(Enum):
    SAFE = auto()
    NETWORK = auto()
    DESTRUCTIVE = auto()
    GIT_REWRITE = auto()
    EXFILTRATION = auto()
    BLOCKED = auto()
    UNKNOWN = auto()

@dataclass
class RuleResult:
    command: str
    tier: RuleTier
    matched_pattern: Optional[str] = None
    reason: str = ""
    is_blocked: bool = False

class RuleEngine:
    """
    High-speed regex engine for command classification.
    Runs locally and deterministically to prevent exfiltration and destruction.
    """

    def __init__(self):
        # Explicitly blocked patterns (Instant Kill)
        self._blocked_patterns = [
            # Exfiltration
            re.compile(r"\b(pbcopy|xclip|xsel)\b"),
            re.compile(r"\bcurl\s+.*-d\s+"),
            re.compile(r"\bcurl\s+.*--data\b"),
            re.compile(r"\b(nc|netcat)\s+"),
            re.compile(r"bash\s+-i\s+>\s*&?\s*/dev/tcp/"),
            re.compile(r"(?:env|printenv)\s*\|\s*(?:nc|curl|wget)"),
            
            # Root Destruction
            re.compile(r"\brm\s+-rf\s+(/|~|\$HOME|\.\.\s|/\s)"),
            re.compile(r"\bmkfs\b"),
            re.compile(r"\bdd\s+if=/dev/"),
            re.compile(r"\btruncate\s+-s\s+0\s+/"),
            
            # Shell Escapes / Fork Bombs
            re.compile(r":(){ :\|:& };:"),
            re.compile(r"\bsudo\b"),
            re.compile(r"\bchown\b"),
        ]

        # Tiered patterns
        self._tiers = [
            (re.compile(r"\bgit\s+(reset|push\s+.*--force|clean|rebase|filter-branch)"), RuleTier.GIT_REWRITE),
            (re.compile(r"\b(rm\s|rmdir|shred|truncate|dd\s)"), RuleTier.DESTRUCTIVE),
            (re.compile(r"\b(curl|wget|npm\s+install|pip\s+install|yarn\s+add|apt|brew|docker\s+(pull|push))"), RuleTier.NETWORK),
            (re.compile(r"\b(cat|ls|grep|rg|git\s+(status|log|diff|add|commit|branch|init|config|checkout))\b"), RuleTier.SAFE),
            (re.compile(r"\b(mkdir|touch|cp|pwd|whoami|id|hostname|uname|date|env|printenv)\b"), RuleTier.SAFE),
            (re.compile(r"\b(npm\s+(test|run|start|build)|pytest|jest|python|node|go|cargo|make|pip)\b"), RuleTier.SAFE),
        ]

    def check(self, command: str, repo_path: str = None) -> RuleResult:
        """
        Scan a command against deterministic safety rules.
        """
        stripped = command.strip()

        # 1. Check explicit blocks
        for pattern in self._blocked_patterns:
            if pattern.search(stripped):
                return RuleResult(
                    command=stripped,
                    tier=RuleTier.BLOCKED,
                    matched_pattern=pattern.pattern,
                    reason=f"Globally blocked dangerous pattern: {pattern.pattern}",
                    is_blocked=True
                )

        # 2. State-Aware Guardrail: rm in /tmp is fine
        if re.search(r"\brm\s+", stripped):
            # If repo_path is provided, we can allow deletions inside /tmp or specific agent folders
            if repo_path and ("/tmp/" in stripped or ".agent_log" in stripped):
                # We still classify as DESTRUCTIVE logically but maybe not blocked
                pass

        # 3. Classify by tier
        for pattern, tier in self._tiers:
            if pattern.search(stripped):
                return RuleResult(
                    command=stripped,
                    tier=tier,
                    matched_pattern=pattern.pattern,
                    reason=f"Matched {tier.name} pattern",
                    is_blocked=(tier == RuleTier.BLOCKED)
                )

        return RuleResult(
            command=stripped,
            tier=RuleTier.UNKNOWN,
            reason="Unrecognized command pattern",
            is_blocked=False # Let LLM reason if unknown? Or block by default?
        )

# Singleton
rule_engine = RuleEngine()
