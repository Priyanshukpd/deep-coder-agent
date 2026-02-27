"""
Persistent Chat Mode — Copilot / Windsurf style.

Provides a conversational interface where the agent remembers context
across messages. The LLM decides whether each message needs code
generation (triggers the full pipeline) or is just a conversation.

Usage:
    session = ChatSession(provider, repo_path)
    session.loop()  # enters interactive REPL
"""

from __future__ import annotations

import os
import json
import logging
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.status import Status
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.theme import Theme

# Define a custom premium theme matching the God Mode aesthetic
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "danger": "bold red",
    "success": "bold green",
    "user": "bold cyan",
    "agent": "bold bright_green",
    "system": "dim white"
})
console = Console(theme=custom_theme)

from agent.planning.agents_loader import inject_agents_md
from agent.core.context_manager import trim_history, estimate_tokens, estimate_message_tokens
from agent.core.session_store import save_message, save_session_meta, session_path
from agent.planning.session_state_manager import SessionStateManager
from agent.core.logger import HumanReadableLogger

logger = logging.getLogger(__name__)


# ── System Prompt ────────────────────────────────────────────────

CHAT_SYSTEM_PROMPT = """You are God Mode Agent — a senior software engineer AI assistant.
You are working in a repository and helping the user with coding tasks through a persistent chat.
You can work with ANY programming language or framework: Python, Java, Node.js, Go, Rust, Flutter, React, Django, Flask, FastAPI, Spring Boot, Express, Docker, and more.

IMPORTANT: You must decide how to respond to each user message. Respond with a JSON block:

{
  "mode": "CHAT" or "ACTION",
  "message": "Your conversational response to the user",
  "action": null or {
    "type": "generate" | "fix" | "modify" | "run" | "research" | "cd" | "shell",
    "task": "Clear task description for the executor",
    "run_command": "optional shell command to run, or path to cd into"
  }
}

CRITICAL SCHEMA ENFORCEMENT: 
You MUST NOT hallucinate any properties outside of `type`, `task`, and `run_command` inside the `action` object. Specifically, NEVER output a `files` array here. Use `shell` with a command like `cat file.py` or use `research` if you want to read files.
NEVER, EVER use `<tool_call>` or XML tags. DO NOT output any `<` or `>` brackets. Output ONLY raw JSON starting with `{` and ending with `}`.

Guidelines:
- Use "CHAT" mode for simple questions, greetings, or quick confirmations AFTER you have gathered enough information.
- Use "ACTION" with type "shell" to gain raw, unfettered access to the bash terminal.
- HEURISTIC EXPLORATION: When searching for text or files on vague prompts, ALWAYS prioritize using raw UNIX tools like `grep`, `rg` (ripgrep), `fd`, and `ls` through the `shell` action. Do not ask for tools like 'KnowledgeGraph'. Explore the filesystem just like a human engineer would.
- THE AMBITION DIRECTIVE: For tasks that have no prior context, feel free to be ambitious and demonstrate creativity with your implementation. Use judicious initiative to fix vague prompts like "improve the UI" without halting repeatedly for user approval. Try to solve the problem directly.
- Use "ACTION" with type "research" when the user asks for high-level explanations or codebase structure that the knowledge graph handles well. 
- PROACTIVE AGENTIC BEHAVIOR: If you are asked to "explain the code" or "how does X work", do NOT just answer from the file tree. You MUST trigger an "ACTION" of type "shell" or "research" first. 
- FLUID WORKSPACE: If the user asks to "create a new project" and you are in the wrong directory, DO NOT tell the user to change directories. Instead, trigger a "run" action to `mkdir` the new project, or output a "cd" action with the relative path to move there autonomously.
- SMART CODING (LONG TASKS): If writing code that takes hours to run (e.g., ML training, large data processing), NEVER hardcode the long loop in the main block. Use structured functions or classes. Provide a way (e.g., via CLI args or a test flag) to verify the logic on a microscopic scale (1% of data/1 epoch) to prove the code works before the user runs the full job.
- You will receive the output of your ACTIONs in the next turn automatically. Use those findings to provide a detailed, accurate response in "CHAT" mode or chain another "ACTION".
- In ACTION mode, your "message" should say something like "I'll run some commands to give you a detailed answer."
- Never promise to do something in CHAT mode that you aren't actually triggering an ACTION for.

CRITICAL: Output ONLY the JSON block. No markdown fences, no extra text.
"""


