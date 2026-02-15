"""
Decision Logger — Structured audit trail for the agent.

Records every state transition, tool call, risk check, and human approval
in a structured JSON format for replayability and compliance.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.state import AgentState, TaskIntent, StateSnapshot
from agent.mechanisms.risk_budget import BudgetViolation
from agent.security.command_safety import CommandClassification
from agent.mechanisms.approval import ApprovalRequest, ApprovalAction


@dataclass
class AuditEntry:
    """A single record in the decision log."""

    timestamp: float
    event_type: str            # TRANSITION, TOOL_USE, RISK_VIOLATION, APPROVAL, etc.
    details: dict[str, Any]
    agent_id: str = "god-mode-v1"
    session_id: str = "unknown"


class DecisionLogger:
    """
    Writes structured JSON logs to disk.
    Also handles standard lib logging for console output.
    """

    def __init__(self, log_dir: str = "logs", session_id: str = "session"):
        self.session_id = session_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # JSON lines log file
        timestamp = int(time.time())
        self.log_file = self.log_dir / f"decision_log_{session_id}_{timestamp}.jsonl"
        
        # Configure standard logging for console/debug
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(self.log_dir / "agent_debug.log"),
            ],
        )
        self.logger = logging.getLogger("DecisionLogger")

    def _write(self, event_type: str, details: dict[str, Any]):
        """Write an entry to the JSONL log."""
        entry = AuditEntry(
            timestamp=time.time(),
            event_type=event_type,
            details=details,
            session_id=self.session_id,
        )
        
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
        except Exception as e:
            self.logger.error(f"Failed to write decision log: {e}")

    # -- Typed Log Methods --

    def log_transition(self, snapshot: StateSnapshot):
        """Log a state transition."""
        self.logger.info(
            f"State Change: {snapshot.from_state.name} -> {snapshot.to_state.name} "
            f"({snapshot.reason})"
        )
        self._write("STATE_TRANSITION", {
            "from": snapshot.from_state.name,
            "to": snapshot.to_state.name,
            "reason": snapshot.reason,
            "metadata": snapshot.metadata,
        })

    def log_intent(self, intent: str, confidence: float, reasoning: str):
        """Log intent classification."""
        self.logger.info(f"Intent Classified: {intent} ({confidence:.2f})")
        self._write("INTENT_CLASSIFIED", {
            "intent": intent,
            "confidence": confidence,
            "reasoning": reasoning,
        })

    def log_risk_violation(self, violation: BudgetViolation):
        """Log a risk budget violation."""
        self.logger.warning(
            f"Risk Violation: {violation.dimension.name} - {violation.message}"
        )
        self._write("RISK_VIOLATION", {
            "dimension": violation.dimension.name,
            "current_value": violation.current_value,
            "limit": violation.limit,
            "message": violation.message,
        })

    def log_command_check(self, cmd_classification: CommandClassification, allowed: bool):
        """Log a command safety check."""
        status = "ALLOWED" if allowed else "BLOCKED"
        self.logger.info(f"Command {status}: {cmd_classification.command} [{cmd_classification.tier.name}]")
        self._write("COMMAND_CHECK", {
            "command": cmd_classification.command,
            "tier": cmd_classification.tier.name,
            "policy": cmd_classification.policy.name,
            "allowed": allowed,
            "reasoning": cmd_classification.reasoning,
        })

    def log_approval_request(self, request: ApprovalRequest):
        """Log that human approval was requested."""
        self.logger.info(f"Approval Requested: {request.approval_type.name} - {request.summary}")
        self._write("APPROVAL_REQUESTED", {
            "request_id": request.request_id,
            "type": request.approval_type.name,
            "summary": request.summary,
            "timeout": request.timeout_seconds,
        })

    def log_approval_response(self, request_id: str, action: ApprovalAction, comment: str = ""):
        """Log the human's response."""
        self.logger.info(f"Approval Response: {action.name} for {request_id}")
        self._write("APPROVAL_RESPONSE", {
            "request_id": request_id,
            "action": action.name,
            "comment": comment,
        })
