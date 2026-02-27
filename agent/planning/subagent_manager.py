import os
import glob
import yaml
from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class SubagentConfig:
    name: str
    description: str
    system_prompt: str
    model: Optional[str] = None
    max_turns: Optional[int] = None
    tools: list[str] = None

class SubagentManager:
    """
    Manages loading and retrieving custom subagents defined in `.godmode/agents/*.md`.
    These files use YAML frontmatter to define the agent's properties, and the markdown
    body acts as the system prompt.
    """
    
    def __init__(self, repo_path: str):
        self._repo_path = repo_path
        self._agents_dir = os.path.join(repo_path, ".godmode", "agents")
        self._subagents: Dict[str, SubagentConfig] = {}
        self._load_agents()

    def _load_agents(self):
        """Discovers and parses all .md files in the agents directory."""
        if not os.path.exists(self._agents_dir):
            return

        for filepath in glob.glob(os.path.join(self._agents_dir, "*.md")):
            try:
                self._parse_file(filepath)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to load subagent {filepath}: {e}")

    def _parse_file(self, filepath: str):
        """Parses a markdown file with YAML frontmatter."""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if not content.startswith("---"):
            return # No frontmatter

        parts = content.split("---", 2)
        if len(parts) < 3:
            return

        frontmatter_str = parts[1]
        body = parts[2].strip()

        try:
            metadata = yaml.safe_load(frontmatter_str) or {}
        except yaml.YAMLError:
            return

        name = metadata.get("name", os.path.splitext(os.path.basename(filepath))[0])
        self._subagents[name.lower()] = SubagentConfig(
            name=name,
            description=metadata.get("description", f"Custom subagent: {name}"),
            system_prompt=body,
            model=metadata.get("model"),
            max_turns=metadata.get("max_turns"),
            tools=metadata.get("tools", [])
        )

    def get_agent(self, name: str) -> Optional[SubagentConfig]:
        """Retrieve a specific subagent configuration by name."""
        return self._subagents.get(name.lower())

    def list_agents(self) -> Dict[str, SubagentConfig]:
        """Return all loaded subagents."""
        return self._subagents

    def inject_agent_prompt(self, name: str, default_prompt: str) -> str:
        """
        Returns the custom agent's system prompt if it exists, otherwise
        falls back to a default prompt.
        """
        agent = self.get_agent(name)
        if agent:
            return agent.system_prompt
        return default_prompt
