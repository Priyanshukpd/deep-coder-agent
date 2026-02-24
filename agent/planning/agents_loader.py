"""
AGENTS.md Hierarchical Loader — Load project-specific agent instructions.

Walks from the current working directory up to the repo root (or filesystem root),
collecting all AGENTS.md files. Deeper files (closer to CWD) take priority and
their instructions are appended after shallower ones (so deeper = more specific override).

This mirrors how Codex automatically loads AGENTS.md on every task.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_agents_md(start_path: str = ".") -> str:
    """
    Walk from `start_path` up to root, collect and merge all AGENTS.md files.

    Returns merged string: shallower files first (general rules),
    then deeper files (project-specific overrides).

    Args:
        start_path: Starting directory (usually cwd or repo root).

    Returns:
        Merged AGENTS.md content, or empty string if none found.
    """
    start = Path(os.path.abspath(start_path))
    candidates: list[tuple[int, str, str]] = []  # (depth, path, content)

    current = start
    depth = 0
    max_depth = 20  # Safety limit

    while depth < max_depth:
        agents_file = current / "AGENTS.md"
        if agents_file.exists():
            try:
                content = agents_file.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    candidates.append((depth, str(agents_file), content))
            except OSError:
                pass

        parent = current.parent
        if parent == current:  # Reached filesystem root
            break
        current = parent
        depth += 1

    if not candidates:
        return ""

    # Sort: shallowest (highest depth number) first, deepest last
    # Since we walk upward, depth=0 is CWD (most specific), depth=N is root (least specific)
    # We want: root content first (general), then CWD content (specific override)
    candidates.sort(key=lambda x: x[0], reverse=True)  # Root first

    parts = ["# Project Instructions (from AGENTS.md files)\n"]
    for depth, path, content in candidates:
        rel = os.path.relpath(path, start_path)
        parts.append(f"## From: {rel}\n{content}")

    return "\n\n".join(parts)


def inject_agents_md(system_prompt: str, repo_path: str = ".") -> str:
    """
    Append AGENTS.md content to a system prompt if any files exist.

    Args:
        system_prompt: Existing system prompt.
        repo_path: Repo root to start walking from.

    Returns:
        Enhanced system prompt with AGENTS.md appended (or unchanged if none).
    """
    agents_content = load_agents_md(repo_path)
    if not agents_content:
        return system_prompt

    return system_prompt + f"\n\n{agents_content}"
