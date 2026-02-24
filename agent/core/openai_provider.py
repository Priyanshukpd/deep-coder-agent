"""
OpenAI LLM Provider — Standard adapter for OpenAI-compatible APIs.

Compatible with: OpenAI, OpenRouter, Ollama, vLLM, LM Studio, and any
OpenAI-compatible endpoint.

New in this version:
  - Real-time token streaming to terminal (Codex-style OutputTextDelta)
  - Reasoning/thinking token display (🧠 dimmed block before answer)
  - reasoning_effort parameter for o1/o3/DeepSeek-R1/Apriel-Thinker
  - reasoning_tokens separate tracking in usage stats
"""

from __future__ import annotations

import json
import sys
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Iterator

from agent.config import AgentConfig, LLMConfig
from agent.core.llm_provider import (
    LLMProviderError, LLMConnectionError, LLMResponseError, LLMToolCallError,
    CompletionResult, ToolCallResult
)

logger = logging.getLogger(__name__)

# Models that support/need reasoning_effort parameter
REASONING_MODELS = {"o1", "o3", "o1-mini", "o3-mini", "deepseek-r1", "apriel", "thinker"}

# ANSI codes for dimmed/italic reasoning display
_DIM = "\033[2m\033[3m"
_RESET = "\033[0m"


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return any(r in m for r in REASONING_MODELS)


