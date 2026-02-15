"""
Plan Envelope Validator — Scope Security for the Agent.

Architecture §2.F:
    - Immutable input snapshot hash
    - Lockfile hash for dependency freeze
    - Scope enforcement (reject files outside plan)
    - MAX_FILE_CAP enforcement
    - Clean working tree assertion
"""

from __future__ import annotations

import hashlib
import subprocess
import logging
import json
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


# Policy Constants
MAX_FILE_CAP = 50  # Configurable


class PlanEnvelopeError(Exception):
    """Base error for plan envelope violations."""
    pass


class ScopeTooLargeError(PlanEnvelopeError):
    """File count exceeds MAX_FILE_CAP."""
    pass


class PlanViolationError(PlanEnvelopeError):
    """Files created/deleted outside the plan."""
    pass


class DependencyFreezeError(PlanEnvelopeError):
    """Lockfile hash mismatch — dependencies changed."""
    pass


@dataclass(frozen=True)
class PlanEnvelope:
    """
    Immutable plan envelope — frozen after PLANNING state.
    
    Architecture §2.F.3: plan_envelope_hash is immutable.
    """
    plan_hash: str
    input_snapshot_hash: str
    lockfile_hash: str
    planned_files: tuple[str, ...]  # Immutable list
    max_file_cap: int = MAX_FILE_CAP
    toolchain_manifest: dict = field(default_factory=dict)

    @property
    def envelope_hash(self) -> str:
        """Combined hash of all envelope fields."""
        data = (
            f"plan={self.plan_hash}|"
            f"input={self.input_snapshot_hash}|"
            f"lockfile={self.lockfile_hash}|"
            f"files={','.join(sorted(self.planned_files))}"
        )
        return hashlib.sha256(data.encode()).hexdigest()


class PlanEnvelopeValidator:
    """
    Validates and enforces the Plan Envelope.
    
    Responsibilities:
        1. Compute input snapshot hash (Architecture §2.B)
        2. Compute lockfile hash for dependency freeze
        3. Enforce scope (MAX_FILE_CAP, no files outside plan)
        4. Verify immutability of the envelope
    """

    @staticmethod
    def compute_input_snapshot_hash(
        user_input: str,
        repo_map: str = "",
        base_tree_hash: str = "",
        toolchain_manifest: dict = None,
    ) -> str:
        """
        Architecture §2.B: sha256(user_input + repo_map + base_tree_hash + toolchain_manifest_hash)
        """
        manifest_str = json.dumps(toolchain_manifest or {}, sort_keys=True)
        combined = f"{user_input}|{repo_map}|{base_tree_hash}|{manifest_str}"
        return hashlib.sha256(combined.encode()).hexdigest()

    @staticmethod
    def compute_lockfile_hash(lockfile_paths: list[str] = None) -> str:
        """
        Compute hash of dependency lockfiles.
        
        Checks: requirements.txt, Pipfile.lock, package-lock.json, yarn.lock, poetry.lock
        """
        if lockfile_paths is None:
            lockfile_paths = [
                "requirements.txt",
                "Pipfile.lock",
                "package-lock.json",
                "yarn.lock",
                "poetry.lock",
                "pyproject.toml",
            ]

        hasher = hashlib.sha256()
        found_any = False

        for path in lockfile_paths:
            p = Path(path)
            if p.exists():
                hasher.update(p.read_bytes())
                found_any = True

        return hasher.hexdigest() if found_any else "no_lockfiles"

    @staticmethod
    def get_toolchain_manifest() -> dict:
        """
        Architecture §2.B.2: Log versions of Python, Node, Linter, CI Image.
        """
        manifest = {}

        for cmd, key in [
            (["python", "--version"], "python"),
            (["node", "--version"], "node"),
            (["npm", "--version"], "npm"),
            (["git", "--version"], "git"),
        ]:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                manifest[key] = result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                manifest[key] = "not_found"

        return manifest

    @staticmethod
    def assert_file_cap(file_count: int, max_cap: int = MAX_FILE_CAP):
        """
        Architecture §1 REPO_DISCOVERY: file_count > MAX_FILE_CAP → FAIL(ScopeTooLarge)
        """
        if file_count > max_cap:
            raise ScopeTooLargeError(
                f"Scope too large: {file_count} files > MAX_FILE_CAP ({max_cap}). "
                f"Require narrower prompt."
            )

    @staticmethod
    def validate_scope(
        changed_files: list[str],
        planned_files: list[str],
    ) -> list[str]:
        """
        Architecture §2.F.4: Reject created/deleted files outside plan.
        
        Returns list of violations (empty if all OK).
        """
        planned_set = set(planned_files)
        violations = []

        for f in changed_files:
            if f not in planned_set:
                violations.append(f"File outside plan scope: {f}")

        return violations

    @staticmethod
    def verify_lockfile(original_hash: str, lockfile_paths: list[str] = None) -> bool:
        """
        Architecture §2.F.4: Re-compute lockfile hash at VERIFYING.
        Returns True if match, False if dependency drift detected.
        """
        current_hash = PlanEnvelopeValidator.compute_lockfile_hash(lockfile_paths)
        if current_hash != original_hash:
            logger.critical(
                f"Dependency freeze violation! "
                f"Original: {original_hash[:16]}, Current: {current_hash[:16]}"
            )
            return False
        return True

    @staticmethod
    def create_envelope(
        user_input: str,
        planned_files: list[str],
        repo_map: str = "",
        base_tree_hash: str = "",
    ) -> PlanEnvelope:
        """Create a frozen plan envelope after PLANNING state."""
        toolchain = PlanEnvelopeValidator.get_toolchain_manifest()
        input_hash = PlanEnvelopeValidator.compute_input_snapshot_hash(
            user_input, repo_map, base_tree_hash, toolchain
        )
        lockfile_hash = PlanEnvelopeValidator.compute_lockfile_hash()
        plan_hash = hashlib.sha256(
            "|".join(sorted(planned_files)).encode()
        ).hexdigest()

        return PlanEnvelope(
            plan_hash=plan_hash,
            input_snapshot_hash=input_hash,
            lockfile_hash=lockfile_hash,
            planned_files=tuple(planned_files),
            toolchain_manifest=toolchain,
        )
