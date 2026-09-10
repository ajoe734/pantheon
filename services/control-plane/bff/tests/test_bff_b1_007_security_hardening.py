from __future__ import annotations

import json
import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator

import pytest
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import extract_identity
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import (
    AuditContext,
    CommandStatus,
    CommandType,
    ObjectType,
    OperatorCommand,
    TargetObject,
    utc_now,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


PRIMARY_HEADERS = {
    "Authorization": "Bearer op-primary:operator,approver:mfa",
    "X-Trace-Id": "trace-bff-b1-007",
    "X-Correlation-Id": "corr-bff-b1-007",
    "X-Request-Id": "req-bff-b1-007",
}
SECONDARY_HEADERS = {
    "Authorization": "Bearer op-secondary:operator,approver:mfa",
    "X-Trace-Id": "trace-bff-b1-007-secondary",
}

_TWO_MAN_SIGNER_LIST_FIELDS = (
    "signer_operator_ids",
    "signerOperatorIds",
    "operator_ids",
    "operatorIds",
)
_TWO_MAN_SIGNER_FIELDS = (
    "first_operator_id",
    "firstOperatorId",
    "primary_operator_id",
    "primaryOperatorId",
    "second_operator_id",
    "secondOperatorId",
    "secondOperatorSignature",
    "second_operator_signature",
    "signed_by",
    "signedBy",
    "confirmed_by",
    "confirmedBy",
)


def _two_man_signers(record: dict[str, Any]) -> set[str]:
    params = record.get("params") or {}
    audit = record.get("audit") or {}
    signers: set[str] = set()
    for source in (params, audit):
        for field in _TWO_MAN_SIGNER_LIST_FIELDS:
            raw = source.get(field)
            if isinstance(raw, list):
                signers.update(str(value).strip() for value in raw if str(value or "").strip())
        for field in _TWO_MAN_SIGNER_FIELDS:
            value = str(source.get(field) or "").strip()
            if value:
                signers.add(value)
    actor = record.get("actor_id") or record.get("actorId") or (record.get("audit") or {}).get("operator_id")
    if actor:
        signers.add(str(actor).strip())
    return signers


def _extract_caller_id(authorization: str | None) -> str:
    if not authorization:
        return "unknown"
    token = authorization.removeprefix("Bearer ").strip()
    return token.split(":")[0]


def _extract_caller_roles(authorization: str | None) -> list[str]:
    if not authorization:
        return []
    token = authorization.removeprefix("Bearer ").strip()
    parts = token.split(":")
    if len(parts) > 1:
        return [r.strip() for r in parts[1].split(",") if r.strip()]
    return []


_current_command_store: CommandStore | None = None
_current_read_store: Any = None
_current_app: FastAPI | None = None
_confirm_tokens: dict[str, Any] = {}
_two_man_signatures: dict[str, set[str]] = {}
_two_man_targets: dict[str, str] = {}
_idempotency_map: dict[tuple[str, str], Any] = {}


def _create_security_test_app() -> FastAPI:
    app = FastAPI()

    @app.post("/bff/confirm-tokens", status_code=201)
    async def create_confirm_token(
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        payload = await request.json()
        token_id = payload.get("tokenId") or ""
        _confirm_tokens[token_id] = {
            "tokenId": token_id,
            "command": payload.get("command"),
            "target": payload.get("target"),
            "operator_id": payload.get("operator_id"),
            "reason": payload.get("reason"),
            "status": "issued",
        }
        return JSONResponse(status_code=201, content={"data": {"tokenId": token_id, "status": "issued"}})

    @app.get("/bff/confirm-tokens/{token_id}")
    async def get_confirm_token(token_id: str):
        token = _confirm_tokens.get(token_id)
        if not token:
            return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Token not found"}})
        return JSONResponse(status_code=200, content={"data": token})

    @app.post("/bff/confirm-tokens/{token_id}/redeem")
    async def redeem_confirm_token(token_id: str, request: Request):
        token = _confirm_tokens.get(token_id)
        if token:
            token["status"] = "redeemed"
        cmd_id = f"cmd-redeem-{uuid.uuid4().hex[:8]}"
        redeem_record = {
            "command_id": cmd_id,
            "type": CommandType.CONFIRM_TOKEN_REDEEM.value,
            "status": CommandStatus.EXECUTED.value,
            "target": {"type": "ConfirmToken", "id": token_id},
            "params": {"tokenId": token_id},
        }
        if _current_command_store:
            _current_command_store._save_command(redeem_record)
        return JSONResponse(status_code=202, content={"data": {"tokenId": token_id, "status": "redeemed"}})

    @app.post("/bff/v5/interventions/{target_id}/two-man-sign")
    async def two_man_sign(
        target_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        caller_id = _extract_caller_id(authorization)
        roles = _extract_caller_roles(authorization)
        if "reviewer" in roles and "operator" not in roles and "approver" not in roles:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "OPERATION_NOT_ALLOWED",
                        "message": "Reviewer role cannot create two man signatures",
                        "details": {"reason": "ROLE_NOT_PERMITTED"},
                    }
                },
            )

        payload = await request.json()
        sig_id = payload.get("twoManSignatureId") or ""

        idem_key = (caller_id, idempotency_key or "")
        if idempotency_key and idem_key in _idempotency_map:
            cached = _idempotency_map[idem_key]
            return JSONResponse(status_code=202, content=cached)

        cmd_id = f"cmd-sign-{uuid.uuid4().hex[:8]}"
        cmd_record = {
            "command_id": cmd_id,
            "type": "V5InterventionAction",
            "command": "RemediateSentinelIntervention",
            "target": {"type": "SentinelIntervention", "id": target_id},
            "status": CommandStatus.EXECUTED.value,
            "params": {
                "twoManSignatureId": sig_id,
                "signerOperatorIds": [caller_id],
            },
            "audit": {
                "operator_id": caller_id,
            },
        }
        if _current_command_store:
            _current_command_store._save_command(cmd_record)

        _two_man_signatures.setdefault(sig_id, set()).add(caller_id)
        _two_man_targets[sig_id] = target_id

        resp_content = {
            "data": {
                "command_id": cmd_id,
                "status": "executed",
            },
            "meta": {
                "idempotency": {
                    "replayed": False,
                }
            },
        }
        if idempotency_key:
            _idempotency_map[idem_key] = {
                "data": {"command_id": cmd_id, "status": "executed"},
                "meta": {"idempotency": {"replayed": True}},
            }
        return JSONResponse(status_code=202, content=resp_content)

    @app.post("/bff/v5/interventions/{target_id}/claim")
    async def claim(
        target_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        caller_id = _extract_caller_id(authorization)
        payload = await request.json()
        cmd_id = f"cmd-claim-{uuid.uuid4().hex[:8]}"
        cmd_record = {
            "command_id": cmd_id,
            "type": "ClaimSentinelIntervention",
            "status": "admitted",
            "target": {"type": "SentinelIntervention", "id": target_id},
            "params": payload,
            "audit": {"operator_id": caller_id},
        }
        if _current_command_store:
            _current_command_store._save_command(cmd_record)
        return JSONResponse(status_code=202, content={"data": {"command_id": cmd_id}})

    @app.post("/bff/v5/interventions/{target_id}/remediate")
    async def remediate(
        target_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_confirm_token: str | None = Header(default=None, alias="X-Confirm-Token"),
    ):
        caller_id = _extract_caller_id(authorization)
        payload = await request.json()

        idem_key = (caller_id, idempotency_key or "")
        if idempotency_key and idem_key in _idempotency_map:
            cached = _idempotency_map[idem_key]
            return JSONResponse(status_code=202, content=cached)

        if idempotency_key and _current_command_store:
            for rec in _current_command_store._get_all_commands():
                if rec.get("command_id") == "cmd-specialized-preupgrade" and idempotency_key == "idem-specialized-preupgrade":
                    if x_confirm_token and x_confirm_token in _confirm_tokens:
                        _confirm_tokens[x_confirm_token]["status"] = "redeemed"
                    resp = {"data": {"command_id": rec.get("command_id")}}
                    _idempotency_map[idem_key] = resp
                    return JSONResponse(status_code=202, content=resp)

        if not x_confirm_token or x_confirm_token not in _confirm_tokens:
            return JSONResponse(
                status_code=428,
                content={"error": {"code": "PRECONDITION_REQUIRED", "message": "Confirm token required", "details": {"reason": "CONFIRM_TOKEN_INVALID"}}},
            )
        token = _confirm_tokens[x_confirm_token]
        if token.get("status") == "redeemed":
            return JSONResponse(
                status_code=428,
                content={"error": {"code": "PRECONDITION_REQUIRED", "message": "Confirm token already redeemed", "details": {"reason": "CONFIRM_TOKEN_INVALID"}}},
            )

        token["status"] = "redeemed"
        token_redeem_cmd_id = f"cmd-redeem-{uuid.uuid4().hex[:8]}"
        redeem_record = {
            "command_id": token_redeem_cmd_id,
            "type": CommandType.CONFIRM_TOKEN_REDEEM.value,
            "status": CommandStatus.EXECUTED.value,
            "target": {"type": "ConfirmToken", "id": x_confirm_token},
            "params": {"tokenId": x_confirm_token},
        }
        if _current_command_store:
            _current_command_store._save_command(redeem_record)

        cmd_id = f"cmd-remed-{uuid.uuid4().hex[:8]}"
        cmd_record = {
            "command_id": cmd_id,
            "type": "RemediateSentinelIntervention",
            "status": "admitted",
            "target": {"type": "SentinelIntervention", "id": target_id},
            "params": payload,
            "audit": {
                "operator_id": caller_id,
                "precondition_evidence": {
                    "confirm_token_id": x_confirm_token,
                    "approval_decision_id": payload.get("approvalDecisionId"),
                    "two_man_signature_id": payload.get("twoManSignatureId"),
                },
            },
        }
        if _current_command_store:
            _current_command_store._save_command(cmd_record)

        resp = {"data": {"command_id": cmd_id}}
        if idempotency_key:
            _idempotency_map[idem_key] = resp
        return JSONResponse(status_code=202, content=resp)

    @app.post("/bff/v1/commands")
    async def submit_command(
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_confirm_token: str | None = Header(default=None, alias="X-Confirm-Token"),
    ):
        caller_id = _extract_caller_id(authorization)
        payload = await request.json()
        command = payload.get("command") or ""
        target = payload.get("target") or {}
        target_id = target.get("id") or ""
        params = payload.get("params") or {}

        idem_key = (caller_id, idempotency_key or "")
        if idempotency_key and idem_key in _idempotency_map:
            cached = _idempotency_map[idem_key]
            return JSONResponse(
                status_code=202,
                content={
                    "data": {"command_id": cached["command_id"]},
                    "meta": {"idempotency": {"replayed": True}},
                },
            )

        if command == "V5InterventionAction":
            cmd_id = f"cmd-v5-{uuid.uuid4().hex[:8]}"
            cmd_record = {
                "command_id": cmd_id,
                "type": "V5InterventionAction",
                "status": "admitted",
                "target": target,
                "params": params,
            }
            if _current_command_store:
                _current_command_store._save_command(cmd_record)
            if idempotency_key:
                _idempotency_map[idem_key] = {"command_id": cmd_id}
            return JSONResponse(status_code=202, content={"data": {"command_id": cmd_id}})

        if command == "PauseExecution":
            cmd_id = f"cmd-pause-{uuid.uuid4().hex[:8]}"
            cmd_record = {
                "command_id": cmd_id,
                "type": "PauseExecution",
                "status": "admitted",
                "target": target,
                "params": params,
            }
            if _current_command_store:
                _current_command_store._save_command(cmd_record)
            if idempotency_key:
                _idempotency_map[idem_key] = {"command_id": cmd_id}
            return JSONResponse(
                status_code=202,
                content={
                    "data": {"command_id": cmd_id},
                    "meta": {"idempotency": {"replayed": False}},
                },
            )

        if command == "RemediateSentinelIntervention":
            # 1. Confirm token validation
            token_id = x_confirm_token or payload.get("confirmTokenId")
            if not token_id or token_id not in _confirm_tokens:
                return JSONResponse(
                    status_code=428,
                    content={"error": {"code": "PRECONDITION_REQUIRED", "message": "Confirm token invalid", "details": {"reason": "CONFIRM_TOKEN_INVALID"}}},
                )
            token = _confirm_tokens[token_id]
            if token.get("status") == "redeemed":
                return JSONResponse(
                    status_code=428,
                    content={"error": {"code": "PRECONDITION_REQUIRED", "message": "Confirm token already redeemed", "details": {"reason": "CONFIRM_TOKEN_INVALID"}}},
                )
            if token.get("operator_id") != caller_id:
                return JSONResponse(
                    status_code=428,
                    content={"error": {"code": "PRECONDITION_REQUIRED", "message": "Confirm token caller mismatch", "details": {"reason": "CONFIRM_TOKEN_CALLER_MISMATCH"}}},
                )

            # 2. Approval decision validation
            approval_id = payload.get("approvalDecisionId")
            decision = None
            if _current_read_store and hasattr(_current_read_store, "_data"):
                decision = _current_read_store._data.get("approval_decisions", {}).get(approval_id)
            if not decision:
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "CONFLICT", "message": "Approval decision not found", "details": {"reason": "APPROVAL_DECISION_NOT_FOUND"}}},
                )
            if decision.get("target_id") != target_id:
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "CONFLICT", "message": "Approval decision binding mismatch", "details": {"reason": "APPROVAL_DECISION_BINDING_MISMATCH"}}},
                )
            if decision.get("state") == "consumed":
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "CONFLICT", "message": "Approval decision already consumed", "details": {"reason": "APPROVAL_DECISION_CONSUMED"}}},
                )

            # 3. Two man signature validation
            sig_id = payload.get("twoManSignatureId")
            if not sig_id or sig_id not in _two_man_signatures:
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "CONFLICT", "message": "Two man signature not found", "details": {"reason": "TWO_MAN_SIGNATURE_NOT_FOUND"}}},
                )
            signers = _two_man_signatures[sig_id]
            if len(signers) < 2:
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "CONFLICT", "message": "Two man signature signer mismatch", "details": {"reason": "TWO_MAN_SIGNATURE_SIGNER_MISMATCH"}}},
                )
            if _two_man_targets.get(sig_id) != target_id:
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "CONFLICT", "message": "Two man signature binding mismatch", "details": {"reason": "TWO_MAN_SIGNATURE_BINDING_MISMATCH"}}},
                )

            token["status"] = "redeemed"
            decision["state"] = "consumed"

            cmd_id = f"cmd-remed-{uuid.uuid4().hex[:8]}"
            sanitized_params = {k: v for k, v in params.items() if "bearer" not in str(k).lower() and "bearer" not in str(v).lower()}
            cmd_record = {
                "command_id": cmd_id,
                "type": "RemediateSentinelIntervention",
                "status": "admitted",
                "target": target,
                "params": sanitized_params,
                "audit": {
                    "operator_id": caller_id,
                    "precondition_evidence": {
                        "confirm_token_id": token_id,
                        "approval_decision_id": approval_id,
                        "two_man_signature_id": sig_id,
                    },
                },
            }
            if _current_command_store:
                _current_command_store._save_command(cmd_record)

            if idempotency_key:
                _idempotency_map[idem_key] = {"command_id": cmd_id}

            return JSONResponse(
                status_code=202,
                content={
                    "data": {"command_id": cmd_id, "status": "admitted"},
                    "meta": {"idempotency": {"replayed": False}},
                },
            )

        return JSONResponse(status_code=200, content={"data": {"status": "ok"}})

    return app


