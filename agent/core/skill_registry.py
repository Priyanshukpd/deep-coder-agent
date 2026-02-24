"""
Skill Registry — Progressively hydrate agent context based on repo discovery.
Inspired by Claude Code's "Skills" pattern.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional

logger = logging.getLogger(__name__)

@dataclass
class Skill:
    name: str
    trigger_files: List[str]
    description: str
    documentation: str

# Define core skills
DOCKER_SKILL = Skill(
    name="Docker",
    trigger_files=["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
    description="Container orchestration and environment inspection.",
    documentation="""### Skill: Docker
You have access to the `DockerInspector`. 
Use it to list containers, check logs, and inspect images.
Triggers: Found Dockerfile or docker-compose.yml in the repo.
"""
)

DATABASE_SKILL = Skill(
    name="Database",
    trigger_files=["schema.sql", "prisma.schema", "models.py", "migrations/", "init-db.js"],
    description="Database schema discovery and data inspection.",
    documentation="""### Skill: Database
You have access to the `DatabaseInspector`.
Use it to list tables, describe schemas, and peek at data.
Triggers: Found database migration files or schemas.
"""
)

BROWSER_SKILL = Skill(
    name="Browser",
    trigger_files=["index.html", "App.tsx", "main.js", "tailwind.config.js"],
    description="Live web application testing and visual verification.",
    documentation="""### Skill: Browser
You have access to the `BrowserTester`.
Use it to open URLs, click elements, and take screenshots.
Triggers: Found web frontend markers.
"""
)

LSP_SKILL = Skill(
    name="LSP",
    trigger_files=["pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml"],
    description="Language Server Protocol for deep code intelligence.",
    documentation="""### Skill: LSP
You have access to the `LSPTool`.
Use it to find definitions, references, and hover documentation.
Triggers: Found project manifest files.
"""
)

class SkillRegistry:
    """
    Manages the library of agent skills and handles dynamic hydration.
    """

    def __init__(self):
        self._skills: List[Skill] = [
            DOCKER_SKILL,
            DATABASE_SKILL,
            BROWSER_SKILL,
            LSP_SKILL
        ]

    def discover_skills(self, file_list: List[str]) -> List[Skill]:
        """
        Identify active skills based on the provided file list.
        """
        active_skills = []
        file_set = set(file_list)
        
        for skill in self._skills:
            # Check for exact matches
            if any(trigger in file_set for trigger in skill.trigger_files):
                active_skills.append(skill)
                continue
            
            # Check for directory matches (e.g., migrations/)
            if any(trigger.endswith("/") and any(f.startswith(trigger) for f in file_list) for trigger in skill.trigger_files):
                active_skills.append(skill)
                
        return active_skills

    def get_hydrated_docs(self, file_list: List[str]) -> str:
        """
        Produce a documentation block for the system prompt based on discovered skills.
        """
        active = self.discover_skills(file_list)
        if not active:
            return ""
            
        docs = ["## Activated Skills (Context Hydrated)"]
        for skill in active:
            docs.append(skill.documentation)
            
        return "\n".join(docs)

# Singleton
skill_registry = SkillRegistry()
