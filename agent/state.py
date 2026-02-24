"""
Agent State Machine — Full lifecycle states and transition rules.

This is the governance backbone. Every agent action must happen
within a valid state, and transitions are deterministic.

Architecture v7.5.1.1 — Includes granular terminal states.
"""

from __future__ import annotations

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional
import time


class AgentState(Enum):
    """Full lifecycle states for the agent."""

    IDLE = auto()
    INTENT_ANALYSIS = auto()
    REPO_DISCOVERY = auto()
    PLANNING = auto()
    TASK_ISOLATION = auto()      # NEW: git branch creation
    PROVING_GROUND = auto()      # NEW: TDD red test phase
    RESEARCHING = auto()
    IMPACT_ANALYSIS = auto()
    IMPLEMENTING = auto()
    VERIFYING = auto()
    FEEDBACK_WAIT = auto()
    RETRYING = auto()
    COMPLETE = auto()

    # Granular terminal states (Architecture §1)
    FAILED = auto()              # Generic failure
    FAILED_BY_STALE = auto()     # main moved during task
    FAILED_BY_INTERRUPT = auto() # SIGINT / user stop
    FAILED_BY_TIMEOUT = auto()   # Runtime > 15m
    FAILED_BY_SCOPE = auto()     # RepoMap > MAX_FILE_CAP


# Terminal states set — used by controller for quick checks
TERMINAL_STATES = {
    AgentState.COMPLETE,
    AgentState.FAILED,
    AgentState.FAILED_BY_STALE,
    AgentState.FAILED_BY_INTERRUPT,
    AgentState.FAILED_BY_TIMEOUT,
    AgentState.FAILED_BY_SCOPE,
}

# All failure states
FAILURE_STATES = TERMINAL_STATES - {AgentState.COMPLETE}


class TaskIntent(Enum):
    """Classified intent types — determines the broad directional goal."""

    FIX = "fix"              # Correct errors or broken behavior
    DEVELOP = "develop"      # Broad category for features, refactors, optimizations, etc.
    EXPLAIN = "explain"      # Read-only analysis
    GENERATE = "generate"    # Greenfield project scaffolding
    META = "meta"            # Meta-commands (e.g., "stop", "undo", "wait")


# -- Transition Rules --
# Maps: (current_state) → set of valid next states.
# Any transition not in this map is ILLEGAL and will raise.
# All non-terminal states can also transition to any FAILURE state.

_FAILURE_TARGETS = {
    AgentState.FAILED,
    AgentState.FAILED_BY_STALE,
    AgentState.FAILED_BY_INTERRUPT,
    AgentState.FAILED_BY_TIMEOUT,
    AgentState.FAILED_BY_SCOPE,
}

VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {
        AgentState.INTENT_ANALYSIS,
    },
    AgentState.INTENT_ANALYSIS: {
        AgentState.REPO_DISCOVERY,
        AgentState.IMPLEMENTING,        # Express Lane: docs-only changes
        AgentState.FEEDBACK_WAIT,       # if intent confidence too low → ask user
    } | _FAILURE_TARGETS,
    AgentState.REPO_DISCOVERY: {
        AgentState.PLANNING,
    } | _FAILURE_TARGETS,
    AgentState.PLANNING: {
        AgentState.TASK_ISOLATION,       # NEW: branch before implement
        AgentState.RESEARCHING,
        AgentState.IMPLEMENTING,         # for trivial fixes, skip research
        AgentState.FEEDBACK_WAIT,        # plan requires approval
    } | _FAILURE_TARGETS,
    AgentState.TASK_ISOLATION: {
        AgentState.PROVING_GROUND,       # TDD: write tests first
        AgentState.IMPLEMENTING,         # skip TDD for docs/refactor
    } | _FAILURE_TARGETS,
    AgentState.PROVING_GROUND: {
        AgentState.IMPLEMENTING,         # tests written, now implement
    } | _FAILURE_TARGETS,
    AgentState.RESEARCHING: {
        AgentState.IMPACT_ANALYSIS,
        AgentState.PLANNING,             # research reveals need to re-plan
    } | _FAILURE_TARGETS,
    AgentState.IMPACT_ANALYSIS: {
        AgentState.IMPLEMENTING,         # minor patch — proceed
        AgentState.FEEDBACK_WAIT,        # refactor/arch change — ask user
    } | _FAILURE_TARGETS,
    AgentState.IMPLEMENTING: {
        AgentState.VERIFYING,            # always verify after implementing
    } | _FAILURE_TARGETS,
    AgentState.VERIFYING: {
        AgentState.COMPLETE,             # all checks passed
        AgentState.RETRYING,             # some checks failed, within budget
        AgentState.FEEDBACK_WAIT,        # verification needs human review
    } | _FAILURE_TARGETS,
    AgentState.FEEDBACK_WAIT: {
        AgentState.IMPLEMENTING,         # user approved
        AgentState.PLANNING,             # user requested changes
        AgentState.INTENT_ANALYSIS,      # user clarified intent
        AgentState.COMPLETE,             # user manually resolved
    } | _FAILURE_TARGETS,
    AgentState.RETRYING: {
        AgentState.RESEARCHING,          # re-research before retry
        AgentState.IMPLEMENTING,         # direct retry
    } | _FAILURE_TARGETS,
    # Terminal states — no outgoing transitions
    AgentState.COMPLETE: set(),
    AgentState.FAILED: set(),
    AgentState.FAILED_BY_STALE: set(),
    AgentState.FAILED_BY_INTERRUPT: set(),
    AgentState.FAILED_BY_TIMEOUT: set(),
    AgentState.FAILED_BY_SCOPE: set(),
}


