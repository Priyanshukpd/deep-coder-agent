"""
ReAct Orchestrator — The "Brain" that manages the Thought-Action-Observation loop.
Decouples high-level reasoning from low-level tool execution.
"""

import logging
import json
import os
import time
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
        self._max_steps = getattr(executor, "max_turns", 15)

    def orchestrate(self, task: str, intent: str, full_history: list = None) -> bool:
        """
        Run the ReAct loop until 'finish' or max steps reached.
        """
        self._history = []
        self._action_history = []  # Track (action, action_input) for loop detection
        self._full_history = full_history or []
        self._start_time = time.time()
        self._total_timeout = getattr(self._executor, "total_timeout", 300)
        self._extensions_made = 0
        self._max_extensions = 2
        
        print(f"\n🚀 Starting autonomous ReAct loop for task: {task[:100]}...")
        
        stuck_hint = ""
        try:
            for step_num in range(1, self._max_steps + 1):
                remaining_turns = self._max_steps - step_num + 1
                elapsed_time = time.time() - self._start_time
                remaining_seconds = max(0, int(self._total_timeout - elapsed_time))
                
                print(f"\n🧠 Turn {step_num}/{self._max_steps} | ⏳ {remaining_seconds}s remaining")
            
                # 1. Think & Act
                thought_action = self._decide_next_step(
                    task, 
                    intent, 
                    stuck_hint=stuck_hint,
                    remaining_turns=remaining_turns,
                    remaining_seconds=remaining_seconds
                )
                stuck_hint = "" # Reset hint after use
            
                if not thought_action:
                    logger.error("Failed to decide next step.")
                    self._print_failure_summary(task, "LLM failed to produce a valid action (parse error)")
                    return False
                
                step = ReActStep(
                    thought=thought_action.get("thought", ""),
                    action=thought_action.get("action", ""),
                    action_input=thought_action.get("action_input", {})
                )
            
                print(f"  🤔 Thought: {step.thought}")
                print(f"  🛠️  Action: {step.action}({json.dumps(step.action_input)})")
            
                # Phase 82: Persistent Logging for ReAct turns
                self._executor.readable_logger.log_thought(step.thought)
                self._executor.readable_logger.log_action(step.action, json.dumps(step.action_input))

                # Phase 83: Loop Detection
                current_action_tuple = (step.action, json.dumps(step.action_input, sort_keys=True))
                self._action_history.append(current_action_tuple)
            
                # Check for repetition (e.g., 3 identical calls in a row)
                if len(self._action_history) >= 3:
                    recent = self._action_history[-3:]
                    if all(a == current_action_tuple for a in recent):
                        stuck_hint = (
                            f"SYSTEM WARNING: You have performed action '{step.action}' with the same inputs "
                            "3 times in a row. You are likely STUCK. Do NOT repeat this again. "
                            "Try a different tool, check for missing configuration files, or verify "
                            "if your assumptions about the environment are correct."
                        )
                        print(f"  ⚠️  Loop detected! Injecting recovery hint...")
            
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
            
                # Phase 87: Dynamic Budget Scaling (Progress Detection)
                # If we are at the last turn but seem to be making progress, extend.
                progress_detected = self._detect_progress(step.observation)
                if step_num == self._max_steps and self._extensions_made < self._max_extensions:
                    if progress_detected:
                        extension = 5
                        self._max_steps += extension
                        self._extensions_made += 1
                        print(f"  📈 Progress detected! Dynamically extending budget by {extension} turns ({self._max_steps} total).")
                        self._executor.readable_logger.log_thought(f"Dynamic Budget Scaling triggered: Extended by {extension} turns due to progress.")
            
                if remaining_seconds <= 0:
                    print(f"  🛑 Hard timeout reached ({self._total_timeout}s). Terminating mission.")
                    break
                
            print(f"  ⚠️  Max steps ({self._max_steps}) or timeout reached without completion.")
            self._print_failure_summary(task, "Max steps or timeout reached")
            return False
            
        except KeyboardInterrupt:
            print("\n  🛑 User aborted run (KeyboardInterrupt). Generating final summary before exiting...")
            self._print_failure_summary(task, "User aborted via KeyboardInterrupt")
            raise
        except Exception as e:
            error_str = str(e).lower()
            if "401" in error_str or "invalid_api_key" in error_str or "unauthorized" in error_str:
                logger.warning(f"Authentication error in orchestrate. Bubbling up...")
                raise
            logger.error(f"Agent crashed with exception: {e}", exc_info=True)
            self._print_failure_summary(task, f"Agent crashed: {e}")
            return False

    def _print_failure_summary(self, task: str, reason: str):
        """Generates a final summary of what was discovered if the agent fails or times out."""
        if not self._history:
            return
            
        print(f"\n📝 Generating Wrap-up Summary ({reason})...")
        system_prompt = (
            "You are an assistant reporting on a failed or incomplete autonomous agent run.\n"
            "Review the history of steps taken and provide a concise, user-friendly summary of what was discovered, "
            "what was completed, and what the root cause of the current issue seems to be. "
            "If the agent realized something was already correct (e.g. no circular imports), mention it explicitly!"
        )
        
        user_content = f"Original Task: {task}\nReason for stopping: {reason}\n\nHistory:\n"
        for i, h in enumerate(self._history, 1):
            obs = (h.observation[:300] + '...') if len(h.observation) > 300 else h.observation
            user_content += f"Step {i}:\n  Thought: {h.thought}\n  Action: {h.action}({json.dumps(h.action_input)})\n  Observation: {obs}\n"
            
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        try:
            # We don't use complete_with_tools here, just standard completion to get raw text
            result = self._provider.complete(messages, tools=None)
            print(f"\n============================================================")
            print(f"📋 FINAL WRAP-UP SUMMARY:")
            print(f"============================================================")
            print(result.content)
            print(f"============================================================\n")
            
            # Phase 82: Persistent Logging
            if self._executor and hasattr(self._executor, "readable_logger"):
                self._executor.readable_logger.log_thought(f"FINAL SUMARY: {result.content}")
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")

    def _decide_next_step(self, task: str, intent: str, stuck_hint: str = "", remaining_turns: int = 15, remaining_seconds: int = 300) -> Optional[Dict[str, Any]]:
        """Ask the LLM for the next thought and action."""
        system_prompt = self._build_system_prompt(intent)
        
        # Phase 85/86: Resource Budget Header
        budget_header = f"BUDGET: {remaining_turns} turns remaining | {remaining_seconds}s time remaining.\n"
        if remaining_turns <= 5:
            budget_header += "!!! WARNING: You are nearly out of turns. Deliver your final result or a high-fidelity summary NOW. !!!\n"
        if remaining_seconds < 60:
             budget_header += "!!! WARNING: You are nearly out of time. Complete your task immediately. !!!\n"

        user_content = budget_header + f"\nTask: {task}\n\n"
        
        if stuck_hint:
            user_content += f"!!! {stuck_hint} !!!\n\n"
            
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
            
            # Fallback: Model called the tool directly instead of using decide_step
            valid_actions = ["search_code", "ls", "read_file", "write_file", "run_command", "spawn_subagent", "memory_store", "memory_retrieve", "todo_add", "finish"]
            if result and result.function_name in valid_actions:
                logger.warning(f"Auto-correcting direct tool call: {result.function_name}")
                return {
                    "thought": "(Model directly invoked tool)",
                    "action": result.function_name,
                    "action_input": result.arguments
                }

            logger.error(f"Function name mismatch or None. Result: {result}")
            return None
        except Exception as e:
            error_str = str(e).lower()
            if "401" in error_str or "invalid_api_key" in error_str or "unauthorized" in error_str:
                logger.warning(f"Authentication error in decide_next_step. Bubbling up...")
                raise
            logger.error(f"Error in decide_next_step: {e}")
            return None

    def _detect_progress(self, observation: str) -> bool:
        """Heuristic to detect if the last action was productive."""
        # Signals of progress:
        # 1. Success exit codes
        # 2. Key success keywords
        # 3. File modifications
        success_signals = [
            "Exit Code: 0",
            "Successfully patched",
            "Successfully wrote",
            "audit passed",
            "test passed",
            "PASSED"
        ]
        
        if any(signal in observation for signal in success_signals):
            return True
            
        # Also check if error log is shrinking (heuristic)
        # For now, stick to explicit success signals to avoid infinite loops on "changing" but not "fixing"
        return False

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
                    try:
                        self._executor._write_file(fa, content)
                        return f"Successfully wrote full content to {path}"
                    except (PermissionError, OSError) as e:
                        # Phase 95 Fix 2: Auto-retry via shell command before giving up.
                        # _write_file already has a shell fallback, but if even that
                        # fails (e.g. a truly locked file), try run_command as last resort.
                        import shlex
                        import tempfile
                        full_target = os.path.join(self._executor._repo_path, path)
                        with tempfile.NamedTemporaryFile(
                            mode='w', suffix='.agent_tmp', delete=False
                        ) as tmp:
                            tmp.write(content)
                            tmp_path = tmp.name
                        # cp first; only rm the temp on success
                        shell_cmd = (
                            f"cp {shlex.quote(tmp_path)} {shlex.quote(full_target)}"
                            f" && rm -f {shlex.quote(tmp_path)}"
                            f" || {{ rm -f {shlex.quote(tmp_path)}; exit 1; }}"
                        )
                        res = self._executor.run_code(shell_cmd)
                        if res.return_code == 0:
                            fa.content = content
                            return f"Wrote {path} via shell command fallback (direct write failed: {e})"
                        return (
                            f"Failed to write {path}: {e}. "
                            f"Shell fallback also failed: {res.stderr[:200]}. "
                            f"Try using run_command with a heredoc: "
                            f"cat << 'EOF' > {path} ... EOF"
                        )

            elif action == "run_command":
                cmd = action_input.get("command")
                res = self._executor.run_code(cmd)
                return f"Exit Code: {res.return_code}\nOutput:\n{res.stdout}\n{res.stderr}"
                
            elif action == "spawn_subagent":
                persona = action_input.get("persona")
                sub_task = action_input.get("task")
                print(f"  🤖 Spawning Subagent '{persona}' for task:\n     {sub_task}...")
                
                from agent.planning.subagent_manager import SubagentManager
                manager = SubagentManager(self._executor._repo_path)
                agent = manager.get_agent(persona)
                
                # Spawn a nested orchestrator for the subagent
                sub_orchestrator = ReActOrchestrator(self._provider, self._executor)
                if agent:
                    sub_orchestrator._custom_agent = agent
                    if agent.max_turns:
                        sub_orchestrator._max_steps = agent.max_turns
                        
                success = sub_orchestrator.orchestrate(sub_task, intent=persona.lower())
                status = "Success" if success else "Failed/Timeout"
                return f"Subagent {persona} finished. Status: {status}."

            elif action == "memory_store":
                key = action_input.get("key", "").strip()
                val = action_input.get("value", "")
                if not hasattr(self._executor, "_ephemeral_memory"):
                    self._executor._ephemeral_memory = {}
                self._executor._ephemeral_memory[key] = val
                return f"Stored '{key}' in memory."

            elif action == "memory_retrieve":
                key = action_input.get("key", "").strip()
                mem = getattr(self._executor, "_ephemeral_memory", {})
                return f"Memory for '{key}': {mem.get(key, 'Not found')}"

            elif action == "todo_add":
                task = action_input.get("task", "")
                if not hasattr(self._executor, "_ephemeral_todos"):
                    self._executor._ephemeral_todos = []
                self._executor._ephemeral_todos.append(task)
                return f"Added TODO: {task}. Total TODOs: {len(self._executor._ephemeral_todos)}"

            else:
                return f"Error: Unknown action {action}"
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return f"Error executing {action}: {str(e)}"

    def _build_system_prompt(self, intent: str) -> str:
        """Hydrated system prompt with ReAct logic and Skill Registry."""
        from agent.core.skill_registry import skill_registry
        
        file_list = []
        if getattr(self._executor, "_knowledge_graph", None):
            file_list = self._executor._knowledge_graph.get_all_files()
            
        hydrated_docs = skill_registry.get_hydrated_docs(file_list)
        
        custom_instructions = ""
        if hasattr(self, "_custom_agent") and self._custom_agent:
            custom_instructions = f"\n### Custom Subagent Instructions ({self._custom_agent.name}):\n{self._custom_agent.system_prompt}\n"
            
        from agent.planning.memory import ArchitectureMemory
        arch_mem = ArchitectureMemory(self._executor._repo_path, self._executor._provider)
        memory_context = arch_mem.read_context()
        if memory_context:
            custom_instructions += f"\n### [LONG-TERM MEMORY]\n{memory_context}\n"
        
        return f"""You are a ReAct-based autonomous software engineer. 
Your goal is to complete the user's task by reasoning and taking steps.

Current Intent Context: {intent}
{custom_instructions}
Available Tools:
- search_code(query): Search the codebase for symbols or text.
- ls(path): List files in a directory.
- read_file(path): Read the full content of a file.
- write_file(path, content, description): Create or update a file.
- run_command(command): Execute a shell command in the project root.
- spawn_subagent(persona, task): Delegate a sub-task to a specialist.
- memory_store(key, value): Save context to your long-term memory.
- memory_retrieve(key): Retrieve context from long-term memory.
- todo_add(task): Add an explicit task to your internal checklist.
- finish(): Call this when you have verified your work and the task is complete.

Guidelines:
1. Always state your Thought before choosing an Action.
2. Use observations from previous steps to inform your next decision.
3. If a command fails, analyze the error. If it's a 'Permission Denied' or 'Missing File', check your environment (id, hostname) and look for config files (.ini, .cfg, .toml).
4. **Write failures**: If `write_file` fails with a permission error, immediately try writing the same content using `run_command` with shell redirection instead of giving up. For example: `run_command("printf '%s' '...content...' > path/to/file")` or for multi-line: use a heredoc via `run_command("cat << 'AGENT_EOF' > filename\n...content...\nAGENT_EOF")`.
5. If you repeat an action twice with the same result, STOP and try a DIFFERENT approach (e.g., switch from write_file to run_command, or use a different directory).
7. Strategic Debugging: If tests fail because of missing data or uninitialized state, verify your data hydration strategy (e.g., eager vs lazy loading) and ensure all dependencies are resolved before use.
8. Architectural Resilience: If you encounter circular dependency or "partially initialized" errors, analyze the import/dependency graph. Move shared definitions to a "Base" or "Common" module to break the loop.
9. Environment Knowledge: If a tool fails with unexpected errors, use discovery tools (`ls`, `run_command('id')`, `search_code`) to map the environment before making assumptions.
10. Python Paths: If you get a 'ModuleNotFoundError' when running a local script, remember to inject the current directory into the python path (e.g., `PYTHONPATH=. python script.py`).
11. Port Management: If a server fails with 'Address already in use' or 'Errno 48', you must use `lsof -i :PORT` to find the blocking PID and `kill -9 PID` to terminate it before retrying.
12. Background Execution (CRITICAL): If you need to start a web server, database, or ANY long-running process for live testing, you MUST run it in the background (e.g., `nohup command &` or `command &`) to free your terminal. Then, use `curl`, `nc`, or `ping` to verify it is running successfully. Do NOT run servers synchronously or you will block your own event loop.

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
                    "enum": ["search_code", "ls", "read_file", "write_file", "run_command", "spawn_subagent", "memory_store", "memory_retrieve", "todo_add", "finish"],
                    "description": "The action to take."
                },
                "action_input": {
                    "type": "object",
                    "description": "Parameters for the action. For run_command, you may include 'is_background' (boolean) to explicitly declare if the command should be run in the background (e.g. for servers, watchers, daemons). Set is_background=true for long-running processes, false for one-shot commands."
                }
            },
            "required": ["thought", "action", "action_input"]
        }
    }
}
