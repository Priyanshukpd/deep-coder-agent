import os
import time
import subprocess
from agent.controller import StateMachineController
from agent.state import AgentState, TaskIntent

def run_simulation():
    print(">>> Starting Drift Simulation <<<")
    
    # 1. Initialize Controller
    controller = StateMachineController(session_id="drift_sim")
    
    # 2. Start Task (Captures Initial State)
    if not controller.transition_to(AgentState.INTENT_ANALYSIS, "Starting Task"):
        print("ERROR: Failed to start task")
        return

    # Simulate Intent Analysis
    controller.set_intent(TaskIntent.FIX, 0.9, "Fixing a bug")
    controller.transition_to(AgentState.REPO_DISCOVERY, "Analyzing repo")
    
    # 3. Define Plan (Add a dummy file to monitor)
    dummy_file = "drift_test_file.txt"
    with open(dummy_file, "w") as f:
        f.write("Original Content")
    
    # Add to context manually for simulation (normally done by Planner)
    from agent.preconditions import PreconditionChecker
    controller.context.planned_files.append(os.path.abspath(dummy_file))
    checksum = PreconditionChecker.get_file_checksum(dummy_file)
    controller.context.initial_file_checksums[os.path.abspath(dummy_file)] = checksum
    print(f"Captured Checksum: {checksum}")
    
    controller.transition_to(AgentState.PLANNING, "Planning fix")
    
    # 4. SIMULATE DRIFT (Modify file externally)
    print(">>> SIMULATING EXTERNAL DRIFT <<<")
    with open(dummy_file, "w") as f:
        f.write("Modified Content by Evil User")
    
    # 5. Attempt Execution
    print(">>> Attempting to transition to IMPLEMENTING... <<<")
    result = controller.transition_to(AgentState.IMPLEMENTING, "Starting implementation")
    
    if not result:
        print("SUCCESS: Transition Rejected due to Drift!")
    else:
        print("FAILURE: Transition Allowed despite Drift!")
        
    # Cleanup
    if os.path.exists(dummy_file):
        os.remove(dummy_file)

if __name__ == "__main__":
    run_simulation()
