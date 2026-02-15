"""
Ultra-Strict Express Lane — Diff Guard for Safe Fast-Path.

Architecture §1 INTENT_ANALYSIS:
    - Express Lane Guard: No shebang/chmod changes
    - Docs-only changes can skip TDD/PROVING_GROUND
    
Architecture §3 flow:
    - INTENT_ANALYSIS → [STRICT DOCS CHECK] → (Docs? IMPLEMENTING)

This module gates which changes qualify for the express lane
(bypassing TDD and full verification for documentation-only edits).
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class ExpressLaneViolation(Exception):
    """Raised when a change doesn't qualify for express lane."""
    pass


@dataclass
class DiffAnalysis:
    """Analysis of a diff for express lane eligibility."""
    is_docs_only: bool = True
    has_executable_changes: bool = False
    has_shebang_changes: bool = False
    has_chmod_changes: bool = False
    has_code_changes: bool = False
    changed_files: list[str] = None
    violations: list[str] = None

    def __post_init__(self):
        if self.changed_files is None:
            self.changed_files = []
        if self.violations is None:
            self.violations = []

    @property
    def qualifies_for_express(self) -> bool:
        return (
            self.is_docs_only
            and not self.has_executable_changes
            and not self.has_shebang_changes
            and not self.has_chmod_changes
            and not self.has_code_changes
            and len(self.violations) == 0
        )


# File extensions that qualify as documentation
DOCS_EXTENSIONS = {
    ".md", ".txt", ".rst", ".adoc", ".asciidoc",
    ".mdx", ".wiki", ".textile",
}

# File names that qualify as documentation
DOCS_FILES = {
    "README", "LICENSE", "CHANGELOG", "CONTRIBUTING",
    "AUTHORS", "HISTORY", "FAQ", "SECURITY", "CODE_OF_CONDUCT",
    ".gitignore", ".editorconfig", ".prettierrc",
}

# File extensions that are NEVER allowed in express lane
BLOCKED_EXTENSIONS = {
    ".sh", ".bash", ".zsh", ".fish",   # Scripts
    ".ps1", ".bat", ".cmd",             # Windows scripts
    ".exe", ".bin", ".so", ".dylib",    # Binaries
}


class ExpressLane:
    """
    Gates which changes qualify for the express documentation lane.
    
    Express lane bypasses TDD/PROVING_GROUND for docs-only edits.
    """

    @staticmethod
    def analyze_files(changed_files: list[str]) -> DiffAnalysis:
        """Analyze a list of changed files for express lane eligibility."""
        analysis = DiffAnalysis(changed_files=changed_files)

        for filepath in changed_files:
            lower = filepath.lower()

            # Check extension
            ext = ""
            if "." in filepath:
                ext = "." + filepath.rsplit(".", 1)[1].lower()

            # Check if blocked
            if ext in BLOCKED_EXTENSIONS:
                analysis.has_executable_changes = True
                analysis.is_docs_only = False
                analysis.violations.append(f"Blocked extension: {filepath}")

            # Check if docs
            elif ext in DOCS_EXTENSIONS:
                continue  # Docs file — OK for express

            # Check if known docs file name
            elif any(filepath.upper().endswith(name) for name in DOCS_FILES):
                continue

            # Everything else is "code"
            else:
                analysis.has_code_changes = True
                analysis.is_docs_only = False
                analysis.violations.append(f"Code file change: {filepath}")

        return analysis

    @staticmethod
    def analyze_diff_content(diff_text: str) -> DiffAnalysis:
        """
        Analyze diff content for shebang/chmod changes.
        
        Architecture §1: Express Lane Guard: No shebang/chmod changes.
        """
        analysis = DiffAnalysis()

        lines = diff_text.split("\n")
        for line in lines:
            stripped = line.strip()

            # Check for shebang changes
            if stripped.startswith("+#!") or stripped.startswith("-#!"):
                analysis.has_shebang_changes = True
                analysis.is_docs_only = False
                analysis.violations.append(f"Shebang change: {stripped[:60]}")

            # Check for chmod/permission changes
            if "old mode" in stripped or "new mode" in stripped:
                analysis.has_chmod_changes = True
                analysis.is_docs_only = False
                analysis.violations.append(f"Permission change: {stripped[:60]}")

            # Check for executable bit
            if re.search(r"chmod\s+\+x", stripped):
                analysis.has_chmod_changes = True
                analysis.is_docs_only = False
                analysis.violations.append(f"Executable bit change: {stripped[:60]}")

        return analysis

    @staticmethod
    def check(
        changed_files: list[str],
        diff_text: str = "",
    ) -> DiffAnalysis:
        """
        Full express lane check combining file analysis and diff analysis.
        
        Returns DiffAnalysis with qualifies_for_express property.
        """
        file_analysis = ExpressLane.analyze_files(changed_files)
        
        if diff_text:
            diff_analysis = ExpressLane.analyze_diff_content(diff_text)
            # Merge results
            file_analysis.has_shebang_changes = diff_analysis.has_shebang_changes
            file_analysis.has_chmod_changes = diff_analysis.has_chmod_changes
            file_analysis.violations.extend(diff_analysis.violations)
            if not diff_analysis.is_docs_only:
                file_analysis.is_docs_only = False

        if file_analysis.qualifies_for_express:
            logger.info("Express Lane: Change qualifies for fast path (docs-only)")
        else:
            logger.info(f"Express Lane: Denied — {len(file_analysis.violations)} violations")

        return file_analysis
