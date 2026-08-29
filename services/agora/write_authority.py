"""
Write-authority matrix and role-checking for the Agora service boundary.

Agora is the canonical domain-owned write path for Agora sessions, memos,
evidence packs, notes, insights, signals, feedback, decision journal entries,
workshops, proposals, and interactions. Callers must satisfy role-based write
authority gates; unauthorized callers fail closed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple, Union


WRITE_AUTHORITY_MATRIX: Dict[Tuple[str, str], List[str]] = {
    ("AgoraSession", "create"): ["operator", "admin", "trader"],
    ("AgoraSession", "open"): ["operator", "admin", "trader"],
    ("AgoraSession", "close"): ["operator", "admin", "trader"],
    ("AgoraSessionMessage", "append"): ["operator", "admin", "trader", "persona"],
    ("AgoraHandoff", "create"): ["operator", "admin", "trader"],
    ("AgoraCommitteeMemo", "submit"): ["operator", "admin", "trader"],
    ("AgoraCommitteeMemo", "publish"): ["operator", "admin", "trader", "approver"],
    ("AgoraCommitteeEvidencePack", "create"): ["operator", "admin", "trader"],
    ("AgoraCommitteeEvidencePack", "append_files"): ["operator", "admin", "trader"],
    ("AgoraNote", "create"): ["operator", "admin", "trader", "analyst"],
    ("AgoraInsight", "create"): ["operator", "admin", "trader", "analyst"],
    ("AgoraTrainingExample", "create"): ["operator", "admin", "trader", "trainer"],
    ("AgoraSignal", "create"): ["operator", "admin", "trader", "researcher"],
    ("AgoraSignalFeedback", "record"): ["operator", "admin", "trader"],
    ("AgoraFeedback", "create"): ["operator", "admin", "trader"],
    ("AgoraAuditEvent", "record"): ["operator", "admin", "trader", "system", "auditor"],
    ("DecisionJournalEntry", "create"): ["operator", "admin", "trader"],
    ("DecisionJournalEntry", "patch"): ["operator", "admin", "trader"],
    ("AgoraWorkshop", "create"): ["operator", "admin", "trader"],
    ("AgoraWorkshop", "mutate"): ["operator", "admin", "trader"],
    ("AgoraProposal", "create"): ["operator", "admin", "trader", "strategy.review"],
    ("AgoraProposal", "modify"): ["operator", "admin", "trader", "strategy.review"],
    ("AgoraInteraction", "create"): ["operator", "admin", "trader"],
    ("AgoraInteraction", "resolve_context"): ["operator", "admin", "trader"],
}


class AgoraWriteForbiddenError(PermissionError):
    """Raised when an actor lacks the required role for an Agora write operation."""

    def __init__(
        self,
        resource_type: str,
        operation: str,
        actor_roles: Sequence[str] | str,
        reason: str = "Operator does not hold the required command role",
        precondition_failed: str = "role_check",
    ) -> None:
        roles_str = [actor_roles] if isinstance(actor_roles, str) else list(actor_roles)
        super().__init__(f"Forbidden: {resource_type}.{operation} denied for roles {roles_str}: {reason}")
        self.resource_type = resource_type
        self.operation = operation
        self.actor_roles = roles_str
        self.reason = reason
        self.precondition_failed = precondition_failed
        self.status_code = 403
        self.error_code = "FORBIDDEN"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.error_code,
            "details": {
                "reason": self.reason,
                "precondition_failed": self.precondition_failed,
                "resource_type": self.resource_type,
                "operation": self.operation,
            },
        }


def is_authorized(resource_type: str, operation: str, actor_roles: Union[Sequence[str], str]) -> bool:
    """Check whether any of the given roles are authorized for the resource operation."""
    allowed_roles = WRITE_AUTHORITY_MATRIX.get((resource_type, operation), [])
    if isinstance(actor_roles, str):
        return actor_roles in allowed_roles
    return any(r in allowed_roles for r in actor_roles)


def assert_authorized(resource_type: str, operation: str, actor_roles: Union[Sequence[str], str]) -> None:
    """Raise AgoraWriteForbiddenError if actor roles do not satisfy the write authority matrix."""
    if not is_authorized(resource_type, operation, actor_roles):
        raise AgoraWriteForbiddenError(resource_type, operation, actor_roles)


def matrix_as_list() -> List[Dict[str, Any]]:
    """Return the write authority matrix as a list of dict records."""
    return [
        {
            "resource_type": resource_type,
            "operation": operation,
            "authorized_roles": list(roles),
        }
        for (resource_type, operation), roles in WRITE_AUTHORITY_MATRIX.items()
    ]


__all__ = [
    "AgoraWriteForbiddenError",
    "WRITE_AUTHORITY_MATRIX",
    "assert_authorized",
    "is_authorized",
    "matrix_as_list",
]
