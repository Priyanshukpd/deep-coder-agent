"""
Session State Manager — Maintain a high-fidelity "Mental Map" across long chat sessions.
Part of Phase 63 (Stateful Pre-Compaction).
"""

import os
import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

SESSION_STATE_FILE = ".agent/session_state.json"

STATE_COMPACTION_PROMPT = """Analyze the following conversation history and update the "Mental Map" of this session.
The Mental Map is a structured summary that ensures we don't forget the core objectives and progress even if the older chat logs are deleted.

Current Mental Map:
{current_state}

New Conversation Turns to Integrate:
{new_history}

Your Goal: Update the following JSON structure:
{{
  "core_objectives": ["Major goal 1", "Major goal 2"],
  "progress": "Detailed summary of what has been accomplished so far",
  "technical_decisions": ["Decision a", "Decision b"],
  "pending_tasks": ["Task x", "Task y"],
  "user_preferences": ["Preference i", "Preference j"]
}}

Output ONLY the JSON block.
"""

class SessionStateManager:
    """
    Manages the session_state.json file and handles state compaction.
    """

    def __init__(self, repo_path: str, provider):
        self.repo_path = repo_path
        self.provider = provider
        self.state_file = os.path.join(repo_path, SESSION_STATE_FILE)

    def load_state(self) -> Dict[str, Any]:
        """Load the current mental map from disk."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load session state: {e}")
        return {
            "core_objectives": [],
            "progress": "Initial state.",
            "technical_decisions": [],
            "pending_tasks": [],
            "user_preferences": []
        }

    def save_state(self, state: Dict[str, Any]):
        """Save the mental map to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
            logger.info(f"Session state saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save session state: {e}")

    def pre_compact_hook(self, messages: List[Dict[str, str]]):
        """
        Triggered before context pruning. Summarizes history into the mental map.
        """
        current_state = self.load_state()
        
        # Format history for the LLM
        history_text = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            history_text += f"[{role.upper()}]: {content[:500]}...\n" if len(content) > 500 else f"[{role.upper()}]: {content}\n"

        prompt = STATE_COMPACTION_PROMPT.format(
            current_state=json.dumps(current_state, indent=2),
            new_history=history_text
        )

        try:
            logger.info("Triggering stateful pre-compaction...")
            response = self.provider.complete([
                {"role": "system", "content": "You are a state-aware agent responsible for session longevity."},
                {"role": "user", "content": prompt}
            ])
            
            text = response.content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            new_state = json.loads(text)
            self.save_state(new_state)
            return True
        except Exception as e:
            logger.error(f"Pre-compaction hook failed: {e}")
            return False

    def post_action_update(self, plan: Any):
        """
        Update the mental map after a successful action (e.g. file edit).
        """
        current_state = self.load_state()
        
        prompt = f"""We just successfully executed an action.
Task: {plan.task}
Summary: {plan.summary}
Files Edited: {[f.path for f in plan.files]}

Current Mental Map:
{json.dumps(current_state, indent=2)}

Please update the core_objectives, progress, and technical_decisions in the JSON structure.
Output ONLY the JSON block.
"""
        try:
            logger.info("Updating mental map after successful action...")
            response = self.provider.complete([
                {"role": "system", "content": "You are a state-aware agent responsible for session longevity."},
                {"role": "user", "content": prompt}
            ])
            text = response.content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            new_state = json.loads(text)
            self.save_state(new_state)
            return True
        except Exception as e:
            logger.error(f"Post-action update failed: {e}")
            return False

    def get_state_context(self) -> str:
        """Returns the mental map as a string to be injected into the system prompt."""
        state = self.load_state()
        if not state.get("core_objectives") and state.get("progress") == "Initial state.":
            return ""
            
        return f"""
### SESSION MENTAL MAP (Restored from memory)
Objectives: {", ".join(state['core_objectives'])}
Progress: {state['progress']}
Decisions: {", ".join(state['technical_decisions'])}
Pending: {", ".join(state['pending_tasks'])}
Preferences: {", ".join(state['user_preferences'])}
"""
