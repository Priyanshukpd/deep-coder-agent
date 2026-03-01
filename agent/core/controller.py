"""
State Machine Controller — The brain that drives the agent.

Coordinates the AgentState, RiskBudget, and DecisionLogger.
This is the entry point for the "Control & Safety" layer.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional, Any

from agent.state import AgentState, TaskIntent, StateSnapshot, validate_transition, AgentContext
from agent.mechanisms.risk_budget import RiskBudget
from agent.mechanisms.decision_logger import DecisionLogger
from agent.security.preconditions import PreconditionChecker


class StateMachineController:
    """
    Governs the agent's lifecycle.
    Ensures no illegal transitions occur and risk budgets are respected.
    """

    def __init__(self, session_id: str = "default_session"):
        self.session_id = session_id
        self.state = AgentState.IDLE
        self.intent: Optional[TaskIntent] = None
        
        # Subsystems
        self.risk_budget = RiskBudget()
        self.logger = DecisionLogger(session_id=session_id)
        self.context = AgentContext()  # New: Holds execution guarantees

        
        self.risk_budget.start()
        self.logger.logger.info(f"Agent initialized in {self.state.name}")

    def transition_to(self, target_state: AgentState, reason: str, metadata: dict = None) -> bool:
        """
        Attempt to transition to a new state.
        
        1. Validates transition rules.
        2. Checks risk budget.
        3. [NEW] Runs Precondition Checks (Anti-Drift).
        4. Logs the transition.
        5. Updates state.
        
        Returns True if successful, False (and moves to FAILED) if rejected.
        """
        if metadata is None:
            metadata = {}

        # 1. State Rule Check
        is_valid, err_msg = validate_transition(self.state, target_state, self.intent)
        if not is_valid:
            self.logger.logger.error(f"Transition Validation Failed: {err_msg}")
            self._fail_safe(f"Illegal transition attempted: {err_msg}")
            return False

        # -- Precondition Hooks --
        
        # A. Start of Task Logic (IDLE -> INTENT_ANALYSIS)
        if self.state == AgentState.IDLE and target_state == AgentState.INTENT_ANALYSIS:
            self.context.clear()
            self.context.initial_git_head = PreconditionChecker.get_git_head()
            self.logger.logger.info(f"Captured initial Git HEAD: {self.context.initial_git_head}")

        # B. Anti-Drift Gate (Any -> IMPLEMENTING)
        if target_state == AgentState.IMPLEMENTING:
            if not self._run_precondition_checks():
                return False

        # 2. Risk Budget Check
        # (Check time budget on every transition)
        time_violation = self.risk_budget.check_time()
        if time_violation:
            self.logger.log_risk_violation(time_violation)
            self._fail_safe("Execution time limit exceeded")
            return False

        # Special budget checks for specific states
        if target_state == AgentState.RETRYING:
            retry_violation = self.risk_budget.record_retry(self.state.name)
            if retry_violation:
                self.logger.log_risk_violation(retry_violation)
                self._fail_safe("Retry limit exceeded")
                return False

        # 3. Log & Update
        snapshot = StateSnapshot(
            from_state=self.state,
            to_state=target_state,
            reason=reason,
            metadata=metadata
        )
        self.logger.log_transition(snapshot)
        self.state = target_state
        
        return True

    def _run_precondition_checks(self) -> bool:
        """
        Verifies that the environment has not drifted from the initial state.
        Returns True if safe to proceed, False if drift detected.
        """
        violations = []
        
        # 1. Check Git Consistency
        if self.context.initial_git_head:
            git_violation = PreconditionChecker.check_git_consistency(self.context.initial_git_head)
            if git_violation:
                violations.append(git_violation)
                
        # 2. Check File Consistency (if we have planned files)
        if self.context.initial_file_checksums:
            file_violations = PreconditionChecker.check_file_consistency(self.context.initial_file_checksums)
            violations.extend(file_violations)
            
        if violations:
            violation_msg = "; ".join([v.details for v in violations])
            self.logger.logger.critical(f"PRECONDITION VIOLATION: {violation_msg}")
            
            # Fail-safe transition
            self._fail_safe(f"Drift Detected: {violation_msg}")
            return False
            
        return True

    def set_intent(self, intent: TaskIntent, confidence: float, reasoning: str):
        """Set the task intent (must happen in INTENT_ANALYSIS state)."""
        if self.state != AgentState.INTENT_ANALYSIS:
            self.logger.logger.warning("Attempted to set intent outside INTENT_ANALYSIS state")
            return

        self.intent = intent
        self.logger.log_intent(intent.value, confidence, reasoning)

    def analyze_user_intent(self, user_input: str) -> bool:
        """
        Analyzes user input to determine intent.
        Handles the 'Ambiguity Fallback' logic.
        
        Returns True if intent is successfully set and we can proceed.
        Returns False if we need to stop for Feedback (Ambiguity).
        """
        from agent.planning.intent import IntentClassifier
        from agent.config import AgentConfig
        
        # Build provider if API key is available
        provider = None
        config = AgentConfig()
        if config.has_api_key:
            from agent.core.llm_provider import TogetherProvider
            provider = TogetherProvider(config)
            self.logger.logger.info("Using LLM-powered intent classification")
        else:
            self.logger.logger.info("No API key — using keyword heuristic classification")
        
        classifier = IntentClassifier(provider=provider)
        result = classifier.classify(user_input)
        
        # Log the raw classification
        self.logger.log_intent(result.intent.value, result.confidence, result.reasoning)
        
        # -- AMBIGUITY CHECK --
        if not result.is_confident:
            self.logger.logger.warning(
                f"Intent confidence {result.confidence:.2f} < {result.CONFIDENCE_THRESHOLD}. "
                f"Ambiguity Fallback Triggered. Proceeding with best guess."
            )
            # Proceed with the best guess instead of pausing
            self.intent = result.intent
            return True

        # If confident, set intent and proceed
        self.intent = result.intent
        return True

    def _fail_safe(self, reason: str):
        """Emergency transition to FAILED state."""
        self.state = AgentState.FAILED
        self.logger.logger.critical(f"FAIL-SAFE TRIGGERED: {reason}")
        # We manually log this transition because normal transition_to might have failed
        self.logger._write("FAIL_SAFE", {"reason": reason})

    def run_dry_run(self, intent: TaskIntent, sequence: list[AgentState]):
        """
        Simulate a task execution for testing/validation.
        """
        self.logger.logger.info(f"Starting DRY RUN for intent: {intent.name}")
        
        # 1. Start
        if not self.transition_to(AgentState.INTENT_ANALYSIS, "Starting task"):
            return
            
        # 2. Set Intent
        self.set_intent(intent, 0.95, "Simulated dry run")
        
        # 3. Running through sequence
        for next_state in sequence:
            # Simulate work
            time.sleep(0.1)
            if not self.transition_to(next_state, "Simulating step"):
                break
                
        self.logger.logger.info("Dry run complete.")
