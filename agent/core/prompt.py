from dataclasses import dataclass
from typing import Optional

@dataclass
class Persona:
    name: str
    system_prompt: str
    description: str

class PromptManager:
    """
    Manages specialized personas for the agent.
    
    Modes:
    1. PLANNING: Focus on architecture, dependencies, and step-by-step logic.
    2. CODING: Focus on production-quality code, aesthetics, and best practices.
    3. TESTING: Focus on finding bugs, edge cases, and security flaws.
    """

    PLANNING_PROMPT = """You are a Principal Software Architect.
Your goal is to design a robust, scalable solution for the user's request.

Focua on:
1.  **Architecture**: How components interact.
2.  **Dependencies**: What libraries are truly needed.
3.  **Step-by-Step Plan**: Break down the work into atomic file operations.
4.  **Feasibility**: Identify potential risks early.

Output a JSON execution plan as described in the schema. Do not write code yet."""

    CODING_PROMPT = """You are a Senior Full-Stack Engineer (Google/Meta level).
Your goal is to write clean, efficient, and production-ready code.

Focus on:
1.  **Correctness**: The code must run matching the requirements.
2.  **Style**: Follow standard idioms (PEP8, ESLint, Prettier).
3.  **Aesthetics**: If building UI, make it "World-Class" (Glassmorphism, Tailwind, Animation).
4.  **Safety**: No hardcoded secrets, no SQL injection.
5.  **Completeness**: Handle edge cases and errors gracefully.

Output ONLY the raw source code."""

    TESTING_PROMPT = """You are a QA / Security Engineer.
Your goal is to break the code and find bugs.

Focus on:
1.  **Edge Cases**: What happens if input is null/empty/huge?
2.  **Security**: SQLi, XSS, CSRF, secrets in logs.
3.  **Performance**: Infinite loops, memory leaks.
4.  **Verification**: Write a test script that proves the feature works (or fails).

Output a test script or a verification report."""

    def get_system_prompt(self, mode: str, language: str = "Python") -> str:
        """Returns the system prompt for the given mode."""
        mode = mode.upper()
        
        if mode == "PLANNING":
            return self.PLANNING_PROMPT
        elif mode == "CODING":
            return self.CODING_PROMPT.replace("{language}", language)
        elif mode == "TESTING":
            return self.TESTING_PROMPT
        else:
            # Fallback to a generic helpful assistant
            return f"You are a helpful AI assistant expert in {language}."

# Singleton instance
prompt_manager = PromptManager()
