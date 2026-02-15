"""
Human Feedback Loop — Structured feedback collection and routing.

Manages the agent's interaction with human reviewers:
    - FEEDBACK_WAIT state management
    - Approval/rejection handling
    - Timeout enforcement (24h per Architecture §1)
    - Feedback routing back to appropriate state
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable

from agent.state import AgentState

logger = logging.getLogger(__name__)


# Architecture §1 FEEDBACK_WAIT: Timeout: 24h
FEEDBACK_TIMEOUT_HOURS = 24


class FeedbackAction(Enum):
    """Possible human feedback actions."""
    APPROVE = auto()         # Proceed with plan/merge
    REJECT = auto()          # Stop and fail
    REQUEST_CHANGES = auto() # Go back to PLANNING
    CLARIFY_INTENT = auto()  # Go back to INTENT_ANALYSIS
    MANUAL_RESOLVE = auto()  # Mark as COMPLETE (human fixed it)


@dataclass
class FeedbackRequest:
    """Request for human feedback."""
    request_id: str
    context: str                # What we're asking about
    question: str               # Specific question for the human
    options: list[str] = field(default_factory=list)
    required_role: str = "DEVELOPER"   # Minimum role for approval
    timestamp: float = field(default_factory=time.time)
    timeout_hours: float = FEEDBACK_TIMEOUT_HOURS

    @property
    def is_expired(self) -> bool:
        elapsed = time.time() - self.timestamp
        return elapsed > (self.timeout_hours * 3600)

    @property
    def remaining_hours(self) -> float:
        elapsed = time.time() - self.timestamp
        remaining = (self.timeout_hours * 3600) - elapsed
        return max(0.0, remaining / 3600)


@dataclass
class FeedbackResponse:
    """Human response to a feedback request."""
    request_id: str
    action: FeedbackAction
    comment: str = ""
    timestamp: float = field(default_factory=time.time)


# Action → Next state mapping
FEEDBACK_STATE_MAP: dict[FeedbackAction, AgentState] = {
    FeedbackAction.APPROVE: AgentState.IMPLEMENTING,
    FeedbackAction.REJECT: AgentState.FAILED,
    FeedbackAction.REQUEST_CHANGES: AgentState.PLANNING,
    FeedbackAction.CLARIFY_INTENT: AgentState.INTENT_ANALYSIS,
    FeedbackAction.MANUAL_RESOLVE: AgentState.COMPLETE,
}


class FeedbackLoop:
    """
    Manages the human feedback loop for the agent.
    
    Handles feedback requests, responses, timeouts, and state routing.
    """

    def __init__(self):
        self._pending: dict[str, FeedbackRequest] = {}
        self._history: list[tuple[FeedbackRequest, FeedbackResponse]] = []

    def request_feedback(
        self,
        request_id: str,
        context: str,
        question: str,
        options: list[str] = None,
    ) -> FeedbackRequest:
        """
        Create a feedback request and enter FEEDBACK_WAIT state.
        """
        req = FeedbackRequest(
            request_id=request_id,
            context=context,
            question=question,
            options=options or [],
        )
        self._pending[request_id] = req
        logger.info(
            f"Feedback requested [{request_id}]: {question} "
            f"(timeout: {req.timeout_hours}h)"
        )
        return req

    def respond(
        self,
        request_id: str,
        action: FeedbackAction,
        comment: str = "",
    ) -> AgentState:
        """
        Process a human response and determine the next state.
        
        Returns the AgentState to transition to.
        """
        if request_id not in self._pending:
            logger.warning(f"Unknown feedback request: {request_id}")
            return AgentState.FAILED

        request = self._pending.pop(request_id)

        if request.is_expired:
            logger.warning(
                f"Feedback timed out for [{request_id}] "
                f"after {FEEDBACK_TIMEOUT_HOURS}h"
            )
            return AgentState.FAILED

        response = FeedbackResponse(
            request_id=request_id,
            action=action,
            comment=comment,
        )
        self._history.append((request, response))

        next_state = FEEDBACK_STATE_MAP.get(action, AgentState.FAILED)
        logger.info(
            f"Feedback [{request_id}]: {action.name} → {next_state.name}"
            + (f" ({comment})" if comment else "")
        )
        return next_state

    def check_timeouts(self) -> list[str]:
        """Check for expired feedback requests. Returns expired IDs."""
        expired = [
            rid for rid, req in self._pending.items()
            if req.is_expired
        ]
        for rid in expired:
            logger.warning(f"Feedback request [{rid}] expired")
        return expired

    @property
    def pending_count(self) -> int:
        return len(self._pending)
