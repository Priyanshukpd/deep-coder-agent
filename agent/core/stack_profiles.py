"""
Stack Profiles — Per-language runtime configurations for multi-stack execution.

Provides COMMON profiles as hints, but the real power is in the LLM planning
step where it specifies the exact install/compile/run/test commands for
ANY technology stack — including Django, Flask, FastAPI, React, Flutter,
Chainlit, Streamlit, Spring Boot, Docker Compose, etc.

The profiles here are used for:
1. File extension detection (which files to read for context)
2. Prompt language hints (so the LLM generates the right language)
3. Fallback lint commands when the LLM doesn't specify one
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class StackProfile:
    """Runtime profile for a technology stack."""

    name: str                              # "python", "java", "node", etc.
    display_name: str                      # "Python", "Java", "Node.js"
    extensions: tuple[str, ...]            # (".py",), (".java",), (".js", ".ts")
    code_prompt_language: str              # Language name for LLM prompts
    file_read_extensions: tuple[str, ...] = ()  # Extensions to read for context
    fallback_lint: Optional[str] = None    # Fallback lint command
    timeout_seconds: int = 120             # Execution timeout


# ── Common Profiles ──────────────────────────────────────────────
# These are HINTS, not requirements. The LLM planning step determines
# the actual commands for any stack — these profiles just help with
# context reading and prompt language.

PYTHON = StackProfile(
    name="python",
    display_name="Python",
    extensions=(".py",),
    code_prompt_language="Python",
    fallback_lint="python -m py_compile",
    file_read_extensions=(".py", ".toml", ".yaml", ".yml", ".json", ".md",
                          ".txt", ".cfg", ".ini", ".sh", ".env",
                          ".html", ".css"),
)

JAVA = StackProfile(
    name="java",
    display_name="Java",
    extensions=(".java", ".kt"),
    code_prompt_language="Java/Kotlin",
    fallback_lint="javac -Xlint:all",
    file_read_extensions=(".java", ".kt", ".xml", ".gradle", ".properties",
                          ".yaml", ".yml", ".json", ".md"),
)

NODE = StackProfile(
    name="node",
    display_name="Node.js",
    extensions=(".js", ".ts", ".jsx", ".tsx", ".mjs"),
    code_prompt_language="JavaScript/TypeScript",
    fallback_lint="npx tsc --noEmit",
    file_read_extensions=(".js", ".ts", ".jsx", ".tsx", ".json", ".md",
                          ".html", ".css", ".scss", ".yaml", ".yml", ".env"),
)

GO = StackProfile(
    name="go",
    display_name="Go",
    extensions=(".go",),
    code_prompt_language="Go",
    fallback_lint="go vet ./...",
    file_read_extensions=(".go", ".mod", ".sum", ".yaml", ".yml", ".md"),
)

RUST = StackProfile(
    name="rust",
    display_name="Rust",
    extensions=(".rs",),
    code_prompt_language="Rust",
    fallback_lint="cargo check",
    file_read_extensions=(".rs", ".toml", ".md"),
)

DART = StackProfile(
    name="dart",
    display_name="Dart/Flutter",
    extensions=(".dart",),
    code_prompt_language="Dart (Flutter)",
    fallback_lint="dart analyze",
    file_read_extensions=(".dart", ".yaml", ".yml", ".json", ".md"),
)

DOCKER = StackProfile(
    name="docker",
    display_name="Docker",
    extensions=(),
    code_prompt_language="Dockerfile + application code (any language)",
    timeout_seconds=300,
    file_read_extensions=(".py", ".js", ".ts", ".java", ".go", ".rs",
                          ".yaml", ".yml", ".json", ".toml", ".md",
                          ".html", ".css", ".env", ".sh"),
)

# Generic / unknown — reads everything, no lint assumptions
GENERIC = StackProfile(
    name="generic",
    display_name="Multi-Language",
    extensions=(),
    code_prompt_language="the appropriate programming language",
    file_read_extensions=(".py", ".js", ".ts", ".java", ".go", ".rs", ".dart",
                          ".rb", ".php", ".c", ".cpp", ".cs", ".swift",
                          ".yaml", ".yml", ".json", ".toml", ".xml", ".md",
                          ".html", ".css", ".sh", ".env"),
)


# ── Lookup ───────────────────────────────────────────────────────

ALL_PROFILES = {
    "python": PYTHON,
    "java": JAVA, "kotlin": JAVA,
    "node": NODE, "javascript": NODE, "typescript": NODE,
    "go": GO, "golang": GO,
    "rust": RUST,
    "dart": DART, "flutter": DART,
    "docker": DOCKER,
}


def detect_profile_from_stack(primary_language: str,
                              frameworks: list[str] = None) -> StackProfile:
    """
    Pick the best StackProfile from repo discovery results.
    Falls back to GENERIC if unrecognized.
    """
    frameworks = frameworks or []

    # Docker takes priority if present
    if "Docker" in frameworks or "Docker Compose" in frameworks:
        return DOCKER

    lang_lower = primary_language.lower()
    return ALL_PROFILES.get(lang_lower, GENERIC)


def detect_profile_from_task(task: str) -> Optional[StackProfile]:
    """
    Detect stack from the user's task description.
    Returns None if no strong signal found (let the LLM figure it out).
    """
    task_lower = task.lower()

    # Order matters — check specific frameworks before generic language names
    TASK_KEYWORDS: list[tuple[StackProfile, list[str]]] = [
        (DOCKER, ["docker", "dockerfile", "container", "docker-compose",
                  "docker compose", "kubernetes", "k8s"]),
        (DART, ["flutter", "dart", "widget"]),
        (JAVA, ["java ", "javac", "spring boot", "spring", "maven", "gradle",
                ".java", "jdk", "jvm", "kotlin"]),
        (NODE, ["node", "npm", "javascript", "typescript", "react", "next.js",
                "nextjs", "express", "vue", "angular", "vite", "svelte",
                "chainlit", "package.json", "yarn", "pnpm", "bun"]),
        (GO, ["golang", "go run", "go build", "go mod", "gin", "fiber"]),
        (RUST, ["rust", "cargo", "crate"]),
        (PYTHON, ["python", "pip", "django", "flask", "fastapi", "streamlit",
                  "chainlit", "gradio", "pytorch", "tensorflow", "pandas",
                  "numpy", "scipy", "matplotlib"]),
    ]

    for profile, keywords in TASK_KEYWORDS:
        for kw in keywords:
            if kw in task_lower:
                return profile

    return None