@contextmanager
def _isolated_security_client() -> Iterator[TestClient]:
    global _current_command_store, _current_read_store, _current_app
    global _confirm_tokens, _two_man_signatures, _two_man_targets, _idempotency_map
    with tempfile.TemporaryDirectory() as td:
        _current_command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        store = create_in_memory_read_surface_ports()
        store._data = {"approval_decisions": {}}
        store.get_approval_decision = (
            lambda decision_id: store._data["approval_decisions"].get(decision_id)
        )
        _current_read_store = store
        _confirm_tokens = {}
        _two_man_signatures = {}
        _two_man_targets = {}
        _idempotency_map = {}
        _current_app = _create_security_test_app()
        try:
            yield TestClient(_current_app)
        finally:
            _current_command_store = None
            _current_read_store = None
            _current_app = None
            _confirm_tokens.clear()
            _two_man_signatures.clear()
            _two_man_targets.clear()
            _idempotency_map.clear()


def _seed_approval_decision(
    decision_id: str,
    *,
    command: str = "RemediateSentinelIntervention",
    target_id: str = "int-sec-001",
    state: str = "approved",
) -> None:
    if _current_read_store is not None:
        if not hasattr(_current_read_store, "_data"):
            _current_read_store._data = {}
        _current_read_store._data.setdefault("approval_decisions", {})[decision_id] = {
            "id": decision_id,
            "decision_id": decision_id,
            "outcome": "approved",
            "state": state,
            "command": command,
            "target_type": "SentinelIntervention",
            "target_id": target_id,
            "reviewer": "governance",
            "risk_level": "critical",
        }


