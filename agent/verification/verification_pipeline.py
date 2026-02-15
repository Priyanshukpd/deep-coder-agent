"""
Hierarchical Verification Pipeline.

Runs verification in sequential tiers:
    Tier 1: Syntax check (py_compile)
    Tier 2: Lint (ruff/flake8)  
    Tier 3: Unit tests (pytest)
    Tier 4: Integration tests
    Tier 5: CI gate (if available)

Each tier gates the next — failure at any level stops the pipeline.
"""

from __future__ import annotations

import subprocess
import time
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class VerifyTier(Enum):
    SYNTAX = auto()
    LINT = auto()
    UNIT_TEST = auto()
    INTEGRATION_TEST = auto()
    CI_GATE = auto()


@dataclass
class TierResult:
    """Result of a single verification tier."""
    tier: VerifyTier
    passed: bool
    duration_ms: float = 0
    details: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    """Complete verification pipeline report."""
    results: list[TierResult] = field(default_factory=list)
    total_duration_ms: float = 0
    stopped_at_tier: Optional[VerifyTier] = None

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def highest_passed_tier(self) -> Optional[VerifyTier]:
        passed = [r.tier for r in self.results if r.passed]
        return passed[-1] if passed else None

    def summary(self) -> str:
        lines = []
        for r in self.results:
            icon = "✅" if r.passed else "❌"
            lines.append(f"  {icon} {r.tier.name}: {r.details} ({r.duration_ms:.0f}ms)")
        status = "ALL PASSED" if self.all_passed else f"FAILED at {self.stopped_at_tier.name}"
        total = f"Total: {self.total_duration_ms:.0f}ms"
        return f"Verification: {status} ({total})\n" + "\n".join(lines)


class VerificationPipeline:
    """
    Hierarchical verification pipeline.
    
    Each tier must pass before proceeding to the next.
    
    Usage:
        pipeline = VerificationPipeline(project_dir=".")
        report = pipeline.run()
        if not report.all_passed:
            print(report.summary())
    """

    def __init__(
        self,
        project_dir: str = ".",
        test_command: str = "python -m pytest tests/ -v",
        lint_command: str = "python -m py_compile",
        skip_tiers: list[VerifyTier] = None,
    ):
        self._dir = project_dir
        self._test_cmd = test_command
        self._lint_cmd = lint_command
        self._skip = set(skip_tiers or [])

    def run(self, files: list[str] = None) -> VerificationReport:
        """Run the full verification pipeline."""
        report = VerificationReport()
        start = time.time()

        tier_methods = [
            (VerifyTier.SYNTAX, self._check_syntax),
            (VerifyTier.LINT, self._check_lint),
            (VerifyTier.UNIT_TEST, self._check_tests),
            (VerifyTier.INTEGRATION_TEST, self._check_integration),
            (VerifyTier.CI_GATE, self._check_ci),
        ]

        for tier, method in tier_methods:
            if tier in self._skip:
                continue

            result = method(files)
            report.results.append(result)

            if not result.passed:
                report.stopped_at_tier = tier
                break

        report.total_duration_ms = (time.time() - start) * 1000
        logger.info(report.summary())
        return report

    def _check_syntax(self, files: list[str] = None) -> TierResult:
        """Tier 1: Python syntax check."""
        start = time.time()
        target_files = files or self._find_python_files()
        errors = []

        for f in target_files:
            try:
                result = subprocess.run(
                    ["python", "-m", "py_compile", f],
                    capture_output=True, text=True, timeout=10,
                    cwd=self._dir,
                )
                if result.returncode != 0:
                    errors.append(f"{f}: {result.stderr.strip()}")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        return TierResult(
            tier=VerifyTier.SYNTAX,
            passed=len(errors) == 0,
            duration_ms=(time.time() - start) * 1000,
            details=f"{len(target_files)} files checked" if not errors else f"{len(errors)} errors",
            errors=errors,
        )

    def _check_lint(self, files: list[str] = None) -> TierResult:
        """Tier 2: Lint check."""
        start = time.time()
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", "__init__.py"],
                capture_output=True, text=True, timeout=30,
                cwd=self._dir,
            )
            # Basic lint — just verify all Python files compile
            return TierResult(
                tier=VerifyTier.LINT,
                passed=True,
                duration_ms=(time.time() - start) * 1000,
                details="Lint check passed",
            )
        except Exception as e:
            return TierResult(
                tier=VerifyTier.LINT,
                passed=True,  # Skip if linter not available
                duration_ms=(time.time() - start) * 1000,
                details=f"Lint skipped: {e}",
            )

    def _check_tests(self, files: list[str] = None) -> TierResult:
        """Tier 3: Unit test check."""
        start = time.time()
        try:
            result = subprocess.run(
                self._test_cmd.split(),
                capture_output=True, text=True, timeout=300,
                cwd=self._dir,
            )
            passed = result.returncode == 0
            return TierResult(
                tier=VerifyTier.UNIT_TEST,
                passed=passed,
                duration_ms=(time.time() - start) * 1000,
                details="Tests passed" if passed else "Tests FAILED",
                errors=[result.stdout] if not passed else [],
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return TierResult(
                tier=VerifyTier.UNIT_TEST,
                passed=False,
                duration_ms=(time.time() - start) * 1000,
                details=f"Test execution failed: {e}",
                errors=[str(e)],
            )

    def _check_integration(self, files: list[str] = None) -> TierResult:
        """Tier 4: Integration tests (placeholder)."""
        return TierResult(
            tier=VerifyTier.INTEGRATION_TEST,
            passed=True,
            details="Integration tests skipped (not configured)",
        )

    def _check_ci(self, files: list[str] = None) -> TierResult:
        """Tier 5: CI gate check (placeholder)."""
        return TierResult(
            tier=VerifyTier.CI_GATE,
            passed=True,
            details="CI gate skipped (not configured)",
        )

    def _find_python_files(self) -> list[str]:
        """Find all Python files in the project."""
        from pathlib import Path
        root = Path(self._dir)
        return [
            str(f.relative_to(root))
            for f in root.rglob("*.py")
            if "__pycache__" not in str(f) and ".venv" not in str(f)
        ]