class OpenAIProvider:
    """
    Wrapper around the OpenAI Python SDK.
    Compatible with OpenAI, OpenRouter, Ollama, vLLM, etc.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = None
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_reasoning_tokens = 0
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
        stream_options: bool = False,
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

        # reasoning_effort for models that support it
        if _is_reasoning_model(cfg.model):
            effort = cfg.reasoning_effort or "high"
            params["reasoning_effort"] = effort

        # stream_options to get usage in stream
        if stream and stream_options:
            params["stream_options"] = {"include_usage": True}

        # Add tools if present
        if tools:
            params["tools"] = tools
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

        # Use streaming if requested or if configured globally
        use_stream = stream or getattr(self.config, "enable_streaming", False)
        params = self._build_params(
            messages, tools, tool_choice, use_stream, llm_config,
            stream_options=use_stream
        )

        logger.info(
            f"OpenAI call: model={cfg.model}, "
            f"provider={getattr(self.config, 'provider', 'openai')}, "
            f"stream={use_stream}"
        )

        try:
            response = client.chat.completions.create(**params)
            self.call_count += 1
        except Exception as e:
            raise LLMConnectionError(f"OpenAI API call failed: {e}") from e

        if use_stream:
            return self._stream_to_terminal(response, cfg)
        else:
            self._track_usage(response)
            return self._parse_response(response, cfg)

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "required",
        llm_config: Optional[LLMConfig] = None,
    ) -> ToolCallResult:
        """Force a tool call (no streaming — needs structured output)."""
        cfg = llm_config or self.config.llm
        params = self._build_params(messages, tools, tool_choice, False, llm_config)

        try:
            client = self._get_client()
            response = client.chat.completions.create(**params)
            self.call_count += 1
            self._track_usage(response)
        except Exception as e:
            raise LLMConnectionError(f"OpenAI API call failed: {e}") from e

        result = self._parse_response(response, cfg)
        if not result.has_tool_calls:
            raise LLMToolCallError(
                f"Expected tool call but got text: {result.content[:200] if result.content else '(empty)'}"
            )
        return result.first_tool_call

    def _stream_to_terminal(self, stream_response: Any, cfg: LLMConfig) -> CompletionResult:
        """
        Stream tokens to terminal live (Codex OutputTextDelta style).

        Features:
          - Print reasoning_content deltas as dim/italic 🧠 Thinking... block
          - Print content deltas directly to stdout as they arrive
          - Collect full content for return value
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_parts: dict[int, dict] = {}
        finish_reason = None

        in_reasoning = False
        reasoning_started = False
        content_started = False

        try:
            for chunk in stream_response:
                # Usage chunk (final chunk with usage info)
                if hasattr(chunk, 'usage') and chunk.usage and not hasattr(chunk, 'choices'):
                    self._track_usage(chunk)
                    continue

                if not hasattr(chunk, 'choices') or not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # ── Reasoning tokens (DeepSeek-R1, o1, o3) ──────────────
                reasoning_delta = getattr(delta, 'reasoning_content', None)
                if reasoning_delta:
                    if not reasoning_started:
                        sys.stdout.write(f"\n{_DIM}🧠 Thinking...{_RESET}\n{_DIM}")
                        sys.stdout.flush()
                        reasoning_started = True
                        in_reasoning = True
                    sys.stdout.write(f"{_DIM}{reasoning_delta}{_RESET}")
                    sys.stdout.flush()
                    reasoning_parts.append(reasoning_delta)

                # ── Regular content tokens ────────────────────────────────
                content_delta = getattr(delta, 'content', None)
                if content_delta:
                    if in_reasoning:
                        # End reasoning block
                        sys.stdout.write(f"\n{_RESET}\n")
                        sys.stdout.flush()
                        in_reasoning = False
                    if not content_started:
                        content_started = True
                    sys.stdout.write(content_delta)
                    sys.stdout.flush()
                    content_parts.append(content_delta)

                # ── Tool call deltas ──────────────────────────────────────
                tc_deltas = getattr(delta, 'tool_calls', None)
                if tc_deltas:
                    for tc_delta in tc_deltas:
                        idx = getattr(tc_delta, 'index', 0)
                        if idx not in tool_call_parts:
                            tool_call_parts[idx] = {"name": "", "args_chunks": []}
                        fn = getattr(tc_delta, 'function', None)
                        if fn:
                            if getattr(fn, 'name', None):
                                tool_call_parts[idx]["name"] = fn.name
                            if getattr(fn, 'arguments', None):
                                tool_call_parts[idx]["args_chunks"].append(fn.arguments)

                # ── Finish reason ─────────────────────────────────────────
                fr = getattr(choice, 'finish_reason', None)
                if fr:
                    finish_reason = fr

        except Exception as e:
            raise LLMConnectionError(f"Stream interrupted: {e}") from e
        finally:
            # Ensure cursor is on a new line
            if content_started or reasoning_started:
                sys.stdout.write("\n")
                sys.stdout.flush()

        # Assemble tool calls from streamed parts
        tool_calls = []
        for idx in sorted(tool_call_parts.keys()):
            tc = tool_call_parts[idx]
            args_str = "".join(tc["args_chunks"])
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse streamed tool call args: {args_str[:200]}")
                args = {}
            tool_calls.append(ToolCallResult(
                function_name=tc["name"],
                arguments=args,
            ))

        return CompletionResult(
            content="".join(content_parts) if content_parts else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            sampling_policy_hash=cfg.sampling_policy_hash,
            reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
        )

    def _parse_response(self, response: Any, cfg: LLMConfig) -> CompletionResult:
        """Parse non-streamed response."""
        try:
            choice = response.choices[0]
            message = choice.message

            tool_calls = []
            if getattr(message, 'tool_calls', None):
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

            # Reasoning content (non-streamed, e.g. DeepSeek-R1)
            reasoning = getattr(message, 'reasoning_content', None)
            if reasoning:
                print(f"\n{_DIM}🧠 Thinking...{_RESET}\n{_DIM}{reasoning[:2000]}{_RESET}\n")

            return CompletionResult(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason,
                sampling_policy_hash=cfg.sampling_policy_hash,
                raw_response=response,
                reasoning_content=reasoning,
            )
        except Exception as e:
            raise LLMResponseError(f"Failed to parse OpenAI response: {e}") from e

    def _track_usage(self, response: Any) -> None:
        """Track token usage including reasoning tokens."""
        try:
            usage = getattr(response, 'usage', None)
            if not usage:
                return
            input_tok = getattr(usage, 'prompt_tokens', 0) or 0
            output_tok = getattr(usage, 'completion_tokens', 0) or 0
            self.total_input_tokens += input_tok
            self.total_output_tokens += output_tok
            self.total_tokens += input_tok + output_tok

            # Reasoning tokens (OpenAI o1/o3)
            details = getattr(usage, 'completion_tokens_details', None)
            if details:
                reasoning_tok = getattr(details, 'reasoning_tokens', 0) or 0
                self.total_reasoning_tokens += reasoning_tok
                if reasoning_tok:
                    logger.info(f"Reasoning tokens: {reasoning_tok}")

            logger.info(
                f"Usage: +{input_tok} in, +{output_tok} out "
                f"(total: {self.total_tokens})"
            )
        except Exception:
            pass

    def _collect_stream(self, stream_response: Any, cfg: LLMConfig) -> CompletionResult:
        """Alias for backward compat — uses _stream_to_terminal."""
        return self._stream_to_terminal(stream_response, cfg)