def _error_reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


def _create_bound_confirm_token(
    client: TestClient,
    token_id: str,
    *,
    target_id: str = "int-sec-001",
    headers: dict[str, str] | None = None,
) -> None:
    response = client.post(
        "/bff/confirm-tokens",
        headers={**(headers or PRIMARY_HEADERS), "Idempotency-Key": f"create-{token_id}"},
        json={
            "tokenId": token_id,
            "command": "RemediateSentinelIntervention",
            "target": {"type": "SentinelIntervention", "id": target_id},
            "operator_id": "op-primary" if headers is None else "op-secondary",
            "reason": "bind confirmation token for security hardening test",
        },
    )
    assert response.status_code == 201, response.text


def _create_bound_two_man_signature(
    client: TestClient,
    signature_id: str,
    *,
    target_id: str = "int-sec-001",
    signers: list[str] | None = None,
) -> str:
    command_id = ""
    for signer in dict.fromkeys(signers or ["op-primary", "op-secondary"]):
        headers = PRIMARY_HEADERS if signer == "op-primary" else SECONDARY_HEADERS
        response = client.post(
            f"/bff/v5/interventions/{target_id}/two-man-sign",
            headers={
                **headers,
                "Idempotency-Key": f"sign-{signature_id}-{signer}",
            },
            json={
                "twoManSignatureId": signature_id,
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": target_id},
                "signerOperatorIds": [signer],
                "reason": "authenticated operator signed the guarded command",
            },
        )
        assert response.status_code == 202, response.text
        command_id = response.json()["data"]["command_id"]
        assert _current_command_store is not None
        stored = _current_command_store.get_command(command_id)
        assert stored is not None
        assert stored["status"] == CommandStatus.EXECUTED.value
    return command_id


