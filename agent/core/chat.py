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
from typing import Optional

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
    "type": "generate" | "fix" | "modify" | "run" | "research",
    "task": "Clear task description for the executor",
    "run_command": "optional shell command to run"
  }
}

Guidelines:
- Use "CHAT" mode for simple questions, greetings, or quick confirmations AFTER you have gathered enough information.
- Use "ACTION" with type "research" when the user asks for explanations, codebase structure, or deep logic analysis. 
- PROACTIVE AGENTIC BEHAVIOR: If you are asked to "explain the code" or "how does X work", do NOT just answer from the file tree. You MUST trigger an "ACTION" of type "research" first. 
- You will receive the research findings in the next turn. Use those findings to provide a detailed, accurate response in "CHAT" mode.
- In ACTION mode, your "message" should say something like "I'll analyze the code to give you a detailed answer."
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
            for root, dirs, files in os.walk(self._repo_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')
                           and d != '__pycache__' and d != 'node_modules'
                           and d != '.agent_log']
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
        msgs = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

        # Inject repo context into the first system message
        if self._repo_context:
            msgs[0]["content"] += f"\n\n{self._repo_context}"

        # Add last error context if available
        if self._last_error:
            msgs[0]["content"] += f"\n\nLast error from running code:\n{self._last_error[-1000:]}"

        # Add conversation history (keep last 20 turns to avoid token overflow)
        history = self._messages[-40:]  # 20 turns = 40 messages (user + assistant)
        for msg in history:
            msgs.append(msg.to_llm())

        return msgs

    def _parse_response(self, raw: str) -> ChatResponse:
        """Parse the LLM's JSON response."""
        text = raw.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]

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
            logger.debug(f"Failed to parse chat response as JSON: {e}")
            # Treat unparseable responses as plain chat
            return ChatResponse(mode="CHAT", message=text)

    def _send(self, user_msg: str) -> ChatResponse:
        """Single-turn send (legacy, but kept for simplicity)."""
        return self._send_agentic(user_msg)

    def _send_agentic(self, user_msg: str) -> ChatResponse:
        """
        Multi-turn agentic send:
        RESEARCH -> EXECUTE -> SYNTHESIZE -> RESPOND
        """
        # 1. Add user message
        self._messages.append(ChatMessage(
            role="user",
            content=user_msg,
            timestamp=datetime.now().isoformat(),
        ))

        max_turns = 3
        current_turn = 0
        
        last_response = None
        
        while current_turn < max_turns:
            current_turn += 1
            
            # 2. Build and call
            llm_messages = self._build_llm_messages()
            result = self._provider.complete(llm_messages)
            response = self._parse_response(result.content)
            
            # 3. Add to history
            self._messages.append(ChatMessage(
                role="assistant",
                content=response.message,
                timestamp=datetime.now().isoformat(),
                action_taken=response.action.type if response.action else None,
            ))
            
            last_response = response

            # 4. Handle Actions
            if response.mode == "ACTION" and response.action:
                print(f"  🎬 Agent triggers action: {response.action.type}")
                action_res = self._execute_action(response.action)
                
                # feedback to LLM
                self._messages.append(ChatMessage(
                    role="system",
                    content=f"Observation from {response.action.type} action:\n{action_res}",
                    timestamp=datetime.now().isoformat(),
                ))
                
                # If it's research, we MUST have another turn to explain
                if response.action.type == "research":
                    continue
                
                # For code changes, we return the plan and let the server handle it (legacy)
                # But here we return so server can show the box
                return response

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

        if action.type == "run":

            # Just run a command
            if action.run_command:
                result = executor.run_code(action.run_command)
                if result.success:
                    output = result.stdout[-1500:] if result.stdout else "(no output)"
                    self._last_error = None
                    return f"✅ Command succeeded:\n```\n{output}\n```"
                else:
                    error = (result.stderr or result.stdout)[-1500:]
                    self._last_error = error
                    return f"❌ Command failed:\n```\n{error}\n```"
            return "No command specified."

        # For generate/fix/modify — run the full pipeline
        try:
            plan = executor.execute(action.task)
            self._last_plan = plan
            self.last_action_success = executor.last_run_success
            files = [f.path for f in plan.files]

            if self.last_action_success:
                self._last_error = None
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
        print(f"\n✋ **Approval Requested: {stage.upper()}**")
        print(f"   {details}")
        try:
             choice = input("   Proceed? [Y/n] or enter feedback: ").strip()
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
        print(f"\n{'═' * 60}")
        print(f"  🤖 God Mode Agent — Chat Mode")
        print(f"  📂 Repo: {self._repo_path}")
        print(f"  💡 Type /help for commands, /quit to exit")
        print(f"{'═' * 60}\n")

        # Load repo context once
        print("  📡 Scanning repo...", end="", flush=True)
        self._load_repo_context()
        print(" done\n")

        while True:
            # Prompt
            try:
                user_input = input("\033[1;36m  you>\033[0m ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n  👋 Goodbye!")
                self.save_session()
                break

            if not user_input:
                continue

            # Handle slash commands
            if user_input.startswith("/"):
                result = self._handle_command(user_input)
                if result == "QUIT":
                    print("\n  👋 Goodbye!")
                    self.save_session()
                    break
                if result is not None:
                    for line in result.split("\n"):
                        print(f"  {line}")
                    print()
                    continue

            # Send to LLM
            print()
            start = time.time()
            try:
                response = self._send(user_input)
            except Exception as e:
                print(f"  ❌ LLM error: {e}\n")
                continue

            # Display message
            for line in response.message.split("\n"):
                print(f"  \033[1;32m🤖\033[0m {line}")

            # Execute action if needed
            if response.mode == "ACTION" and response.action:
                action = response.action
                print(f"\n  ⚡ Executing: {action.type} — {action.task}")
                print(f"  {'─' * 50}")

                result = self._execute_action(action)

                print(f"  {'─' * 50}")
                for line in result.split("\n"):
                    print(f"  {line}")

                # Add result to conversation memory
                self._messages.append(ChatMessage(
                    role="assistant",
                    content=f"[Execution result]: {result}",
                    timestamp=datetime.now().isoformat(),
                ))


            elapsed = time.time() - start
            print(f"  \033[2m({elapsed:.1f}s)\033[0m\n")

        # Auto-save on exit
        path = self.save_session()
        print(f"  📝 Session saved: {path}")
