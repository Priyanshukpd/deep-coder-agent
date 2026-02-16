"""
Supply Chain Security — Typosquatting Check.

Detects potential typosquatting attacks in dependency names.
Also validates that dependencies are from trusted sources.

Uses edit distance and known-package databases to flag suspicious deps.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class SupplyChainViolation(Exception):
    """Raised when a supply chain security issue is detected."""
    pass


@dataclass
class DependencyCheck:
    """Result of checking a single dependency."""
    name: str
    is_suspicious: bool = False
    reason: str = ""
    similar_to: str = ""  # Known package it might be typosquatting
    edit_distance: int = 0


# Standard Python Libraries (utility constant)
STANDARD_PYTHON_LIBRARIES = {
    "os", "sys", "shutil", "pathlib", "json", "re", "datetime", "math", "random",
    "time", "uuid", "logging", "argparse", "subprocess", "threading", "multiprocessing",
    "collections", "itertools", "functools", "tempfile", "glob", "fnmatch", "io",
    "abc", "ast", "asyncio", "base64", "bisect", "copy", "csv", "decimal", "enum",
    "hashlib", "inspect", "operator", "pickle", "queue", "select", "shlex", "socket",
    "sqlite3", "ssl", "statistics", "struct", "tarfile", "traceback", "types",
    "typing", "unittest", "urllib", "xml", "zipfile", 
}

# Well-known Python packages (for typosquatting detection)
KNOWN_PYTHON_PACKAGES = {
    "requests", "flask", "django", "numpy", "pandas",
    "scipy", "matplotlib", "pillow", "sqlalchemy", "celery",
    "redis", "boto3", "pyyaml", "cryptography", "paramiko",
    "pytest", "setuptools", "pip", "wheel", "virtualenv",
    "black", "flake8", "mypy", "ruff", "pylint",
    "fastapi", "uvicorn", "gunicorn", "httpx", "aiohttp",
    "pydantic", "attrs", "dataclasses", "typing_extensions",
    "click", "rich", "tqdm", "colorama", "together",
    # ML Ecosystem
    "torch", "transformers", "peft", "datasets", "accelerate",
    "bitsandbytes", "scikit-learn", "wandb", "huggingface-hub",
    "tokenizers", "safetensors", "sentencepiece",
}

# Well-known npm packages
KNOWN_NPM_PACKAGES = {
    "react", "vue", "angular", "express", "next",
    "webpack", "babel", "typescript", "eslint", "prettier",
    "lodash", "axios", "moment", "dayjs", "chalk",
    "commander", "inquirer", "yargs", "dotenv", "cors",
}

# Suspicious patterns
SUSPICIOUS_PATTERNS = [
    re.compile(r"python-"),       # python-requests vs requests
    re.compile(r"-python$"),
    re.compile(r"py-"),
    re.compile(r"\d+$"),          # requests2
    re.compile(r"^[a-z]{1,2}-"),  # a-requests
]


def _edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr

    return prev[len(b)]


class SupplyChainChecker:
    """
    Checks dependencies for typosquatting and supply chain attacks.
    
    Uses edit distance against known packages to detect suspicious names.
    """

    def __init__(
        self,
        known_packages: set[str] = None,
        max_edit_distance: int = 2,
    ):
        self._known = known_packages or (KNOWN_PYTHON_PACKAGES | KNOWN_NPM_PACKAGES)
        self._max_edit_distance = max_edit_distance

    def check_dependency(self, dep_name: str) -> DependencyCheck:
        """
        Check a single dependency name for typosquatting.
        
        Returns DependencyCheck with is_suspicious flag.
        """
        normalized = dep_name.lower().strip()

        # If it's a known package, it's fine
        if normalized in self._known:
            return DependencyCheck(name=dep_name)

        # Check edit distance against known packages
        for known in self._known:
            dist = _edit_distance(normalized, known)
            if 0 < dist <= self._max_edit_distance:
                logger.warning(
                    f"Supply chain: '{dep_name}' is {dist} edits from '{known}' — "
                    f"potential typosquat!"
                )
                return DependencyCheck(
                    name=dep_name,
                    is_suspicious=True,
                    reason=f"Typosquatting risk: similar to '{known}' (edit distance: {dist})",
                    similar_to=known,
                    edit_distance=dist,
                )

        # Check suspicious patterns
        for pattern in SUSPICIOUS_PATTERNS:
            if pattern.search(normalized):
                # Check if removing the pattern matches a known package
                cleaned = pattern.sub("", normalized)
                if cleaned in self._known:
                    return DependencyCheck(
                        name=dep_name,
                        is_suspicious=True,
                        reason=f"Suspicious pattern: might be targeting '{cleaned}'",
                        similar_to=cleaned,
                    )

        return DependencyCheck(name=dep_name)

    def check_dependencies(self, dep_names: list[str]) -> list[DependencyCheck]:
        """
        Check a list of dependencies for typosquatting.
        
        Returns list of checks, with suspicious ones flagged.
        """
        results = []
        for name in dep_names:
            result = self.check_dependency(name)
            results.append(result)
            if result.is_suspicious:
                logger.critical(
                    f"⚠️ SUPPLY CHAIN ALERT: {result.name} — {result.reason}"
                )

        suspicious = [r for r in results if r.is_suspicious]
        if suspicious:
            logger.critical(f"Found {len(suspicious)} suspicious dependencies!")
        else:
            logger.info(f"All {len(results)} dependencies passed supply chain check")

        return results

    @staticmethod
    def parse_requirements(content: str) -> list[str]:
        """Parse dependency names from requirements.txt format."""
        deps = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Extract package name (before version specifier)
            name = re.split(r"[>=<!\[\];]", line)[0].strip()
            if name:
                deps.append(name)
        return deps
