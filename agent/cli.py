"""
God Mode Agent — CLI Runner.

Usage:
    god-mode start                      Launch the God Mode local daemon and Web UI
    god-mode chat                       Launch the interactive terminal chat (REPL)
    python -m agent "Fix the bug"       Run directly against a task
    python -m agent --repo /path "Add dark mode"
    python -m agent --dry-run "Refactor"
    python -m agent --self-test
    python -m agent --scan .
    python -m agent --interactive
    python -m agent --version
"""

from __future__ import annotations

import argparse
import sys
import os
import json
import logging
import subprocess
import shutil
import time
from datetime import datetime
from typing import Optional

VERSION = "7.5.1.1"

# Suppress Together banner
os.environ.setdefault("TOGETHER_NO_BANNER", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent")


# ── Execution Log ────────────────────────────────────────────────

class ExecutionLog:
    """Saves a JSON log of what the agent did for auditability."""

    def __init__(self, repo_path: str):
        self._entries: list[dict] = []
        self._repo_path = os.path.abspath(repo_path)
        self._start = datetime.now()

    def add(self, step: str, status: str, details: str = ""):
        self._entries.append({
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "status": status,
            "details": details,
        })

    def save(self):
        """Save log to .agent_log/ in the target repo, with fallback."""
        log_dir = os.path.join(self._repo_path, ".agent_log")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except (PermissionError, OSError):
            # Fallback to tmp
            import tempfile
            log_dir = os.path.join(tempfile.gettempdir(), "god-mode-agent", os.path.basename(self._repo_path), ".agent_log")
            os.makedirs(log_dir, exist_ok=True)

        filename = f"run_{self._start.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(log_dir, filename)
        data = {
            "agent_version": VERSION,
            "repo": self._repo_path,
            "started": self._start.isoformat(),
            "finished": datetime.now().isoformat(),
            "steps": self._entries,
        }
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  📝 Log saved: {filepath}")
        except Exception as e:
            print(f"  ⚠️  Failed to save execution log: {e}")


# ── Rollback Manager ────────────────────────────────────────────

class RollbackManager:
    """Backs up files before modification and restores on failure."""

    def __init__(self, repo_path: str):
        self._repo_path = os.path.abspath(repo_path)
        self._backups: dict[str, str | None] = {}  # filepath -> original content (None = new file)

    def backup(self, filepath: str):
        """Backup a file before modifying it."""
        abs_path = os.path.join(self._repo_path, filepath)
        if abs_path in self._backups:
            return  # Already backed up
        if os.path.exists(abs_path):
            with open(abs_path, 'r', errors='replace') as f:
                self._backups[abs_path] = f.read()
        else:
            self._backups[abs_path] = None  # New file

    def rollback(self):
        """Restore all backed-up files to their original state."""
        restored = 0
        for path, content in self._backups.items():
            if content is None:
                # Was a new file — delete it
                if os.path.exists(path):
                    os.remove(path)
                    restored += 1
            else:
                with open(path, 'w') as f:
                    f.write(content)
                restored += 1
        if restored:
            print(f"  ↩️  Rolled back {restored} files")
        self._backups.clear()

    @property
    def has_backups(self) -> bool:
        return len(self._backups) > 0


# ── Git Helper ───────────────────────────────────────────────────

def git_auto_commit(repo_path: str, task: str) -> bool:
    """Create a branch and commit changes after a successful run."""
    abs_path = os.path.abspath(repo_path)

    # Check if it's a git repo
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, cwd=abs_path,
    )
    if result.returncode != 0:
        print("  ⚠️  Not a git repo — skipping commit")
        return False

    # Create branch name from task
    branch_name = "agent/" + task.lower()[:40].replace(" ", "-").replace("/", "-")
    branch_name = ''.join(c for c in branch_name if c.isalnum() or c in '-_/')

    # Create and switch to branch
    subprocess.run(["git", "checkout", "-b", branch_name],
                   capture_output=True, text=True, cwd=abs_path)

    # Stage and commit
    subprocess.run(["git", "add", "."], capture_output=True, text=True, cwd=abs_path)
    result = subprocess.run(
        ["git", "commit", "-m", f"agent: {task}"],
        capture_output=True, text=True, cwd=abs_path,
    )

    if result.returncode == 0:
        print(f"  ✅ Committed to branch: {branch_name}")
        return True
    else:
        print(f"  ⚠️  Nothing to commit")
        return False


