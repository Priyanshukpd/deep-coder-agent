"""
Together AI LLM Provider — Thin wrapper around the Together SDK.

Responsibilities:
1. Send chat completions with tool/function calling support.
2. Parse structured tool call responses.
3. Support streaming for interactive use cases.
4. Compute and expose sampling_policy_hash for audit (Architecture §2.C).

Architecture Constraint: System prompt mutation mid-run is FORBIDDEN (§2.C).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.config import AgentConfig, LLMConfig

logger = logging.getLogger(__name__)


# -- Exceptions --

class LLMProviderError(Exception):
    """Base exception for LLM provider errors."""
    pass


class LLMConnectionError(LLMProviderError):
    """Failed to connect to the Together API."""
    pass


class LLMResponseError(LLMProviderError):
    """Malformed or unexpected response from the LLM."""
    pass


class LLMToolCallError(LLMProviderError):
    """Expected a tool call but didn't get one, or tool call args are invalid."""
    pass


# -- Response Types --

@dataclass
class ToolCallResult:
    """Parsed result of a function/tool call from the LLM."""
    function_name: str
    arguments: dict[str, Any]
    raw_response: Any = field(default=None, repr=False)


@dataclass
class CompletionResult:
    """Result of a chat completion."""
    content: Optional[str] = None
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    finish_reason: Optional[str] = None
    sampling_policy_hash: str = ""
    raw_response: Any = field(default=None, repr=False)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def first_tool_call(self) -> Optional[ToolCallResult]:
        return self.tool_calls[0] if self.tool_calls else None


# -- Provider --