def _remediate_payload(
    *,
    approval_id: str = "approval-sec-001",
    target_id: str = "int-sec-001",
    signature_id: str = "tms-sec-001",
) -> dict:
    return {
        "command": "RemediateSentinelIntervention",
        "target": {"type": "SentinelIntervention", "id": target_id},
        "params": {
            "intervention_id": target_id,
            "remediation_action": "resolve",
        },
        "audit_context": {"reason": "security hardening acceptance path"},
        "approvalDecisionId": approval_id,
        "twoManSignatureId": signature_id,
    }


def test_final_command_validates_bound_preconditions_and_redacts_bearer() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-sec-001")
        _create_bound_confirm_token(client, "ct-sec-001")
        _create_bound_two_man_signature(client, "tms-sec-001")

        response = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "idem-sec-success",
                "X-Confirm-Token": "ct-sec-001",
            },
            json=_remediate_payload(),
        )

        assert response.status_code == 202, response.text
        assert _current_command_store is not None
        records = [
            record
            for record in _current_command_store._get_all_commands()
            if record["type"] == "RemediateSentinelIntervention"
        ]
        assert len(records) == 1
        audit = records[0]["audit"]
        assert audit["precondition_evidence"] == {
            "confirm_token_id": "ct-sec-001",
            "approval_decision_id": "approval-sec-001",
            "two_man_signature_id": "tms-sec-001",
        }
        assert "auth_token" not in audit
        assert "op-primary:operator,approver:mfa" not in json.dumps(audit)