# ── Summary Report ───────────────────────────────────────────────

def print_summary(task: str, repo_path: str, start_time: float,
                  intent: str = "", files_written: int = 0,
                  llm_calls: int = 0, fix_attempts: int = 0,
                  success: bool = True):
    """Print a summary report at the end of a run."""
    elapsed = time.time() - start_time
    print("\n" + "─" * 60)
    print("📊 Run Summary")
    print("─" * 60)
    print(f"  Task:         {task}")
    print(f"  Repo:         {os.path.abspath(repo_path)}")
    print(f"  Intent:       {intent}")
    print(f"  Files:        {files_written} written")
    print(f"  LLM calls:    {llm_calls}")
    if fix_attempts:
        print(f"  Fix attempts: {fix_attempts}")
    print(f"  Duration:     {elapsed:.1f}s")
    print(f"  Result:       {'✅ Success' if success else '❌ Failed'}")
    print("─" * 60)


# ── Sub-Commands ─────────────────────────────────────────────────

def run_self_test():
    """Run all governance self-tests."""
    print("\n🔒 Running Governance Self-Test...\n")
    from agent.verification.governance_self_test import GovernanceSelfTest, TestResult
    tester = GovernanceSelfTest()
    report = tester.run_all()

    for c in report.cases:
        icon = "✅" if c.result == TestResult.PASS else "❌"
        print(f"  {icon} {c.name}: {c.details}")

    print(f"\n  Result: {report.passed}/{report.total} passed\n")
    return report.all_passed


def run_scan(directory: str):
    """Scan a repository and show the RepoMap."""
    print(f"\n🔍 Scanning {directory}...\n")
    from agent.planning.repo_discovery import RepoDiscovery
    discovery = RepoDiscovery(directory)
    repo_map = discovery.scan()

    print(f"  📁 Files: {repo_map.file_count}")
    print(f"  📝 Lines: {repo_map.total_lines:,}")
    print(f"  🏗️  Stack: {repo_map.stack.summary}")
    print(f"  📊 Scope OK: {'✅' if repo_map.is_within_scope else '❌ Too large!'}")

    if repo_map.stack.languages:
        print("\n  Languages:")
        for lang, count in repo_map.stack.languages.items():
            print(f"    {lang}: {count} files")

    print()
    return repo_map


def run_classify(task: str, repo_path: str = None):
    """Classify a task using the intent classifier."""
    from agent.config import AgentConfig
    from agent.planning.intent import IntentClassifier

    config = AgentConfig()

    provider = None
    if config.has_api_key:
        from agent.core.factory import create_provider
        provider = create_provider(config)
        mode = "LLM"
    else:
        mode = "heuristic"

    repo_context = ""
    if repo_path and os.path.isdir(repo_path):
        from agent.planning.repo_discovery import RepoDiscovery
        # Quick scan to give LLM context on whether this is a greenfield or existing project
        repo_map = RepoDiscovery(repo_path).scan()
        repo_context = f"Project type: {repo_map.stack.summary}. Files: {repo_map.file_count}."

    classifier = IntentClassifier(provider=provider)
    result = classifier.classify(task, repo_context=repo_context)

    print(f"\n🧠 Intent Classification ({mode})\n")
    print(f"  Task:       {task}")
    print(f"  Intent:     {result.intent.value}")
    print(f"  Confidence: {result.confidence:.0%}")
    print(f"  Reasoning:  {result.reasoning}")
    if result.clarification_needed:
        print(f"  ⚠️  Clarification needed: {result.suggested_question}")
    print()
    return result