class TogetherProvider:
    """
    Wrapper around the Together Python SDK.

    Usage:
        config = AgentConfig()
        provider = TogetherProvider(config)
        result = provider.complete(messages=[...], tools=[...])
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = None  # Lazy init
        self.call_count = 0   # Track total LLM calls
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0

    def _get_client(self):
        """Lazy-initialize the Together client."""
        if self._client is None:
            try:
                from together import Together
            except ImportError:
                raise LLMProviderError(
                    "The 'together' package is not installed. "
                    "Run: pip install together"
                )

            if not self.config.has_api_key:
                raise LLMProviderError(
                    "TOGETHER_API_KEY environment variable is not set."
                )

            self._client = Together(api_key=self.config.together_api_key)

        return self._client

    def _build_params(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False,
        llm_config: Optional[LLMConfig] = None,
    ) -> dict:
        """Build the API call parameters from config."""
        cfg = llm_config or self.config.llm

        params = {
            "model": cfg.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "top_k": cfg.top_k,
            "repetition_penalty": cfg.repetition_penalty,
            "presence_penalty": cfg.presence_penalty,
            "frequency_penalty": cfg.frequency_penalty,
        }

        # Only include reasoning_effort if set
        if cfg.reasoning_effort:
            params["reasoning_effort"] = cfg.reasoning_effort

        # Only include min_p if non-zero (not all APIs support it)
        if cfg.min_p > 0:
            params["min_p"] = cfg.min_p

        # Tools
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
        """
        Send a chat completion request.

        Args:
            messages: Chat messages in OpenAI format.
            tools: Optional tool/function definitions.
            tool_choice: "auto", "required", or "none".
            stream: If True, collects streamed tokens into final result.
            llm_config: Override the default LLM config for this call.

        Returns:
            CompletionResult with content and/or tool calls.

        Raises:
            LLMConnectionError: If the API is unreachable.
            LLMResponseError: If the response is malformed.
        """
        client = self._get_client()
        cfg = llm_config or self.config.llm
        params = self._build_params(messages, tools, tool_choice, stream, llm_config)

        logger.info(
            f"LLM call: model={cfg.model}, "
            f"sampling_hash={cfg.sampling_policy_hash}, "
            f"stream={stream}, "
            f"tools={len(tools) if tools else 0}"
        )

        try:
            response = client.chat.completions.create(**params)
            self.call_count += 1
            # Extract token usage from response
            self._track_usage(response)
        except Exception as e:
            raise LLMConnectionError(f"Together API call failed: {e}") from e

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
        """
        Send a completion that MUST return a tool call.

        Convenience method that forces tool_choice and extracts the first tool call.

        Args:
            messages: Chat messages.
            tools: Tool definitions (must have at least one).
            tool_choice: Defaults to "required" to guarantee a function call.
            llm_config: Override config for this call.

        Returns:
            ToolCallResult with parsed function name and arguments.

        Raises:
            LLMToolCallError: If no tool call was returned.
        """
        result = self.complete(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=False,
            llm_config=llm_config,
        )

        if not result.has_tool_calls:
            # Phase 75: Heuristic fallback for non-native tool calling models
            if result.content:
                import re
                # Try to extract JSON from common markdown blocks or raw text
                json_match = re.search(r"```json\s*(\{.*?\})\s*```", result.content, re.DOTALL) or \
                             re.search(r"(\{.*?\})", result.content, re.DOTALL)
                
                if json_match:
                    try:
                        args = json.loads(json_match.group(1))
                        # If it looks like a tool call, wrap it
                        if "thought" in args and "action" in args:
                            return ToolCallResult(
                                function_name="decide_step",
                                arguments=args,
                                raw_response=result.content
                            )
                    except json.JSONDecodeError:
                        pass

            raise LLMToolCallError(
                f"Expected a tool call but got text response: "
                f"{result.content[:200] if result.content else '(empty)'}"
            )

        return result.first_tool_call

    def _parse_response(self, response: Any, cfg: LLMConfig) -> CompletionResult:
        """Parse a non-streamed response."""
        try:
            choice = response.choices[0]
            message = choice.message

            # Parse tool calls if present
            tool_calls = []
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tc in message.tool_calls:
                    args = tc.function.arguments
                    if isinstance(args, str):
                        args = json.loads(args)
                    tool_calls.append(ToolCallResult(
                        function_name=tc.function.name,
                        arguments=args,
                        raw_response=tc,
                    ))

            return CompletionResult(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason if hasattr(choice, 'finish_reason') else None,
                sampling_policy_hash=cfg.sampling_policy_hash,
                raw_response=response,
            )
        except (AttributeError, IndexError, json.JSONDecodeError) as e:
            raise LLMResponseError(f"Failed to parse LLM response: {e}") from e

    def _track_usage(self, response: Any) -> None:
        """Extract and accumulate token usage from an API response."""
        try:
            if hasattr(response, 'usage') and response.usage:
                usage = response.usage
                input_tok = getattr(usage, 'prompt_tokens', 0) or 0
                output_tok = getattr(usage, 'completion_tokens', 0) or 0
                self.total_input_tokens += input_tok
                self.total_output_tokens += output_tok
                self.total_tokens += input_tok + output_tok
                logger.info(f"Tokens: +{input_tok} in, +{output_tok} out (total: {self.total_tokens})")
        except Exception:
            pass  # Don't crash on usage parsing failures

    def _collect_stream(self, stream_response: Any, cfg: LLMConfig) -> CompletionResult:
        """Collect and stream tokens to terminal live."""
        import sys
        content_parts: list[str] = []
        tool_call_parts: dict[int, dict] = {}  # index -> {name, args_chunks}
        finish_reason = None
        content_started = False

        try:
            for chunk in stream_response:
                # Handle usage in stream if provided
                if hasattr(chunk, 'usage') and chunk.usage:
                    self._track_usage(chunk)
                    continue

                if not hasattr(chunk, 'choices') or not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # ── Content deltas ────────────────────────────────────────
                content_delta = getattr(delta, 'content', None)
                if content_delta:
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
            raise LLMConnectionError(f"Together stream interrupted: {e}") from e
        finally:
            if content_started:
                sys.stdout.write("\n")
                sys.stdout.flush()

        # Assemble tool calls
        tool_calls = []
        for idx in sorted(tool_call_parts.keys()):
            tc = tool_call_parts[idx]
            args_str = "".join(tc["args_chunks"])
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCallResult(
                function_name=tc["name"],
                arguments=args,
            ))

        return CompletionResult(
            content="".join(content_parts) if content_parts else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            sampling_policy_hash=cfg.sampling_policy_hash
        )
