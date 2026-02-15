"""
Anti-Mocking Validator — Detects Tautological Tests.

Ensures that tests are meaningful by detecting:
    - Tests that mock the SUT (System Under Test) itself
    - Tests that assert on mock return values (tautologies)
    - Tests with no real assertions
    - Tests that don't import the actual module under test
"""

from __future__ import annotations

import ast
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class MockingViolation(Exception):
    """Raised when a tautological test is detected."""
    pass


@dataclass
class MockingAnalysis:
    """Result of anti-mocking analysis on a test file."""
    test_file: str
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    imports_sut: bool = False
    has_real_assertions: bool = False
    mock_count: int = 0
    assert_count: int = 0

    @property
    def is_valid(self) -> bool:
        return len(self.violations) == 0


class AntiMockingValidator:
    """
    Validates that test files are not tautological.
    
    Checks:
        1. Test imports the SUT (system under test)
        2. Test has real assertions (not just mock.assert_called)
        3. Test doesn't mock the SUT itself
        4. Not all assertions are on mock return values
    """

    @staticmethod
    def analyze(test_content: str, sut_module: str, test_file: str = "<unknown>") -> MockingAnalysis:
        """
        Analyze a test file for tautological patterns.
        
        Args:
            test_content: The source code of the test file
            sut_module: The module being tested (e.g., "agent.intent")
            test_file: Path to the test file for reporting
        """
        analysis = MockingAnalysis(test_file=test_file)

        lines = test_content.split("\n")

        # Check 1: Does the test import the SUT?
        sut_parts = sut_module.split(".")
        sut_import_patterns = [
            f"from {sut_module}",
            f"import {sut_module}",
            f"from {'.'.join(sut_parts[:-1])}",
        ]
        analysis.imports_sut = any(
            any(pattern in line for pattern in sut_import_patterns)
            for line in lines
        )
        if not analysis.imports_sut:
            analysis.violations.append(
                f"Test does not import SUT module '{sut_module}'. "
                f"Must test actual code, not mocks."
            )

        # Check 2: Count real assertions vs mock assertions
        assert_pattern = re.compile(r"\bself\.assert\w+\(|assert\s+")
        mock_assert_pattern = re.compile(r"\.assert_called|\.assert_any_call|\.assert_not_called")
        mock_pattern = re.compile(r"\bMock\(|MagicMock\(|patch\(|@patch")

        for line in lines:
            stripped = line.strip()
            if assert_pattern.search(stripped):
                analysis.assert_count += 1
            if mock_assert_pattern.search(stripped):
                analysis.mock_count += 1
                analysis.warnings.append(
                    f"Mock assertion found: {stripped[:80]}"
                )
            if mock_pattern.search(stripped):
                analysis.mock_count += 1

        analysis.has_real_assertions = analysis.assert_count > 0

        if not analysis.has_real_assertions:
            analysis.violations.append(
                "Test has no real assertions. Every test must assert something."
            )

        # Check 3: All assertions are mock-only
        if analysis.assert_count > 0 and analysis.assert_count == analysis.mock_count:
            analysis.warnings.append(
                "All assertions are mock assertions — test may be tautological."
            )

        # Check 4: Detect mocking the SUT itself
        sut_mock_pattern = re.compile(
            rf"@patch\(['\"]({re.escape(sut_module)})\.[^'\"]+['\"]\)"
        )
        for line in lines:
            if sut_mock_pattern.search(line.strip()):
                analysis.violations.append(
                    f"Test mocks the SUT itself ({sut_module}). "
                    f"Mock dependencies, not the thing being tested."
                )
                break

        return analysis

    @staticmethod
    def validate(test_content: str, sut_module: str, test_file: str = "<unknown>"):
        """
        Validate and raise if test is tautological.
        
        Raises MockingViolation if critical violations found.
        """
        analysis = AntiMockingValidator.analyze(test_content, sut_module, test_file)

        if not analysis.is_valid:
            violations_str = "\n  - ".join(analysis.violations)
            raise MockingViolation(
                f"Anti-mocking violations in {test_file}:\n  - {violations_str}"
            )

        for warning in analysis.warnings:
            logger.warning(f"[AntiMocking] {test_file}: {warning}")

        return analysis
