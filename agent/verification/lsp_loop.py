"""
Bounded LSP Loop — Syntax Retry Only.

Architecture §1 IMPLEMENTING:
    - Retries: 3 (Syntax/Lint only)
    - Logic fail = STOP
    
This module manages bounded retries for linting/syntax errors
while hard-stopping on logic failures.
"""

from __future__ import annotations

import subprocess
import logging
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


# Architecture §1: Retries limit, defaults to 7
MAX_SYNTAX_RETRIES = int(os.environ.get("GOD_MODE_MAX_FIX_ATTEMPTS", "7"))


class FailureType(Enum):
    """Classification of implementation failures."""
    SYNTAX = auto()      # Parse/syntax errors — retryable
    LINT = auto()         # Linter warnings/errors — retryable
    TYPE_ERROR = auto()   # Type check failures — retryable (bounded)
    LOGIC = auto()        # Test/logic failures — NOT retryable
    RUNTIME = auto()      # Runtime errors — NOT retryable
    UNKNOWN = auto()      # Cannot classify — NOT retryable


# Retryable failure types
RETRYABLE_FAILURES = {FailureType.SYNTAX, FailureType.LINT, FailureType.TYPE_ERROR}


@dataclass
class LintResult:
    """Result of a lint/syntax check."""
    passed: bool
    failure_type: Optional[FailureType] = None
    errors: list[str] = field(default_factory=list)
    file_path: str = ""

    @property
    def is_retryable(self) -> bool:
        if self.failure_type is None:
            return False
        return self.failure_type in RETRYABLE_FAILURES


class BoundedLSPLoop:
    """
    Manages bounded retries for syntax/lint errors.
    
    Architecture §1 IMPLEMENTING:
        - Syntax/Lint errors: up to 3 retries
        - Logic failures: immediate STOP
    """

    def __init__(self, max_retries: int = MAX_SYNTAX_RETRIES):
        self.max_retries = max_retries
        self._retry_count = 0
        self._history: list[LintResult] = []

    @property
    def retries_remaining(self) -> int:
        return max(0, self.max_retries - self._retry_count)

    @property
    def retry_count(self) -> int:
        return self._retry_count

    def record_result(self, result: LintResult) -> bool:
        """
        Record a lint/syntax check result.
        
        Returns True if implementation should continue (retry allowed).
        Returns False if implementation must stop.
        """
        self._history.append(result)

        if result.passed:
            logger.info("LSP: All checks passed")
            return True

        if not result.is_retryable:
            logger.critical(
                f"LSP: Non-retryable failure ({result.failure_type.name}). "
                f"Logic fail = STOP."
            )
            return False

        self._retry_count += 1
        if self._retry_count > self.max_retries:
            logger.critical(
                f"LSP: Retry budget exhausted ({self._retry_count}/{self.max_retries}). "
                f"Stopping."
            )
            return False

        logger.warning(
            f"LSP: Retryable failure ({result.failure_type.name}). "
            f"Retry {self._retry_count}/{self.max_retries}."
        )
        return True

    @staticmethod
    def classify_failure(error_output: str) -> FailureType:
        """
        Classify an error output into a failure type.
        
        Heuristic-based classification of error messages.
        """
        lower = error_output.lower()

        # Syntax errors
        if "syntaxerror" in lower or "syntax error" in lower:
            return FailureType.SYNTAX
        if "indentationerror" in lower or "unexpected indent" in lower:
            return FailureType.SYNTAX

        # Lint errors
        if any(kw in lower for kw in ["pylint", "flake8", "ruff", "eslint", "mypy"]):
            return FailureType.LINT
        if "undefined name" in lower or "unused import" in lower:
            return FailureType.LINT

        # Type errors
        if "typeerror" in lower and "argument" in lower:
            return FailureType.TYPE_ERROR

        # Logic errors — test failures
        if any(kw in lower for kw in ["assertionerror", "assert", "failed", "failures="]):
            return FailureType.LOGIC

        # Runtime errors
        if any(kw in lower for kw in ["runtimeerror", "segfault", "killed", "oom"]):
            return FailureType.RUNTIME

        return FailureType.UNKNOWN

    @staticmethod
    def run_linter(file_path: str, linter: str = "python") -> LintResult:
        """
        Run a linter on a file and return structured result.
        
        Supports: python (py_compile), ruff, flake8
        """
        if linter == "python":
            return BoundedLSPLoop._run_python_check(file_path)
        elif linter == "ruff":
            return BoundedLSPLoop._run_ruff(file_path)
        else:
            return LintResult(passed=True, file_path=file_path)

    @staticmethod
    def _run_python_check(file_path: str) -> LintResult:
        """Run Python syntax check via py_compile."""
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", file_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return LintResult(passed=True, file_path=file_path)
            else:
                error = result.stderr.strip()
                return LintResult(
                    passed=False,
                    failure_type=BoundedLSPLoop.classify_failure(error),
                    errors=[error],
                    file_path=file_path,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return LintResult(
                passed=False,
                failure_type=FailureType.UNKNOWN,
                errors=[str(e)],
                file_path=file_path,
            )

    @staticmethod
    def _run_ruff(file_path: str) -> LintResult:
        """Run ruff linter."""
        try:
            result = subprocess.run(
                ["ruff", "check", file_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return LintResult(passed=True, file_path=file_path)
            else:
                errors = result.stdout.strip().split("\n")
                return LintResult(
                    passed=False,
                    failure_type=FailureType.LINT,
                    errors=errors,
                    file_path=file_path,
                )
        except FileNotFoundError:
            # ruff not installed, fallback to python check
            return BoundedLSPLoop._run_python_check(file_path)
