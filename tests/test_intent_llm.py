"""
Tests for the LLM-powered Intent Classifier.

Tests both the LLM path (mocked) and the heuristic fallback path.
"""

import unittest
from unittest.mock import MagicMock, patch

from agent.state import TaskIntent, IntentResult
from agent.planning.intent import IntentClassifier, _INTENT_MAP
from agent.core.llm_provider import ToolCallResult, LLMConnectionError


class TestIntentClassifierLLM(unittest.TestCase):
    """Test the LLM-powered classification path."""

    def _make_classifier_with_mock_provider(self, tool_result: ToolCallResult):
        """Create a classifier with a mocked provider that returns the given tool result."""
        mock_provider = MagicMock()
        mock_provider.complete_with_tools.return_value = tool_result
        return IntentClassifier(provider=mock_provider), mock_provider

    def test_llm_fix_intent(self):
        tool_result = ToolCallResult(
            function_name="classify_intent",
            arguments={
                "intent": "fix",
                "confidence": 0.95,
                "reasoning": "User wants to fix a bug",
                "clarification_needed": False,
                "suggested_question": "",
            },
        )
        classifier, _ = self._make_classifier_with_mock_provider(tool_result)
        result = classifier.classify("Fix the NPE in AuthService")

        self.assertEqual(result.intent, TaskIntent.FIX)
        self.assertAlmostEqual(result.confidence, 0.95)
        self.assertTrue(result.is_confident)
        self.assertFalse(result.clarification_needed)

    def test_llm_feature_intent(self):
        tool_result = ToolCallResult(
            function_name="classify_intent",
            arguments={
                "intent": "feature",
                "confidence": 0.88,
                "reasoning": "User wants a new feature",
                "clarification_needed": False,
                "suggested_question": "",
            },
        )
        classifier, _ = self._make_classifier_with_mock_provider(tool_result)
        result = classifier.classify("Add dark mode to the settings page")

        self.assertEqual(result.intent, TaskIntent.FEATURE)
        self.assertTrue(result.is_confident)

    def test_llm_ambiguous_intent(self):
        tool_result = ToolCallResult(
            function_name="classify_intent",
            arguments={
                "intent": "explain",
                "confidence": 0.35,
                "reasoning": "Request is vague",
                "clarification_needed": True,
                "suggested_question": "Do you want me to analyze or fix something?",
            },
        )
        classifier, _ = self._make_classifier_with_mock_provider(tool_result)
        result = classifier.classify("look at the code")

        self.assertEqual(result.intent, TaskIntent.EXPLAIN)
        self.assertFalse(result.is_confident)
        self.assertTrue(result.requires_clarification)
        self.assertIsNotNone(result.suggested_question)

    def test_all_intent_types_mapped(self):
        """Verify every TaskIntent has a mapping from string."""
        for intent in TaskIntent:
            self.assertIn(intent.value, _INTENT_MAP)
            self.assertEqual(_INTENT_MAP[intent.value], intent)

    def test_llm_confidence_is_clamped(self):
        """Confidence values outside [0, 1] should be clamped."""
        tool_result = ToolCallResult(
            function_name="classify_intent",
            arguments={
                "intent": "fix",
                "confidence": 1.5,  # Over 1.0
                "reasoning": "test",
                "clarification_needed": False,
                "suggested_question": "",
            },
        )
        classifier, _ = self._make_classifier_with_mock_provider(tool_result)
        result = classifier.classify("test")
        self.assertLessEqual(result.confidence, 1.0)

    def test_unknown_intent_falls_back_to_explain(self):
        """Unrecognized intent string should map to EXPLAIN."""
        tool_result = ToolCallResult(
            function_name="classify_intent",
            arguments={
                "intent": "unknown_intent_xyz",
                "confidence": 0.5,
                "reasoning": "test",
                "clarification_needed": True,
                "suggested_question": "What?",
            },
        )
        classifier, _ = self._make_classifier_with_mock_provider(tool_result)
        result = classifier.classify("test")
        self.assertEqual(result.intent, TaskIntent.EXPLAIN)


class TestIntentClassifierFallback(unittest.TestCase):
    """Test fallback to heuristics when LLM fails."""

    def test_fallback_on_connection_error(self):
        """If LLM provider raises, fall back to keyword heuristics."""
        mock_provider = MagicMock()
        mock_provider.complete_with_tools.side_effect = LLMConnectionError("timeout")

        classifier = IntentClassifier(provider=mock_provider)
        result = classifier.classify("fix the error in login")

        # Should use heuristic path and still get FIX
        self.assertEqual(result.intent, TaskIntent.FIX)
        self.assertAlmostEqual(result.confidence, 0.95)

    def test_no_provider_uses_heuristics(self):
        """No provider = pure heuristic mode."""
        classifier = IntentClassifier(provider=None)
        result = classifier.classify("create a new dashboard")
        self.assertEqual(result.intent, TaskIntent.FEATURE)

    def test_heuristic_refactor(self):
        classifier = IntentClassifier(provider=None)
        result = classifier.classify("refactor the user module")
        self.assertEqual(result.intent, TaskIntent.REFACTOR)

    def test_heuristic_ambiguous(self):
        classifier = IntentClassifier(provider=None)
        result = classifier.classify("maybe check the performance?")
        self.assertFalse(result.is_confident)
        self.assertTrue(result.requires_clarification)

    def test_heuristic_default(self):
        """Unknown input defaults to EXPLAIN with low confidence."""
        classifier = IntentClassifier(provider=None)
        result = classifier.classify("do the thing")
        self.assertEqual(result.intent, TaskIntent.EXPLAIN)
        self.assertLess(result.confidence, 0.75)

    def test_fallback_intent_is_explain(self):
        classifier = IntentClassifier()
        self.assertEqual(classifier.get_fallback_intent(), TaskIntent.EXPLAIN)


if __name__ == "__main__":
    unittest.main()
