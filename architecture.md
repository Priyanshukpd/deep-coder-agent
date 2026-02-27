# The "God Mode" Agent — Architecture v11.1 (Production)

**Philosophy:** "Mission Orthogonality. Federated Extensibility. Selective Verification. Zero-Latency Resilience."
**Status:** **Build Target (Final - Sealed)**

---

## 1. The "Precision" State Machine

### The Invariants Table (v7.5.1.1)

| State                   | **Entry Invariant**                          | **Exit Invariant**                | **Hard Constraints**                                                                                     |
| ----------------------- | -------------------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------- |
| **INTENT_ANALYSIS**     | User Input exists.                           | `Intent` classified.              | **Read-Only**. **Express Lane Guard:** No shebang/chmod changes.                                         |
| **REPO_DISCOVERY**      | Intent is Valid.                             | `RepoMap` built.                  | **Read-Only**. file_count > `MAX_FILE_CAP` (Def: 50) -> **FAIL(ScopeTooLarge)**.                         |
| **PLANNING**            | `RepoMap` exists.                            | `PlanEnvelope` Signed & Frozen.   | **Input Snapshot Hashed**. **Freeze Plan BEFORE Branch**.                                                |
| **PROVING_GROUND**      | `PlanEnvelope` exists. Intent != `REFACTOR`. | **TEST FAILED (AssertionError)**. | **Strict TDD Binding**. Must import SUT.                                                                 |
| **IMPLEMENTING**        | `TestFile` exists.                           | **Linter PASS**.                  | **Retries:** 3 (Syntax/Lint only). Logic fail = STOP. **No Network**. **No Dep Install**.                |
| **VERIFYING**           | Linter Passed.                               | **TEST PASSED**. CI Green.        | **Drift Check:** `branch_base_sha` == `origin/main`. **TDD Check:** Test Code Unmodified. **Dep Check.** |
| **FEEDBACK_WAIT**       | CI Green.                                    | `APPROVAL_SIGNATURE` exists.      | **Timeout:** 24h.                                                                                        |
| **COMPLETE**            | Approval Signed.                             | Merge Commit recorded.            | **Optimistic Merge:** `ci_sha` == `branch_head` AND `status` == SUCCESS (Latest Run).                    |
| **FAILED_BY_STALE**     | `main` moved during task.                    | Terminal State.                   | **No Retry**. New invocation required.                                                                   |
| **FAILED_BY_INTERRUPT** | `SIGINT` / User Stop.                        | Terminal State.                   | **Audit Distinct**. Stops auto-retry.                                                                    |
| **FAILED_BY_TIMEOUT**   | Runtime > 15m.                               | Terminal State.                   | **Hard Stop**. SIGTERM -> Log -> Exit.                                                                   |
| **FAILED_BY_SCOPE**     | `RepoMap` > `MAX_FILE_CAP`.                  | Terminal State.                   | **Hard Stop**. Require narrower prompt.                                                                  |

---

## 2. Key Mechanisms (Kernel Hardened)

### A. Kernel-Level Guard (Invariant Re-Validation)

**Global Transition Logic**

1.  **Atomic Check:** Before any transition `Current -> Next`:
    ```python
    if not (check_invariant(Current.Exit) and check_invariant(Next.Entry)):
        abort_transition() # Race condition protection
    ```

### B. Immutable Inputs Snapshot (Forensic Integrity)

**Location:** `PLANNING` start

1.  **Hash:** `input_snapshot_hash = sha256(user_input + repo_map + base_tree_hash + toolchain_manifest_hash)`.
2.  **Toolchain Manifest:** Logs versions of Python, Node, Linter, CI Image.
3.  **Verify:** Replay runs must match this hash exactly.

### C. Non-Determinism Budget

**Global Constraint**

1.  **Policy:** `temperature=0`, `top_p` fixed.
2.  **Audit:** Log `sampling_policy_hash`.
3.  **Enforcement:** System prompt mutation mid-run is FORBIDDEN.

### D. Atomic State Transitions & Operational Rollback

**Global Invariant**

1.  **Atomicity:** Tool failure -> State Rollback. No partial transitions.
2.  **Operational Rollback Protocol:**
    - **Before Branch Creation:** No action (Filesystem clean).
    - **After Branch Creation / During IMPLEMENTING:** `git reset --hard HEAD` (Reset to last clean commit inside task branch).
    - **Locks:** Force release all Redis locks.

