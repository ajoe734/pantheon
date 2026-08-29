"""
Write-authority matrix for the capital service boundary.

The service is the canonical write path for CapitalPool and
PersonaCapitalBinding persistence. Callers must go through this API instead of
writing store files directly.
"""
from __future__ import annotations

from typing import Dict, List, Tuple


WRITE_AUTHORITY_MATRIX: Dict[Tuple[str, str], List[str]] = {
    ("CapitalPool", "create"): [
        "operator",
        "approver",
        "admin",
        "capital.admin",
    ],
    ("CapitalPool", "update_status"): ["capital.admin"],
    ("CapitalPool", "update"): ["capital.admin"],
    ("PersonaCapitalBinding", "create"): [
        "operator",
        "approver",
        "admin",
        "persona.admin",
    ],
    ("PersonaCapitalBinding", "activate"): ["persona.admin"],
    ("PersonaCapitalBinding", "update_status"): ["persona.admin"],
    ("Rebalance", "create"): [
        "operator",
        "approver",
        "admin",
        "capital.operator",
        "capital.admin",
    ],
    ("Rebalance", "apply"): [
        "operator",
        "approver",
        "admin",
        "capital.operator",
        "capital.admin",
    ],
    ("Containment", "create"): [
        "operator",
        "approver",
        "reviewer",
        "admin",
        "capital.operator",
        "capital.admin",
        "risk.admin",
    ],
}


def is_authorized(resource_type: str, operation: str, actor_role: str) -> bool:
    return actor_role in WRITE_AUTHORITY_MATRIX.get((resource_type, operation), [])


def matrix_as_list() -> List[Dict[str, object]]:
    return [
        {
            "resource_type": resource_type,
            "operation": operation,
            "authorized_roles": roles,
        }
        for (resource_type, operation), roles in WRITE_AUTHORITY_MATRIX.items()
    ]