# -- Intent → Allowed States --
# EXPLAIN intent should never reach IMPLEMENTING.
# DEPLOY intent should never reach IMPACT_ANALYSIS on code.

INTENT_ALLOWED_STATES: dict[TaskIntent, set[AgentState]] = {
    TaskIntent.EXPLAIN: {
        AgentState.IDLE,
        AgentState.INTENT_ANALYSIS,
        AgentState.REPO_DISCOVERY,
        AgentState.RESEARCHING,
        AgentState.COMPLETE,
    } | FAILURE_STATES,
    # All other intents: full state set allowed (no restriction)
}


@dataclass
class IntentResult:
    """Result of intent classification with confidence."""

    intent: TaskIntent
    confidence: float              # 0.0 to 1.0
    reasoning: str                 # why this intent was chosen
    clarification_needed: bool = False
    suggested_question: Optional[str] = None

    # Policy threshold (configurable)
    CONFIDENCE_THRESHOLD: float = 0.75

    @property
    def is_confident(self) -> bool:
        return self.confidence >= self.CONFIDENCE_THRESHOLD

    @property
    def requires_clarification(self) -> bool:
        return not self.is_confident or self.clarification_needed


@dataclass
class StateSnapshot:
    """Immutable record of a state transition for audit logging."""

    from_state: AgentState
    to_state: AgentState
    reason: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentContext:
    """
    Mutable context for the current task execution.
    Stores environmental guarantees (Preconditions) and plan artifacts.
    """
    
    # Precondition Guarantees
    initial_git_head: Optional[str] = None
    initial_file_checksums: dict[str, str] = field(default_factory=dict)
    
    # Plan Artifacts to verify (Anti-Drift)
    planned_files: list[str] = field(default_factory=list)
    
    # Plan Envelope (Phase 2)
    plan_envelope_hash: Optional[str] = None
    input_snapshot_hash: Optional[str] = None
    lockfile_hash: Optional[str] = None
    task_branch: Optional[str] = None
    
    def clear(self):
        """Reset context for new task."""
        self.initial_git_head = None
        self.initial_file_checksums.clear()
        self.planned_files.clear()
        self.plan_envelope_hash = None
        self.input_snapshot_hash = None
        self.lockfile_hash = None
        self.task_branch = None


def validate_transition(
    current: AgentState,
    target: AgentState,
    intent: Optional[TaskIntent] = None,
) -> tuple[bool, str]:
    """
    Check if a state transition is valid.

    Returns (is_valid, reason).
    """
    # Check basic transition legality
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        return False, (
            f"Illegal transition: {current.name} → {target.name}. "
            f"Allowed: {[s.name for s in allowed]}"
        )

    # Check intent restrictions
    if intent and intent in INTENT_ALLOWED_STATES:
        if target not in INTENT_ALLOWED_STATES[intent]:
            return False, (
                f"State {target.name} not allowed for intent {intent.value}. "
                f"EXPLAIN tasks cannot enter IMPLEMENTING."
            )

    return True, "OK"
