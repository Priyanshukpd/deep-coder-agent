"""
Persistent Architecture Memory.

Maintains a cognitive log of key architectural decisions in `.agent/architecture.md`.
This allows the agent to "remember" past decisions (e.g. "We used Tailwind", "Auth is via Firebase")
across different sessions.
"""
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

ARCH_MEMORY_FILE = ".agent/architecture.md"

ARCH_UPDATE_PROMPT = """Analyze the completed task and generate a concise architectural log entry.

Context:
- Task: {task}
- Summary: {summary}

Goal: Capture key architectural decisions, design patterns, or major dependencies introduced.
Do NOT list every file changed. Focus on the "Why" and "How" for future reference.

Output Markdown format:
### [{date}] {task}
- **Decision**: [e.g. Switched to PostgreSQL for JSONB support]
- **Pattern**: [e.g. Repository pattern for data access]
- **Key Change**: [e.g. Added generic StackProfile detected_stack field]
"""

class ArchitectureMemory:
    def __init__(self, repo_path: str, provider):
        """
        Initialize memory manager.
        :param repo_path: Root of the repository.
        :param provider: LLM provider for summarizing updates.
        """
        self.repo_path = repo_path
        self.provider = provider
        self.memory_file = os.path.join(repo_path, ARCH_MEMORY_FILE)

    def read_context(self) -> str:
        """Read existing architectural memory to provide context for planning."""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    return f"\n\nArchitecture History (from {ARCH_MEMORY_FILE}):\n{content}"
            except Exception as e:
                logger.warning(f"Failed to read architecture memory: {e}")
        return ""

    def update(self, task: str, summary: str):
        """
        Analyze the completed task and append an entry to architecture.md.
        Should be called after successful execution.
        """
        try:
            # Create .agent dir if needed
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)

            date_str = datetime.now().strftime("%Y-%m-%d")
            
            messages = [
                {"role": "system", "content": "You are a software architect documenting project evolution."},
                {"role": "user", "content": ARCH_UPDATE_PROMPT.format(
                    task=task,
                    summary=summary,
                    date=date_str
                )}
            ]

            logger.info("Updating architecture memory...")
            result = self.provider.complete(messages)
            entry = result.content.strip()

            if entry.startswith("```markdown"):
                entry = entry.split("```markdown")[1].split("```")[0].strip()
            elif entry.startswith("```"):
                entry = entry.strip("`").strip()

            # Append to file
            with open(self.memory_file, 'a', encoding='utf-8') as f:
                f.write(f"\n\n{entry}")
            
            logger.info(f"Architecture memory updated in {self.memory_file}")

        except Exception as e:
            logger.warning(f"Failed to update architecture memory: {e}")
