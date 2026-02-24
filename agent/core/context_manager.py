"""
Context Window Manager — Track and trim conversation history to avoid token limit errors.

Works with the model registry to get per-model limits.
Summarizes old conversation turns when approaching 80% capacity.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default safety margin — trim when history exceeds this % of context window
TRIM_THRESHOLD = 0.80


def estimate_tokens(text: str) -> int:
    """Quick token estimate: 1 token ≈ 4 characters."""
    return max(1, len(text) // 4)


def estimate_message_tokens(messages: list[dict]) -> int:
    """Estimate total tokens of a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += estimate_tokens(content)
        total += 4  # role + overhead per message
    return total


def trim_history(
    messages: list[dict],
    model_name: str,
    system_prompt_tokens: int = 0,
) -> list[dict]:
    """
    Trim conversation history to stay within 80% of model's context window.

    Strategy:
        1. Keep the system message (index 0) always.
        2. Keep the last 4 messages always (most recent context).
        3. Summarize/drop middle messages if approaching limit.

    Args:
        messages: Full message list including system prompt.
        model_name: Model name (looked up in registry for limit).
        system_prompt_tokens: Estimated tokens for system prompt injection.

    Returns:
        Trimmed messages list.
    """
    from agent.core.model_registry import get_model_meta

    meta = get_model_meta(model_name)
    limit = meta.context_window
    budget = int(limit * TRIM_THRESHOLD) - system_prompt_tokens

    total = estimate_message_tokens(messages)
    if total <= budget:
        return messages  # No trimming needed

    logger.info(
        f"Context trim triggered: {total} tokens estimated, budget={budget} "
        f"(model={model_name}, limit={limit})"
    )
    print(f"\n  📏 Context trim: {total} tokens → trimming old turns...")

    # Separate system + first user message from the rest
    if not messages:
        return messages

    head = []  # System message(s) always kept
    tail = []  # Last 4 messages always kept
    middle = []

    for msg in messages:
        if msg.get("role") == "system":
            head.append(msg)
        else:
            middle.append(msg)

    # Always keep the last 4 non-system messages
    if len(middle) > 4:
        tail = middle[-4:]
        middle = middle[:-4]
    else:
        tail = middle
        middle = []

    # Drop oldest middle messages until within budget
    while middle and estimate_message_tokens(head + middle + tail) > budget:
        dropped = middle.pop(0)
        logger.debug(f"Dropped old message: role={dropped.get('role')}, len={len(dropped.get('content',''))}")

    # If middle is tiny, add a summary note
    if not middle:
        summary_msg = {
            "role": "system",
            "content": "[Earlier conversation history was truncated to stay within context window limits. Continue from the current context.]"
        }
        remaining = [summary_msg] + tail
    else:
        remaining = middle + tail

    result = head + remaining
    new_total = estimate_message_tokens(result)
    logger.info(f"Context after trim: {new_total} tokens ({len(result)} messages)")
    return result