# ── Full Pipeline ────────────────────────────────────────────────

def run_full_pipeline(task: str, repo_path: str = ".", dry_run: bool = False,
                      yes: bool = False, auto_commit: bool = False,
                      rollback_on_fail: bool = True,
                      provider_name: Optional[str] = None,
                      model_name: Optional[str] = None,
                      sandbox_mode: Optional[str] = None):
    """Run the full agent pipeline for a task."""
    from agent.config import AgentConfig
    from agent.planning.intent import IntentClassifier
    from agent.state import AgentState, validate_transition
    from agent.mechanisms.risk_budget import RiskBudget
    from agent.security.preconditions import PreconditionChecker

    config = AgentConfig()
    
    # ── CLI Overrides ──
    if provider_name or model_name:
        from agent.config import LLMConfig
        object.__setattr__(config, "provider", provider_name or config.provider)
        if model_name:
            new_llm = LLMConfig(model=model_name)
            object.__setattr__(config, "llm", new_llm)

    # Initialize Sandbox
    from agent.core.sandbox import Sandbox
    mode_str = sandbox_mode or os.environ.get("AGENT_SANDBOX", "full-access")
    sandbox = Sandbox.from_string(mode_str, repo_path=repo_path)

    exec_log = ExecutionLog(repo_path)

    rollback_mgr = RollbackManager(repo_path)
    start_time = time.time()
    intent_str = ""
    files_written = 0
    llm_calls = 0
    pipeline_success = True

    # ── Header ──
    print("\n" + "=" * 60)
    print(f"🚀 GOD MODE AGENT v{VERSION}")
    print("=" * 60)

    print(f"\n📋 Task: {task}")
    print(f"📂 Repo: {os.path.abspath(repo_path)}")
    if dry_run:
        print(f"🔍 Mode: DRY RUN (plan only, no writes)")
    print()

    # ── Step 1: Governance Self-Test [REMOVED] ──
    # Self-test removed from critical path to speed up agent boot time.
    # It can still be run via `python -m agent --self-test` if needed.

    # ── Step 2: Intent Analysis ──
    print("─── Step 2: Intent Analysis ───")
    intent_result = run_classify(task, repo_path)
    intent_str = intent_result.intent.value
    exec_log.add("intent_analysis", intent_str,
                 f"confidence={intent_result.confidence:.0%}")

    # Check if clarification needed
    if intent_result.clarification_needed:
        print(f"  ⚠️  Low confidence ({intent_result.confidence:.0%}). "
              f"Consider rephrasing your task.")
        if not yes:
            try:
                answer = input("   Continue anyway? [Y/n] ").strip().lower()
                if answer and answer not in ('y', 'yes'):
                    print("   Aborted.")
                    return False
            except (EOFError, KeyboardInterrupt):
                return False
        else:
            print("  🛑 Ambiguous task aborted due to --yes (no-interaction) mode. Please rephrase your request.")
            exec_log.add("execution", "aborted", "ambiguous intent in --yes mode")
            exec_log.save()
            return False

    # ── Step 3: Precondition Checks ──
    print("─── Step 3: Precondition Checks ───")
    head = PreconditionChecker.get_git_head(repo_path)
    git_ok = head != "unknown_or_no_git"
    print(f"  Git consistency: {'✅' if git_ok else '⚠️  Not a git repo'}")
    exec_log.add("preconditions", "pass" if git_ok else "warn",
                 f"git_head={head[:7] if git_ok else 'none'}")

    # ── Step 4: Repo Discovery ──
    print("─── Step 4: Repo Discovery ───")
    repo_map = run_scan(repo_path)
    exec_log.add("repo_discovery", "pass",
                 f"files={repo_map.file_count}, lines={repo_map.total_lines}")

    # ── Step 5: Risk Budget ──
    print("─── Step 5: Risk Budget ───")
    budget = RiskBudget()
    budget.start()
    print(f"  Exhausted: {budget.is_exhausted}")
    print(f"  Violations: {len(budget.violations)}")
    exec_log.add("risk_budget", "ok", f"exhausted={budget.is_exhausted}")

    # ── Step 6: Execution ──
    if not config.has_api_key:
        print("\n⚠️  No API key — skipping code generation")
        exec_log.add("execution", "skipped", "no API key")
        exec_log.save()
        return True

    print("─── Step 6: Task Execution ───")
    from agent.core.provider_factory import create_provider
    from agent.core.task_executor import TaskExecutor
    
    provider = create_provider(config)
    executor = TaskExecutor(provider, repo_path)
    
    # Use the same mode evaluated at the start of the function
    sandbox_mode_eval = sandbox_mode or os.environ.get("AGENT_SANDBOX", "full-access")
    sandbox = Sandbox.from_string(sandbox_mode_eval, repo_path=repo_path)

    def _display_plan(plan):
        if plan.is_ambiguous:
            print(f"\n❓ CLARIFICATION NEEDED")
            print(f"─" * 40)
            print(f"I need more information before I can build a solid plan:")
            for i, q in enumerate(plan.questions, 1):
                print(f"   {i}. {q}")
            print(f"\n💡 My best guess assumption:")
            print(f"   {plan.best_guess_scenario}")
            print(f"─" * 40)
            return

        print(f"\n📋 Plan: {plan.summary}")
        if plan.dependencies:
            print(f"📦 Dependencies: {', '.join(plan.dependencies)}")
        print(f"📂 Files ({len(plan.files)}):")
        for f in plan.files:
            print(f"   [{f.action.upper()}] {f.path} — {f.description}")
        if plan.run_command:
            print(f"🏃 Run: {plan.run_command}")
        if plan.test_command:
            print(f"🧪 Test: {plan.test_command}")

    if dry_run:
        plan = executor.execute(task, intent=intent_str)
        _display_plan(plan)
        if plan.is_ambiguous:
            print("\n🔍 Stopping because task is ambiguous.")
            return False
        print("\n🔍 Dry run complete — no files written.")
        llm_calls = provider.call_count
        exec_log.add("execution", "dry_run", f"{len(plan.files)} files planned")
    else:
        plan = executor.execute(task, intent=intent_str)
        
        # Phase 59: Handle ambiguity in interactive loop (Now bypassed for autonomy)
        while plan.is_ambiguous:
            print("\n⚠️  Ambiguous task detected. Proceeding automatically with best guess.")
            executor.add_feedback(f"Proceed with your best guess: {plan.best_guess_scenario}")
            # Re-run execute with feedback
            print("🧠 Re-analyzing task with best guess...")
            plan = executor.execute(task, intent=intent_str)
            break # Ensure we break out after applying the best guess once

        # User confirmation / Interactive Feedback Loop (Bypassed in Phase 100 for autonomy)
        # We proceed directly to execution without pausing.
        pass

        # Backup files for rollback
        executor.set_rollback_manager(rollback_mgr)

        # plan = executor.execute(task, intent=intent_str) # This line was moved up
        files_written = len(plan.files)
        llm_calls = provider.call_count

        # Check if execution succeeded
        pipeline_success = executor.last_run_success
        exec_log.add("execution",
                      "complete" if pipeline_success else "failed",
                      f"files={[f.path for f in plan.files]}, "
                      f"fix_attempts={executor.fix_attempts_used}")

        # Rollback on failure
        if not pipeline_success and rollback_on_fail and rollback_mgr.has_backups:
            print("\n↩️  Rolling back changes due to failure...")
            rollback_mgr.rollback()
            exec_log.add("rollback", "executed")

        # Git commit on success
        if pipeline_success and auto_commit:
            print("\n─── Git Commit ───")
            git_auto_commit(repo_path, task)
            exec_log.add("git_commit", "done")

    # ── Summary ──
    print_summary(
        task=task, repo_path=repo_path, start_time=start_time,
        intent=intent_str, files_written=files_written,
        llm_calls=llm_calls,
        fix_attempts=getattr(executor, 'fix_attempts_used', 0) if 'executor' in dir() else 0,
        success=pipeline_success,
    )

    print("=" * 60)
    print(f"{'✅' if pipeline_success else '❌'} Pipeline "
          f"{'complete' if pipeline_success else 'failed'}")
    print("=" * 60)

    exec_log.save()
    return pipeline_success


