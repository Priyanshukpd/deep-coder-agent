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
        "description": "Classify the user's development request into a structured task intent with confidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["fix", "refactor", "feature", "explain", "generate", "deploy", "debug"],
                    "description": "The classified task intent type."
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score from 0.0 to 1.0"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation for why this intent was chosen"
                },
                "clarification_needed": {
                    "type": "boolean",
                    "description": "True if the request is ambiguous and needs user clarification"
                },
                "suggested_question": {
                    "type": "string",
                    "description": "Question to ask the user if clarification is needed. Empty string if not needed."
                }
            },
            "required": ["intent", "confidence", "reasoning", "clarification_needed", "suggested_question"],
            "additionalProperties": False
        }
    }
}

SYSTEM_PROMPT = """You are the Intent Classifier for a deterministic dev agent. 
Given a user's request, classify it into exactly one task intent by calling the classify_intent function.

Intent types:
- fix: Bug fix, error resolution, patching broken behavior
- refactor: Code restructuring without behavior change
- feature: New functionality, adding capabilities
- explain: Code analysis, read-only investigation
- generate: Greenfield project, scaffolding from scratch
- deploy: Infrastructure, CI/CD, deployment changes
- debug: Performance profiling, trace-first investigation

Rules:
1. Always call the classify_intent function — never respond with plain text.
2. Set confidence between 0.0 and 1.0 based on how clear the intent is.
3. If the request is vague or could be multiple intents, set clarification_needed=true and suggest a disambiguating question.
4. Keep reasoning concise (one sentence)."""


# -- Intent Enum Mapping --

_INTENT_MAP: dict[str, TaskIntent] = {
    "fix": TaskIntent.FIX,
    "refactor": TaskIntent.REFACTOR,
    "feature": TaskIntent.FEATURE,
    "explain": TaskIntent.EXPLAIN,
    "generate": TaskIntent.GENERATE,
    "deploy": TaskIntent.DEPLOY,
    "debug": TaskIntent.DEBUG,
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

    def classify(self, user_input: str) -> IntentResult:
        """
        Classify the user input into an IntentResult.

        Strategy:
            1. Try LLM classification if provider is available.
            2. Fall back to keyword heuristics on any LLM failure.
        """
        if self.provider is not None:
            try:
                return self._classify_with_llm(user_input)
            except Exception as e:
                logger.warning(f"LLM classification failed, falling back to heuristics: {e}")

        return self._classify_with_heuristics(user_input)

    def _classify_with_llm(self, user_input: str) -> IntentResult:
        """
        LLM-powered classification using Together AI function calling.

        Uses tool_choice="required" to guarantee structured output.
        """
        from agent.core.llm_provider import LLMToolCallError

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
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

    def _classify_with_heuristics(self, user_input: str) -> IntentResult:
        """
        Keyword-based fallback classifier.

        Used when:
            - No LLM provider is configured
            - LLM API call fails (network, rate limit, etc.)
        """
        lower_input = user_input.lower()

        # High Confidence FIX
        if "fix" in lower_input and "error" in lower_input:
            return IntentResult(
                intent=TaskIntent.FIX,
                confidence=0.95,
                reasoning="Explicit 'fix' and 'error' keywords present.",
            )

        # High Confidence FEATURE
        if "create" in lower_input or "add feature" in lower_input:
            return IntentResult(
                intent=TaskIntent.FEATURE,
                confidence=0.90,
                reasoning="Explicit creation keywords present.",
            )

        # High Confidence REFACTOR
        if "refactor" in lower_input or "clean up" in lower_input:
            return IntentResult(
                intent=TaskIntent.REFACTOR,
                confidence=0.85,
                reasoning="Explicit refactoring keywords present.",
            )

        # Low Confidence / Ambiguous (The "Ambiguity Fallback" Trigger)
        if "check" in lower_input or "maybe" in lower_input:
            return IntentResult(
                intent=TaskIntent.EXPLAIN,
                confidence=0.40,
                reasoning="Vague request ('check', 'maybe'). Unsure if actionable.",
                clarification_needed=True,
                suggested_question="Do you want me to just analyze the code (EXPLAIN) or fix issues I find (FIX)?",
            )

        # Default: Unknown/Explain with low confidence
        return IntentResult(
            intent=TaskIntent.EXPLAIN,
            confidence=0.50,
            reasoning="No strong keywords found.",
            clarification_needed=True,
            suggested_question="I'm not sure what you want to do. Can you verify?",
        )

    def get_fallback_intent(self) -> TaskIntent:
        """Return the safe fallback intent for low confidence."""
        return TaskIntent.EXPLAIN
