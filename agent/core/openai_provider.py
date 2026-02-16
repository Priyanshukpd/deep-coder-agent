"""
OpenAI LLM Provider — Standard adapter for OpenAI-compatible APIs (including vLLM, Ollama, Local).

Responsibilities:
1. Send chat completions to any OpenAI-compatible endpoint.
2. support tool calling (if supported by backend).
3. Support streaming.
4. Compute sampling_policy_hash.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.config import AgentConfig, LLMConfig
# Re-use exception types/data classes from llm_provider to keep interface consistent
from agent.core.llm_provider import (
    LLMProviderError, LLMConnectionError, LLMResponseError, LLMToolCallError,
    CompletionResult, ToolCallResult
)

logger = logging.getLogger(__name__)

class OpenAIProvider:
    """
    Wrapper around the OpenAI Python SDK.
    Compatible with OpenAI, vLLM, Ollama, LM Studio, etc.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = None
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0

    def _get_client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise LLMProviderError(
                    "The 'openai' package is not installed. "
                    "Run: pip install openai"
                )

            self._client = OpenAI(
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url
            )
        return self._client

    def _build_params(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False,
        llm_config: Optional[LLMConfig] = None,
    ) -> dict:
        """Build the API call parameters."""
        cfg = llm_config or self.config.llm

        params = {
            "model": cfg.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "presence_penalty": cfg.presence_penalty,
            "frequency_penalty": cfg.frequency_penalty,
        }
        
        # Add tools if present
        if tools:
            params["tools"] = tools
            # Map "required" to "auto" or specific choice if needed, 
            # but OpenAI supports "required" (or "auto").
            # However, some local models might glitch with "required".
            params["tool_choice"] = tool_choice or cfg.tool_choice

        return params

    def complete(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False,
        llm_config: Optional[LLMConfig] = None,
    ) -> CompletionResult:
        """Send chat completion request."""
        client = self._get_client()
        cfg = llm_config or self.config.llm
        params = self._build_params(messages, tools, tool_choice, stream, llm_config)

        logger.info(
            f"OpenAI call: model={cfg.model}, "
            f"url={self.config.openai_base_url}, "
            f"stream={stream}"
        )

        try:
            response = client.chat.completions.create(**params)
            self.call_count += 1
            if not stream:
                self._track_usage(response)
        except Exception as e:
            raise LLMConnectionError(f"OpenAI API call failed: {e}") from e

        if stream:
            return self._collect_stream(response, cfg)
        else:
            return self._parse_response(response, cfg)

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "required",
        llm_config: Optional[LLMConfig] = None,
    ) -> ToolCallResult:
        """Force a tool call."""
        result = self.complete(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=False,
            llm_config=llm_config,
        )

        if not result.has_tool_calls:
            raise LLMToolCallError(
                f"Expected tool call but got text: {result.content[:200]}"
            )
        return result.first_tool_call

    def _parse_response(self, response: Any, cfg: LLMConfig) -> CompletionResult:
        """Parse non-streamed response."""
        try:
            choice = response.choices[0]
            message = choice.message
            
            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    args = tc.function.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse tool args: {args}")
                            args = {}
                    tool_calls.append(ToolCallResult(
                        function_name=tc.function.name,
                        arguments=args,
                        raw_response=tc
                    ))

            return CompletionResult(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason,
                sampling_policy_hash=cfg.sampling_policy_hash,
                raw_response=response
            )
        except Exception as e:
            raise LLMResponseError(f"Failed to parse OpenAI response: {e}") from e

    def _track_usage(self, response: Any) -> None:
        """Track token usage."""
        try:
            if hasattr(response, 'usage') and response.usage:
                u = response.usage
                self.total_input_tokens += u.prompt_tokens
                self.total_output_tokens += u.completion_tokens
                self.total_tokens = self.total_input_tokens + self.total_output_tokens
        except:
            pass

    def _collect_stream(self, stream_response: Any, cfg: LLMConfig) -> CompletionResult:
        """Collect streamed chunks."""
        content_parts = []
        finish_reason = None
        # Note: Local models might not support streaming tool calls perfectly.
        # We assume standard OpenAI delta structure.
        
        try:
            for chunk in stream_response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
        except Exception as e:
            raise LLMConnectionError(f"Stream interrupted: {e}")

        return CompletionResult(
            content="".join(content_parts),
            tool_calls=[], # Streaming tool calls not fully implemented for brevity
            finish_reason=finish_reason,
            sampling_policy_hash=cfg.sampling_policy_hash
        )
