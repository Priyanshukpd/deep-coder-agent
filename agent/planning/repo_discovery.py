"""
Repo Discovery — Detect Stack & Build RepoMap.

Scans a repository to discover:
    - Programming languages used
    - Build systems / package managers
    - Framework detection
    - File count for scope estimation
    - Project structure mapping

Architecture §1 REPO_DISCOVERY:
    - Intent is Valid → RepoMap built
    - file_count > MAX_FILE_CAP → FAIL(ScopeTooLarge)
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Common ignore patterns
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist",
    "build", ".egg-info", ".tox", "coverage", ".next",
    ".nuxt", "target", "out",
}

IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".class", ".o", ".so", ".dylib",
    ".lock", ".min.js", ".min.css",
}


@dataclass
class StackInfo:
    """Detected technology stack."""
    languages: dict[str, int] = field(default_factory=dict)   # lang → file count
    build_systems: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    primary_language: str = ""

    @property
    def summary(self) -> str:
        parts = []
        if self.primary_language:
            parts.append(f"Primary: {self.primary_language}")
        if self.frameworks:
            parts.append(f"Frameworks: {', '.join(self.frameworks)}")
        if self.build_systems:
            parts.append(f"Build: {', '.join(self.build_systems)}")
        return " | ".join(parts)


@dataclass
class RepoMap:
    """
    Map of the repository structure.
    
    Built during REPO_DISCOVERY state.
    """
    root_path: str
    file_count: int = 0
    dir_count: int = 0
    total_lines: int = 0
    files: list[str] = field(default_factory=list)
    stack: StackInfo = field(default_factory=StackInfo)
    structure: dict = field(default_factory=dict)

    @property
    def is_within_scope(self) -> bool:
        from agent.planning.plan_envelope import MAX_FILE_CAP
        return self.file_count <= MAX_FILE_CAP


# Framework detection patterns
FRAMEWORK_MARKERS = {
    "pyproject.toml": ["Python"],
    "setup.py": ["Python"],
    "requirements.txt": ["Python", "pip"],
    "Pipfile": ["Python", "pipenv"],
    "package.json": ["Node.js", "npm"],
    "tsconfig.json": ["TypeScript"],
    "next.config.js": ["Next.js"],
    "next.config.mjs": ["Next.js"],
    "vite.config.ts": ["Vite"],
    "vite.config.js": ["Vite"],
    "angular.json": ["Angular"],
    "Cargo.toml": ["Rust", "Cargo"],
    "go.mod": ["Go"],
    "pom.xml": ["Java", "Maven"],
    "build.gradle": ["Java", "Gradle"],
    "Gemfile": ["Ruby", "Bundler"],
    "composer.json": ["PHP", "Composer"],
    "Dockerfile": ["Docker"],
    "docker-compose.yml": ["Docker Compose"],
    ".flake8": ["flake8"],
    "ruff.toml": ["ruff"],
    "pytest.ini": ["pytest"],
}

LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".rs": "Rust",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".dart": "Dart",
    ".md": "Markdown",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".sh": "Shell",
}


class RepoDiscovery:
    """
    Scans a repository and builds a RepoMap.
    
    Usage:
        discovery = RepoDiscovery("/path/to/repo")
        repo_map = discovery.scan()
        
        if not repo_map.is_within_scope:
            # Trigger FAILED_BY_SCOPE
            ...
    """

    def __init__(self, root_path: str):
        self._root = Path(root_path)

    def scan(self) -> RepoMap:
        """
        Scan the repository and build a complete RepoMap.
        
        Returns RepoMap with file listing, language detection, and stack info.
        """
        repo_map = RepoMap(root_path=str(self._root))
        lang_counter: Counter = Counter()
        frameworks = set()
        build_systems = set()
        package_managers = set()

        for path in self._walk_files():
            rel_path = str(path.relative_to(self._root))
            repo_map.files.append(rel_path)
            repo_map.file_count += 1

            # Detect language
            ext = path.suffix.lower()
            if ext in LANGUAGE_EXTENSIONS:
                lang = LANGUAGE_EXTENSIONS[ext]
                lang_counter[lang] += 1

            # Detect frameworks/build systems
            name = path.name
            if name in FRAMEWORK_MARKERS:
                for marker in FRAMEWORK_MARKERS[name]:
                    frameworks.add(marker)

            # Count lines (for text files)
            try:
                if ext in LANGUAGE_EXTENSIONS:
                    lines = len(path.read_text().splitlines())
                    repo_map.total_lines += lines
            except (UnicodeDecodeError, PermissionError):
                pass

        # Build stack info
        stack = StackInfo(
            languages=dict(lang_counter.most_common()),
            frameworks=sorted(frameworks),
            build_systems=sorted(build_systems),
            package_managers=sorted(package_managers),
        )
        if lang_counter:
            stack.primary_language = lang_counter.most_common(1)[0][0]

        repo_map.stack = stack

        logger.info(
            f"Repo discovery: {repo_map.file_count} files, "
            f"{repo_map.total_lines} lines. "
            f"Stack: {stack.summary}"
        )

        return repo_map

    def _walk_files(self):
        """Walk directory tree, skipping ignored directories."""
        for path in self._root.rglob("*"):
            # Skip ignored directories
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            # Skip ignored extensions
            if path.suffix in IGNORE_EXTENSIONS:
                continue
            # Only files
            if path.is_file():
                yield path