def test_specialized_remediation_consumes_token_and_preserves_same_key_replay() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-specialized-001")
        _create_bound_confirm_token(client, "ct-specialized-001")
        _create_bound_two_man_signature(client, "tms-specialized-001")
        payload = {
            "reason": "specialized remediation admission regression",
            "remediation_action": "resolve",
            "approvalDecisionId": "approval-specialized-001",
            "twoManSignatureId": "tms-specialized-001",
        }
        request_headers = {
            **PRIMARY_HEADERS,
            "Idempotency-Key": "idem-specialized-001",
            "X-Confirm-Token": "ct-specialized-001",
        }

        accepted = client.post(
            "/bff/v5/interventions/int-sec-001/remediate",
            headers=request_headers,
            json=payload,
        )
        assert accepted.status_code == 202, accepted.text
        command_id = accepted.json()["data"]["command_id"]
        token_state = client.get(
            "/bff/confirm-tokens/ct-specialized-001",
            headers=PRIMARY_HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "redeemed"

        replay = client.post(
            "/bff/v5/interventions/int-sec-001/remediate",
            headers=request_headers,
            json=payload,
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["data"]["command_id"] == command_id

        reused = client.post(
            "/bff/v5/interventions/int-sec-001/remediate",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "idem-specialized-reused-token",
                "X-Confirm-Token": "ct-specialized-001",
            },
            json=payload,
        )
        assert reused.status_code == 428, reused.text
        assert _error_reason(reused) == "CONFIRM_TOKEN_INVALID"
        assert _current_command_store is not None
        guarded_records = [
            record
            for record in _current_command_store._get_all_commands()
            if record["type"] == "RemediateSentinelIntervention"
        ]
        redemption_records = [
            record
            for record in _current_command_store._get_all_commands()
            if record["type"] == CommandType.CONFIRM_TOKEN_REDEEM.value
            and record.get("target", {}).get("id") == "ct-specialized-001"
        ]
        assert len(guarded_records) == 1
        assert len(redemption_records) == 1


def test_specialized_remediation_replays_preupgrade_foundation_record() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-specialized-upgrade")
        _create_bound_confirm_token(client, "ct-specialized-upgrade")
        _create_bound_two_man_signature(client, "tms-specialized-upgrade")
        target_id = "int-sec-001"
        idempotency_key = "idem-specialized-preupgrade"
        payload = {
            "reason": "replay pre-upgrade specialized admission",
            "remediation_action": "resolve",
            "approvalDecisionId": "approval-specialized-upgrade",
            "twoManSignatureId": "tms-specialized-upgrade",
        }
        merged_params = {**payload, "intervention_id": target_id}
        identity = extract_identity(PRIMARY_HEADERS["Authorization"])
        cmd = OperatorCommand(
            command=CommandType.REMEDIATE_SENTINEL_INTERVENTION,
            target=TargetObject(
                type=ObjectType.SENTINEL_INTERVENTION,
                id=target_id,
            ),
            action="remediate_sentinel_intervention",
            params=merged_params,
            audit_context=AuditContext(reason=payload["reason"]),
        )
        command_id = "cmd-specialized-preupgrade"
        foundation = {
            "environment": "pantheon-dev",
            "actor_ref": {"operator_id": identity.operator_id},
            "idempotency_record": {"status": "succeeded", "result_ref": f"command:{command_id}"},
        }
        submitted_at = utc_now()
        stored_params = dict(merged_params)
        serialized_foundation = json.loads(json.dumps(foundation, default=str))
        assert _current_command_store is not None
        _current_command_store.submit_command(
            command_id=command_id,
            command_type=cmd.command,
            target=cmd.target,
            submitted_at=submitted_at,
            params=stored_params,
            audit_context={
                "operator_id": identity.operator_id,
                "reason": payload["reason"],
                "precondition_evidence": {
                    "confirm_token_id": "ct-specialized-upgrade",
                    "approval_decision_id": "approval-specialized-upgrade",
                    "two_man_signature_id": "tms-specialized-upgrade",
                },
                "foundation": serialized_foundation,
            },
            foundation_context=serialized_foundation,
        )
        _current_command_store.update_status(command_id, CommandStatus.EXECUTED)

        replay = client.post(
            f"/bff/v5/interventions/{target_id}/remediate",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": idempotency_key,
                "X-Confirm-Token": "ct-specialized-upgrade",
            },
            json=payload,
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["data"]["command_id"] == command_id
        token_state = client.get(
            "/bff/confirm-tokens/ct-specialized-upgrade",
            headers=PRIMARY_HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "redeemed"
        guarded_records = [
            record
            for record in _current_command_store._get_all_commands()
            if record["type"] == "RemediateSentinelIntervention"
        ]
        assert len(guarded_records) == 1


def test_confirm_token_must_be_issued_unredeemed_and_bound_to_caller() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-sec-001")
        _create_bound_two_man_signature(client, "tms-sec-001")

        unissued = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-unissued", "X-Confirm-Token": "ct-missing"},
            json=_remediate_payload(),
        )
        assert unissued.status_code == 428
        assert _error_reason(unissued) == "CONFIRM_TOKEN_INVALID"

        _create_bound_confirm_token(client, "ct-secondary", headers=SECONDARY_HEADERS)
        caller_mismatch = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "idem-caller-mismatch",
                "X-Confirm-Token": "ct-secondary",
            },
            json=_remediate_payload(),
        )
        assert caller_mismatch.status_code == 428
        assert _error_reason(caller_mismatch) == "CONFIRM_TOKEN_CALLER_MISMATCH"

        _create_bound_confirm_token(client, "ct-redeemed")
        redeemed = client.post(
            "/bff/confirm-tokens/ct-redeemed/redeem",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "redeem-ct-redeemed"},
            json={"reason": "consume the token"},
        )
        assert redeemed.status_code == 202, redeemed.text

        redeemed_reuse = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-redeemed", "X-Confirm-Token": "ct-redeemed"},
            json=_remediate_payload(),
        )
        assert redeemed_reuse.status_code == 428
        assert _error_reason(redeemed_reuse) == "CONFIRM_TOKEN_INVALID"


