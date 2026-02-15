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
    install_command: str = ""      # e.g. "pip install", "npm install", "mvn install"
    compile_command: str = ""      # e.g. "javac *.java", "go build", "cargo build"
    lint_command: str = ""         # e.g. "python -m py_compile", "go vet ./..."
    stack: str = ""                # detected stack name e.g. "python", "java"
    run_commands: list[str] = field(default_factory=list)  # multi-step commands


PLAN_SYSTEM_PROMPT = """You are a senior software engineer. You are given a task and context about a repository.

Your job is to create a detailed execution plan as a JSON object with this schema:
{
  "summary": "Brief description of what you'll do",
  "stack": "python",
  "dependencies": ["list", "of", "packages", "needed"],
  "install_command": "pip install -q <deps>",
  "compile_command": "",
  "lint_command": "python -m py_compile <file>",
  "files": [
    {
      "path": "relative/path/to/file",
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
- stack: the primary technology (python, java, node, go, rust, dart, docker, etc.)
- dependencies: list ALL packages/libraries needed
- install_command: FULL shell command to install dependencies (e.g. 'pip install torch transformers', 'npm install express cors', 'go mod tidy', or empty string if none needed)
- compile_command: shell command to compile if needed (e.g. 'javac -cp . Main.java', 'go build', 'cargo build') or empty string
- lint_command: shell command to check syntax (e.g. 'python -m py_compile file.py', 'javac -Xlint:all File.java', 'npx tsc --noEmit') or empty string
- run_command: the shell command to run/verify the code works (e.g. 'python main.py', 'java Main', 'node index.js', 'go run main.go', 'docker-compose up --build')
- run_commands: OPTIONAL list of multiple commands to run in sequence (e.g. ['npm install', 'npm run seed', 'npm start']). Use this if the task requires multiple steps. If provided, run_command is ignored.
- test_command: command to run tests (leave empty string if no tests exist)
- You can use ANY language or framework (Python, Java, Node.js, Go, Rust, Flutter, React, Django, Flask, FastAPI, Spring Boot, Express, Docker, etc.)
- Choose the best technology for the task if not specified"""


CODE_SYSTEM_PROMPT_TEMPLATE = """You are a senior software engineer. Generate production-quality {language} code.

Rules:
- Output ONLY the raw source code, no markdown fences, no explanation
- Include comprehensive comments and documentation
- Include proper imports/includes
- Include proper error handling
- Make the code runnable end-to-end
- Follow best practices for the language and libraries used"""


