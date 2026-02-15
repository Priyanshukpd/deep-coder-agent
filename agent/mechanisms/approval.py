"""
Human Approval Contract — UI/UX for the FEEDBACK_WAIT state.

Defines exactly what the human sees when the agent pauses for approval.
This is the "contract" between the agent backend and the frontend UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List


class ApprovalType(Enum):
    """What kind of approval is being requested?"""

    PLAN_REVIEW = auto()       # Review task.md before starting
    DESTRUCTIVE_COMMAND = auto() # Allow rm -rf or similar
    LARGE_DIFF = auto()        # Diff exceeds line limit
    CRITICAL_FILE = auto()     # Touching sensitive file (auth, payments)
    AMBIGUOUS_INTENT = auto()  # Confidence < 0.75
    TOOL_USE = auto()          # Use of sensitive tool (e.g. deploy)


class ApprovalAction(Enum):
    """Possible human responses."""

    APPROVE = "approve"
    APPROVE_WITH_FEEDBACK = "approve_feedback"
    REJECT = "reject"
    MODIFY_PLAN = "modify_plan"
    ABORT_TASK = "abort"


@dataclass
class BlastRadius:
    """Quantified impact of the proposed change."""

    files_touched: int
    lines_changed: int
    dependent_files: int       # files verifying/importing this
    test_coverage_percent: float
    estimated_risk_score: int  # 1-10 scale
    breaking_changes: List[str] = field(default_factory=list)


from agent.security.rbac import UserRole

@dataclass
class ApprovalRequest:
    """
    The artifact presented to the user during FEEDBACK_WAIT.
    Must be compact, actionable, and visually clear.
    """

    request_id: str
    approval_type: ApprovalType
    summary: str               # One-line summary (e.g. "Refactor auth module...")
    description: str           # Detailed context
    
    # RBAC Constraint
    minimum_required_role: UserRole = UserRole.DEVELOPER

    # Impact Data
    blast_radius: Optional[BlastRadius] = None
    diff_preview: Optional[str] = None  # URL or snippet
    
    # Metadata
    timeout_seconds: int = 86400  # Default 24h
    timestamp: float = field(default_factory=float)
    
    # Options presented to user
    allowed_actions: List[ApprovalAction] = field(
        default_factory=lambda: [ApprovalAction.APPROVE, ApprovalAction.REJECT]
    )

    def to_markdown(self) -> str:
        """Render the request as a markdown artifact for the UI."""
        
        # Icon mapping
        icon = {
            ApprovalType.PLAN_REVIEW: "📋",
            ApprovalType.DESTRUCTIVE_COMMAND: "⚠️",
            ApprovalType.LARGE_DIFF: "📊",
            ApprovalType.CRITICAL_FILE: "🔥",
            ApprovalType.AMBIGUOUS_INTENT: "🤔",
            ApprovalType.TOOL_USE: "🛠️"
        }.get(self.approval_type, "❓")

        md = f"# {icon} Approval Required: {self.approval_type.name}\n\n"
        md += f"**{self.summary}**\n\n"
        md += f"**🔒 Required Role:** `{self.minimum_required_role.name}`\n\n"
        md += f"{self.description}\n\n"

        if self.blast_radius:
            br = self.blast_radius
            md += "## 💥 Blast Radius\n"
            md += f"- **Files Touched:** {br.files_touched}\n"
            md += f"- **Lines:** {br.lines_changed}\n"
            md += f"- **Dependent Modules:** {br.dependent_files}\n"
            md += f"- **Risk Score:** {br.estimated_risk_score}/10\n"
            if br.breaking_changes:
                md += "\n**⚠️ Breaking Changes:**\n"
                for change in br.breaking_changes:
                    md += f"- {change}\n"
            md += "\n"

        if self.diff_preview:
            md += "## 📝 Diff Preview\n"
            md += "```diff\n"
            md += self.diff_preview[:1000] + ("\n... (truncated)" if len(self.diff_preview) > 1000 else "")
            md += "\n```\n\n"

        md += "## 🚦 Actions\n"
        for action in self.allowed_actions:
            md += f"- [{action.value.title()}]\n"

        return md
