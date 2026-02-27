import os
import time
from datetime import datetime
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

# Professional theme for God Mode Agent
AGENT_THEME = Theme({
    "phase": "bold cyan",
    "thought": "italic grey50",
    "action": "bold green",
    "error": "bold red",
    "warning": "bold yellow",
    "info": "blue",
    "time": "dim white"
})

class SessionLogger:
    """
    Premium logging for God Mode Agent.
    Generates:
    1. agent.log (Plain text, technical)
    2. session.md (Markdown, human-friendly, premium)
    """
    PHASES = ["RESEARCH", "PLANNING", "EXECUTING", "VERIFYING", "CLEANUP"]

    def __init__(self, repo_path: str, task: str = "Autonomous Task"):
        self.repo_path = repo_path
        self.task = task
        self.log_dir = os.path.join(repo_path, ".agent_log")
        self.log_file = os.path.join(self.log_dir, "agent.log")
        self.md_file = os.path.join(self.log_dir, "session.md")
        self.console = Console(theme=AGENT_THEME)
        self.current_phase = None
        self._is_writable = True
        self._ensure_log_dir()
        self._init_md()

    def _ensure_log_dir(self):
        """Standardize the log directory location, with fallback."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except (PermissionError, OSError):
            # Fallback to tmp
            import tempfile
            self.log_dir = os.path.join(tempfile.gettempdir(), "god-mode-agent", os.path.basename(self.repo_path), ".agent_log")
            self.log_file = os.path.join(self.log_dir, "agent.log")
            self.md_file = os.path.join(self.log_dir, "session.md")
            try:
                os.makedirs(self.log_dir, exist_ok=True)
            except Exception:
                self._is_writable = False

    def _init_md(self):
        """Initialize the markdown session log with a header."""
        if not self._is_writable: return
        try:
            with open(self.md_file, "w") as f:
                f.write(f"# 🚀 God Mode Session: {self.task}\n\n")
                f.write(f"- **Repo**: `{self.repo_path}`\n")
                f.write(f"- **Started**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("---\n\n")
        except Exception:
            self._is_writable = False

    def _write_log(self, message: str, level: str = "INFO"):
        """Append a timestamped message to the log file."""
        if not self._is_writable: return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_file, "a") as f:
                f.write(f"[{timestamp}] [{level}] {message}\n")
        except Exception:
            self._is_writable = False

    def _write_md(self, content: str):
        """Append content to the markdown session log."""
        if not self._is_writable: return
        try:
            with open(self.md_file, "a") as f:
                f.write(content + "\n")
        except Exception:
            self._is_writable = False

    def set_phase(self, phase: str):
        """Transition to a new execution phase."""
        if phase not in self.PHASES:
            phase = "UNKNOWN"
        self.current_phase = phase
        self._write_log(f"--- PHASE: {phase} ---", level="PHASE")
        self._write_md(f"\n## 🏗️ Phase: {phase}\n")
        
        # UI indicator
        self.console.print(Panel(
            Text(f"Phase: {phase}", style="phase"),
            border_style="cyan",
            expand=False
        ))

    def log_thought(self, thought: str):
        """Record agent reasoning/analysis."""
        self._write_log(f"THOUGHT: {thought}", level="BRAIN")
        self._write_md(f"> 🧠 **Thought**: {thought}\n")
        self.console.print(f"  [thought]🧠 {thought}[/thought]")

    def log_action(self, action: str, details: Optional[str] = None):
        """Record a concrete tool use or code operation."""
        msg = f"ACTION: {action}"
        if details:
            msg += f" ({details})"
        self._write_log(msg, level="ACTION")
        
        md_msg = f"### ⚡ Action: {action}\n"
        if details:
            if "\n" in details:
                md_msg += f"```\n{details}\n```\n"
            else:
                md_msg += f"- *Details*: {details}\n"
        self._write_md(md_msg)
        
        self.console.print(f"  [action]⚡ {action}[/action]")
        if details:
             self.console.print(f"     [time]{details}[/time]")

    def log_error(self, error: str):
        """Record a failure or exception."""
        self._write_log(f"ERROR: {error}", level="ERROR")
        self._write_md(f"\n#### ❌ Error\n> {error}\n")
        self.console.print(f"  [error]❌ ERROR: {error}[/error]")

    def log_success(self, message: str):
        """Record a successful completion."""
        self._write_log(f"SUCCESS: {message}", level="SUCCESS")
        self._write_md(f"\n### ✅ Success\n{message}\n")
        self.console.print(f"  [action]✅ {message}[/action]")

# Keep the old name for backward compatibility
HumanReadableLogger = SessionLogger

# Singleton instance for internal core use if needed
# But usually instantiated per TaskExecutor run