### E. Merge Security (CI Binding)

**Location:** `VERIFYING` -> `COMPLETE`

1.  **Optimistic Guard:** `origin/main` SHA must match `branch_base_sha`.
2.  **CI Guard:** `ci_validated_sha` must match `branch_head_sha`.
3.  **Status Guard:** CI Status must be `SUCCESS`.
4.  **Freshness:** CI run must be the **LATEST** execution for that SHA. Stale/Cached green runs from previous pushes are invalid if re-run.

### F. Plan Envelope Validator (Scope Security)

**Location:** `PLANNING` & `IMPLEMENTING`

1.  **Precondition: Clean Working Tree:**
    - `git status --porcelain` must be empty.
    - Excludes ignored/untracked files (unless `.gitignore` itself is modified).
    - If dirty -> ABORT immediately.
2.  **Ordering:** `RepoMap` -> `Plan` -> `Hash` -> **THEN** `Task Isolation`.
3.  **Immutability:** `plan_envelope_hash` is immutable.
4.  **Scope Enforcement:**
    - Reject created/deleted files outside plan.
    - **Dependency Freeze:**
      - Compute `lockfile_hash` at `PLANNING`.
      - Re-compute at `VERIFYING`.
      - If mismatch -> FAIL.
    - **No Implicit Install:** Block `pip install`, `npm install`.
5.  **Policy Constants:**
    - `MAX_FILE_CAP`: Default 50 (Configurable).

---

## 3. Multi-Stack Execution Engine (v8.0)

### A. Stack Detection (`stack_profiles.py`)

**Design:** LLM-driven, not hardcoded. Stack profiles are **hints**, not rigid definitions.

| Profile      | Language              | Extensions                   | Fallback Lint           |
| ------------ | --------------------- | ---------------------------- | ----------------------- |
| Python       | Python                | `.py`, `.toml`, `.cfg`       | `python -m py_compile`  |
| Java         | Java                  | `.java`, `.xml`, `.gradle`   | `javac -Xlint:all`      |
| Node.js      | JavaScript/TypeScript | `.js`, `.ts`, `.jsx`, `.tsx` | `npx tsc --noEmit`      |
| Go           | Go                    | `.go`, `.mod`, `.sum`        | `go vet ./...`          |
| Rust         | Rust                  | `.rs`, `.toml`               | `cargo check`           |
| Dart/Flutter | Dart                  | `.dart`, `.yaml`             | `dart analyze`          |
| Docker       | Dockerfile            | `Dockerfile`, `.yml`         | `docker compose config` |
| Generic      | Any                   | All common                   | —                       |

**Detection Flow:**

1. Task keywords (e.g., "React app" → Node.js, "Spring Boot" → Java)
2. Repo analysis (`RepoDiscovery` → frameworks, primary language)
3. Fallback: Python

### B. LLM-Driven Command Planning

The LLM specifies **all commands** during planning — no hardcoded tool chains:

```json
{
  "stack": "node",
  "install_command": "npm install express cors",
  "compile_command": "",
  "lint_command": "npx tsc --noEmit",
  "run_command": "node index.js",
  "run_commands": ["npm install", "npm run seed", "npm start"],
  "test_command": "npm test"
}
```

### C. Runtime Pre-Check (Step 1b)

Before execution, validates required binaries via `shutil.which()`:

| Stack  | Required Tools    |
| ------ | ----------------- |
| Java   | `java`, `javac`   |
| Node   | `node`, `npm`     |
| Go     | `go`              |
| Rust   | `cargo`, `rustc`  |
| Docker | `docker`          |
| Dart   | `dart`, `flutter` |

Also scans all commands for keyword→binary mappings (15+ tools: `mvn`, `gradle`, `yarn`, `pnpm`, `psql`, `mysql`, etc.). On failure, provides install hints per tool.

### D. Smart Command Execution (`run_code()`)

**Timeouts (auto-detected):**

| Command Type                                    | Timeout |
| ----------------------------------------------- | ------- |
| `docker build`, `docker-compose`                | 10 min  |
| `mvn`, `gradle`, `cargo build`, `flutter build` | 5 min   |
| `npm install`, `pip install`, `go mod tidy`     | 5 min   |
| `pytest`, `jest`, `mvn test`, `go test`         | 3 min   |
| DB migrations (`alembic`, `flyway`, `prisma`)   | 2 min   |
| Default                                         | 2 min   |