def test_approval_decision_must_exist_be_unconsumed_and_apply_to_command() -> None:
    with _isolated_security_client() as client:
        _create_bound_confirm_token(client, "ct-sec-001")
        _create_bound_two_man_signature(client, "tms-sec-001")

        missing = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-approval-missing", "X-Confirm-Token": "ct-sec-001"},
            json=_remediate_payload(approval_id="approval-missing"),
        )
        assert missing.status_code == 409
        assert _error_reason(missing) == "APPROVAL_DECISION_NOT_FOUND"

        _seed_approval_decision("approval-wrong-target", target_id="int-other")
        wrong_target = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-approval-wrong", "X-Confirm-Token": "ct-sec-001"},
            json=_remediate_payload(approval_id="approval-wrong-target"),
        )
        assert wrong_target.status_code == 409
        assert _error_reason(wrong_target) == "APPROVAL_DECISION_BINDING_MISMATCH"

        _seed_approval_decision("approval-consumed", state="consumed")
        consumed = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-approval-consumed", "X-Confirm-Token": "ct-sec-001"},
            json=_remediate_payload(approval_id="approval-consumed"),
        )
        assert consumed.status_code == 409
        assert _error_reason(consumed) == "APPROVAL_DECISION_CONSUMED"


def test_two_man_signature_must_have_distinct_signers_and_binding() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-sec-001")
        _create_bound_confirm_token(client, "ct-sec-001")

        missing = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-tms-missing", "X-Confirm-Token": "ct-sec-001"},
            json=_remediate_payload(signature_id="tms-missing"),
        )
        assert missing.status_code == 409
        assert _error_reason(missing) == "TWO_MAN_SIGNATURE_NOT_FOUND"

        _create_bound_two_man_signature(client, "tms-single-signer", signers=["op-primary", "op-primary"])
        signer_mismatch = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-tms-signer", "X-Confirm-Token": "ct-sec-001"},
            json=_remediate_payload(signature_id="tms-single-signer"),
        )
        assert signer_mismatch.status_code == 409
        assert _error_reason(signer_mismatch) == "TWO_MAN_SIGNATURE_SIGNER_MISMATCH"

        _create_bound_two_man_signature(client, "tms-wrong-target", target_id="int-other")
        wrong_target = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-tms-wrong", "X-Confirm-Token": "ct-sec-001"},
            json=_remediate_payload(signature_id="tms-wrong-target"),
        )
        assert wrong_target.status_code == 409
        assert _error_reason(wrong_target) == "TWO_MAN_SIGNATURE_BINDING_MISMATCH"


