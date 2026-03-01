"""
Intent Classifier — Determines the type of task and the confidence level.

This module is responsible for:
1.  Classifying user input into a TaskIntent (FIX, FEATURE, etc.)
2.  Assigning a confidence score (0.0 - 1.0)
3.  Triggering Ambiguity Fallback if confidence < 0.75

Supports two modes:
    - LLM-powered (Together AI with function calling) — default
    - Keyword heuristics — offline fallback
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, List, TYPE_CHECKING

from agent.state import TaskIntent, IntentResult

if TYPE_CHECKING:
    from agent.core.llm_provider import TogetherProvider

logger = logging.getLogger(__name__)


# -- Tool Schema for LLM Function Calling --

CLASSIFY_INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "strict": True,
        "description": "Classify the user's development request into a broad directional goal.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["fix", "develop", "explain", "generate", "meta"],
                    "description": "The directional goal."
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score from 0.0 to 1.0"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation"
                },
                "clarification_needed": {
                    "type": "boolean",
                    "description": "True if the request is ambiguous"
                },
                "suggested_question": {
                    "type": "string",
                    "description": "Question to ask the user if clarification is needed."
                }
            },
            "required": ["intent", "confidence", "reasoning", "clarification_needed", "suggested_question"],
            "additionalProperties": False
        }
    }
}

SYSTEM_PROMPT = """You are the Intent Classifier for a ReAct-based dev agent. 
Classify the user's request into a directional goal by calling classify_intent.

Goals:
- fix: Fixing bugs, errors, or broken behavior.
- develop: Any active code development including new features, refactors, optimizations, or architectural shifts.
- explain: Code analysis and questions (read-only).
- generate: Creating a new project or file from nothing.
- meta: Meta-commands for the agent (e.g., stop, wait, redo).

    * CRITICAL DISAMBIGUATION RULE: If the task contains action verbs like 'run', 'make', 'start', 'install', 'deploy', 'launch', 'serve', 'setup', 'execute', 'apply', 'enable', 'configure', 'restart', 'verify', 'debug', 'monitor', 'test', it is NOT 'explain'. It must be 'develop' or 'fix'. Prioritize the action even if the user asks for analysis first.
    * Output ONLY the JSON block.

The agent will autonomously determine the fine-grained steps (ReAct loop) once the goal is set."""


# -- Intent Enum Mapping --

_INTENT_MAP: dict[str, TaskIntent] = {
    "fix": TaskIntent.FIX,
    "develop": TaskIntent.DEVELOP,
    "explain": TaskIntent.EXPLAIN,
    "generate": TaskIntent.GENERATE,
    "meta": TaskIntent.META,
}


class IntentClassifier:
    """
    Classifies natural language requests into structured intents.
    Enforces the confidence threshold policy.

    Modes:
        - LLM mode (default): Uses Together AI with function calling.
        - Heuristic mode: Keyword-based fallback when LLM is unavailable.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.75,
        provider: Optional[TogetherProvider] = None,
    ):
        self.confidence_threshold = confidence_threshold
        self.provider = provider

    def classify(self, user_input: str, repo_context: str = "") -> IntentResult:
        """
        Classify the user input into an IntentResult.

        Strategy:
            1. Try LLM classification if provider is available.
            2. Fall back to keyword heuristics on any LLM failure.
        """
        if self.provider is not None:
            try:
                return self._classify_with_llm(user_input, repo_context)
            except Exception as e:
                logger.warning(f"LLM classification failed, falling back to heuristics: {e}")

        return self._classify_with_heuristics(user_input, repo_context)

    def _classify_with_llm(self, user_input: str, repo_context: str = "") -> IntentResult:
        """
        LLM-powered classification using Together AI function calling.

        Uses tool_choice="required" to guarantee structured output.
        """
        from agent.core.llm_provider import LLMToolCallError
        
        user_content = user_input
        if repo_context:
            user_content += f"\n\n[Repository Context]\n{repo_context}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        tool_result = self.provider.complete_with_tools(
            messages=messages,
            tools=[CLASSIFY_INTENT_TOOL],
            tool_choice="required",
        )

        # Validate the function name
        if tool_result.function_name != "classify_intent":
            raise LLMToolCallError(
                f"Expected 'classify_intent' but got '{tool_result.function_name}'"
            )

        args = tool_result.arguments

        # Map string intent to enum
        intent_str = args.get("intent", "explain").lower()
        intent = _INTENT_MAP.get(intent_str, TaskIntent.EXPLAIN)

        confidence = float(args.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # clamp

        reasoning = args.get("reasoning", "LLM classification")
        clarification_needed = args.get("clarification_needed", False)
        suggested_question = args.get("suggested_question", "")

        logger.info(
            f"LLM intent: {intent.value}, confidence: {confidence:.2f}, "
            f"reasoning: {reasoning}"
        )

        return IntentResult(
            intent=intent,
            confidence=confidence,
            reasoning=reasoning,
            clarification_needed=bool(clarification_needed),
            suggested_question=suggested_question if suggested_question else None,
        )

    def _classify_with_heuristics(self, user_input: str, repo_context: str = "") -> IntentResult:
        """
        Keyword-based fallback classifier.
        Maps to broad directional goals.
        """
        lower_input = user_input.lower()

        # High Confidence FIX
        if any(w in lower_input for w in ["fix", "error", "bug", "broken", "correct"]):
            return IntentResult(
                intent=TaskIntent.FIX,
                confidence=0.95,
                reasoning="Explicit error-related keywords present.",
            )

        # High Confidence DEVELOP (Consolidated)
        if any(w in lower_input for w in ["add", "feature", "refactor", "optimize", "ui", "framework", "migrate", "implement", "update", "build", "change", "run", "make", "start", "install", "deploy", "launch", "serve", "setup", "execute", "apply", "enable", "configure", "restart", "verify", "debug", "monitor", "test"]):
            return IntentResult(
                intent=TaskIntent.DEVELOP,
                confidence=0.90,
                reasoning="Active development or structural keywords present.",
            )

        # High Confidence GENERATE
        if any(w in lower_input for w in ["generate", "scaffold", "create", "write a script", "write code"]):
            return IntentResult(
                intent=TaskIntent.GENERATE,
                confidence=0.95,
                reasoning="Request to generate new files/projects.",
            )

        # High Confidence META
        if any(w in lower_input for w in ["stop", "wait", "undo", "redo", "pause", "capabilities", "status", "who are you", "are you done"]):
            return IntentResult(
                intent=TaskIntent.META,
                confidence=0.95,
                reasoning="Meta-command or capability query detected.",
            )

        # High Confidence EXPLAIN (Read-Only)
        if any(w in lower_input for w in ["explain", "read", "describe", "analyze", "what does", "how does"]):
            return IntentResult(
                intent=TaskIntent.EXPLAIN,
                confidence=0.85,
                reasoning="Read-only inquiry keywords present.",
            )

        # Low Confidence / Ambiguous
        if "check" in lower_input or "look" in lower_input:
            return IntentResult(
                intent=TaskIntent.EXPLAIN,
                confidence=0.60,
                reasoning="Vague request. Defaulting to EXPLAIN (read-only).",
            )

        # Default
        return IntentResult(
            intent=TaskIntent.DEVELOP,
            confidence=0.50,
            reasoning="Unknown intent. Defaulting to DEVELOP for ambition directive.",
            clarification_needed=False,
            suggested_question=None,
        )

    def get_fallback_intent(self) -> TaskIntent:
        """Return the safe fallback intent for low confidence."""
        return TaskIntent.EXPLAIN
