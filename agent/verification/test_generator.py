"""
Test Generation Workflow — Test-first approach for the agent.

Generates test stubs/scaffolds before implementation:
    1. Analyze the planned changes
    2. Generate test file scaffolds with assertions
    3. Register with TDD Gate for Red/Green tracking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TestSpec:
    """Specification for a generated test."""
    test_file: str
    sut_module: str          # System Under Test module
    test_class: str
    test_methods: list[str] = field(default_factory=list)
    description: str = ""


class TestGenerator:
    """
    Generates test file scaffolds for the agent's TDD workflow.
    
    Creates pytest-compatible test files with:
        - Proper imports of the SUT
        - Test class structure
        - Assertion placeholders
        - TDD gate registration hooks
    """

    @staticmethod
    def generate_test_scaffold(spec: TestSpec) -> str:
        """
        Generate a test file scaffold from a TestSpec.
        
        The generated test should FAIL initially (Red phase).
        """
        lines = [
            f'"""',
            f'Tests for {spec.sut_module}',
            f'',
            f'Auto-generated scaffold — tests should FAIL initially (Red phase).',
            f'"""',
            f'',
            f'import unittest',
            f'from {spec.sut_module} import *  # Import SUT',
            f'',
            f'',
            f'class {spec.test_class}(unittest.TestCase):',
            f'    """{spec.description}"""',
            f'',
        ]

        for method in spec.test_methods:
            lines.extend([
                f'    def {method}(self):',
                f'        """TODO: Implement this test."""',
                f'        # This should FAIL until the feature is implemented',
                f'        self.fail("Not implemented yet — Red phase")',
                f'',
            ])

        lines.extend([
            f'',
            f'if __name__ == "__main__":',
            f'    unittest.main()',
            f'',
        ])

        return "\n".join(lines)

    @staticmethod
    def specs_from_plan(
        planned_files: list[str],
        sut_prefix: str = "agent",
    ) -> list[TestSpec]:
        """
        Generate TestSpecs from a list of planned implementation files.
        
        Creates one test spec per planned source file.
        """
        specs = []

        for file_path in planned_files:
            if not file_path.endswith(".py"):
                continue
            if "test" in file_path:
                continue  # Don't generate tests for test files

            # Convert file path to module name
            module = file_path.replace("/", ".").replace("\\", ".").rstrip(".py")
            if module.endswith(".py"):
                module = module[:-3]

            # Generate test file name
            parts = file_path.rsplit("/", 1)
            if len(parts) == 2:
                test_file = f"tests/test_{parts[1]}"
            else:
                test_file = f"tests/test_{file_path}"

            # Generate class name
            base_name = parts[-1].replace(".py", "")
            class_name = f"Test{''.join(w.capitalize() for w in base_name.split('_'))}"

            spec = TestSpec(
                test_file=test_file,
                sut_module=module,
                test_class=class_name,
                test_methods=[
                    f"test_{base_name}_basic",
                    f"test_{base_name}_edge_cases",
                    f"test_{base_name}_error_handling",
                ],
                description=f"Tests for {module}",
            )
            specs.append(spec)

        return specs
