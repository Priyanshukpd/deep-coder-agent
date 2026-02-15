"""
RBAC — Role-Based Access Control for Human Approvals.

Defines WHO is allowed to approve WHAT.
This acts as the authorization layer for the agent's governance.
"""

from __future__ import annotations

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Set


class UserRole(Enum):
    """Hierarchy of human approvers."""
    
    DEVELOPER = auto()     # Can approve tests, safe fixes
    SENIOR = auto()        # Can approve refactors, medium diffs
    ARCHITECT = auto()     # Can approve large diffs, destructive ops
    ADMIN = auto()         # God mode (infra, nuking things)


class Permission(Enum):
    """Specific actions that require permission."""
    
    APPROVE_TEST_RUN = auto()
    APPROVE_MINOR_FIX = auto()          # < 200 lines
    APPROVE_REFACTOR = auto()           # > 200 lines
    APPROVE_ARCH_CHANGE = auto()        # > 500 lines or > 20 files
    APPROVE_DESTRUCTIVE_FILE_OP = auto() # rm, mv, etc.
    APPROVE_GIT_REWRITE = auto()        # force push
    APPROVE_NETWORK_OP = auto()         # pip install, curl
    APPROVE_DEPLOY = auto()             # infra changes
    APPROVE_CRITICAL_ROLLBACK = auto()  # database rollback


@dataclass
class RBACPolicy:
    """Mapping of Roles to Permissions."""
    
    role: UserRole
    permissions: Set[Permission]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


# -- Policy Definitions --

_POLICIES: dict[UserRole, Set[Permission]] = {
    UserRole.DEVELOPER: {
        Permission.APPROVE_TEST_RUN,
        Permission.APPROVE_MINOR_FIX,
    },
    UserRole.SENIOR: {
        Permission.APPROVE_TEST_RUN,
        Permission.APPROVE_MINOR_FIX,
        Permission.APPROVE_REFACTOR,
        Permission.APPROVE_NETWORK_OP,
    },
    UserRole.ARCHITECT: {
        # Inherits all SENIOR permissions implicitly by design intent, 
        # but explicit set for clarity here
        Permission.APPROVE_TEST_RUN,
        Permission.APPROVE_MINOR_FIX,
        Permission.APPROVE_REFACTOR,
        Permission.APPROVE_NETWORK_OP,
        Permission.APPROVE_ARCH_CHANGE,
        Permission.APPROVE_DESTRUCTIVE_FILE_OP,
        Permission.APPROVE_GIT_REWRITE,
    },
    UserRole.ADMIN: set(Permission)  # All permissions
}


def get_required_role(permission: Permission) -> UserRole:
    """Returns the minimum role required for a permission."""
    
    # Order matters: check lowest privilege first
    if permission in _POLICIES[UserRole.DEVELOPER]:
        return UserRole.DEVELOPER
    if permission in _POLICIES[UserRole.SENIOR]:
        return UserRole.SENIOR
    if permission in _POLICIES[UserRole.ARCHITECT]:
        return UserRole.ARCHITECT
    return UserRole.ADMIN


def check_access(user_role: UserRole, permission: Permission) -> bool:
    """
    Check if a user with a given role can perform an action.
    """
    allowed_perms = _POLICIES.get(user_role, set())
    return permission in allowed_perms
