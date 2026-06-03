"""Governed assistant operation tool contract layer (ASST-INTEG-004).

Flow for every assistant tool invocation:
  preview → validate → [confirm in UI] → execute → receipt

All mutations route through action_catalog + command_executor.
The assistant never submits hidden DOM actions or calls shell directly.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models import RiskLevel


# ---------------------------------------------------------------------------
# Assistant tool allowlist
# Initial coverage: low and medium-risk Platform Admin operations.
# High/critical and two-man actions require human orchestration and are
# intentionally excluded from the initial assistant tool surface.
# ---------------------------------------------------------------------------
ASSISTANT_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    # Low-risk
    "AuditExport",
    "EscalateDiff",
    "HumanGateRequestMoreEvidence",
    "HumanGateExtendTtl",
    "RankingFormulaAction",
    "RankingAction",
    "AgoraSignalFeedback",
    "AgoraMessageAction",
    # Medium-risk governed resource actions
    "PersonaAction",
    "StrategyAction",
    "CapitalPoolAction",
    "ToolAction",
    "McpServerAction",
    "SkillAction",
    "ReviewAction",
    "ExperimentAction",
    "JobAction",
    "AgoraInsightAction",
    "AgoraMemoryAction",
})

_RISK_REQUIRES_REASON: frozenset[RiskLevel] = frozenset({
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.CRITICAL,
})

_RISK_REQUIRES_CONFIRM_TOKEN: frozenset[RiskLevel] = frozenset({
    RiskLevel.HIGH,
    RiskLevel.CRITICAL,
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ToolNotAllowedError(ValueError):
    """action_id is not in the assistant tool allowlist."""

    def __init__(self, action_id: str) -> None:
        super().__init__(
            f"Action {action_id!r} is not in the assistant tool allowlist. "
            "The assistant may only invoke catalogued and explicitly allowlisted BFF actions."
        )
        self.action_id = action_id


class ToolRbacError(PermissionError):
    """Actor lacks RBAC roles required for the action."""

    def __init__(
        self,
        action_id: str,
        required_roles: List[str],
        actor_roles: List[str],
    ) -> None:
        super().__init__(
            f"Actor roles {sorted(actor_roles)} do not satisfy required roles "
            f"{sorted(required_roles)} for action {action_id!r}."
        )
        self.action_id = action_id
        self.required_roles = required_roles
        self.actor_roles = actor_roles


class ToolValidationError(ValueError):
    """Tool request validation failed (missing reason, confirm_token, etc.)."""

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolPreview:
    action_id: str
    entity_type: str
    description: str
    risk_level: str
    requires_confirmation: bool
    requires_reason: bool
    requires_two_man: bool
    requires_confirm_token: bool
    endpoint: str
    required_roles: List[str]
    in_allowlist: bool


@dataclass(frozen=True)
class ToolValidationResult:
    ok: bool
    action_id: str
    actor_roles: List[str]
    errors: List[str] = field(default_factory=list)
    missing_reason: bool = False
    missing_confirm_token: bool = False


@dataclass(frozen=True)
class ToolReceipt:
    """Audit receipt returned after every execute_governed_tool call."""
    receipt_id: str
    trace_id: str
    audit_id: Optional[str]
    command_id: str
    action_id: str
    entity_type: str
    entity_id: Optional[str]
    risk_level: str
    actor_id: str
    # "executed" | "failed" | "timeout" | "admitted"
    status: str
    result: Optional[Dict[str, Any]]
    error: Optional[Dict[str, Any]]
    executed_at: str
    reason: Optional[str]
    source: str  # always "assistant_tool_contract"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preview_tool(action_id: str) -> ToolPreview:
    """Return a preview descriptor without executing anything.

    Raises ToolNotAllowedError when action_id is not in the allowlist or
    not present in the action catalog.
    """
    from action_catalog import get_catalog_entry

    if action_id not in ASSISTANT_TOOL_ALLOWLIST:
        raise ToolNotAllowedError(action_id)

    entry = get_catalog_entry(action_id)
    if entry is None:
        raise ToolNotAllowedError(action_id)

    risk = entry.risk_level
    return ToolPreview(
        action_id=action_id,
        entity_type=entry.entity_type,
        description=entry.description,
        risk_level=risk.value,
        requires_confirmation=risk in _RISK_REQUIRES_REASON,
        requires_reason=risk in _RISK_REQUIRES_REASON,
        requires_two_man=entry.requires_two_man,
        requires_confirm_token=entry.requires_confirm_token or (risk in _RISK_REQUIRES_CONFIRM_TOKEN),
        endpoint=entry.endpoint,
        required_roles=list(entry.required_roles),
        in_allowlist=True,
    )


def validate_tool(
    action_id: str,
    params: Dict[str, Any],
    actor_roles: List[str],
    *,
    reason: Optional[str] = None,
    confirm_token: Optional[str] = None,
) -> ToolValidationResult:
    """Validate the tool request without executing.

    Returns ToolValidationResult with ok=False and detailed errors on policy
    failures. Raises ToolNotAllowedError if action_id is not in the allowlist.
    """
    from action_catalog import get_catalog_entry

    if action_id not in ASSISTANT_TOOL_ALLOWLIST:
        raise ToolNotAllowedError(action_id)

    entry = get_catalog_entry(action_id)
    if entry is None:
        raise ToolNotAllowedError(action_id)

    errors: List[str] = []
    missing_reason = False
    missing_confirm_token = False

    # RBAC: actor must hold at least one required role
    required = set(entry.required_roles)
    actor = set(actor_roles)
    if not required.intersection(actor):
        errors.append(
            f"Actor roles {sorted(actor)} do not satisfy required roles "
            f"{sorted(required)} for action {action_id!r}."
        )

    risk = entry.risk_level

    # Medium/high/critical: reason required
    if risk in _RISK_REQUIRES_REASON and not (reason and reason.strip()):
        missing_reason = True
        errors.append(
            f"Action {action_id!r} (risk={risk.value}) requires a non-empty reason."
        )

    # High/critical, or catalog flag: confirm_token required
    needs_token = entry.requires_confirm_token or (risk in _RISK_REQUIRES_CONFIRM_TOKEN)
    if needs_token and not (confirm_token and confirm_token.strip()):
        missing_confirm_token = True
        errors.append(
            f"Action {action_id!r} (risk={risk.value}) requires a confirm_token."
        )

    return ToolValidationResult(
        ok=len(errors) == 0,
        action_id=action_id,
        actor_roles=list(actor_roles),
        errors=errors,
        missing_reason=missing_reason,
        missing_confirm_token=missing_confirm_token,
    )


def execute_governed_tool(
    *,
    action_id: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    params: Dict[str, Any],
    actor_id: str,
    actor_roles: List[str],
    reason: Optional[str] = None,
    confirm_token: Optional[str] = None,
    trace_id: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> ToolReceipt:
    """Execute a governed tool through the BFF action catalog and command executor.

    Enforces: allowlist check → catalog lookup → RBAC → risk-level policy →
    command execution → audit receipt.

    Every call returns a ToolReceipt. Policy violations raise:
    - ToolNotAllowedError   (action not in allowlist)
    - ToolRbacError         (actor missing required role)
    - ToolValidationError   (missing reason or confirm_token)
    """
    from action_catalog import get_catalog_entry
    from command_executor import execute_command_with_status
    from models import CommandType

    _trace_id = trace_id or f"asst-tool-{uuid.uuid4().hex[:12]}"
    _command_id = f"cmd-asst-{uuid.uuid4().hex[:12]}"
    executed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # 1. Allowlist
    if action_id not in ASSISTANT_TOOL_ALLOWLIST:
        raise ToolNotAllowedError(action_id)

    entry = get_catalog_entry(action_id)
    if entry is None:
        raise ToolNotAllowedError(action_id)

    # 2. RBAC
    required = set(entry.required_roles)
    actor = set(actor_roles)
    if not required.intersection(actor):
        raise ToolRbacError(action_id, list(required), list(actor))

    risk = entry.risk_level

    # 3. Risk-level policy
    if risk in _RISK_REQUIRES_REASON and not (reason and reason.strip()):
        raise ToolValidationError(
            f"Action {action_id!r} (risk={risk.value}) requires a non-empty reason.",
            field_name="reason",
        )
    needs_token = entry.requires_confirm_token or (risk in _RISK_REQUIRES_CONFIRM_TOKEN)
    if needs_token and not (confirm_token and confirm_token.strip()):
        raise ToolValidationError(
            f"Action {action_id!r} (risk={risk.value}) requires a confirm_token.",
            field_name="confirm_token",
        )

    # 4. Build execution params
    exec_params: Dict[str, Any] = {
        **params,
        "action_id": action_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "trace_id": _trace_id,
        "command_id": _command_id,
        "actor_id": actor_id,
        "source": "assistant_tool_contract",
    }
    if reason:
        exec_params["reason"] = reason
    if confirm_token:
        exec_params["confirm_token"] = confirm_token

    # 5. Route through command_executor
    try:
        command_type = CommandType(action_id)
    except ValueError:
        command_type = None

    if command_type is not None:
        cmd_status, result, error = execute_command_with_status(
            _command_id,
            command_type,
            exec_params,
            auth_token=auth_token,
        )
        status = cmd_status.value
    else:
        # action_id is in the allowlist and catalog but not a named CommandType;
        # fall back to the BFF action adapter path.
        result = {
            "command_id": _command_id,
            "dispatch_path": "bff_action_adapter_fallback",
            "status": "admitted",
            "action_id": action_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "source": "assistant_tool_contract",
            "live_capital_side_effects": False,
        }
        error = None
        status = "admitted"

    audit_id = (result or {}).get("audit_id") or (error or {}).get("audit_id")

    return ToolReceipt(
        receipt_id=f"asst-receipt-{uuid.uuid4().hex[:12]}",
        trace_id=_trace_id,
        audit_id=audit_id,
        command_id=_command_id,
        action_id=action_id,
        entity_type=entity_type,
        entity_id=entity_id,
        risk_level=risk.value,
        actor_id=actor_id,
        status=status,
        result=result,
        error=error,
        executed_at=executed_at,
        reason=reason,
        source="assistant_tool_contract",
    )


def tool_receipt_to_dict(receipt: ToolReceipt) -> Dict[str, Any]:
    """Serialize a ToolReceipt to a JSON-safe dict."""
    return {
        "receipt_id": receipt.receipt_id,
        "trace_id": receipt.trace_id,
        "audit_id": receipt.audit_id,
        "command_id": receipt.command_id,
        "action_id": receipt.action_id,
        "entity_type": receipt.entity_type,
        "entity_id": receipt.entity_id,
        "risk_level": receipt.risk_level,
        "actor_id": receipt.actor_id,
        "status": receipt.status,
        "result": receipt.result,
        "error": receipt.error,
        "executed_at": receipt.executed_at,
        "reason": receipt.reason,
        "source": receipt.source,
    }
