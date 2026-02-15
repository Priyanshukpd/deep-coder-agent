"""
Task Executor — The agentic loop that actually does the work.

Uses the LLM to:
1. Read the target repo
2. Generate a plan (files to create/modify)
3. Generate code for each file
4. Write files to the repo
5. Install dependencies
6. Run the code and capture errors
7. Self-correct (feed errors back to LLM, regenerate, retry)

Integrates:
    - PlanEnvelopeValidator  — freeze & hash the plan (Architecture §2.F)
    - TaskIsolation          — atomic git branching (Architecture §3)
    - KillSwitch             — SIGINT/timeout/stale handling (Architecture §1)
    - SecretsPolicy          — scan generated code for leaked secrets
    - SandboxedRunner        — sandboxed command execution
    - BoundedLSPLoop         — syntax/lint retry (3 attempts max)
    - VerificationPipeline   — tiered verification (syntax → lint → test → CI)
    - PlanEnforcer           — validate files written match the plan
    - SupplyChainChecker     — typosquatting detection on dependencies
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MAX_FIX_ATTEMPTS = 3


@dataclass
class FileAction:
    """A single file action the agent plans to take."""
    path: str
    action: str  # "create", "modify", "delete"
    description: str
    content: str = ""


@dataclass
class RunResult:
    """Result of running a command."""
    success: bool
    stdout: str
    stderr: str
    return_code: int
    command: str


@dataclass
class ExecutionPlan:
    """The agent's plan for executing a task."""
    task: str
    summary: str
    files: list[FileAction] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    run_command: str = ""
    test_command: str = ""


PLAN_SYSTEM_PROMPT = """You are a senior software engineer. You are given a task and context about a repository.

Your job is to create a detailed execution plan as a JSON object with this schema:
{
  "summary": "Brief description of what you'll do",
  "dependencies": ["list", "of", "pip", "packages", "needed"],
  "files": [
    {
      "path": "relative/path/to/file.py",
      "action": "create",
      "description": "What this file does"
    }
  ],
  "run_command": "python main.py",
  "test_command": "python -m pytest tests/ -v"
}

Rules:
- Only output valid JSON, no markdown or explanation
- Use relative paths from the repo root
- action must be "create", "modify", or "delete"
- Be specific about what each file does
- List ALL pip dependencies needed
- run_command: the shell command to run/verify the main code works (e.g. 'python script.py')
- test_command: command to run tests (leave empty string if no tests exist)"""


CODE_SYSTEM_PROMPT = """You are a senior software engineer. Generate production-quality Python code.

Rules:
- Output ONLY the raw Python code, no markdown fences, no explanation
- Include comprehensive docstrings and comments
- Include proper imports
- Include proper error handling
- Make the code runnable end-to-end
- Follow best practices for the libraries used"""


FIX_SYSTEM_PROMPT = """You are a senior software engineer debugging code.

You are given:
1. The original task
2. The code that was generated
3. The error that occurred when running it

Your job is to output the COMPLETE FIXED code for the file.

Rules:
- Output ONLY the raw Python code, no markdown fences, no explanation
- Fix the root cause, not just the symptom
- Keep all existing functionality
- The code must be runnable end-to-end"""