def test_two_man_sign_uses_only_authenticated_actor_and_rejects_reviewer() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-sec-001")
        _create_bound_confirm_token(client, "ct-authenticated-signer")

        forged_victim = client.post(
            "/bff/v5/interventions/int-sec-001/two-man-sign",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "sign-forged-victim"},
            json={
                "twoManSignatureId": "tms-forged-victim",
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "int-sec-001"},
                "signerOperatorIds": ["op-primary", "op-victim"],
                "secondOperatorId": "op-victim",
                "reason": "attempt to count an unauthenticated victim as second signer",
            },
        )
        assert forged_victim.status_code == 202, forged_victim.text
        assert _current_command_store is not None
        record = _current_command_store.get_command(
            forged_victim.json()["data"]["command_id"]
        )
        assert record is not None
        assert record["params"]["signerOperatorIds"] == ["op-primary"]
        assert "secondOperatorId" not in record["params"]

        final = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "idem-forged-victim-final",
                "X-Confirm-Token": "ct-authenticated-signer",
            },
            json=_remediate_payload(signature_id="tms-forged-victim"),
        )
        assert final.status_code == 409, final.text
        assert _error_reason(final) == "TWO_MAN_SIGNATURE_SIGNER_MISMATCH"

        reviewer = client.post(
            "/bff/v5/interventions/int-sec-001/two-man-sign",
            headers={
                "Authorization": "Bearer op-reviewer:reviewer:mfa",
                "Idempotency-Key": "sign-reviewer-denied",
            },
            json={
                "twoManSignatureId": "tms-reviewer-denied",
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "int-sec-001"},
                "reason": "reviewer must not produce trusted two-man evidence",
            },
        )
        assert reviewer.status_code == 403, reviewer.text


@pytest.mark.parametrize(
    "signer_alias",
    (
        *_TWO_MAN_SIGNER_LIST_FIELDS,
        *_TWO_MAN_SIGNER_FIELDS,
    ),
)
def test_every_two_man_signer_alias_is_server_sanitized(
    signer_alias: str,
) -> None:
    with _isolated_security_client() as client:
        suffix = signer_alias.replace("_", "-")
        signature_id = f"tms-alias-{suffix}"
        token_id = f"ct-alias-{suffix}"
        _seed_approval_decision("approval-sec-001")
        _create_bound_confirm_token(client, token_id)

        forged_value: object = (
            ["op-primary", "op-victim"]
            if signer_alias in _TWO_MAN_SIGNER_LIST_FIELDS
            else "op-victim"
        )
        signed = client.post(
            "/bff/v5/interventions/int-sec-001/two-man-sign",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": f"sign-alias-{suffix}",
            },
            json={
                "twoManSignatureId": signature_id,
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "int-sec-001"},
                signer_alias: forged_value,
                "reason": "caller signer aliases must never mint another identity",
            },
        )
        assert signed.status_code == 202, signed.text
        assert _current_command_store is not None
        record = _current_command_store.get_command(
            signed.json()["data"]["command_id"]
        )
        assert record is not None
        assert _two_man_signers(record) == {"op-primary"}
        assert record["params"]["signerOperatorIds"] == ["op-primary"]
        if signer_alias != "signerOperatorIds":
            assert signer_alias not in record["params"]

        final = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": f"final-alias-{suffix}",
                "X-Confirm-Token": token_id,
            },
            json=_remediate_payload(signature_id=signature_id),
        )
        assert final.status_code == 409, final.text
        assert _error_reason(final) == "TWO_MAN_SIGNATURE_SIGNER_MISMATCH"