# ── Chat Mode (Persistent) ──────────────────────────────────────

def run_chat(repo_path: str = ".", session_id: Optional[str] = None, 
             provider_name: Optional[str] = None, model_name: Optional[str] = None):
    """Persistent chat mode with conversation memory."""
    from agent.config import AgentConfig, LLMConfig
    config = AgentConfig()
    
    if provider_name or model_name:
        object.__setattr__(config, "provider", provider_name or config.provider)
        if model_name:
            new_llm = LLMConfig(model=model_name)
            object.__setattr__(config, "llm", new_llm)

    if not config.has_api_key:
        print("\n⚠️  No API key configured. Set TOGETHER_API_KEY to use chat mode.")
        return

    from agent.core.provider_factory import create_provider
    from agent.core.chat import ChatSession, ChatMessage
    from agent.core.session_store import load_session

    provider = create_provider(config)
    session = ChatSession(provider, repo_path)
    
    if session_id:
        print(f"🔄 Resuming session: {session_id}")
        history = load_session(session_id)
        # Convert dict to ChatMessage objects
        session._messages = [
            ChatMessage(role=m["role"], content=m["content"]) 
            for m in history
        ]
        session._session_id = session_id
        print(f"✅ Loaded {len(session._messages)} messages.")

    session.interactive_mode = True # Default for CLI chat
    session.loop()


