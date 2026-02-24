"""
ReAct Orchestrator — The "Brain" that manages the Thought-Action-Observation loop.
Decouples high-level reasoning from low-level tool execution.
"""

import logging
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class ReActStep:
    thought: str
    action: str
    action_input: Dict[str, Any]
    observation: str = ""

class ReActOrchestrator:
    """
    Drives the agent using a ReAct (Reason + Act) loop.
    
    Tools provided to the ReAct agent:
    - search_code: Search for patterns/files
    - read_file: Read file content
    - write_file: Write or patch a file
    - run_command: Execute shell commands
    - analyze_impact: Perform impact analysis
    - finish: Mark the task as complete
    """

    def __init__(self, provider, executor):
        self._provider = provider
        self._executor = executor
        self._history: List[ReActStep] = []
        self._max_steps = 15

    def orchestrate(self, task: str, intent: str, full_history: list = None) -> bool:
        """
        Run the ReAct loop until 'finish' or max steps reached.
        """
        self._history = []
        self._full_history = full_history or []
        print(f"\n🚀 Starting autonomous ReAct loop for task: {task[:100]}...")
        
        for step_num in range(1, self._max_steps + 1):
            print(f"\n🧠 Turn {step_num}/{self._max_steps}")
            
            # 1. Think & Act
            thought_action = self._decide_next_step(task, intent)
            if not thought_action:
                logger.error("Failed to decide next step.")
                return False
                
            step = ReActStep(
                thought=thought_action.get("thought", ""),
                action=thought_action.get("action", ""),
                action_input=thought_action.get("action_input", {})
            )
            
            print(f"  🤔 Thought: {step.thought}")
            print(f"  🛠️  Action: {step.action}({json.dumps(step.action_input)})")
            
            if step.action == "finish":
                # Phase 69: Transcript Audit (Optimized)
                # Decision: Skip audit for trivial tasks (<= 2 steps) to reduce latency
                if len(self._history) <= 2:
                    print(f"  🏁 Task complete (Trivial task - skipping audit)")
                    return True
                
                from agent.verification.transcript_auditor import TranscriptAuditor
                auditor = TranscriptAuditor(self._provider)
                # If we don't have full_history, use the orchestrator's own history
                messages_to_audit = self._full_history if self._full_history else [{"role": "user", "content": task}]
                audit_res = auditor.audit(messages_to_audit, task_summary=step.thought)
                
                if audit_res.get("pass"):
                    print(f"  🏁 Task complete and AUDIT PASSED!")
                    return True
                else:
                    violations = ", ".join(audit_res.get("violations", []))
                    print(f"  ❌ AUDIT FAILED: {violations}")
                    observation = f"AUDIT FAILED: The following constraints were violated: {violations}. Please fix them before finishing."
                    step.observation = observation
                    self._history.append(step)
                    continue
                
            # 2. Execute Action & Observe
            observation = self._execute_action(step.action, step.action_input)
            step.observation = observation
            
            print(f"  👁️  Observation: {observation[:200]}..." if len(observation) > 200 else f"  👁️  Observation: {observation}")
            
            # 3. Record History
            self._history.append(step)
            
        print(f"  ⚠️  Max steps ({self._max_steps}) reached without completion.")
        return False

    def _decide_next_step(self, task: str, intent: str) -> Optional[Dict[str, Any]]:
        """Ask the LLM for the next thought and action."""
        system_prompt = self._build_system_prompt(intent)
        user_content = f"Task: {task}\n\n"
        
        if self._history:
            user_content += "History of steps:\n"
            for i, h in enumerate(self._history, 1):
                user_content += f"Step {i}:\n- Thought: {h.thought}\n- Action: {h.action}({json.dumps(h.action_input)})\n- Observation: {h.observation}\n"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        try:
            # We use tool-calling schema for structured output
            result = self._provider.complete_with_tools(messages, tools=[REACT_STEP_TOOL])
            if result and result.function_name == "decide_step":
                return result.arguments
            return None
        except Exception as e:
            logger.error(f"Error in decide_next_step: {e}")
            return None

    def _execute_action(self, action: str, action_input: Dict[str, Any]) -> str:
        """Delegates the action to the TaskExecutor."""
        try:
            if action == "search_code":
                query = action_input.get("query", "")
                # Use grep-like search if possible, or fallback to file list
                return self._executor._read_smart_context(query)
                
            elif action == "read_file":
                path = action_input.get("path")
                full_path = os.path.join(self._executor._repo_path, path)
                if os.path.exists(full_path):
                    with open(full_path, 'r', errors='replace') as f:
                        return f"--- {path} ---\n{f.read()}"
                return f"Error: File {path} not found."
                
            elif action == "ls":
                path = action_input.get("path", ".")
                full_path = os.path.join(self._executor._repo_path, path)
                if os.path.isdir(full_path):
                    items = os.listdir(full_path)
                    return f"Directory listing for {path}:\n" + "\n".join(items)
                return f"Error: {path} is not a directory."
                
            elif action == "write_file":
                path = action_input.get("path")
                content = action_input.get("content", "")
                description = action_input.get("description", "ReAct loop edit")
                
                # Use TaskExecutor's write/patch logic
                from agent.core.task_executor import FileAction
                fa = FileAction(path=path, action="modify", description=description)
                
                # Check if it's a diff or full code
                if content.startswith("@@") or "@@ -" in content:
                    success = self._executor._apply_surgical_patch(fa, content)
                    if success:
                         return f"Successfully patched {path}"
                    return f"Failed to apply patch to {path}. Try sending full content."
                else:
                    self._executor._write_file(fa, content)
                    return f"Successfully wrote full content to {path}"

            elif action == "run_command":
                cmd = action_input.get("command")
                res = self._executor.run_code(cmd)
                return f"Exit Code: {res.return_code}\nOutput:\n{res.stdout}\n{res.stderr}"
                
            elif action == "spawn_subagent":
                persona = action_input.get("persona")
                sub_task = action_input.get("task")
                print(f"  🤖 Spawning {persona} for task: {sub_task}...")
                
                # In PM mode, we can recursive call execute or a simplified specialist loop
                # For now, we'll perform the specialist mission using the TaskExecutor's logic
                # but with the specific persona-based system prompt.
                res = self._executor.execute(sub_task, intent=persona.lower())
                return f"Subagent {persona} finished. Result: {res.summary}. Success: {res.is_complete}"
                
            else:
                return f"Error: Unknown action {action}"
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return f"Error executing {action}: {str(e)}"

    def _build_system_prompt(self, intent: str) -> str:
        """Hydrated system prompt with ReAct logic and Skill Registry."""
        from agent.core.skill_registry import skill_registry
        
        file_list = []
        if self._executor._knowledge_graph:
            file_list = self._executor._knowledge_graph.get_all_files()
            
        hydrated_docs = skill_registry.get_hydrated_docs(file_list)
        
        return f"""You are a ReAct-based autonomous software engineer. 
Your goal is to complete the user's task by reasoning and taking steps.

Current Intent Context: {intent}

Available Tools:
- search_code(query): Search the codebase for symbols or text.
- ls(path): List files in a directory.
- read_file(path): Read the full content of a file.
- write_file(path, content, description): Create or update a file.
- run_command(command): Execute a shell command in the project root.
- spawn_subagent(persona, task): Delegate a sub-task to a specialist (e.g. Explorer, Architect, Implementer, Verifier).
- finish(): Call this when you have verified your work and the task is complete.

Guidelines:
1. Always state your Thought before choosing an Action.
2. Use observations from previous steps to inform your next decision.
3. If a command fails, analyze the error and try a different approach.
4. If you need more information, use search_code or read_file.
5. Be surgical. Only modify what is necessary.

Response Format:
You must call the 'decide_step' function with your thought, action, and action_input.

{hydrated_docs}
"""

REACT_STEP_TOOL = {
    "type": "function",
    "function": {
        "name": "decide_step",
        "description": "Decide the next thought and action in the ReAct loop.",
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Explain your reasoning for the next step."
                },
                "action": {
                    "type": "string",
                    "enum": ["search_code", "ls", "read_file", "write_file", "run_command", "spawn_subagent", "finish"],
                    "description": "The action to take."
                },
                "action_input": {
                    "type": "object",
                    "description": "Parameters for the action."
                }
            },
            "required": ["thought", "action", "action_input"]
        }
    }
}
