"""
Write-authority matrix for the Governance Service.

Defines which actor roles are authorized to record (or revoke) an
ApprovalDecision at each risk level.  This is the single canonical
reference; control-plane platform objects delegate to this policy.

Other services that need to validate approval authority should call this
module rather than maintaining their own role lists.
"""
from __future__ import annotations

from typing import Dict, List

# Risk level → actor roles permitted to write a decision at that level.
WRITE_AUTHORITY_MATRIX: Dict[str, List[str]] = {
    "low":      ["governance_reviewer", "automated_gate"],
    "medium":   ["governance_reviewer", "risk_owner"],
    "high":     ["risk_owner",          "governance_committee"],
    "critical": ["governance_committee"],
}

# Roles permitted to revoke any decided decision.
REVOKE_AUTHORITY: List[str] = ["risk_owner", "governance_committee"]


def is_authorized_to_decide(actor_role: str, risk_level: str) -> bool:
    """Return True if actor_role may record a decision at risk_level."""
    return actor_role in WRITE_AUTHORITY_MATRIX.get(risk_level, [])


def is_authorized_to_revoke(actor_role: str) -> bool:
    """Return True if actor_role may revoke a decided approval."""
    return actor_role in REVOKE_AUTHORITY


def matrix_as_list() -> List[Dict]:
    """Return the full matrix as a list of dicts (for the /write-authority endpoint)."""
    return [
        {
            "risk_level":       risk_level,
            "authorized_roles": roles,
            "revoke_roles":     REVOKE_AUTHORITY,
        }
        for risk_level, roles in WRITE_AUTHORITY_MATRIX.items()
    ]
