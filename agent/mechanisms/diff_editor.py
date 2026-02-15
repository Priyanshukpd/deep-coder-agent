"""
Diff-First Editing — Patch → Apply → Rollback.

Implements a diff-first editing strategy where:
    1. Changes are expressed as diffs/patches
    2. Patches are applied atomically
    3. Failed patches can be rolled back
    
This prevents partial file corruption from interrupted edits.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PatchError(Exception):
    """Raised when a patch operation fails."""
    pass


@dataclass
class FilePatch:
    """A single file patch operation."""
    file_path: str
    original_content: str       # Backup for rollback
    new_content: str             # Content after patch
    original_hash: str = ""
    applied: bool = False

    def __post_init__(self):
        self.original_hash = hashlib.sha256(
            self.original_content.encode()
        ).hexdigest()

    @property
    def new_hash(self) -> str:
        return hashlib.sha256(self.new_content.encode()).hexdigest()


@dataclass
class PatchSet:
    """A set of file patches to apply atomically."""
    patches: list[FilePatch] = field(default_factory=list)
    description: str = ""
    applied: bool = False

    @property
    def file_count(self) -> int:
        return len(self.patches)


class DiffEditor:
    """
    Applies file changes as atomic patch sets.
    
    Workflow:
        1. Create a PatchSet with all intended changes
        2. Apply the PatchSet atomically
        3. If any patch fails, rollback all changes
    """

    def __init__(self):
        self._applied_sets: list[PatchSet] = []

    def create_patch(
        self,
        file_path: str,
        new_content: str,
    ) -> FilePatch:
        """
        Create a file patch by reading current content and storing new.
        
        The original content is kept for rollback.
        """
        path = Path(file_path)
        original = ""
        if path.exists():
            original = path.read_text()

        return FilePatch(
            file_path=file_path,
            original_content=original,
            new_content=new_content,
        )

    def apply_patch_set(self, patch_set: PatchSet) -> bool:
        """
        Apply a set of patches atomically.
        
        If any patch fails, all patches are rolled back.
        Returns True if all patches applied successfully.
        """
        applied_patches: list[FilePatch] = []

        try:
            for patch in patch_set.patches:
                self._apply_single(patch)
                applied_patches.append(patch)

            patch_set.applied = True
            self._applied_sets.append(patch_set)
            logger.info(f"PatchSet applied: {patch_set.file_count} files ({patch_set.description})")
            return True

        except Exception as e:
            logger.error(f"Patch failed: {e}. Rolling back {len(applied_patches)} files.")
            # Rollback all applied patches in reverse order
            for patch in reversed(applied_patches):
                self._rollback_single(patch)
            return False

    def rollback_last(self) -> bool:
        """Rollback the most recent patch set."""
        if not self._applied_sets:
            logger.warning("No patch sets to rollback")
            return False

        patch_set = self._applied_sets.pop()
        for patch in reversed(patch_set.patches):
            self._rollback_single(patch)

        patch_set.applied = False
        logger.info(f"Rolled back: {patch_set.file_count} files ({patch_set.description})")
        return True

    def _apply_single(self, patch: FilePatch):
        """Apply a single file patch."""
        path = Path(patch.file_path)

        # Verify original hasn't changed since patch creation
        if path.exists():
            current = path.read_text()
            current_hash = hashlib.sha256(current.encode()).hexdigest()
            if current_hash != patch.original_hash:
                raise PatchError(
                    f"File drift detected: {patch.file_path} changed since patch creation. "
                    f"Expected: {patch.original_hash[:12]}, Got: {current_hash[:12]}"
                )

        # Create parent directories if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write new content
        path.write_text(patch.new_content)
        patch.applied = True

    def _rollback_single(self, patch: FilePatch):
        """Rollback a single file patch."""
        path = Path(patch.file_path)
        try:
            if patch.original_content:
                path.write_text(patch.original_content)
            elif path.exists():
                path.unlink()  # File didn't exist before, remove it
            patch.applied = False
            logger.info(f"Rolled back: {patch.file_path}")
        except Exception as e:
            logger.error(f"Rollback failed for {patch.file_path}: {e}")

    @staticmethod
    def generate_unified_diff(
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> str:
        """Generate a unified diff string for display/logging."""
        import difflib
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
        return "".join(diff)
