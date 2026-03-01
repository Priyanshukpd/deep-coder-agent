"""
Ambiguity Analyzer — Proactively identify underspecified tasks.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

AMBIGUITY_SYSTEM_PROMPT = """You are a senior software architect. 
Before the team starts implementation, your job is to analyze the user's task for ambiguity or underspecified requirements.

Compare the Task against the Repository Context provided.
Identify:
1. Missing technical details (e.g., which framework to use if multiple exist).
2. Missing UX/Functional details (e.g., what happens on error).
3. Vague intent (e.g., \"fix the bug\" without specifying which bug).

### DO NOT ASK ABOUT (ACTION BIAS):
- **Execution Environments**: If a project has multiple ways to run (e.g., `docker-compose.yml` vs `requirements.txt` vs `Dockerfile`), DO NOT ask the user which to use. Assume the standard/robust option (Docker > Local).
- **Missing Credentials**: If the app requires a DB (e.g., PostgreSQL) but no credentials are provided, DO NOT ask for them. Assume you will use a local fallback (like SQLite or `.db` files) if available.

Output a JSON object:
{
  "is_ambiguous": true/false,
  "questions": ["Question 1", "Question 2"],
  "best_guess_scenario": "A description of what you will assume if the user just says 'yes' or 'proceed'.",
  "reasoning": "Brief explanation of why you think it's ambiguous or clear."
}

If the task is PERFECTLY CLEAR, set \"is_ambiguous\" to false and leave questions empty.
Avoid being pedantic. Only ask if it truly prevents a correct implementation.

CRITICAL OVERRIDE: If the ONLY missing details are how to run the app (Docker vs Local) or how to connect to a database (Missing Postgres URL), you MUST set `"is_ambiguous": false` and proceed. You will figure it out during execution using your Action Bias.
"""

@dataclass
class AmbiguityResult:
    is_ambiguous: bool
    questions: List[str] = field(default_factory=list)
    best_guess_scenario: str = ""
    reasoning: str = ""

class AmbiguityAnalyzer:
    """
    Analyzes a task for underspecified requirements before planning begins.
    """

    def __init__(self, provider):
        self._provider = provider

    def analyze(self, task: str, repo_context: str, feedback: List[str] = None) -> AmbiguityResult:
        """
        Analyze the task and return questions if needed.
        """
        feedback_str = "\n".join([f"- {f}" for f in feedback]) if feedback else "None"
        prompt = f"Task: {task}\n\nFeedback/Instructions received:\n{feedback_str}\n\nRepository Context:\n{repo_context}"
        
        messages = [
            {"role": "system", "content": AMBIGUITY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        
        try:
            # We want a structured JSON response
            result = self._provider.complete(messages)
            text = result.content.strip()
            
            # Simple JSON extraction
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            data = json.loads(text)
            
            return AmbiguityResult(
                is_ambiguous=data.get("is_ambiguous", False),
                questions=data.get("questions", []),
                best_guess_scenario=data.get("best_guess_scenario", ""),
                reasoning=data.get("reasoning", "")
            )
        except Exception as e:
            logger.error(f"Ambiguity analysis failed: {e}")
            # Fallback to "not ambiguous" to avoid blocking if the analyzer itself fails
            return AmbiguityResult(is_ambiguous=False, reasoning=f"Analysis error: {str(e)}")
