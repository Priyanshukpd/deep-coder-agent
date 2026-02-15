"""
Simulation Runner — Dry-run the Phase 1 Control Systems.

Executes synthetic tasks to validate:
1. State Machine transitions
2. Risk Budget enforcement
3. Audit Logging
"""

import time
import sys
from agent.controller import StateMachineController
from agent.state import AgentState, TaskIntent
from agent.risk_budget import BudgetViolation


def run_happy_path_fix():
    print("\n--- [SIMULATION] Happy Path: Fix Bug ---")
    ctrl = StateMachineController(session_id="sim_fix_001")
    
    # 1. Start intent analysis
    ctrl.transition_to(AgentState.INTENT_ANALYSIS, "Received user request: 'Fix NPE in auth'")
    ctrl.set_intent(TaskIntent.FIX, 0.98, "Clear bug fix request")
    
    # 2. Discovery -> Planning
    ctrl.transition_to(AgentState.REPO_DISCOVERY, "Checking repo")
    ctrl.transition_to(AgentState.PLANNING, "Created task.md")
    
    # 3. Research -> Locate bug
    ctrl.transition_to(AgentState.RESEARCHING, "Reading logs")
    ctrl.transition_to(AgentState.IMPACT_ANALYSIS, "Checking reverse deps")
    
    # 4. Implement -> Verify
    ctrl.transition_to(AgentState.IMPLEMENTING, "Applied patch")
    ctrl.transition_to(AgentState.VERIFYING, "Running tests")
    
    # 5. Success
    ctrl.transition_to(AgentState.COMPLETE, "Tests passed")
    print("✅ Happy Path Fix completed successfully.")


def run_destructive_command_block():
    print("\n--- [SIMULATION] Safety Check: Destructive Command ---")
    from agent.command_safety import is_command_allowed, CommandTier
    
    cmd = "rm -rf /"
    allowed, classification = is_command_allowed(cmd)
    
    print(f"Command: {cmd}")
    print(f"Tier: {classification.tier.name}")
    print(f"Allowed: {allowed}")
    
    if not allowed and classification.is_explicitly_blocked:
        print("✅ Destructive command correctly BLOCKED.")
    else:
        print("❌ FAILED: Destructive command was allowed!")


def run_risk_budget_exhaustion():
    print("\n--- [SIMULATION] Risk Budget: Max Retries ---")
    ctrl = StateMachineController(session_id="sim_retry_fail")
    
    ctrl.transition_to(AgentState.INTENT_ANALYSIS, "Start")
    ctrl.set_intent(TaskIntent.FEATURE, 0.9, "New feature")
    ctrl.transition_to(AgentState.REPO_DISCOVERY, "Discovery")
    ctrl.transition_to(AgentState.PLANNING, "Plan")
    ctrl.transition_to(AgentState.IMPLEMENTING, "Impl 1")
    
    # Simulate retry loop
    for i in range(5):
        print(f"Retry attempt {i+1}...")
        ctrl.transition_to(AgentState.VERIFYING, "Verify failed")
        
        # This interaction mimics the loop: Verify -> Retry -> Implement -> Verify
        # But 'RETRYING' is the state that increments the counter
        if not ctrl.transition_to(AgentState.RETRYING,f"Retry #{i+1}"):
             print("✅ Budget exhausted correctly.")
             return

    print("❌ FAILED: Budget did not stop the loop!")


def run_ambiguous_intent_fallback():
    print("\n--- [SIMULATION] Safety Check: Ambiguity Fallback ---")
    ctrl = StateMachineController(session_id="sim_ambiguity_001")
    
    # 1. Start
    ctrl.transition_to(AgentState.INTENT_ANALYSIS, "Starting task")
    
    # 2. Provide vague input to trigger low confidence (< 0.75)
    vague_input = "maybe check the code?"
    print(f"User Input: '{vague_input}'")
    
    proceed = ctrl.analyze_user_intent(vague_input)
    
    if not proceed and ctrl.state == AgentState.FEEDBACK_WAIT:
        print("✅ Ambiguity Fallback triggered correctly (State -> FEEDBACK_WAIT).")
    else:
        print(f"❌ FAILED: Helper did not halt! State: {ctrl.state.name}, Proceed: {proceed}")


if __name__ == "__main__":
    run_happy_path_fix()
    run_destructive_command_block()
    run_risk_budget_exhaustion()
    run_ambiguous_intent_fallback()