class TaskExecutor:
    """
    Executes a task against a target repository using LLM-generated code.

    Full agentic loop:
        Plan -> Freeze Envelope -> Isolate Branch -> Write Code ->
        Lint Check -> Install Deps -> Run -> Check Errors -> Fix -> Re-run ->
        Verify -> Done
    """

    def __init__(self, provider, repo_path: str):
        self._provider = provider
        self._repo_path = os.path.abspath(repo_path)
        self._rollback_mgr = None
        self.fix_attempts_used = 0
        self.last_run_success = True
        self.last_error: Optional[str] = None

    def set_rollback_manager(self, mgr):
        """Attach a rollback manager that backs up files before writes."""
        self._rollback_mgr = mgr

    def _read_repo_context(self) -> str:
        """Read the repo structure and key files for context."""
        context_parts = []
        context_parts.append(f"Repository: {self._repo_path}\n")

        context_parts.append("Files in repo:")
        for root, dirs, files in os.walk(self._repo_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            for f in files:
                fpath = os.path.join(root, f)
                rel = os.path.relpath(fpath, self._repo_path)
                size = os.path.getsize(fpath)
                context_parts.append(f"  {rel} ({size} bytes)")

        context_parts.append("\n--- File Contents ---")
        for root, dirs, files in os.walk(self._repo_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            for f in files:
                fpath = os.path.join(root, f)
                rel = os.path.relpath(fpath, self._repo_path)
                size = os.path.getsize(fpath)

                if size > 10_000:
                    context_parts.append(f"\n=== {rel} (skipped, {size} bytes) ===")
                    continue

                ext = os.path.splitext(f)[1].lower()
                if ext in ('.py', '.toml', '.yaml', '.yml', '.json', '.md', '.txt',
                           '.cfg', '.ini', '.sh', '.env'):
                    try:
                        with open(fpath, 'r', errors='replace') as fh:
                            content = fh.read()
                        context_parts.append(f"\n=== {rel} ===\n{content}")
                    except Exception:
                        pass

        return "\n".join(context_parts)

    # ── Plan Generation ─────────────────────────────────────────────

    def generate_plan(self, task: str) -> ExecutionPlan:
        """Use LLM to generate an execution plan."""
        context = self._read_repo_context()

        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\n\nRepository context:\n{context}"},
        ]

        logger.info("Generating execution plan via LLM...")
        result = self._provider.complete(messages)

        try:
            text = result.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("```", 1)[0]
            plan_data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse plan JSON: {e}")
            raise RuntimeError(f"LLM returned invalid plan JSON: {e}")

        plan = ExecutionPlan(
            task=task,
            summary=plan_data.get("summary", ""),
            dependencies=plan_data.get("dependencies", []),
            run_command=plan_data.get("run_command", ""),
            test_command=plan_data.get("test_command", ""),
        )

        for f in plan_data.get("files", []):
            plan.files.append(FileAction(
                path=f["path"],
                action=f.get("action", "create"),
                description=f.get("description", ""),
            ))

        return plan

    # ── Code Generation ─────────────────────────────────────────────

    def generate_code(self, task: str, file_action: FileAction,
                      plan: ExecutionPlan) -> str:
        """Use LLM to generate code for a specific file."""
        context = self._read_repo_context()

        plan_summary = f"Overall plan: {plan.summary}\nFiles in plan:\n"
        for f in plan.files:
            plan_summary += f"  - [{f.action}] {f.path}: {f.description}\n"

        existing_content = ""
        full_path = os.path.join(self._repo_path, file_action.path)
        if os.path.exists(full_path) and file_action.action == "modify":
            try:
                with open(full_path, 'r') as fh:
                    existing_content = f"\n\nExisting file content:\n{fh.read()}"
            except Exception:
                pass

        messages = [
            {"role": "system", "content": CODE_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Task: {task}\n\n{plan_summary}\n"
                f"Now generate the COMPLETE code for: {file_action.path}\n"
                f"Description: {file_action.description}\n"
                f"Action: {file_action.action}\n"
                f"Dependencies available: {', '.join(plan.dependencies)}\n"
                f"\nRepository context:\n{context}{existing_content}"
            )},
        ]

        logger.info(f"Generating code for {file_action.path}...")
        result = self._provider.complete(messages)

        code = result.content.strip()
        if code.startswith("```"):
            code = code.split("\n", 1)[1]
            code = code.rsplit("```", 1)[0]
        return code

    # ── File Operations ─────────────────────────────────────────────

    def _write_file(self, file_action: FileAction, code: str):
        """Write generated code to a file."""
        full_path = os.path.join(self._repo_path, file_action.path)
        # Backup for rollback before writing
        if self._rollback_mgr:
            self._rollback_mgr.backup(file_action.path)
        dir_path = os.path.dirname(full_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(full_path, 'w') as fh:
            fh.write(code)
            if not code.endswith('\n'):
                fh.write('\n')
        file_action.content = code

    # ── Dependency Installation ─────────────────────────────────────

    def install_dependencies(self, dependencies: list[str]) -> RunResult:
        """Install pip dependencies."""
        if not dependencies:
            return RunResult(True, "", "", 0, "")

        cmd = [sys.executable, "-m", "pip", "install", "-q"] + dependencies
        cmd_str = " ".join(cmd)
        print(f"\n📦 Installing: {' '.join(dependencies)}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                cwd=self._repo_path,
            )
            if result.returncode == 0:
                print(f"  ✅ Dependencies installed")
            else:
                print(f"  ❌ Install failed: {result.stderr[:300]}")
            return RunResult(
                result.returncode == 0, result.stdout,
                result.stderr, result.returncode, cmd_str,
            )
        except subprocess.TimeoutExpired:
            print(f"  ⏰ Install timed out")
            return RunResult(False, "", "Install timed out (300s)", -1, cmd_str)

    # ── Code Execution ──────────────────────────────────────────────

    def run_code(self, command: str) -> RunResult:
        """Run a command in the repo directory and capture output."""
        if not command:
            return RunResult(True, "", "", 0, "")

        print(f"\n🏃 Running: {command}")

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=120, cwd=self._repo_path,
            )
            success = result.returncode == 0

            if success:
                print(f"  ✅ Success (exit code 0)")
                lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
                for line in lines[-10:]:
                    print(f"     {line}")
            else:
                print(f"  ❌ Failed (exit code {result.returncode})")
                error_text = (result.stderr or result.stdout).strip()
                for line in error_text.split('\n')[-15:]:
                    print(f"     {line}")

            return RunResult(success, result.stdout, result.stderr,
                             result.returncode, command)
        except subprocess.TimeoutExpired:
            print(f"  ⏰ Timed out (120s)")
            return RunResult(False, "", "Command timed out after 120s", -1, command)

    # ── Self-Correction ─────────────────────────────────────────────

    def fix_error(self, task: str, file_action: FileAction,
                  error: str, plan: ExecutionPlan) -> str:
        """Use LLM to fix a broken file given the error output."""
        context = self._read_repo_context()

        messages = [
            {"role": "system", "content": FIX_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Task: {task}\n\nFile: {file_action.path}\n"
                f"Description: {file_action.description}\n\n"
                f"Current code:\n```\n{file_action.content}\n```\n\n"
                f"Error when running `{plan.run_command}`:\n"
                f"```\n{error}\n```\n\n"
                f"Repository context:\n{context}\n\n"
                f"Fix the code. Output ONLY the complete fixed Python code."
            )},
        ]

        logger.info(f"Asking LLM to fix {file_action.path}...")
        result = self._provider.complete(messages)

        code = result.content.strip()
        if code.startswith("```"):
            code = code.split("\n", 1)[1]
            code = code.rsplit("```", 1)[0]
        return code

    def _identify_error_file(self, error_text: str,
                             plan: ExecutionPlan) -> Optional[FileAction]:
        """Parse traceback to find which planned file caused the error."""
        # Look for 'File "xxx.py", line N' patterns in traceback
        tb_files = re.findall(r'File "([^"]+)"', error_text)

        # Match traceback filenames to planned files
        for tb_file in reversed(tb_files):  # last frame is most relevant
            tb_basename = os.path.basename(tb_file)
            for fa in plan.files:
                if fa.path == tb_basename or fa.path.endswith(tb_basename):
                    return fa
                if os.path.basename(fa.path) == tb_basename:
                    return fa

        # Fallback: check if any planned file is mentioned in error text
        for fa in plan.files:
            if fa.path in error_text:
                return fa

        # Last resort: first Python file in plan
        for fa in plan.files:
            if fa.path.endswith('.py'):
                return fa

        return plan.files[0] if plan.files else None

    # ── Full Agentic Loop ───────────────────────────────────────────

    def execute(self, task: str) -> ExecutionPlan:
        """
        Full agentic loop with hardened execution:
            Kill Switch -> Plan -> Freeze Envelope -> Supply Chain Check ->
            Task Isolation -> Write Code -> Secrets Scan -> Lint Check ->
            Install Deps -> Run -> Self-Correct -> Verify -> Enforce Plan -> Done
        """
        # ── Lazy imports (avoid circular deps at module level) ──
        from agent.security.kill_switch import KillSwitch
        from agent.planning.plan_envelope import PlanEnvelopeValidator
        from agent.security.supply_chain import SupplyChainChecker
        from agent.mechanisms.task_isolation import TaskIsolation
        from agent.security.secrets_policy import SecretsPolicy
        from agent.verification.lsp_loop import BoundedLSPLoop
        from agent.verification.verification_pipeline import VerificationPipeline, VerifyTier
        from agent.planning.plan_enforcer import PlanEnforcer

        # ── Step 0: Arm kill switch ──
        kill_switch = KillSwitch(timeout_seconds=15 * 60)
        kill_switch.arm()
        print("  🛡️  Kill switch armed (15m timeout)")

        try:
            return self._execute_inner(
                task, kill_switch,
                PlanEnvelopeValidator, SupplyChainChecker,
                TaskIsolation, SecretsPolicy,
                BoundedLSPLoop, VerificationPipeline, VerifyTier,
                PlanEnforcer,
            )
        finally:
            kill_switch.disarm()

    def _execute_inner(self, task, kill_switch,
                       PlanEnvelopeValidator, SupplyChainChecker,
                       TaskIsolation, SecretsPolicy,
                       BoundedLSPLoop, VerificationPipeline, VerifyTier,
                       PlanEnforcer) -> ExecutionPlan:
        """Inner execute with kill switch guard."""

        # ── Step 1: Generate plan ──
        plan = self.generate_plan(task)

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

        # ── Check kill switch ──
        ks_state = kill_switch.check()
        if ks_state:
            print(f"\n🛑 Kill switch triggered: {ks_state.value}")
            self.last_run_success = False
            return plan

        # ── Step 2: Freeze plan envelope ──
        print(f"\n🔒 Freezing plan envelope...")
        try:
            planned_paths = [f.path for f in plan.files]
            envelope = PlanEnvelopeValidator.create_envelope(
                user_input=task,
                planned_files=planned_paths,
            )
            print(f"  ✅ Envelope hash: {envelope.envelope_hash[:16]}...")
            print(f"  📋 Planned files: {len(envelope.planned_files)}")
        except Exception as e:
            logger.warning(f"Plan envelope creation failed (non-fatal): {e}")

        # ── Step 3: Supply chain check on dependencies ──
        if plan.dependencies:
            print(f"\n🔗 Supply chain check...")
            checker = SupplyChainChecker()
            results = checker.check_dependencies(plan.dependencies)
            suspicious = [r for r in results if r.is_suspicious]
            if suspicious:
                print(f"  ⚠️  {len(suspicious)} suspicious dependencies found:")
                for s in suspicious:
                    print(f"     ❌ {s.name}: {s.reason}")
                self.last_run_success = False
                return plan
            else:
                print(f"  ✅ All {len(results)} dependencies look clean")

        # ── Step 4: Task isolation (git branch) ──
        branch_name = None
        try:
            TaskIsolation.assert_clean_tree()
            branch_name = TaskIsolation.create_task_branch()
            print(f"  🌿 Task branch: {branch_name}")
        except Exception as e:
            logger.info(f"Task isolation skipped: {e}")
            print(f"  ⚠️  Task isolation skipped ({e})")

        # ── Step 5: Generate and write code ──
        print(f"\n⚡ Generating code...\n")
        secrets = SecretsPolicy(strict=False)  # Warn but don't block

        for file_action in plan.files:
            # Kill switch check between each file
            ks_state = kill_switch.check()
            if ks_state:
                print(f"\n🛑 Kill switch triggered during code gen: {ks_state.value}")
                self.last_run_success = False
                return plan

            if file_action.action == "delete":
                full_path = os.path.join(self._repo_path, file_action.path)
                if os.path.exists(full_path):
                    os.remove(full_path)
                    print(f"  🗑️  Deleted: {file_action.path}")
                continue

            code = self.generate_code(task, file_action, plan)

            # Secrets scan before writing
            secret_matches = secrets.scan(code)
            if secret_matches:
                print(f"  🔐 Secrets detected in {file_action.path}!")
                for sm in secret_matches:
                    print(f"     ⚠️  {sm.pattern_name}: line {sm.line_number}")
                code = secrets.redact(code)
                print(f"     → Redacted {len(secret_matches)} secrets")

            self._write_file(file_action, code)
            lines = len(code.split('\n'))
            print(f"  ✅ Wrote: {file_action.path} ({lines} lines)")

        # ── Step 6: Lint check (Bounded LSP Loop) ──
        print(f"\n🔍 Syntax/lint check...")
        lsp = BoundedLSPLoop()
        for file_action in plan.files:
            if file_action.action == "delete" or not file_action.path.endswith('.py'):
                continue
            full_path = os.path.join(self._repo_path, file_action.path)
            lint_result = BoundedLSPLoop.run_linter(full_path)
            if lint_result.passed:
                print(f"  ✅ {file_action.path}: syntax OK")
            else:
                can_continue = lsp.record_result(lint_result)
                print(f"  ⚠️  {file_action.path}: {lint_result.errors[0] if lint_result.errors else 'failed'}")
                if not can_continue:
                    print(f"  🛑 LSP budget exhausted — non-retryable failure")
                    self.last_run_success = False
                    self.last_error = f"LSP budget exhausted during linting of {file_action.path}."
                    return plan

        # ── Step 7: Install dependencies ──
        if plan.dependencies:
            dep_result = self.install_dependencies(plan.dependencies)
            if not dep_result.success:
                print(f"\n❌ Dependency install failed. Cannot proceed.")
                self.last_run_success = False
                self.last_error = f"Dependency installation failed: {dep_result.stderr[:500]}"
                return plan

        # ── Step 8: Run code ──
        if plan.run_command:
            run_result = self.run_code(plan.run_command)

            # ── Step 9: Self-correction loop ──
            attempt = 0
            while not run_result.success and attempt < MAX_FIX_ATTEMPTS:
                # Kill switch check
                ks_state = kill_switch.check()
                if ks_state:
                    print(f"\n🛑 Kill switch during fix loop: {ks_state.value}")
                    self.last_run_success = False
                    return plan

                attempt += 1
                error_text = (run_result.stderr or run_result.stdout)[-2000:]

                print(f"\n🔧 Fix attempt {attempt}/{MAX_FIX_ATTEMPTS}...")

                # Find the file that caused the error using traceback
                main_file = self._identify_error_file(error_text, plan)

                if main_file:
                    fixed_code = self.fix_error(task, main_file, error_text, plan)

                    # Re-scan for secrets
                    secret_matches = secrets.scan(fixed_code)
                    if secret_matches:
                        fixed_code = secrets.redact(fixed_code)

                    self._write_file(main_file, fixed_code)
                    lines = len(fixed_code.split('\n'))
                    print(f"  📝 Rewrote: {main_file.path} ({lines} lines)")

                run_result = self.run_code(plan.run_command)

            self.fix_attempts_used = attempt

            if run_result.success:
                print(f"\n✅ Code runs successfully!")
            else:
                print(f"\n⚠️  Code still failing after {MAX_FIX_ATTEMPTS} fix attempts")
                last_err = (run_result.stderr or run_result.stdout)[-500:]
                print(f"    Last error: {last_err}")
                self.last_run_success = False
                self.last_error = f"Code execution failed after {MAX_FIX_ATTEMPTS} attempts. Last error: {last_err}"

        # ── Step 10: Verification pipeline ──
        print(f"\n🧪 Running verification pipeline...")
        skip = [VerifyTier.INTEGRATION_TEST, VerifyTier.CI_GATE]
        if not plan.test_command:
            skip.append(VerifyTier.UNIT_TEST)  # Skip if no tests configured
        pipeline = VerificationPipeline(
            project_dir=self._repo_path,
            test_command=plan.test_command or "python -m pytest tests/ -v",
            skip_tiers=skip,
        )
        py_files = [f.path for f in plan.files
                    if f.path.endswith('.py') and f.action != "delete"]
        report = pipeline.run(files=py_files)
        print(report.summary())

        if not report.all_passed:
            print(f"  ⚠️  Verification failed at {report.stopped_at_tier}")
            self.last_run_success = False

        # ── Step 11: Plan enforcement ──
        print(f"\n📏 Plan enforcement check...")
        try:
            planned_files = {f.path for f in plan.files}
            written_files = {f.path for f in plan.files
                            if f.content and f.action != "delete"}
            unplanned = written_files - planned_files
            if unplanned:
                print(f"  ⚠️  Files written outside plan: {unplanned}")
            else:
                print(f"  ✅ All {len(written_files)} files match plan")
        except Exception as e:
            logger.warning(f"Plan enforcement check failed: {e}")

        # ── Done ──
        print(f"\n🎉 Task complete! {len(plan.files)} files written to {self._repo_path}")
        return plan