**Server Detection:** Commands like `flask run`, `uvicorn`, `npm start`, `docker-compose up`, etc. → `Popen` background mode + health check.

**Health Check Flow:**

1. Detect port from `--port N`, `-p N`, `:NNNN`, or known defaults (Flask=5000, Vite=5173, etc.)
2. Ping `http://localhost:{port}` with 3 retries × 2s delay

### E. Universal Error Detection

Self-correction parses tracebacks/stack traces from **any language**.

Known fast-path patterns for Python, Java, Kotlin, Node.js, Rust, and Dart, plus a **universal catch-all** (`file.ext:lineN`) that works for Go, C/C++, Ruby, PHP, Swift, Elixir, Scala, Zig, Haskell, and any other language:

| Language | Pattern                     |
| -------- | --------------------------- |
| Python   | `File "xxx.py", line N`     |
| Java     | `at pkg.Class(File.java:N)` |
| Kotlin   | `at pkg.Class(File.kt:N)`   |
| Node.js  | `at Object (file.js:N:N)`   |
| Go       | `file.go:N:N: error`        |
| Rust     | `--> src/main.rs:N:N`       |
| C/C++    | `file.c:N:N: error`         |
| Dart     | `file.dart:N`               |

### F. Auto-Git Commit (Step 12)

On successful task completion, auto-commits with `[god-mode-agent] <summary>` and `--no-verify`.

---

## 4. Module Inventory

| Module                    | Path                               | Purpose                                          |
| ------------------------- | ---------------------------------- | ------------------------------------------------ |
| **TaskExecutor**          | `agent/core/task_executor.py`      | 12-step agentic pipeline                         |
| **StackProfiles**         | `agent/core/stack_profiles.py`     | Stack detection & profiles                       |
| **Chat**                  | `agent/core/chat.py`               | Conversational AI interface                      |
| **GovernanceController**  | `agent/core/governance.py`         | State machine + transitions                      |
| **KillSwitch**            | `agent/core/kill_switch.py`        | Emergency halt mechanism                         |
| **CommandSafety**         | `agent/core/command_safety.py`     | Command tier classification (SAFE/NETWORK/BLOCK) |
| **RBAC**                  | `agent/core/rbac.py`               | Role-based access control                        |
| **IntentClassifier**      | `agent/core/intent.py`             | User intent detection                            |
| **PlanEnvelopeValidator** | `agent/core/plan_envelope.py`      | Plan immutability & scope                        |
| **SupplyChainChecker**    | `agent/core/supply_chain.py`       | Dependency scanning                              |
| **SecretsPolicy**         | `agent/core/secrets.py`            | Secret detection & redaction                     |
| **BoundedLSPLoop**        | `agent/core/lsp_loop.py`           | Lint with retry budget                           |
| **VerificationPipeline**  | `agent/core/verification.py`       | Multi-tier test runner                           |
| **RepoDiscovery**         | `agent/planning/repo_discovery.py` | Repo structure analysis                          |
| **LLMProvider**           | `agent/llm_provider.py`            | Together AI integration                          |
| **WebUI**                 | `agent/web_ui.py`                  | Streamlit chat interface                         |
| **CLI**                   | `agent/cli.py`                     | Command-line interface                           |
| **DockerInspector**       | `agent/tools/docker_inspector.py`  | Container runtime analysis                       |
| **DatabaseInspector**     | `agent/tools/db_inspector.py`      | DB schema & data verification                    |
| **BrowserTester**         | `agent/tools/browser_tester.py`    | Headless web UI verification (Playwright)        |
| **DocCrawler**            | `agent/tools/doc_crawler.py`       | External documentation fetcher                   |
| **LSPTool**               | `agent/tools/lsp.py`               | Multi-language linting/diagnostics               |
| **VisualTool**            | `agent/tools/visual.py`            | Screenshot-based visual feedback                 |
| **PromptManager**         | `agent/core/prompt.py`             | Persona-based prompts + Design DNA               |
| **SecurityAdvisor**       | `agent/core/security_advisor.py`   | LLM-powered second opinion for security          |
| **TranscriptAuditor**     | `agent/verification/auditor.py`    | Turn 1 constraint enforcement scan               |
| **PluginLoader**          | `agent/core/plugin_loader.py`      | Federated marketplace + Lazy Loading             |
| **GovernanceManager**     | `agent/security/governance.py`     | Session-aware approval persistence               |