def run_interactive_legacy(repo_path: str = "."):
    """Legacy interactive REPL mode (stateless)."""
    print(f"\n🎮 God Mode Agent v{VERSION} — Interactive Mode (Legacy)")
    print(f"    Repo: {os.path.abspath(repo_path)}")
    print("    Commands: 'self-test', 'scan', 'dry-run <task>', 'classify <task>', 'quit'")
    print("    Or type any task to execute it\n")

    while True:
        try:
            user_input = input("agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye! 👋")
            break

        if not user_input:
            continue
        elif user_input.lower() in ("quit", "exit", "q"):
            print("Bye! 👋")
            break
        elif user_input.lower() == "self-test":
            run_self_test()
        elif user_input.lower().startswith("scan"):
            parts = user_input.split(maxsplit=1)
            run_scan(parts[1] if len(parts) > 1 else repo_path)
        elif user_input.lower().startswith("dry-run "):
            task = user_input[8:].strip()
            if task:
                run_full_pipeline(task, repo_path=repo_path, dry_run=True, yes=True)
        elif user_input.lower().startswith("classify "):
            task = user_input[9:].strip()
            if task:
                run_classify(task)
        else:
            run_full_pipeline(user_input, repo_path=repo_path, yes=True)


def run_web_ui(repo_path: str = "."):
    """Deprecated: The Streamlit Web UI has been retired."""
    print("⚠️  The legacy Streamlit Web UI has been deprecated in favor of the Rich Terminal UI.")
    print("   Please use: god-mode chat")

def run_start():
    """Start the God Mode background server and open the browser."""
    print(f"\n🚀 Starting God Mode Daemon v{VERSION}...")
    print("⚠️  The heavy FastAPI + React Web UI is deprecated in Phase 76.")
    print("   Pivoting to Rich Terminal Interface.")
    print("   Launching interactive REPL instead...\n")
    
    # Launch chat instead of server
    run_chat()