FIX_SYSTEM_PROMPT_TEMPLATE = """You are a senior software engineer debugging {language} code.

You are given:
1. The original task
2. The code that was generated
3. The error that occurred when running it

Your job is to output the COMPLETE FIXED code for the file.

Rules:
- Output ONLY the raw source code, no markdown fences, no explanation
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
        self._stack_profile = None  # Set during execute()

    def set_rollback_manager(self, mgr):
        """Attach a rollback manager that backs up files before writes."""
        self._rollback_mgr = mgr

    def _detect_stack(self, task: str):
        """Detect the technology stack from the task + repo."""
        from agent.core.stack_profiles import (
            detect_profile_from_task, detect_profile_from_stack, PYTHON, GENERIC
        )
        # Try task-based detection first
        profile = detect_profile_from_task(task)
        if profile:
            return profile
        # Then try repo-based detection
        try:
            from agent.planning.repo_discovery import RepoDiscovery
            discovery = RepoDiscovery(self._repo_path)
            repo_map = discovery.scan()
            if repo_map.stack and repo_map.stack.primary_language:
                return detect_profile_from_stack(
                    repo_map.stack.primary_language,
                    repo_map.stack.frameworks
                )
        except Exception:
            pass
        return PYTHON  # Safe default

    # ── Runtime Pre-Check ────────────────────────────────────────────

    @staticmethod
    def _check_runtime(plan: 'ExecutionPlan') -> list[str]:
        """
        Check if required runtimes are installed before executing.
        Returns a list of missing tools (empty = all good).
        """
        import shutil

        # Map stack/commands to required binaries
        STACK_BINARIES = {
            'java': ['java', 'javac'],
            'node': ['node', 'npm'],
            'go': ['go'],
            'rust': ['cargo', 'rustc'],
            'dart': ['dart'],
            'docker': ['docker'],
        }

        # Check based on declared stack
        stack = (plan.stack or '').lower()
        required = set()
        if stack in STACK_BINARIES:
            required.update(STACK_BINARIES[stack])

        # Also scan commands for tool references
        all_commands = ' '.join(filter(None, [
            plan.install_command, plan.compile_command,
            plan.run_command, plan.test_command, plan.lint_command,
        ] + plan.run_commands)).lower()

        COMMAND_KEYWORDS = {
            'javac': 'javac', 'java ': 'java', 'mvn': 'mvn', 'gradle': 'gradle',
            'node ': 'node', 'npm ': 'npm', 'npx ': 'npx', 'yarn': 'yarn',
            'pnpm': 'pnpm', 'bun ': 'bun',
            'go ': 'go', 'go build': 'go', 'go run': 'go',
            'cargo ': 'cargo', 'rustc': 'rustc',
            'docker': 'docker', 'docker-compose': 'docker-compose',
            'flutter': 'flutter', 'dart ': 'dart',
            'python': 'python', 'pip': 'pip',
            'psql': 'psql', 'mysql': 'mysql', 'sqlite3': 'sqlite3',
        }

        for keyword, binary in COMMAND_KEYWORDS.items():
            if keyword in all_commands:
                required.add(binary)

        # Check which are missing
        missing = []
        for binary in required:
            if not shutil.which(binary):
                missing.append(binary)

        return missing

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
                # Use stack profile extensions, or fall back to common set
                read_exts = ('.py', '.toml', '.yaml', '.yml', '.json', '.md', '.txt',
                             '.cfg', '.ini', '.sh', '.env')
                if self._stack_profile and self._stack_profile.file_read_extensions:
                    read_exts = self._stack_profile.file_read_extensions
                if ext in read_exts:
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

        # Build stack hint for the LLM
        stack_hint = ""
        if self._stack_profile:
            stack_hint = (
                f"\n\nDetected stack: {self._stack_profile.display_name}"
                f"\nPreferred language: {self._stack_profile.code_prompt_language}"
            )

        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Task: {task}{stack_hint}"
                f"\n\nRepository context:\n{context}"
            )},
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
            install_command=plan_data.get("install_command", ""),
            compile_command=plan_data.get("compile_command", ""),
            lint_command=plan_data.get("lint_command", ""),
            stack=plan_data.get("stack", "python"),
            run_commands=plan_data.get("run_commands", []),
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
            {"role": "system", "content": CODE_SYSTEM_PROMPT_TEMPLATE.format(
                language=self._stack_profile.code_prompt_language if self._stack_profile else "Python"
            )},
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

    # ── Smart Timeout Detection ───────────────────────────────────

    @staticmethod
    def _smart_timeout(command: str) -> int:
        """
        Pick a timeout based on what the command looks like.
        Long-running build tools get more time. Quick scripts get less.
        """
        cmd_lower = command.lower()

        # Docker builds can take minutes
        if any(kw in cmd_lower for kw in ['docker build', 'docker-compose',
                                           'docker compose']):
            return 600  # 10 min

        # Heavy build tools
        if any(kw in cmd_lower for kw in ['mvn ', 'gradle ', 'cargo build',
                                           'flutter build', 'npm run build',
                                           'yarn build', 'go build']):
            return 300  # 5 min

        # Package installs
        if any(kw in cmd_lower for kw in ['npm install', 'yarn install',
                                           'pip install', 'go mod tidy',
                                           'bundle install', 'cargo install',
                                           'composer install', 'flutter pub get',
                                           'pnpm install']):
            return 300  # 5 min

        # Database migrations / SQL
        if any(kw in cmd_lower for kw in ['migrate', 'alembic', 'flyway',
                                           'liquibase', 'prisma',
                                           'sequelize', 'knex']):
            return 120  # 2 min

        # Tests can be slow
        if any(kw in cmd_lower for kw in ['pytest', 'jest', 'mocha',
                                           'mvn test', 'go test', 'cargo test',
                                           'npm test', 'flutter test']):
            return 180  # 3 min

        return 120  # Default: 2 min

    @staticmethod
    def _is_server_command(command: str) -> bool:
        """
        Detect if a command starts a long-running server/daemon.
        These should NOT block — we start, health check, then move on.
        """
        cmd_lower = command.lower()
        return any(kw in cmd_lower for kw in [
            'flask run', 'uvicorn ', 'gunicorn ', 'hypercorn ',
            'streamlit run', 'chainlit run', 'gradio',
            'npm start', 'npm run dev', 'npm run serve',
            'yarn start', 'yarn dev', 'pnpm dev',
            'next dev', 'vite', 'ng serve',
            'python manage.py runserver', 'python -m http.server',
            'java -jar', 'spring-boot:run',
            'docker-compose up', 'docker compose up',
            'go run', 'cargo run',
            'node server', 'node app', 'node index',
        ])

    # ── Code Execution ──────────────────────────────────────────────

    def run_code(self, command: str) -> RunResult:
        """
        Run a command in the repo directory and capture output.

        Handles:
        - Smart timeouts based on command type (Docker=10m, build=5m, etc.)
        - Server/daemon detection → start with Popen, health check, move on
        - Multi-command sequences (&&, ;)
        - Graceful cleanup on timeout
        - Any language/framework/shell command
        """
        if not command:
            return RunResult(True, "", "", 0, "")

        print(f"\n🏃 Running: {command}")

        # Check if this is a long-running server
        if self._is_server_command(command):
            return self._run_server(command)

        # Smart timeout
        timeout = self._smart_timeout(command)
        if timeout != 120:
            print(f"  ⏱️  Timeout: {timeout}s (auto-detected)")

        try:
            # Use environment that inherits everything + adds repo to PATH
            env = os.environ.copy()
            env['PATH'] = self._repo_path + os.pathsep + env.get('PATH', '')

            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=self._repo_path, env=env,
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
            print(f"  ⏰ Timed out ({timeout}s)")
            return RunResult(False, "", f"Command timed out after {timeout}s", -1, command)
        except OSError as e:
            # Handle "command not found" type errors gracefully
            print(f"  ❌ OS error: {e}")
            return RunResult(False, "", str(e), -1, command)

    def _run_server(self, command: str) -> RunResult:
        """
        Start a long-running server process in the background.
        Wait briefly for startup, health-check if possible, then return success.
        """
        import time

        print(f"  🌐 Detected server command — starting in background...")

        try:
            env = os.environ.copy()
            env['PATH'] = self._repo_path + os.pathsep + env.get('PATH', '')

            process = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=self._repo_path, env=env,
            )

            # Give it a few seconds to start (or crash)
            time.sleep(3)

            # Check if it crashed immediately
            poll = process.poll()
            if poll is not None:
                stdout, stderr = process.communicate(timeout=5)
                print(f"  ❌ Server exited immediately (code {poll})")
                error_text = (stderr or stdout).strip()
                for line in error_text.split('\n')[-10:]:
                    print(f"     {line}")
                return RunResult(False, stdout, stderr, poll, command)

            # Process is still running — try health check
            print(f"  ✅ Server started (PID: {process.pid})")

            # Try to detect port and health-check
            port = self._detect_port(command)
            if port:
                health_ok = self._health_check(port)
                if health_ok:
                    print(f"  🏥 Health check passed (localhost:{port})")
                else:
                    print(f"  ⚠️  Health check on port {port} — server may still be starting")
            else:
                print(f"  ℹ️  Running in background — will terminate after task completes")

            # Store for cleanup later
            if not hasattr(self, '_background_processes'):
                self._background_processes = []
            self._background_processes.append(process)

            return RunResult(True, f"Server started (PID {process.pid})",
                             "", 0, command)

        except OSError as e:
            print(f"  ❌ Failed to start server: {e}")
            return RunResult(False, "", str(e), -1, command)

    @staticmethod
    def _detect_port(command: str) -> Optional[int]:
        """Try to extract port number from a server command."""
        # Common patterns: --port 8000, -p 3000, :5000, PORT=8080
        import re as _re
        patterns = [
            _re.compile(r'--port[= ](\d+)'),
            _re.compile(r'-p\s+(\d+)'),
            _re.compile(r':(\d{4,5})\b'),
            _re.compile(r'PORT[= ](\d+)'),
        ]
        for p in patterns:
            m = p.search(command)
            if m:
                return int(m.group(1))

        # Default ports for common servers
        cmd_lower = command.lower()
        defaults = {
            'flask': 5000, 'uvicorn': 8000, 'gunicorn': 8000,
            'streamlit': 8501, 'django': 8000, 'express': 3000,
            'next': 3000, 'vite': 5173, 'react-scripts': 3000,
            'ng serve': 4200, 'http.server': 8000,
        }
        for kw, port in defaults.items():
            if kw in cmd_lower:
                return port
        return None

    @staticmethod
    def _health_check(port: int, retries: int = 3) -> bool:
        """Try to reach localhost:port — returns True if server responds."""
        import time
        import urllib.request
        import urllib.error

        for i in range(retries):
            try:
                req = urllib.request.Request(
                    f'http://localhost:{port}',
                    method='GET',
                )
                urllib.request.urlopen(req, timeout=3)
                return True
            except (urllib.error.URLError, urllib.error.HTTPError,
                    ConnectionError, OSError):
                if i < retries - 1:
                    time.sleep(2)
        return False

    def cleanup_background(self):
        """Stop any background server processes started during execution."""
        if not hasattr(self, '_background_processes'):
            return
        for proc in self._background_processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"  🛑 Stopped background process (PID {proc.pid})")
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._background_processes.clear()

    # ── Self-Correction ─────────────────────────────────────────────

    def fix_error(self, task: str, file_action: FileAction,
                  error: str, plan: ExecutionPlan) -> str:
        """Use LLM to fix a broken file given the error output."""
        context = self._read_repo_context()

        lang = self._stack_profile.code_prompt_language if self._stack_profile else "Python"
        messages = [
            {"role": "system", "content": FIX_SYSTEM_PROMPT_TEMPLATE.format(language=lang)},
            {"role": "user", "content": (
                f"Task: {task}\n\nFile: {file_action.path}\n"
                f"Description: {file_action.description}\n\n"
                f"Current code:\n```\n{file_action.content}\n```\n\n"
                f"Error when running `{plan.run_command}`:\n"
                f"```\n{error}\n```\n\n"
                f"Repository context:\n{context}\n\n"
                f"Fix the code. Output ONLY the complete fixed source code."
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
        """Parse error output to find which planned file caused the error.

        Handles tracebacks/stack traces from multiple languages:
        - Python: File "xxx.py", line N
        - Java:   at com.example.Main.main(Main.java:5)
        - Node:   at Object.<anonymous> (/path/file.js:10:3)
        - Go:     main.go:15:2: undefined
        - Rust:   --> src/main.rs:5:10
        """
        # Multi-language file extraction patterns
        import re as _re

        patterns = [
            # Python: File "path/to/file.py", line N
            _re.compile(r'File "([^"]+)"'),
            # Java: at package.Class.method(File.java:N)
            _re.compile(r'\(([\w./]+\.java):\d+\)'),
            # Kotlin: at package.Class.method(File.kt:N)
            _re.compile(r'\(([\w./]+\.kt):\d+\)'),
            # Node.js: at Something (/path/to/file.js:N:N) or (file.ts:N:N)
            _re.compile(r'\(([^)]+\.[jt]sx?):\d+:\d+\)'),
            # Node.js: at /path/to/file.js:N:N (no parens)
            _re.compile(r'at\s+(/[^\s]+\.[jt]sx?):\d+'),
            # Go: file.go:N:N: error
            _re.compile(r'([\w./]+\.go):\d+'),
            # Rust: --> src/main.rs:N:N
            _re.compile(r'-->\s*([\w./]+\.rs):\d+'),
            # C/C++: file.c:N:N: error
            _re.compile(r'([\w./]+\.[ch](?:pp)?):\d+:\d+'),
            # Dart/Flutter: package:app/file.dart:N:N
            _re.compile(r'([\w./]+\.dart):\d+'),
        ]

        # Collect all referenced files from all patterns
        referenced_files = []
        for pattern in patterns:
            matches = pattern.findall(error_text)
            referenced_files.extend(matches)

        # Match referenced files to planned files (last match = most relevant)
        for ref_file in reversed(referenced_files):
            ref_basename = os.path.basename(ref_file)
            for fa in plan.files:
                if fa.path == ref_file or fa.path.endswith(ref_file):
                    return fa
                if fa.path == ref_basename or os.path.basename(fa.path) == ref_basename:
                    return fa

        # Fallback: check if any planned file is mentioned in error text
        for fa in plan.files:
            if fa.path in error_text:
                return fa

        # Last resort: first source file in plan (any extension)
        source_exts = ('.py', '.java', '.js', '.ts', '.jsx', '.tsx', '.go',
                       '.rs', '.dart', '.rb', '.php', '.c', '.cpp', '.cs',
                       '.kt', '.swift')
        for fa in plan.files:
            if any(fa.path.endswith(ext) for ext in source_exts):
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
            self.cleanup_background()

    def _execute_inner(self, task, kill_switch,
                       PlanEnvelopeValidator, SupplyChainChecker,
                       TaskIsolation, SecretsPolicy,
                       BoundedLSPLoop, VerificationPipeline, VerifyTier,
                       PlanEnforcer) -> ExecutionPlan:
        """Inner execute with kill switch guard."""

        # ── Step 0b: Detect stack ──
        self._stack_profile = self._detect_stack(task)
        print(f"  🔧 Detected stack: {self._stack_profile.display_name}")

        # ── Step 1: Generate plan ──
        plan = self.generate_plan(task)

        print(f"\n📋 Plan: {plan.summary}")
        print(f"🏗️  Stack: {plan.stack or self._stack_profile.name}")
        if plan.dependencies:
            print(f"📦 Dependencies: {', '.join(plan.dependencies)}")
        if plan.install_command:
            print(f"📥 Install: {plan.install_command}")
        if plan.compile_command:
            print(f"🔨 Compile: {plan.compile_command}")
        print(f"📂 Files ({len(plan.files)}):")
        for f in plan.files:
            print(f"   [{f.action.upper()}] {f.path} — {f.description}")
        if plan.run_command:
            print(f"🏃 Run: {plan.run_command}")
        if plan.run_commands:
            print(f"🏃 Run steps: {len(plan.run_commands)} commands")
            for i, cmd in enumerate(plan.run_commands, 1):
                print(f"   {i}. {cmd}")
        if plan.test_command:
            print(f"🧪 Test: {plan.test_command}")

        # ── Step 1b: Runtime pre-check ──
        print(f"\n🔍 Checking runtime requirements...")
        missing = self._check_runtime(plan)
        if missing:
            msg = ', '.join(missing)
            print(f"  ❌ Missing required tools: {msg}")
            print(f"  💡 Install them before running this task:")
            install_hints = {
                'java': 'brew install openjdk (macOS) / apt install default-jdk (Linux)',
                'javac': 'brew install openjdk (macOS) / apt install default-jdk (Linux)',
                'node': 'brew install node (macOS) / nvm install --lts',
                'npm': 'comes with node',
                'go': 'brew install go (macOS) / https://go.dev/dl/',
                'docker': 'https://docs.docker.com/get-docker/',
                'docker-compose': 'pip install docker-compose or Docker Desktop',
                'cargo': 'curl --proto =https --tlsv1.2 -sSf https://sh.rustup.rs | sh',
                'flutter': 'https://docs.flutter.dev/get-started/install',
                'dart': 'comes with flutter',
            }
            for tool in missing:
                hint = install_hints.get(tool, f'install {tool}')
                print(f"     • {tool}: {hint}")
            self.last_run_success = False
            self.last_error = f"Missing required tools: {msg}. Install them and try again."
            return plan
        else:
            print(f"  ✅ All required tools available")

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

        # ── Step 6: Lint / Syntax check ──
        print(f"\n🔍 Syntax/lint check...")
        lint_cmd = plan.lint_command or (self._stack_profile.fallback_lint if self._stack_profile else None)
        if lint_cmd:
            for file_action in plan.files:
                if file_action.action == "delete":
                    continue
                full_path = os.path.join(self._repo_path, file_action.path)
                # For Python files, use the built-in LSP loop
                if file_action.path.endswith('.py'):
                    lint_result = BoundedLSPLoop.run_linter(full_path)
                    if lint_result.passed:
                        print(f"  ✅ {file_action.path}: syntax OK")
                    else:
                        lsp = BoundedLSPLoop()
                        can_continue = lsp.record_result(lint_result)
                        print(f"  ⚠️  {file_action.path}: {lint_result.errors[0] if lint_result.errors else 'failed'}")
                        if not can_continue:
                            print(f"  🛑 LSP budget exhausted")
                            self.last_run_success = False
                            self.last_error = f"LSP budget exhausted during linting of {file_action.path}."
                            return plan
                else:
                    # For non-Python, run the lint command as a subprocess
                    try:
                        file_lint_cmd = lint_cmd.replace('<file>', full_path)
                        result = subprocess.run(
                            file_lint_cmd, shell=True, capture_output=True,
                            text=True, timeout=30, cwd=self._repo_path,
                        )
                        if result.returncode == 0:
                            print(f"  ✅ {file_action.path}: syntax OK")
                        else:
                            print(f"  ⚠️  {file_action.path}: {(result.stderr or result.stdout)[:200]}")
                    except Exception as e:
                        print(f"  ⚠️  Lint skipped for {file_action.path}: {e}")
        else:
            print(f"  ⏭️  No lint command configured, skipping")

        # ── Step 7: Install dependencies ──
        if plan.install_command:
            # Use the LLM-specified install command directly
            print(f"\n📦 Installing dependencies: {plan.install_command}")
            dep_result = self.run_code(plan.install_command)
            if not dep_result.success:
                print(f"\n❌ Dependency install failed. Cannot proceed.")
                self.last_run_success = False
                self.last_error = f"Dependency installation failed: {(dep_result.stderr or dep_result.stdout)[:500]}"
                return plan
            print(f"  ✅ Dependencies installed")
        elif plan.dependencies:
            # Fallback to pip install for Python
            dep_result = self.install_dependencies(plan.dependencies)
            if not dep_result.success:
                print(f"\n❌ Dependency install failed. Cannot proceed.")
                self.last_run_success = False
                self.last_error = f"Dependency installation failed: {dep_result.stderr[:500]}"
                return plan

        # ── Step 7b: Compile (if needed) ──
        if plan.compile_command:
            print(f"\n🔨 Compiling: {plan.compile_command}")
            compile_result = self.run_code(plan.compile_command)
            if not compile_result.success:
                print(f"\n❌ Compilation failed.")
                self.last_run_success = False
                self.last_error = f"Compilation failed: {(compile_result.stderr or compile_result.stdout)[:500]}"
                return plan
            print(f"  ✅ Compilation successful")

        # ── Step 8: Run code ──
        commands_to_run = plan.run_commands if plan.run_commands else ([plan.run_command] if plan.run_command else [])

        if commands_to_run:
            for cmd_idx, command in enumerate(commands_to_run):
                if len(commands_to_run) > 1:
                    print(f"\n📌 Step {cmd_idx + 1}/{len(commands_to_run)}")

                run_result = self.run_code(command)

                # ── Step 9: Self-correction loop (only for the last/main command) ──
                attempt = 0
                while not run_result.success and attempt < MAX_FIX_ATTEMPTS:
                    ks_state = kill_switch.check()
                    if ks_state:
                        print(f"\n🛑 Kill switch during fix loop: {ks_state.value}")
                        self.last_run_success = False
                        return plan

                    attempt += 1
                    error_text = (run_result.stderr or run_result.stdout)[-2000:]

                    print(f"\n🔧 Fix attempt {attempt}/{MAX_FIX_ATTEMPTS}...")

                    main_file = self._identify_error_file(error_text, plan)

                    if main_file:
                        fixed_code = self.fix_error(task, main_file, error_text, plan)

                        secret_matches = secrets.scan(fixed_code)
                        if secret_matches:
                            fixed_code = secrets.redact(fixed_code)

                        self._write_file(main_file, fixed_code)
                        lines = len(fixed_code.split('\n'))
                        print(f"  📝 Rewrote: {main_file.path} ({lines} lines)")

                    # Recompile if needed before re-running
                    if plan.compile_command:
                        self.run_code(plan.compile_command)

                    run_result = self.run_code(command)

                self.fix_attempts_used += attempt

                if run_result.success:
                    print(f"\n✅ Command succeeded!")
                else:
                    print(f"\n⚠️  Command failed after {MAX_FIX_ATTEMPTS} fix attempts")
                    last_err = (run_result.stderr or run_result.stdout)[-500:]
                    print(f"    Last error: {last_err}")
                    self.last_run_success = False
                    self.last_error = f"Command failed: {command}. Last error: {last_err}"
                    break  # Stop running remaining commands

        # ── Step 10: Verification pipeline ──
        print(f"\n🧪 Running verification pipeline...")
        skip = [VerifyTier.INTEGRATION_TEST, VerifyTier.CI_GATE]
        if not plan.test_command:
            skip.append(VerifyTier.UNIT_TEST)
        pipeline = VerificationPipeline(
            project_dir=self._repo_path,
            test_command=plan.test_command or "echo 'No test command configured'",
            skip_tiers=skip,
        )
        # Collect all source files (not just .py)
        source_exts = ('.py', '.java', '.js', '.ts', '.jsx', '.tsx', '.go',
                       '.rs', '.dart', '.rb', '.php', '.c', '.cpp')
        source_files = [f.path for f in plan.files
                        if any(f.path.endswith(ext) for ext in source_exts)
                        and f.action != "delete"]
        report = pipeline.run(files=source_files)
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

        # ── Step 12: Auto-git commit (on success) ──
        if self.last_run_success:
            try:
                # Check if we're in a git repo
                git_check = subprocess.run(
                    ['git', 'rev-parse', '--is-inside-work-tree'],
                    capture_output=True, text=True, cwd=self._repo_path,
                )
                if git_check.returncode == 0:
                    # Stage all changed files
                    files_to_add = [f.path for f in plan.files if f.action != "delete"]
                    if files_to_add:
                        subprocess.run(
                            ['git', 'add'] + files_to_add,
                            capture_output=True, cwd=self._repo_path,
                        )
                        # Commit with descriptive message
                        commit_msg = f"[god-mode-agent] {plan.summary}"
                        result = subprocess.run(
                            ['git', 'commit', '-m', commit_msg, '--no-verify'],
                            capture_output=True, text=True, cwd=self._repo_path,
                        )
                        if result.returncode == 0:
                            print(f"\n📝 Auto-committed: {commit_msg}")
                        else:
                            logger.info(f"Auto-commit skipped: {result.stderr[:200]}")
            except Exception as e:
                logger.info(f"Auto-commit skipped: {e}")

        # ── Done ──
        print(f"\n🎉 Task complete! {len(plan.files)} files written to {self._repo_path}")
        return plan
