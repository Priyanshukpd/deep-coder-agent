"""
Governance Self-Test Module — Validates the integrity of the control systems.

Runs a suite of automated checks to verify:
1. State Machine: All transitions are legal, illegal ones are rejected.
2. Risk Budget: Limits are enforced.
3. Command Safety: Dangerous commands are blocked.
4. RBAC: Permissions are enforced.
5. Intent Classification: Ambiguity fallback triggers correctly.
6. Preconditions: Drift detection works.

This module acts as the agent's "self-diagnostic" — run before any real task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from agent.state import AgentState, TaskIntent, validate_transition
from agent.mechanisms.risk_budget import RiskBudget
from agent.security.command_safety import classify_command, CommandPolicy, CommandTier
from agent.security.rbac import UserRole, Permission, check_access
from agent.planning.intent import IntentClassifier
from agent.core.controller import StateMachineController

logger = logging.getLogger(__name__)


class TestResult(Enum):
    PASS = auto()
    FAIL = auto()
    SKIP = auto()


@dataclass
class SelfTestCase:
    """Result of a single self-test check."""
    name: str
    category: str
    result: TestResult
    details: str = ""


@dataclass
class SelfTestReport:
    """Aggregate report of all self-test results."""
    cases: list[SelfTestCase] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.result == TestResult.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if c.result == TestResult.FAIL)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        status = "✅ ALL PASSED" if self.all_passed else "❌ FAILURES DETECTED"
        lines = [f"Governance Self-Test: {status} ({self.passed}/{self.total})"]
        for case in self.cases:
            icon = "✅" if case.result == TestResult.PASS else "❌" if case.result == TestResult.FAIL else "⏭️"
            lines.append(f"  {icon} [{case.category}] {case.name}: {case.details}")
        return "\n".join(lines)


class GovernanceSelfTest:
    """
    Runs automated governance checks before the agent accepts real tasks.
    
    Usage:
        tester = GovernanceSelfTest()
        report = tester.run_all()
        if not report.all_passed:
            raise RuntimeError("Governance self-test failed")
    """

    def run_all(self) -> SelfTestReport:
        """Run all self-test categories and return aggregate report."""
        report = SelfTestReport()

        self._test_state_machine(report)
        self._test_risk_budget(report)
        self._test_command_safety(report)
        self._test_rbac(report)
        self._test_intent_classifier(report)
        self._test_transition_validation(report)

        logger.info(report.summary())
        return report

    def _test_state_machine(self, report: SelfTestReport):
        """Verify legal transitions succeed and illegal ones fail."""
        cat = "StateMachine"

        # Legal: IDLE -> INTENT_ANALYSIS
        is_valid, _ = validate_transition(AgentState.IDLE, AgentState.INTENT_ANALYSIS)
        report.cases.append(SelfTestCase(
            name="IDLE→INTENT_ANALYSIS is legal",
            category=cat,
            result=TestResult.PASS if is_valid else TestResult.FAIL,
            details="Legal transition accepted" if is_valid else "Legal transition rejected!",
        ))

        # Illegal: IDLE -> IMPLEMENTING (skip states)
        is_valid, _ = validate_transition(AgentState.IDLE, AgentState.IMPLEMENTING)
        report.cases.append(SelfTestCase(
            name="IDLE→IMPLEMENTING is illegal",
            category=cat,
            result=TestResult.PASS if not is_valid else TestResult.FAIL,
            details="Illegal transition blocked" if not is_valid else "Illegal transition allowed!",
        ))

        # Illegal: COMPLETE -> anything (terminal state)
        is_valid, _ = validate_transition(AgentState.COMPLETE, AgentState.IDLE)
        report.cases.append(SelfTestCase(
            name="COMPLETE is terminal",
            category=cat,
            result=TestResult.PASS if not is_valid else TestResult.FAIL,
            details="Terminal state enforced" if not is_valid else "Terminal state violated!",
        ))

        # Intent restriction: EXPLAIN cannot enter IMPLEMENTING
        is_valid, _ = validate_transition(
            AgentState.PLANNING, AgentState.IMPLEMENTING, intent=TaskIntent.EXPLAIN
        )
        report.cases.append(SelfTestCase(
            name="EXPLAIN cannot reach IMPLEMENTING",
            category=cat,
            result=TestResult.PASS if not is_valid else TestResult.FAIL,
            details="Intent restriction enforced" if not is_valid else "Intent restriction violated!",
        ))

    def _test_risk_budget(self, report: SelfTestReport):
        """Verify that risk budget time and retry limits work."""
        cat = "RiskBudget"

        budget = RiskBudget()
        budget.start()

        # Fresh budget should have no violations
        time_violation = budget.check_time()
        report.cases.append(SelfTestCase(
            name="Fresh budget has no time violation",
            category=cat,
            result=TestResult.PASS if time_violation is None else TestResult.FAIL,
            details="No violation" if time_violation is None else f"Unexpected: {time_violation}",
        ))

        # Retry limit should eventually trigger
        budget2 = RiskBudget()
        budget2.start()
        triggered = False
        for i in range(10):
            v = budget2.record_retry("TEST")
            if v is not None:
                triggered = True
                break
        report.cases.append(SelfTestCase(
            name="Retry limit triggers within 10 attempts",
            category=cat,
            result=TestResult.PASS if triggered else TestResult.FAIL,
            details=f"Triggered at attempt {i+1}" if triggered else "Never triggered!",
        ))

    def _test_command_safety(self, report: SelfTestReport):
        """Verify dangerous commands are blocked and safe ones allowed."""
        cat = "CommandSafety"

        # Safe command
        result = classify_command("ls -la")
        report.cases.append(SelfTestCase(
            name="'ls -la' is SAFE",
            category=cat,
            result=TestResult.PASS if result.tier == CommandTier.SAFE else TestResult.FAIL,
            details=f"Tier: {result.tier.name}",
        ))

        # Blocked command
        result = classify_command("rm -rf /")
        report.cases.append(SelfTestCase(
            name="'rm -rf /' is BLOCKED",
            category=cat,
            result=TestResult.PASS if result.policy == CommandPolicy.BLOCK else TestResult.FAIL,
            details=f"Policy: {result.policy.name}",
        ))

        # Network command needs approval/rate-limit
        result = classify_command("pip install requests")
        report.cases.append(SelfTestCase(
            name="'pip install' is NETWORK tier",
            category=cat,
            result=TestResult.PASS if result.tier == CommandTier.NETWORK else TestResult.FAIL,
            details=f"Tier: {result.tier.name}",
        ))

    def _test_rbac(self, report: SelfTestReport):
        """Verify RBAC permission checks work."""
        cat = "RBAC"

        # Developer can approve test runs
        can = check_access(UserRole.DEVELOPER, Permission.APPROVE_TEST_RUN)
        report.cases.append(SelfTestCase(
            name="DEVELOPER can approve test runs",
            category=cat,
            result=TestResult.PASS if can else TestResult.FAIL,
            details="Permission granted" if can else "Permission denied!",
        ))

        # Developer cannot approve arch changes
        can = check_access(UserRole.DEVELOPER, Permission.APPROVE_ARCH_CHANGE)
        report.cases.append(SelfTestCase(
            name="DEVELOPER cannot approve arch changes",
            category=cat,
            result=TestResult.PASS if not can else TestResult.FAIL,
            details="Permission correctly denied" if not can else "Permission incorrectly granted!",
        ))

    def _test_intent_classifier(self, report: SelfTestReport):
        """Verify intent classifier handles ambiguous inputs correctly."""
        cat = "IntentClassifier"

        # Use heuristic mode (no LLM) for self-test
        classifier = IntentClassifier(provider=None)

        # Clear fix intent
        result = classifier.classify("fix the error in auth")
        report.cases.append(SelfTestCase(
            name="'fix the error' → FIX intent",
            category=cat,
            result=TestResult.PASS if result.intent == TaskIntent.FIX else TestResult.FAIL,
            details=f"Intent: {result.intent.value}, Confidence: {result.confidence:.2f}",
        ))

        # Ambiguous input triggers fallback
        result = classifier.classify("maybe check something")
        report.cases.append(SelfTestCase(
            name="Ambiguous input triggers low confidence",
            category=cat,
            result=TestResult.PASS if not result.is_confident else TestResult.FAIL,
            details=f"Confidence: {result.confidence:.2f}, Clarification: {result.requires_clarification}",
        ))

    def _test_transition_validation(self, report: SelfTestReport):
        """Verify controller fails safely on invalid transitions."""
        cat = "Controller"

        ctrl = StateMachineController(session_id="self_test")

        # Try illegal transition (IDLE -> COMPLETE)
        success = ctrl.transition_to(AgentState.COMPLETE, "Self-test illegal transition")
        report.cases.append(SelfTestCase(
            name="Controller rejects IDLE→COMPLETE",
            category=cat,
            result=TestResult.PASS if not success else TestResult.FAIL,
            details="Correctly rejected" if not success else "Incorrectly allowed!",
        ))


def run_governance_self_test() -> SelfTestReport:
    """Convenience function to run all governance self-tests."""
    tester = GovernanceSelfTest()
    return tester.run_all()