CONTEXT_TEMPLATE = """
Repository: {repo_path}
Files: {file_count}
Stack: {stack}

File structure:
{file_tree}
"""


# ── Data Classes ─────────────────────────────────────────────────

@dataclass
class ChatMessage:
    """A single message in the chat history."""
    role: str        # "user", "assistant", "system"
    content: str
    timestamp: str = ""
    action_taken: Optional[str] = None  # What pipeline action occurred, if any

    def to_llm(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatAction:
    """Parsed action from an LLM response."""
    type: str        # generate, fix, modify, run
    task: str
    run_command: str = ""


@dataclass
class ChatResponse:
    """Parsed LLM response."""
    mode: str        # CHAT or ACTION
    message: str
    action: Optional[ChatAction] = None


# ── Chat Session ─────────────────────────────────────────────────

class ChatSession:
    """
    Persistent chat session with conversation memory.

    Maintains LLM message history across turns so the agent
    remembers context — like Copilot or Windsurf.
    """

    def __init__(self, provider, repo_path: str):
        self._provider = provider
        self._repo_path = os.path.abspath(repo_path)
        self._messages: list[ChatMessage] = []
        self._turn_count = 0
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._repo_context = ""
        self._last_plan = None
        self._last_error = None
        self.last_action_success = True
        self.interactive_mode = False  # Toggle for Co-Pilot behavior
        
        # Save session metadata for resume listing
        save_session_meta(self._session_id, self._repo_path, getattr(self._provider.config.llm, "model", "default"))

        self.state_manager = SessionStateManager(self._repo_path, self._provider)
        self.readable_logger = HumanReadableLogger(self._repo_path)



    # ── Repo Context ─────────────────────────────────────────

    def _load_repo_context(self) -> str:
        """Build a concise repo context string."""
        file_tree = []
        file_count = 0
        stack = "Unknown"

        try:
            from agent.planning.repo_discovery import RepoDiscovery
            discovery = RepoDiscovery(self._repo_path)
            repo_map = discovery.scan()
            file_count = repo_map.file_count
            stack = repo_map.stack.summary if repo_map.stack else "Unknown"
            
            # Compact file tree
            ignore_dirs = {'.git', 'node_modules', 'venv', '.venv', '__pycache__', 'dist', 'build', '.agent_log'}
            for root, dirs, files in os.walk(self._repo_path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ignore_dirs]
                level = root.replace(self._repo_path, '').count(os.sep)
                indent = '  ' * level
                basename = os.path.basename(root) or '.'
                file_tree.append(f"{indent}{basename}/")
                sub_indent = '  ' * (level + 1)
                for f in files[:20]:  # cap per directory
                    file_tree.append(f"{sub_indent}{f}")
                if len(files) > 20:
                    file_tree.append(f"{sub_indent}... and {len(files) - 20} more")
        except Exception as e:
            logger.warning(f"Repo context load failed: {e}")
            file_tree = ["(Could not scan repo)"]

        self._repo_context = CONTEXT_TEMPLATE.format(
            repo_path=self._repo_path,
            file_count=file_count,
            stack=stack,
            file_tree="\n".join(file_tree[:100]),
        )
        return self._repo_context

    # ── LLM Interaction ──────────────────────────────────────

    def _build_llm_messages(self) -> list[dict]:
        """Build the full message list for the LLM."""
        # 1. Hierarchical AGENTS.md loading
        system_base = inject_agents_md(CHAT_SYSTEM_PROMPT, self._repo_path)
        
        msgs = [{"role": "system", "content": system_base}]

        # Inject repo context into the first system message
        if self._repo_context:
            msgs[0]["content"] += f"\n\n{self._repo_context}"

        # Add last error context if available
        if self._last_error:
            msgs[0]["content"] += f"\n\nLast error from running code:\n{self._last_error[-1000:]}"

        # Add conversation history
        for msg in self._messages:
            msgs.append(msg.to_llm())

        # 2. Context Window Management — Trim history if approaching limit
        model_name = getattr(self._provider.config.llm, "model", "gpt-4o")
        
        # Phase 63: Stateful Pre-Compaction
        total_tokens = estimate_message_tokens(msgs)
        from agent.core.context_manager import TRIM_THRESHOLD
        from agent.core.model_registry import get_model_meta
        meta = get_model_meta(model_name)
        budget = int(meta.context_window * TRIM_THRESHOLD)
        
        if total_tokens > budget:
            # History is about to be trimmed. Checkpoint the mental map first.
            self.state_manager.pre_compact_hook(msgs)
            
        # Add the mental map to the system prompt
        state_context = self.state_manager.get_state_context()
        if state_context:
            msgs[0]["content"] += state_context

        msgs = trim_history(msgs, model_name=model_name)

        return msgs

    def _parse_response(self, raw: str) -> ChatResponse:
        """Parse the LLM's JSON response robustly."""
        import re
        text = raw.strip()
        
        # 1. Handle hallucinated XML tool calls from coding models
        xml_match = re.search(r'<tool_call>.*?</tool_call>', text, flags=re.DOTALL)
        if xml_match:
            xml_text = xml_match.group(0)
            
            # Extract task
            task_match = re.search(r'<function=task>\n?(.*?)\n?</function>', xml_text, flags=re.DOTALL)
            task = task_match.group(1).strip() if task_match else "Exploration command"
            
            # Extract run_command
            cmd_match = re.search(r'<function=run_command>\n?(.*?)\n?</function>', xml_text, flags=re.DOTALL)
            cmd = cmd_match.group(1).strip() if cmd_match else ""
            
            # If we didn't find the Qwen specific tags, try generic XML extraction
            if not cmd:
                # Fallback for `<file>` reads if the LLM hallucinated a read function
                file_match = re.search(r'<file>\n?(.*?)\n?</file>', xml_text, flags=re.DOTALL)
                if file_match:
                    cmd = f"cat {file_match.group(1).strip()}"

            if cmd:
                return ChatResponse(
                    mode="ACTION",
                    message="I'll run a shell command to investigate.",
                    action=ChatAction(type="shell", task=task, run_command=cmd)
                )

        # 2. Aggressively extract JSON even if prefixed by conversational text.
        text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
        
        # 3. Try to find ```json ... ``` blocks
        if "```json" in text:
            json_part = text.split("```json")[1]
            text = json_part.split("```")[0].strip()
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1].strip()
                if text.startswith("json\n"):
                    text = text[5:]
        else:
            # 4. Try to find the outermost JSON object
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                text = text[start_idx:end_idx+1]

        try:
            data = json.loads(text)
            action = None
            if data.get("action"):
                a = data["action"]
                action = ChatAction(
                    type=a.get("type", "generate"),
                    task=a.get("task", ""),
                    run_command=a.get("run_command", ""),
                )

            return ChatResponse(
                mode=data.get("mode", "CHAT"),
                message=data.get("message", ""),
                action=action,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to parse chat response as JSON. Error: {e}. Raw Text: {text}")
            # Treat unparseable responses as plain chat
            return ChatResponse(mode="CHAT", message=raw.strip())

    def _send(self, user_msg: str) -> ChatResponse:
        """Single-turn send (legacy, but kept for simplicity)."""
        return self._send_agentic(user_msg)

    def _send_agentic(self, user_msg: str) -> ChatResponse:
        """
        Multi-turn agentic send:
        RESEARCH -> EXECUTE -> SYNTHESIZE -> RESPOND
        """
        # 1. Add user message
        msg_user = ChatMessage(
            role="user",
            content=user_msg,
            timestamp=datetime.now().isoformat(),
        )
        self._messages.append(msg_user)
        save_message(self._session_id, msg_user.to_llm())

        max_turns = 3
        current_turn = 0
        
        last_response = None
        
        while current_turn < max_turns:
            current_turn += 1
            
            # 2. Build and call
            self.readable_logger.set_phase("ANALYZING")
            llm_messages = self._build_llm_messages()
            # Enable streaming for interactive sessions
            stream_mode = getattr(self, "interactive_mode", False)
            result = self._provider.complete(llm_messages, stream=stream_mode)
            response = self._parse_response(result.content or "")

            # Log analysis thoughts if present
            if response.message and response.mode == "ACTION":
                 self.readable_logger.log_thought(response.message[:150] + "...")
            
            # 3. Add to history
            msg_assistant = ChatMessage(
                role="assistant",
                content=response.message,
                timestamp=datetime.now().isoformat(),
                action_taken=response.action.type if response.action else None,
            )
            self._messages.append(msg_assistant)
            save_message(self._session_id, msg_assistant.to_llm())
            
            last_response = response

            if response.mode == "ACTION" and response.action:
                if response.action.type == "research":
                    self.readable_logger.set_phase("RESEARCH")
                    console.print(f"  [warning]⚡ Researching:[/warning] {response.action.task}", style="dim")
                elif response.action.type == "shell":
                    self.readable_logger.log_action("shell", response.action.run_command)
                    console.print(f"  [warning]⚡ Shell Command:[/warning] {response.action.run_command}", style="dim")
                else:
                    self.readable_logger.log_action(response.action.type, response.action.task)
                    console.print(f"  [warning]⚡ Triggering:[/warning] {response.action.type} -> {response.action.task}", style="dim")
                
                action_res = self._execute_action(response.action)
                
                # feedback to LLM
                msg_obs = ChatMessage(
                    role="system",
                    content=f"Observation from {response.action.type} action:\n{action_res}",
                    timestamp=datetime.now().isoformat(),
                )
                self._messages.append(msg_obs)
                save_message(self._session_id, msg_obs.to_llm())
               
                # If it's shell or research, we MUST have another turn to explain or iterate
                if response.action.type in ("research", "shell"):
                    continue

                # For terminal code changes (generate, fix, modify, run, cd), we synthesize a CHAT response 
                # containing the markdown summary, and return it to UI so it's not run twice downstream.
                return ChatResponse(
                    mode="CHAT",
                    message=action_res,
                    action=None  # Cleared so calling loops don't try executing it again
                )

            # 5. If it's just CHAT, we are done
            return response

        return last_response

    # ── Action Execution ─────────────────────────────────────

    def _execute_action(self, action: ChatAction) -> str:
        """Execute a code action using the TaskExecutor."""
        from agent.core.task_executor import TaskExecutor
        from agent.cli import RollbackManager

        executor = TaskExecutor(self._provider, self._repo_path)
        rollback_mgr = RollbackManager(self._repo_path)
        executor.set_rollback_manager(rollback_mgr)

        # Hook up interactive approval if enabled
        if self.interactive_mode:
             executor.set_approval_callback(self._approval_handler)

        if action.type == "research":
            # Just perform deep research and return the context
            research_notes = executor._research_task(action.task)
            if research_notes:
                return f"🧠 **Research Findings:**\n\n{research_notes[:3000]}..."
            else:
                return "Failed to find relevant files for research."

        if action.type == "cd":
            target_dir = action.run_command or action.task
            new_path = os.path.abspath(os.path.join(self._repo_path, target_dir))
            if os.path.isdir(new_path):
                self._repo_path = new_path
                self._load_repo_context()
                return f"✅ Changed directory to `{self._repo_path}`. Workspace context updated."
            else:
                return f"❌ Directory `{new_path}` does not exist. Try creating it first using a `run` action."

        if action.type == "shell":
            return self._execute_raw_shell(action)

    def _execute_raw_shell(self, action: ChatAction) -> str:
        """Execute a raw shell command for heuristic exploration."""
        import subprocess
        cmd = action.run_command or action.task
        if not cmd:
            return "❌ No command specified for shell action."
        
        try:
            # We run it strictly in the current repo path
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=60 # Prevent LLM from hanging forever
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR]\n{result.stderr}"
            
            if not output.strip():
                return f"✅ Command `{cmd}` exited with code {result.returncode} (No output)"
            
            # Truncate to avoid blowing up the context window
            if len(output) > 8000:
                output = output[:4000] + "\n\n... [OUTPUT TRUNCATED] ...\n\n" + output[-4000:]
                
            return f"Command `{cmd}` exited with {result.returncode}:\n```\n{output}\n```"
        except subprocess.TimeoutExpired:
            return f"❌ Command `{cmd}` timed out after 60 seconds."
        except Exception as e:
            return f"❌ Command `{cmd}` failed to execute: {e}"

        if action.type == "run":
            return self._execute_run(action, executor)

        # For generate/fix/modify — run the full pipeline
        try:
            # Map chat action types to TaskIntent
            target_intent = action.type
            if target_intent == "modify":
                target_intent = "develop"
            
            full_history = [m.to_llm() for m in self._messages]
            plan = executor.execute(action.task, intent=target_intent, full_history=full_history)
            self._last_plan = plan
            self.last_action_success = executor.last_run_success
            files = [f.path for f in plan.files]

            if self.last_action_success:
                self._last_error = None
                self.state_manager.post_action_update(plan) # Update state manager after successful action
                self._load_repo_context()
                return self._build_success_summary(action, plan, executor)
            else:
                if rollback_mgr.has_backups:
                    rollback_mgr.rollback()
                return self._build_failure_summary(action, plan, executor)
        except Exception as e:
            self._last_error = str(e)
            self.last_action_success = False
            if rollback_mgr.has_backups:
                rollback_mgr.rollback()
            return (
                f"### ❌ Execution Crashed\n\n"
                f"**Task:** {action.task}\n\n"
                f"**Error:**\n```\n{e}\n```\n\n"
                f"Please check the error above and try again."
            )

    def _build_success_summary(self, action: ChatAction,
                               plan, executor) -> str:
        """Build a rich Markdown summary for a successful task."""
        lines = []
        lines.append(f"### ✅ Task Complete\n")
        lines.append(f"**{plan.summary}**\n")

        # Stack info
        stack_name = plan.stack or "python"
        lines.append(f"🏗️ **Stack:** {stack_name}\n")

        # Files edited section
        lines.append("#### 📁 Files Edited")
        for f in plan.files:
            icon = {"create": "🆕", "modify": "✏️", "delete": "🗑️"}.get(f.action, "📄")
            line_count = len(f.content.split('\n')) if f.content else 0
            lines.append(f"- {icon} `{f.path}` — {f.description} ({line_count} lines)")
        lines.append("")

        # Pipeline progress
        lines.append("#### ⚡ Pipeline Progress")
        lines.append("1. ✅ Plan generated")
        lines.append(f"2. ✅ Code written ({len(plan.files)} file(s))")
        if plan.dependencies:
            lines.append(f"3. ✅ Dependencies installed: `{', '.join(plan.dependencies)}`")
        if plan.compile_command:
            lines.append(f"3b. ✅ Compiled successfully")
        if plan.run_command:
            if executor.fix_attempts_used > 0:
                lines.append(f"4. ✅ Code executed (after {executor.fix_attempts_used} fix attempt(s))")
            else:
                lines.append("4. ✅ Code executed successfully")
        lines.append(f"5. ✅ Verification passed")
        lines.append("")

        # Summary footer
        lines.append(f"---")
        # Token usage
        provider = self._provider
        if provider.total_tokens > 0:
            lines.append(f"🪙 **Tokens used:** {provider.total_input_tokens:,} input, {provider.total_output_tokens:,} output ({provider.total_tokens:,} total)")
        lines.append(f"🎉 All done! Your files are ready in `{self._repo_path}`.")

        return "\n".join(lines)

    def _build_failure_summary(self, action: ChatAction,
                               plan, executor) -> str:
        """Build a rich Markdown summary for a failed task."""
        lines = []
        lines.append(f"### ⚠️ Task Paused — Human Intervention Required\n")
        lines.append(f"**{plan.summary}**\n")

        # Files section
        done_files = [f for f in plan.files if f.content and f.action != "delete"]
        if done_files:
            lines.append("#### 📁 Files Generated (rolled back)")
            for f in done_files:
                lines.append(f"- `{f.path}` — {f.description}")
            lines.append("")

        # Pipeline progress (show where it stopped)
        lines.append("#### ⚡ Pipeline Progress")
        lines.append("1. ✅ Plan generated")
        lines.append(f"2. ✅ Code written ({len(plan.files)} file(s))")
        if plan.dependencies:
            lines.append(f"3. ✅ Dependencies installed")
        if executor.fix_attempts_used > 0:
            lines.append(f"4. ❌ Code execution failed (after {executor.fix_attempts_used} fix attempts)")
        else:
            lines.append(f"4. ❌ Pipeline failed")
        lines.append("5. ↩️ Changes rolled back")
        lines.append("")

        # Error details
        error_summary = executor.last_error or "Unknown error"
        lines.append("#### 🔍 Error Details")
        lines.append(f"```\n{error_summary[:800]}\n```\n")

        # How to proceed
        lines.append("#### 🛠️ How to Proceed")
        lines.append("1. Resolve the issue above (e.g., `huggingface-cli login`, fix permissions)")
        lines.append("2. Tell me **'Try again'** and I'll re-run the pipeline")
        lines.append("")

        return "\n".join(lines)

    # ── Slash Commands ───────────────────────────────────────

    def _handle_command(self, cmd: str) -> Optional[str]:
        """Handle slash commands. Returns response or None if not a command."""
        parts = cmd.strip().split(maxsplit=1)
        command = parts[0].lower()

        if command in ("/quit", "/exit", "/q"):
            return "QUIT"

        if command == "/help":
            return (
                "📖 **Commands:**\n"
                "  /help     — Show this help\n"
                "  /status   — Show session stats\n"
                "  /history  — Show conversation history\n"
                "  /clear    — Clear conversation memory\n"
                "  /files    — List repo files\n"
                "  /run CMD  — Run a shell command\n"
                "  /mode [auto|interactive] — Toggle Co-Pilot mode\n"
                "  /quit     — Exit chat\n"
            )

        if command == "/mode":

            if len(parts) > 1:
                mode = parts[1].lower()
                if mode in ("interactive", "i", "copilot"):
                    self.interactive_mode = True
                    return "🎛️  Switched to **Interactive Mode**. I will ask for approval before executing steps."
                elif mode in ("auto", "a", "god"):
                    self.interactive_mode = False
                    return "🚀 Switched to **Auto Mode**. I will execute autonomously."
            return f"Current mode: **{'Interactive' if self.interactive_mode else 'Auto'}**"


        if command == "/status":
            return (
                f"📊 **Session Status:**\n"
                f"  Turns: {self._turn_count}\n"
                f"  Messages: {len(self._messages)}\n"
                f"  Repo: {self._repo_path}\n"
                f"  LLM calls: {self._provider.call_count}\n"
                f"  Session: {self._session_id}\n"
            )

        if command == "/history":
            if not self._messages:
                return "No conversation history yet."
            lines = []
            for msg in self._messages[-20:]:
                icon = "👤" if msg.role == "user" else "🤖"
                preview = msg.content[:80].replace('\n', ' ')
                lines.append(f"  {icon} {preview}")
            return "\n".join(lines)

        if command == "/clear":
            self._messages.clear()
            self._turn_count = 0
            self._last_error = None
            self._last_plan = None
            return "🗑️  Conversation cleared."

        if command == "/files":
            files = []
            for root, dirs, file_list in os.walk(self._repo_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')
                           and d != '__pycache__' and d != 'node_modules']
                for f in file_list:
                    rel = os.path.relpath(os.path.join(root, f), self._repo_path)
                    files.append(rel)
            if len(files) > 30:
                return "📂 " + "\n  ".join(files[:30]) + f"\n  ... and {len(files) - 30} more"
            return "📂 " + "\n  ".join(files) if files else "📂 (empty repo)"

        if command == "/run":
            if len(parts) > 1:
                from agent.core.task_executor import TaskExecutor
                executor = TaskExecutor(self._provider, self._repo_path)
                result = executor.run_code(parts[1])
                if result.success:
                    return result.stdout[-2000:] if result.stdout else "(no output)"
                else:
                    return f"❌ {(result.stderr or result.stdout)[-2000:]}"
            return "Usage: /run <command>"

        return None  # Not a slash command

    # ── Session Persistence ──────────────────────────────────

    def save_session(self):
        """Save conversation to disk."""
        log_dir = os.path.join(self._repo_path, ".agent_log", "chat_sessions")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"chat_{self._session_id}.json")

        data = {
            "session_id": self._session_id,
            "repo_path": self._repo_path,
            "turn_count": self._turn_count,
            "llm_calls": self._provider.call_count,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "action_taken": m.action_taken,
                }
                for m in self._messages
            ],
        }

        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        return path

    # ── Main Chat Loop ───────────────────────────────────────

    def _approval_handler(self, stage: str, details: str) -> Any:
        """Handle interactive approval requests."""
        console.print(f"\n[danger]✋ Approval Requested: {stage.upper()}[/danger]")
        console.print(f"   [system]{details}[/system]")
        try:
             choice = Prompt.ask("   [bold]Proceed?[/bold] [Y/n] or enter feedback", default="Y").strip()
             if choice.lower() in ('', 'y', 'yes'):
                 return True
             elif choice.lower() in ('n', 'no'):
                 return False
             else:
                 return choice # Feedback string
        except (EOFError, KeyboardInterrupt):
             return False

    def loop(self):

        """Main interactive chat loop."""
        header = Panel(
            f"[bold bright_white]God Mode Agent[/bold bright_white] — Chat Mode\n\n"
            f"📂 [info]Repo:[/info] {self._repo_path}\n"
            f"💡 [system]Type /help for commands, /quit to exit[/system]",
            border_style="cyan",
            expand=False
        )
        console.print(header)

        # Load repo context once
        with console.status("[info]Scanning repository topology...[/info]", spinner="dots") as status:
            self._load_repo_context()
        console.print("  [success]✔ Repo mapped[/success]\n")

        while True:
            # Prompt
            try:
                # Wait for input
                user_input = Prompt.ask("\n[user]you>[/user]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n\n  👋 [bold]Goodbye![/bold]")
                self.save_session()
                break

            if not user_input:
                continue

            # Handle slash commands
            if user_input.startswith("/"):
                result = self._handle_command(user_input)
                if result == "QUIT":
                    console.print("\n  👋 [bold]Goodbye![/bold]")
                    self.save_session()
                    break
                if result is not None:
                    for line in result.split("\n"):
                        console.print(f"  {line}")
                    console.print()
                    continue

            # Send to LLM
            console.print()
            start = time.time()
            try:
                with console.status(f"[agent]🤖 Agent is thinking...[/agent]", spinner="point") as status:
                    response = self._send(user_input)
            except Exception as e:
                console.print(f"  [danger]❌ LLM error:[/danger] {e}\n")
                continue

            # Display message as Markdown
            if response.message:
                console.print(Panel(Markdown(response.message), border_style="dim", title="[agent]🤖 Agent[/agent]", title_align="left"))

            # (Action execution is now strictly encapsulated inside `_send_agentic` 
            # and surfaces its result directly as `response.message` for safe, single rendering)

            elapsed = time.time() - start
            console.print(f"  [system]({elapsed:.1f}s)[/system]\n")

        # Auto-save on exit
        path = self.save_session()
        console.print(f"  📝 Session saved: {path}")