---

## 5. Execution Pipeline (v8.0)

```
USER_INPUT
   ↓
Step 0b: DETECT STACK ──→ [StackProfiles → task keywords + repo analysis]
   ↓
Step 1:  GENERATE PLAN ──→ [LLM + stack context → install/compile/lint/run commands]
   ↓
Step 1b: RUNTIME PRE-CHECK ──→ [shutil.which() → java? node? docker?]
   ↓                            (Missing? → ❌ FAIL with install hints)
Step 2:  FREEZE ENVELOPE ──→ [SHA256 hash of plan]
   ↓
Step 3:  SUPPLY CHAIN CHECK ──→ [Dependency safety scan]
   ↓
Step 3b: SECURITY ADVISOR ──→ [Analyst second opinion if flagged]
   ↓
Step 4:  TASK ISOLATION ──→ [git checkout -b agent/task-id]
   ↓
Step 5:  GENERATE CODE ──→ [LLM (language-aware) → write files → secrets scan]
   ↓
Step 6:  LINT / SYNTAX ──→ [Python: LSP loop | Others: LLM-specified lint cmd]
   ↓
Step 7:  INSTALL DEPS ──→ [plan.install_command (npm/pip/cargo/go mod/etc.)]
   ↓
Step 7b: COMPILE ──→ [plan.compile_command (javac/go build/cargo build/etc.)]
   ↓
Step 8:  RUN CODE ──→ [Smart timeout | Server detection → background + health check]
   ↓                   [Multi-step: run_commands[] executed in sequence]
Step 9:  AUTO-HEALING ──→ [Universal error detection → LLM fix → recompile → re-run]
   ↓                         [Max 3 attempts per command]
Step 10: VERIFICATION ──→ [Multi-tier: syntax → unit → integration]

   ↓
Step 11: PLAN ENFORCEMENT ──→ [Verify no unplanned files written]
   ↓
Step 12: AUTO-GIT COMMIT ──→ [On success: git add + commit "[god-mode-agent] ..."]
   ↓
🎉 DONE ──→ [Cleanup background processes]
```

---

## 6. Security Layers

| Layer                         | Mechanism                                                                                                           |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Kill Switch**               | Adaptive timeouts (stack-aware, extensible) + 90m hard cap                                                          |
| **Command Safety**            | Tier-based: SAFE / NETWORK / BLOCK                                                                                  |
| **RBAC**                      | Role-based permissions (DEVELOPER, ARCHITECT, ADMIN)                                                                |
| **Secrets Policy**            | Regex-based detection + auto-redaction                                                                              |
| **Supply Chain**              | Dependency name analysis for typosquatting                                                                          |
| **Plan Envelope**             | Immutable scope — no unplanned file writes                                                                          |
| **Shell Power**               | Prefer `mv/rm` shell commands over Python scripts                                                                   |
| **Task Isolation**            | Git branch per task — rollback on failure                                                                           |
| **Runtime Pre-Check**         | Verify tools exist before executing unknown commands                                                                |
| **Security Advisor**          | Autonomous validation of suspicious dependencies.                                                                   |
| **Reactive Feedback Loop**    | Real-time course correction and re-planning via CLI & Web interaction.                                              |
| **Premium Web UI**            | Pulsatile God Mode indicators and mode-aware chat decorators.                                                       |
| **Decoupled Architecture**    | REST API (`agent/server.py`) + React Frontend (`ui/`) for scalability.                                              |
| **Stateful Auto-Healing**     | Tracks code manipulation history during verification fixes to eliminate cyclic error regressions.                   |
| **Chain-of-Thought Autonomy** | Explicitly forces LLM reasoning (`<analysis>`) prior to code generation & repair, streamed dynamically to `stdout`. |
| **Mission Orthogonality**     | Decoupled Expert Missions (Explorer, Architect, Coder, Verifier) to eliminate bias and context collapse.            |
| **Federated Extensions**      | Standalone `/plugins` marketplace for specialized toolsets (Docker, DB, Browser, LSP).                              |
| **Selective Auditing**        | Heuristic-based skip for transcript audits on trivial (≤2 step) tasks to eliminate latency.                         |
| **Lazy Tool Initialization**  | Heavy plugins (Puppeteer, LSP) are instantiated on-demand, reducing idle memory overhead by 40%.                    |
