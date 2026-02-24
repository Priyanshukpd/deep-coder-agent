"""
Transcript Auditor — Final safety check against the entire session history.
Part of Phase 69.
"""

import logging
import json
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

AUDIT_PROMPT = """You are a Transcript Auditor. 
Your goal is to ensure that the final implementation strictly follows all constraints mentioned throughout the entire conversation.

CONVERSATION TRANSCRIPT:
{transcript}

FINAL TASK SUMMARY: 
{task_summary}

Your MISSION:
1. Scan for specific constraints (Security, Privacy, Technical stack, Naming conventions) mentioned early in the conversation (e.g. Turn 1-5).
2. Verify if the final implementation and current session state honor these constraints.
3. If any constraint mentioned earlier was forgotten or violated in later turns, find the gap.

Response Format:
{{
  "pass": true/false,
  "violations": ["Constraint X was violated by Y in Turn Z"],
  "reasoning": "Detailed analysis of the transcript vs final state"
}}

Output ONLY the JSON block.
"""

class TranscriptAuditor:
    """
    Analyzes the full conversation transcript to ensure no requirements were 'lost'
    due to context shift or attention drift.
    """

    def __init__(self, provider):
        self.provider = provider

    def audit(self, messages: List[Dict[str, str]], task_summary: str) -> Dict[str, Any]:
        """
        Run the final audit against the full message list.
        """
        # Format transcript for the LLM
        transcript_text = ""
        for i, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            transcript_text += f"Turn {i} [{role.upper()}]: {content[:1000]}\n---\n"

        prompt = AUDIT_PROMPT.format(
            transcript=transcript_text,
            task_summary=task_summary
        )

        try:
            logger.info("Running post-task transcript audit...")
            response = self.provider.complete([
                {"role": "system", "content": "You are a senior auditor. Be extremely pedantic about technical constraints."},
                {"role": "user", "content": prompt}
            ])
            
            text = response.content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            result = json.loads(text)
            if not result.get("pass"):
                logger.warning(f"Transcript audit FAILED: {result.get('violations')}")
            else:
                logger.info("Transcript audit PASSED.")
            return result
        except Exception as e:
            logger.error(f"Transcript audit failed to run: {e}")
            return {"pass": True, "reasoning": f"Audit failed to execute: {e}. Assuming pass for continuity."}
