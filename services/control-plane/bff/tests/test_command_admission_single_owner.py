"""Verification suite for single-owner command admission and idempotency."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import tempfile
from typing import Any, Dict, Optional

import pytest
from fastapi import BackgroundTasks, HTTPException

from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import (
    ActionCommandStatus,
    CommandStatus,
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorIdentity,
    TargetObject,
)


def _extract_cmd_id(res: Any) -> str:
    if hasattr(res, "receipt_id") and res.receipt_id:
        return str(res.receipt_id)
    if hasattr(res, "data") and isinstance(res.data, dict):
        return str(res.data.get("command_id") or res.data.get("receipt_id") or "")
    if hasattr(res, "command_id"):
        return str(res.command_id)
    raise ValueError(f"Unable to extract command_id from {type(res)}")


def _identity_resolver(
    authorization: Optional[str], mfa_token: Optional[str] = None
) -> OperatorIdentity:
    if authorization and authorization.startswith("Bearer "):
        parts = authorization[len("Bearer ") :].split(":")
        op_id = parts[0]
        roles = parts[1].split(",") if len(parts) > 1 else ["operator", "approver"]
        mfa = "mfa" in parts[2:] or mfa_token is not None
        return OperatorIdentity(
            operator_id=op_id,
            roles=roles,
            mfa_verified=mfa,
        )
    return OperatorIdentity(
        operator_id="op-default",
        roles=["operator", "approver"],
        mfa_verified=True,
    )


def _dummy_bff_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    detail: str = "",
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> HTTPException:
    err_detail = {
        "code": code.value,
        "message": message,
        "detail": detail,
    }
    if precondition_failed:
        err_detail["precondition_failed"] = precondition_failed
    if suggestion:
        err_detail["suggestion"] = suggestion
    if details_extra:
        err_detail.update(details_extra)
    if "correlation_id" in kwargs and kwargs["correlation_id"]:
        err_detail["correlation_id"] = kwargs["correlation_id"]
    return HTTPException(status_code=status_code, detail=err_detail)


@pytest.fixture
def temp_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "commands.jsonl")
        yield CommandStore(path)


def test_concurrent_admission_idempotency_single_execution(temp_store: CommandStore):
    service = CommandAdapterService(
        command_store=temp_store,
        extract_identity=_identity_resolver,
        bff_error=_dummy_bff_error,
    )

    payload = {
        "command": CommandType.REJECT_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-concurrent"},
        "params": {"decision_id": "dec-concurrent", "rejection_reason": "concurrent stress test"},
        "audit_context": {"reason": "concurrent stress test"},
    }
    headers_auth = "Bearer op-stress:operator,approver:mfa"
    idempotency_key = "idem-concurrent-admission-001"

    concurrency = 20
    results = []

    def _submit():
        bg = BackgroundTasks()
        return service.submit_command_admission(
            background_tasks=bg,
            payload=payload,
            authorization=headers_auth,
            idempotency_key=idempotency_key,
            route="POST /bff/v1/commands",
            include_durable_meta=True,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_submit) for _ in range(concurrency)]
        for f in futures:
            results.append(f.result())

    assert len(results) == concurrency
    first_cmd_id = _extract_cmd_id(results[0])
    for res in results:
        assert _extract_cmd_id(res) == first_cmd_id
        assert res.status in (CommandStatus.SUBMITTED, ActionCommandStatus.ACCEPTED)

    stored = [
        cmd
        for cmd in temp_store._get_all_commands()
        if str(cmd.get("command_id")) == str(first_cmd_id)
    ]
    assert len(stored) == 1


def test_idempotency_conflict_on_hash_mismatch(temp_store: CommandStore):
    service = CommandAdapterService(
        command_store=temp_store,
        extract_identity=_identity_resolver,
        bff_error=_dummy_bff_error,
    )

    payload_a = {
        "command": CommandType.REJECT_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-conflict-1"},
        "params": {"decision_id": "dec-conflict-1", "rejection_reason": "first call"},
        "audit_context": {"reason": "first call"},
    }
    payload_b = {
        "command": CommandType.REJECT_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-conflict-1"},
        "params": {"decision_id": "dec-conflict-1", "rejection_reason": "conflict call"},
        "audit_context": {"reason": "conflict call"},
    }
    headers_auth = "Bearer op-test:operator,approver:mfa"
    idempotency_key = "idem-conflict-test-001"

    bg1 = BackgroundTasks()
    res1 = service.submit_command_admission(
        background_tasks=bg1,
        payload=payload_a,
        authorization=headers_auth,
        idempotency_key=idempotency_key,
        route="POST /bff/v1/commands",
    )
    assert _extract_cmd_id(res1)

    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as exc_info:
        service.submit_command_admission(
            background_tasks=bg2,
            payload=payload_b,
            authorization=headers_auth,
            idempotency_key=idempotency_key,
            route="POST /bff/v1/commands",
        )
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    err_body = detail.get("error", detail)
    assert err_body.get("code") == ErrorCode.IDEMPOTENCY_CONFLICT.value


def test_cross_transport_replay_parity(temp_store: CommandStore):
    service = CommandAdapterService(
        command_store=temp_store,
        extract_identity=_identity_resolver,
        bff_error=_dummy_bff_error,
    )

    payload = {
        "command": CommandType.REJECT_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-parity-1"},
        "params": {"decision_id": "dec-parity-1", "rejection_reason": "cross transport parity"},
        "audit_context": {"reason": "cross transport parity"},
    }
    headers_auth = "Bearer op-parity:operator,approver:mfa"
    idempotency_key = "idem-cross-transport-001"

    bg1 = BackgroundTasks()
    res1 = service.submit_command_admission(
        background_tasks=bg1,
        payload=payload,
        authorization=headers_auth,
        idempotency_key=idempotency_key,
        route="POST /api/v1/operator/commands",
    )

    bg2 = BackgroundTasks()
    res2 = service.submit_command_admission(
        background_tasks=bg2,
        payload=payload,
        authorization=headers_auth,
        idempotency_key=idempotency_key,
        route="POST /bff/v1/commands",
        include_durable_meta=True,
    )

    assert _extract_cmd_id(res1) == _extract_cmd_id(res2)
    assert res2.meta is not None
    meta_dict = res2.meta if isinstance(res2.meta, dict) else res2.meta.model_dump()
    assert meta_dict["idempotency"]["replayed"] is True


def test_cross_operator_tenant_isolation(temp_store: CommandStore):
    service = CommandAdapterService(
        command_store=temp_store,
        extract_identity=_identity_resolver,
        bff_error=_dummy_bff_error,
    )

    payload1 = {
        "command": CommandType.REJECT_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-iso-1"},
        "params": {"decision_id": "dec-iso-1", "rejection_reason": "operator alpha"},
        "audit_context": {"reason": "operator alpha"},
    }
    payload2 = {
        "command": CommandType.REJECT_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-iso-2"},
        "params": {"decision_id": "dec-iso-2", "rejection_reason": "operator beta"},
        "audit_context": {"reason": "operator beta"},
    }
    idempotency_key = "shared-idempotency-key-001"

    bg1 = BackgroundTasks()
    res1 = service.submit_command_admission(
        background_tasks=bg1,
        payload=payload1,
        authorization="Bearer op-alpha:operator,approver:mfa",
        idempotency_key=idempotency_key,
        route="POST /bff/v1/commands",
    )

    bg2 = BackgroundTasks()
    res2 = service.submit_command_admission(
        background_tasks=bg2,
        payload=payload2,
        authorization="Bearer op-beta:operator,approver:mfa",
        idempotency_key=idempotency_key,
        route="POST /bff/v1/commands",
    )

    assert _extract_cmd_id(res1) != _extract_cmd_id(res2)
    assert len(temp_store._get_all_commands()) == 2


def test_confirm_token_single_use_race_condition(temp_store: CommandStore):
    service = CommandAdapterService(
        command_store=temp_store,
        extract_identity=_identity_resolver,
        bff_error=_dummy_bff_error,
    )

    token_res = service.create_confirm_token(
        payload={
            "action": "PauseRuntime",
            "command": "PauseRuntime",
            "target": {"type": "Runtime", "id": "rt-gate-race-1"},
            "reason": "gated pause",
            "ttl_seconds": 60,
        },
        identity=OperatorIdentity(operator_id="op-admin", roles=["operator", "approver", "admin"], mfa_verified=True),
        idempotency_key="create-token-race-001",
    )
    token_data = json.loads(token_res.body.decode("utf-8"))
    confirm_token = token_data["data"]["tokenId"]

    payload = {
        "command": CommandType.PAUSE_RUNTIME.value,
        "target": {"type": ObjectType.RUNTIME.value, "id": "rt-gate-race-1"},
        "params": {"runtime_binding_id": "rb-1", "pause_action": "pause"},
        "audit_context": {"reason": "gated pause"},
    }

    concurrency = 5
    results = []
    errors = []

    def _redeem(index: int):
        bg = BackgroundTasks()
        try:
            res = service.submit_command_admission(
                background_tasks=bg,
                payload=payload,
                authorization="Bearer op-admin:operator,approver,admin:mfa",
                x_confirm_token=confirm_token,
                idempotency_key=f"redeem-token-race-{index}",
                route="POST /bff/v1/commands",
            )
            return ("success", res)
        except HTTPException as exc:
            return ("error", exc.status_code)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_redeem, i) for i in range(concurrency)]
        for f in futures:
            outcome, val = f.result()
            if outcome == "success":
                results.append(val)
            else:
                errors.append(val)

    # Exactly one submission succeeds in redeeming the single-use token
    assert len(results) == 1
    assert len(errors) == concurrency - 1


def test_command_store_unconfigured_fails_closed():
    service = CommandAdapterService(
        command_store=None,
        extract_identity=_identity_resolver,
        bff_error=_dummy_bff_error,
    )

    payload = {
        "command": CommandType.APPROVE_DECISION.value,
        "target": {"type": ObjectType.APPROVAL_DECISION.value, "id": "dec-unconfigured"},
        "params": {"decision_id": "dec-unconfigured"},
        "audit_context": {"reason": "unconfigured store"},
    }

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as exc_info:
        service.submit_command_admission(
            background_tasks=bg,
            payload=payload,
            authorization="Bearer op-test:operator,approver:mfa",
            idempotency_key="idem-fail-closed-001",
            route="POST /bff/v1/commands",
        )
    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    err_body = detail.get("error", detail)
    assert err_body.get("code") == ErrorCode.DEPENDENCY_UNAVAILABLE.value
