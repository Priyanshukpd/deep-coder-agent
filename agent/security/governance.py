"""
Governance Manager — Prevent consent fatigue by remembering session-level approvals.
Part of Phase 68.
"""

import os
import json
import logging
import hashlib
from typing import Set

logger = logging.getLogger(__name__)

GOVERNANCE_FILE = ".agent/session_warnings.json"

class GovernanceManager:
    """
    Tracks and persists command hashes that have been approved by the user.
    """

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.governance_file = os.path.join(repo_path, GOVERNANCE_FILE)
        self._approved_hashes: Set[str] = self._load_approvals()

    def _load_approvals(self) -> Set[str]:
        """Load approved hashes from disk."""
        if os.path.exists(self.governance_file):
            try:
                with open(self.governance_file, 'r', encoding='utf-8') as f:
                    return set(json.load(f))
            except Exception as e:
                logger.error(f"Failed to load session governance: {e}")
        return set()

    def _save_approvals(self):
        """Save approved hashes to disk."""
        try:
            os.makedirs(os.path.dirname(self.governance_file), exist_ok=True)
            with open(self.governance_file, 'w', encoding='utf-8') as f:
                json.dump(list(self._approved_hashes), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save session governance: {e}")

    def _get_hash(self, command: str) -> str:
        """Generate a stable hash for a command."""
        # Normalize command: strip whitespace and common variations
        normalized = command.strip()
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    def is_approved(self, command: str) -> bool:
        """Check if this specific command has already been approved in this session."""
        h = self._get_hash(command)
        return h in self._approved_hashes

    def approve(self, command: str):
        """Record a user approval for a command."""
        h = self._get_hash(command)
        if h not in self._approved_hashes:
            self._approved_hashes.add(h)
            self._save_approvals()
            logger.info(f"Command approval recorded: {command[:50]}...")

# Singleton-ready instance helper
_governance_instance = None

def get_governance_manager(repo_path: str = ".") -> GovernanceManager:
    global _governance_instance
    if _governance_instance is None:
        _governance_instance = GovernanceManager(repo_path)
    return _governance_instance
