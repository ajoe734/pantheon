"""Removable Management AI repair authorization adapter."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

from models import ErrorCode

from .repair_receipts import RepairReceiptError, verify_repair_receipt


def _first_value(*sources: Dict[str, Any], aliases: str) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for alias in aliases.split("|"):
            if alias in source:
                return source.get(alias)
    return None


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,;\n]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    result: List[str] = []
    seen = set()
    for raw in raw_items:
        clean = str(raw or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def repair_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_openclaw = payload.get("openclaw")
    if raw_openclaw is None:
        raw_openclaw = payload.get("openClaw")
    openclaw = raw_openclaw if isinstance(raw_openclaw, dict) else {}
    raw_repair = openclaw.get("repair") or openclaw.get("task") or payload.get("repair")
    repair = raw_repair if isinstance(raw_repair, dict) else {}

    metadata: Dict[str, Any] = {}
    for target, aliases in {
        "task_id": "task_id|taskId",
        "task_worktree": "task_worktree|taskWorktree|worktree",
        "expected_branch": "expected_branch|expectedBranch",
        "remote": "remote",
        "merge_target": "merge_target|mergeTarget",
        "repo_key": "repo_key|repoKey|repository",
    }.items():
        clean = str(_first_value(repair, openclaw, aliases=aliases) or "").strip()
        if clean:
            metadata[target] = clean

    scope = _string_list(
        _first_value(
            repair,
            openclaw,
            aliases="declared_scope|declaredScope|scope",
        )
    )
    if scope:
        metadata["declared_scope"] = scope

    for target, aliases in {
        "require_clean": "require_clean|requireClean",
        "require_pr": "require_pr|requirePr",
    }.items():
        value = _first_value(repair, openclaw, aliases=aliases)
        if isinstance(value, bool):
            metadata[target] = value

    pull_request = _first_value(
        repair,
        openclaw,
        aliases="pull_request|pullRequest",
    )
    if isinstance(pull_request, dict):
        metadata["pull_request"] = pull_request
    receipt = repair.get("receipt")
    if isinstance(receipt, str) and receipt.strip():
        metadata["receipt"] = receipt.strip()
    return metadata


def authorize_repair_metadata(
    payload: Dict[str, Any],
    *,
    identity: Any,
    caller_tenant_id: str,
    control_mode: Dict[str, Any],
    bff_error: Callable[..., Exception],
    raise_actor_error: Callable[[Any], None],
    require_mode_capability: Callable[[Any, str], None],
) -> Dict[str, Any]:
    supplied = repair_metadata(payload)
    mode = str(control_mode.get("mode") or "") if control_mode.get("active") else "user"
    if mode != "kernel_repair":
        if supplied:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Prepared repair metadata requires active kernel_repair control mode",
                "Activate kernel_repair with the same authenticated operator before forwarding a prepare receipt.",
                precondition_failed="control_mode",
                details_extra={"reason": "kernel_repair_required", "mode": mode},
            )
        return {}

    raise_actor_error(identity)
    require_mode_capability(identity, "kernel_repair")
    activation_capabilities = {
        str(value or "").strip() for value in (control_mode.get("capabilities") or [])
    }
    if "assistant.kernel.repair" not in activation_capabilities:
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Active control mode is not authorized for repair writes",
            "The active control-mode activation does not include assistant.kernel.repair.",
            precondition_failed="control_mode_capability",
            details_extra={
                "reason": "activation_capability_missing",
                "required_capability": "assistant.kernel.repair",
            },
        )

    receipt = str(supplied.pop("receipt", "") or "").strip()
    if not receipt:
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Kernel repair requires a BFF-issued prepare receipt",
            "Call /bff/assistant/repair-worktrees/prepare and forward its exact repair object.",
            precondition_failed="repair_receipt",
            details_extra={"reason": "repair_receipt_missing"},
        )
    try:
        return verify_repair_receipt(
            receipt,
            actor_id=identity.operator_id,
            tenant_id=caller_tenant_id,
            control_status=control_mode,
            supplied_repair=supplied,
        )
    except RepairReceiptError as exc:
        status_code = 503 if exc.reason == "receipt_key_unconfigured" else 403
        code = ErrorCode.PRECONDITION_FAILED if status_code == 503 else ErrorCode.FORBIDDEN
        raise bff_error(
            status_code,
            code,
            "Assistant repair prepare receipt is invalid",
            str(exc),
            precondition_failed="repair_receipt",
            details_extra={"reason": exc.reason},
        ) from exc
