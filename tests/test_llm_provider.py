"""
Tests for the Together AI LLM Provider.

All tests mock the Together SDK — no real API calls.
"""

import unittest
from unittest.mock import patch, MagicMock
import json

from agent.config import AgentConfig, LLMConfig
from agent.core.llm_provider import (
    TogetherProvider,
    CompletionResult,
    ToolCallResult,
    LLMProviderError,
    LLMConnectionError,
    LLMResponseError,
    LLMToolCallError,
)


def _make_provider(api_key: str = "test-key-123") -> tuple[AgentConfig, TogetherProvider]:
    """Create a provider with a test config."""
    config = AgentConfig(together_api_key=api_key)
    return config, TogetherProvider(config)


class TestLLMConfig(unittest.TestCase):
    """Test the configuration layer."""

    def test_default_config_is_deterministic(self):
        cfg = LLMConfig()
        self.assertEqual(cfg.temperature, 0.0)
        self.assertEqual(cfg.top_p, 1.0)
        self.assertEqual(cfg.top_k, 1)

    def test_sampling_policy_hash_is_stable(self):
        cfg = LLMConfig()
        hash1 = cfg.sampling_policy_hash
        hash2 = cfg.sampling_policy_hash
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 16)

    def test_different_config_different_hash(self):
        cfg1 = LLMConfig(temperature=0.0)
        cfg2 = LLMConfig(temperature=0.5)
        self.assertNotEqual(cfg1.sampling_policy_hash, cfg2.sampling_policy_hash)

    def test_api_key_from_env(self):
        with patch.dict("os.environ", {"TOGETHER_API_KEY": "env-key-456"}):
            config = AgentConfig()
            self.assertEqual(config.together_api_key, "env-key-456")
            self.assertTrue(config.has_api_key)

    def test_missing_api_key(self):
        config = AgentConfig(together_api_key="")
        self.assertFalse(config.has_api_key)


class TestTogetherProvider(unittest.TestCase):
    """Test the Together provider wrapper."""

    def test_missing_api_key_raises(self):
        config = AgentConfig(together_api_key="")
        provider = TogetherProvider(config)
        with self.assertRaises(LLMProviderError) as cm:
            provider._get_client()
        self.assertIn("TOGETHER_API_KEY", str(cm.exception))

    def test_complete_with_text_response(self):
        """Test a standard text completion (no tools)."""
        mock_client = MagicMock()

        mock_message = MagicMock()
        mock_message.content = "Hello, world!"
        mock_message.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        _, provider = _make_provider()
        provider._client = mock_client  # Inject mock directly
        result = provider.complete(messages=[{"role": "user", "content": "hi"}])

        self.assertIsInstance(result, CompletionResult)
        self.assertEqual(result.content, "Hello, world!")
        self.assertFalse(result.has_tool_calls)

    def test_complete_with_tool_call_response(self):
        """Test parsing a tool/function call response."""
        mock_client = MagicMock()

        mock_tc = MagicMock()
        mock_tc.function.name = "classify_intent"
        mock_tc.function.arguments = json.dumps({
            "intent": "fix",
            "confidence": 0.95,
            "reasoning": "Bug fix request",
            "clarification_needed": False,
            "suggested_question": "",
        })

        mock_message = MagicMock()
        mock_message.content = None
        mock_message.tool_calls = [mock_tc]

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "tool_calls"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        _, provider = _make_provider()
        provider._client = mock_client
        result = provider.complete(
            messages=[{"role": "user", "content": "fix the NPE"}],
            tools=[{"type": "function", "function": {"name": "classify_intent"}}],
        )

        self.assertTrue(result.has_tool_calls)
        tc = result.first_tool_call
        self.assertEqual(tc.function_name, "classify_intent")
        self.assertEqual(tc.arguments["intent"], "fix")
        self.assertEqual(tc.arguments["confidence"], 0.95)

    def test_complete_with_tools_raises_on_text(self):
        """complete_with_tools should raise if model returns text instead of tool call."""
        mock_client = MagicMock()

        mock_message = MagicMock()
        mock_message.content = "I don't know"
        mock_message.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        _, provider = _make_provider()
        provider._client = mock_client

        with self.assertRaises(LLMToolCallError):
            provider.complete_with_tools(
                messages=[{"role": "user", "content": "what?"}],
                tools=[{"type": "function", "function": {"name": "test"}}],
            )

    def test_connection_error_handling(self):
        """API errors should be wrapped in LLMConnectionError."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("timeout")

        _, provider = _make_provider()
        provider._client = mock_client

        with self.assertRaises(LLMConnectionError):
            provider.complete(messages=[{"role": "user", "content": "hi"}])

    def test_sampling_hash_in_result(self):
        """Verify that sampling_policy_hash is included in every result."""
        cfg = LLMConfig()
        result = CompletionResult(
            content="test",
            sampling_policy_hash=cfg.sampling_policy_hash,
        )
        self.assertTrue(len(result.sampling_policy_hash) > 0)


class TestStreamParsing(unittest.TestCase):
    """Test streaming response collection."""

    def test_collect_streamed_content(self):
        """Test that streamed content tokens are reassembled."""
        mock_client = MagicMock()

        class Chunk:
            def __init__(self, content, finish_reason=None):
                self.choices = [self.Choice(content, finish_reason)]
            class Choice:
                def __init__(self, content, finish_reason):
                    self.delta = self.Delta(content)
                    self.finish_reason = finish_reason
                class Delta:
                    def __init__(self, content):
                        self.content = content
                        self.tool_calls = None

        # Create stream chunks
        chunks = [
            Chunk("Hello"),
            Chunk(", "),
            Chunk("world"),
            Chunk("!"),
            Chunk(None, finish_reason="stop")
        ]

        mock_client.chat.completions.create.return_value = iter(chunks)

        _, provider = _make_provider()
        provider._client = mock_client
        result = provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )

        self.assertEqual(result.content, "Hello, world!")
        self.assertEqual(result.finish_reason, "stop")


if __name__ == "__main__":
    unittest.main()