# ── Entry Point ──────────────────────────────────────────────────

# ── Entry Point ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="god-mode",
        description=f"God Mode Agent v{VERSION} — Deterministic Dev Agent",
    )
    
    # Create subparsers for 'start' command vs positional task
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Start command
    start_parser = subparsers.add_parser("start", help="Start the God Mode server and UI")
    
    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Launch the interactive terminal chat (REPL)")
    
    # Run command (explicit passing of task)
    run_parser = subparsers.add_parser("run", help="Run a specific task")
    run_parser.add_argument("run_task", help="Task to execute")
    
    # Original arguments (we keep these on the main parser for backward compat)
    parser.add_argument(
        "task", nargs="?", default=None,
        help="Task to execute, e.g. 'Fix the bug in auth module'",
    )
    parser.add_argument(
        "--repo", metavar="PATH", default=".",
        help="Path to the target repository (default: current directory)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the plan without writing any files",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Auto-commit changes to a new git branch after success",
    )
    parser.add_argument(
        "--no-rollback", action="store_true",
        help="Don't rollback changes if execution fails",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="Run governance self-tests",
    )
    parser.add_argument(
        "--scan", metavar="DIR",
        help="Scan a directory and show RepoMap",
    )
    parser.add_argument(
        "--classify", metavar="TASK",
        help="Classify a task intent without running the full pipeline",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="Start persistent chat mode (like Copilot / Windsurf)",
    )
    parser.add_argument(
        "--ui", action="store_true",
        help=argparse.SUPPRESS, # Deprecated
    )
    parser.add_argument(
        "--legacy-repl", action="store_true",
        help="Start legacy stateless REPL mode",
    )
    parser.add_argument(
        "--provider", metavar="PROVIDER", default=None,
        help="LLM provider: together, openai, openrouter, ollama",
    )
    parser.add_argument(
        "--model", metavar="MODEL", default=None,
        help="LLM model name",
    )
    parser.add_argument(
        "--sandbox", metavar="MODE", default=None,
        help="Sandbox mode: read-only, workspace-write, full-access",
    )
    parser.add_argument(
        "--version", "-v", action="version",
        version=f"God Mode Agent v{VERSION}",
    )

    # Resume command
    resume_parser = subparsers.add_parser("resume", help="Resume a past conversation")

    args = parser.parse_args()

    args = parser.parse_args()

    if args.command == "start":
        run_start()
        sys.exit(0)
        
    # Handle explicit 'run' command
    task_to_run = args.run_task if args.command == "run" else args.task

    if args.self_test:
        success = run_self_test()
        sys.exit(0 if success else 1)
    elif args.scan:
        run_scan(args.scan)
    elif args.classify:
        run_classify(args.classify, repo_path=args.repo)
    elif args.command == "resume":
        from agent.core.session_store import print_sessions
        sid = print_sessions()
        if sid:
            run_chat(repo_path=args.repo, session_id=sid, provider_name=args.provider, model_name=args.model)
        sys.exit(0)
    elif args.command == "chat" or args.interactive:
        run_chat(repo_path=args.repo, provider_name=args.provider, model_name=args.model)
        sys.exit(0)
    elif args.ui:
        run_web_ui(repo_path=args.repo)
    elif args.legacy_repl:
        run_interactive_legacy(repo_path=args.repo)
    elif task_to_run:
        should_rollback = not args.no_rollback
        if not args.yes:
            should_rollback = False

        success = run_full_pipeline(
            task_to_run, repo_path=args.repo,
            dry_run=args.dry_run, yes=args.yes,
            auto_commit=args.commit,
            rollback_on_fail=should_rollback,
            provider_name=args.provider,
            model_name=args.model,
            sandbox_mode=args.sandbox
        )
        sys.exit(0 if success else 1)



if __name__ == "__main__":
    main()