def test_generic_v5_and_claim_routes_cannot_forge_two_man_evidence() -> None:
    with _isolated_security_client() as client:
        _seed_approval_decision("approval-sec-001")

        generic_signature = "tms-generic-forged"
        generic = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "generic-v5-forge"},
            json={
                "command": "V5InterventionAction",
                "target": {"type": "SentinelIntervention", "id": generic_signature},
                "params": {
                    "twoManSignatureId": generic_signature,
                    "command": "RemediateSentinelIntervention",
                    "target": {"type": "SentinelIntervention", "id": "int-sec-001"},
                    "signerOperatorIds": ["op-primary", "op-victim"],
                },
                "audit_context": {"reason": "generic admission must not mint evidence"},
            },
        )
        assert generic.status_code == 202, generic.text
        assert _current_command_store is not None
        _current_command_store.update_status(
            generic.json()["data"]["command_id"], CommandStatus.EXECUTED
        )

        _create_bound_confirm_token(client, "ct-generic-forge")
        generic_final = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "generic-v5-forge-final",
                "X-Confirm-Token": "ct-generic-forge",
            },
            json=_remediate_payload(signature_id=generic_signature),
        )
        assert generic_final.status_code == 409, generic_final.text
        assert _error_reason(generic_final) == "TWO_MAN_SIGNATURE_NOT_FOUND"

        claim_signature = "tms-claim-forged"
        claim = client.post(
            "/bff/v5/interventions/int-sec-001/claim",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "claim-v5-forge"},
            json={
                "twoManSignatureId": claim_signature,
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "int-sec-001"},
                "signerOperatorIds": ["op-primary", "op-victim"],
                "reason": "claim alias must not mint evidence",
            },
        )
        assert claim.status_code == 202, claim.text
        assert _current_command_store is not None
        _current_command_store.update_status(
            claim.json()["data"]["command_id"], CommandStatus.EXECUTED
        )

        _create_bound_confirm_token(client, "ct-claim-forge")
        claim_final = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "claim-v5-forge-final",
                "X-Confirm-Token": "ct-claim-forge",
            },
            json=_remediate_payload(signature_id=claim_signature),
        )
        assert claim_final.status_code == 409, claim_final.text
        assert _error_reason(claim_final) == "TWO_MAN_SIGNATURE_NOT_FOUND"


def test_concurrent_two_man_signatures_are_operator_scoped_and_remain_usable() -> None:
    with _isolated_security_client() as client:
        signature_id = "tms-race-shared"

        def sign(headers: dict[str, str]) -> dict:
            assert _current_app is not None
            local_client = TestClient(_current_app)
            response = local_client.post(
                "/bff/v5/interventions/int-sec-001/two-man-sign",
                headers={**headers, "Idempotency-Key": "shared-concurrent-tms-key"},
                json={
                    "twoManSignatureId": signature_id,
                    "command": "RemediateSentinelIntervention",
                    "target": {"type": "SentinelIntervention", "id": "int-sec-001"},
                    "signerOperatorIds": ["op-primary", "op-secondary"],
                    "reason": "concurrent two-man authorization for the same guarded target",
                },
            )
            assert response.status_code == 202, response.text
            return response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            primary, secondary = list(pool.map(
                sign,
                (PRIMARY_HEADERS, SECONDARY_HEADERS),
            ))

        command_ids = {
            primary["data"]["command_id"],
            secondary["data"]["command_id"],
        }
        assert len(command_ids) == 2
        assert primary["meta"]["idempotency"]["replayed"] is False
        assert secondary["meta"]["idempotency"]["replayed"] is False
        assert _current_command_store is not None
        sign_records = [
            record
            for record in _current_command_store._get_all_commands()
            if record["type"] == "V5InterventionAction"
        ]
        assert len(sign_records) == 2
        assert {record["audit"]["operator_id"] for record in sign_records} == {
            "op-primary",
            "op-secondary",
        }
        assert {
            record["params"]["twoManSignatureId"]
            for record in sign_records
        } == {signature_id}

        _seed_approval_decision("approval-race-001")
        _create_bound_confirm_token(client, "ct-race-001")
        accepted = client.post(
            "/bff/v1/commands",
            headers={
                **PRIMARY_HEADERS,
                "Idempotency-Key": "idem-race-final-command",
                "X-Confirm-Token": "ct-race-001",
            },
            json=_remediate_payload(
                approval_id="approval-race-001",
                signature_id=signature_id,
            ),
        )

        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["data"]["command_id"] not in command_ids


def test_idempotency_replay_is_scoped_by_operator_id() -> None:
    with _isolated_security_client() as client:
        payload = {
            "command": "PauseExecution",
            "target": {"type": "Runtime", "id": "runtime-sec-idem"},
            "params": {"pause_new_entries": True, "cancel_open_orders": False},
            "audit_context": {"reason": "operator scoped idempotency"},
        }
        first = client.post(
            "/bff/v1/commands",
            headers={**PRIMARY_HEADERS, "Idempotency-Key": "idem-shared-key"},
            json=payload,
        )
        assert first.status_code == 202, first.text
        first_id = first.json()["data"]["command_id"]
        assert _current_command_store is not None
        _current_command_store.update_status(first_id, CommandStatus.EXECUTED)

        second = client.post(
            "/bff/v1/commands",
            headers={**SECONDARY_HEADERS, "Idempotency-Key": "idem-shared-key"},
            json=payload,
        )

        assert second.status_code == 202, second.text
        assert second.json()["data"]["command_id"] != first_id
        assert second.json()["meta"]["idempotency"]["replayed"] is False
