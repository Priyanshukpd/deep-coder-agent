"""
Secrets Policy — Redaction & Injection Prevention.

Detects and redacts secrets/credentials in:
    - Agent outputs (before logging)
    - Code diffs (before committing)
    - LLM prompts (before sending)

Prevents accidental leakage of API keys, passwords, tokens, etc.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class SecretLeakError(Exception):
    """Raised when a potential secret leak is detected."""
    pass


@dataclass
class SecretMatch:
    """A detected secret pattern match."""
    pattern_name: str
    matched_text: str
    redacted_text: str
    line_number: int = 0
    severity: str = "HIGH"  # HIGH, MEDIUM, LOW


# Secret detection patterns
SECRET_PATTERNS = [
    # API Keys
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key", re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"]?([A-Za-z0-9/+=]{40})")),
    ("GitHub Token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("GitHub OAuth", re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("Slack Token", re.compile(r"xox[baprs]-[A-Za-z0-9-]+")),
    
    # Generic patterns
    ("Generic API Key", re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{20,})")),
    ("Generic Secret", re.compile(r"(?i)(secret|password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{8,})")),
    ("Generic Token", re.compile(r"(?i)(token|auth_token|access_token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{20,})")),
    
    # Private Keys
    ("Private Key", re.compile(r"-----BEGIN (RSA |EC |DSA )?(PRIVATE KEY|OPENSSH PRIVATE KEY)-----")),
    
    # Database URLs
    ("Database URL", re.compile(r"(?i)(postgres|mysql|mongodb)://[^\s'\"]{10,}")),
    
    # .env file patterns
    ("Env Variable", re.compile(r"(?i)(TOGETHER_API_KEY|OPENAI_API_KEY|DATABASE_URL)\s*=\s*['\"]?([^\s'\"]{8,})")),
]

REDACTION_PLACEHOLDER = "***REDACTED***"


class SecretsPolicy:
    """
    Detects and redacts secrets in text content.
    
    Usage:
        policy = SecretsPolicy()
        
        # Scan for secrets
        matches = policy.scan("my_api_key = 'ghp_abc123...'")
        
        # Redact secrets
        clean_text = policy.redact("password = 'hunter2'")
    """

    def __init__(
        self,
        patterns: list = None,
        strict: bool = True,
    ):
        self._patterns = patterns or SECRET_PATTERNS
        self.strict = strict

    def scan(self, content: str) -> list[SecretMatch]:
        """
        Scan content for potential secrets.
        
        Returns list of matches.
        """
        matches = []

        for line_num, line in enumerate(content.split("\n"), 1):
            for name, pattern in self._patterns:
                for match in pattern.finditer(line):
                    matched = match.group()
                    redacted = self._redact_match(matched)
                    matches.append(SecretMatch(
                        pattern_name=name,
                        matched_text=matched[:20] + "...",  # Truncate for safety
                        redacted_text=redacted,
                        line_number=line_num,
                    ))

        if matches:
            logger.warning(f"Found {len(matches)} potential secrets!")

        return matches

    def redact(self, content: str) -> str:
        """
        Redact all detected secrets from content.
        
        Returns content with secrets replaced by REDACTED placeholder.
        """
        result = content

        for name, pattern in self._patterns:
            result = pattern.sub(REDACTION_PLACEHOLDER, result)

        return result

    def assert_no_secrets(self, content: str, context: str = ""):
        """
        Assert that content contains no secrets.
        
        Raises SecretLeakError if secrets are found.
        """
        matches = self.scan(content)
        if matches and self.strict:
            details = ", ".join(m.pattern_name for m in matches)
            raise SecretLeakError(
                f"Secret leak detected in {context or 'content'}: {details}. "
                f"Found {len(matches)} potential secrets."
            )
        return matches

    @staticmethod
    def _redact_match(text: str) -> str:
        """Create a redacted version preserving length indication."""
        if len(text) <= 8:
            return REDACTION_PLACEHOLDER
        return text[:4] + REDACTION_PLACEHOLDER + text[-2:]

    @staticmethod
    def is_env_file(file_path: str) -> bool:
        """Check if a file is likely an environment/secrets file."""
        dangerous_names = {
            ".env", ".env.local", ".env.production", ".env.development",
            "credentials", "secrets.yml", "secrets.yaml",
            "id_rsa", "id_ed25519", ".pem", ".key",
        }
        lower = file_path.lower()
        return any(lower.endswith(name) for name in dangerous_names)
