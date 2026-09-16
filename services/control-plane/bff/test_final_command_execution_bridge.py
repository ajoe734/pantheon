from __future__ import annotations

from copy import deepcopy
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters import (
    CommandAdapterService,
    create_command_adapters_router,
)
from services.control_plane.bff.command_adapters.contracts import (
    resolve_final_idempotency_key,
    stable_json_hash,
)
from services.control_plane.bff.command_adapters.preconditions import (
    reject_body_idempotency_key,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import (
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorIdentity,
    TargetObject,
    utc_now,
)


HEADERS = {"Authorization": "Bearer op-sem-002:operator,reviewer,admin:mfa"}


def _test_extract_identity(
    authorization: Optional[str] = None, mfa_token: Optional[str] = None
) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        return OperatorIdentity(operator_id="anonymous", roles=["viewer"], auth_mode="anonymous", has_mfa=False)
    token = authorization[len("Bearer ") :].strip()
    parts = token.split(":")
    actor = parts[0] if parts else "system"
    roles = [r.strip() for r in parts[1].split(",")] if len(parts) > 1 else ["operator"]
    return OperatorIdentity(
        operator_id=actor,
        roles=roles,
        auth_mode="bearer",
        has_mfa=len(parts) > 2 and parts[2] == "mfa",
    )


command_store: Optional[CommandStore] = None
_FINAL_CONTRACT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_CAPITAL_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


def _test_sem_command_response(
    *,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str] = None,
    status_code: int = 202,
    server_generated_target: bool = False,
    trusted_evidence_producer: Optional[str] = None,
    terminal_on_persist: bool = False,
) -> JSONResponse:
    payload = dict(payload or {})
    reject_body_idempotency_key(payload)
    clean_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    hash_body: Dict[str, Any] = {
        "command": command_type.value,
        "target_type": target_type.value,
        "payload": payload,
    }
    if not server_generated_target:
        hash_body["target_id"] = target_id
    request_hash = stable_json_hash(hash_body)
    cache_key = f"{identity.operator_id}\x00{clean_key}"

    existing = _FINAL_CONTRACT_IDEMPOTENCY.get(cache_key)
    if existing:
        if existing.get("request_hash") != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": ErrorCode.IDEMPOTENCY_CONFLICT.value,
                        "message": "Idempotency key was reused with a different command payload",
                        "details": {
                            "precondition_failed": "idempotency_key",
                            "reason": "The idempotency key already belongs to another command payload",
                        },
                    }
                },
            )
        replay = deepcopy(existing["result"])
        replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
        return JSONResponse(status_code=status_code, content=replay)

    store = command_store
    if store is not None:
        existing_record = store.get_command_by_idempotency_key(
            clean_key,
            operator_id=identity.operator_id,
        )
        if existing_record:
            stored_hash = (existing_record.get("foundation") or {}).get("idempotency_record", {}).get("request_hash")
            if stored_hash and stored_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": ErrorCode.IDEMPOTENCY_CONFLICT.value,
                            "message": "Idempotency key was reused with a different command payload",
                            "details": {
                                "precondition_failed": "idempotency_key",
                                "reason": "The idempotency key already belongs to another command payload",
                            },
                        }
                    },
                )
            stored_resp = (existing_record.get("foundation") or {}).get("stored_response")
            if stored_resp:
                replay = deepcopy(stored_resp)
                replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return JSONResponse(status_code=status_code, content=replay)

    now = utc_now()
    command_id = f"cmd-{uuid.uuid4().hex[:16]}"
    receipt = {
        "receipt_id": command_id,
        "status": "accepted",
        "command": command_type.value,
        "target": {"type": target_type.value, "id": target_id},
        "submitted_at": now,
        "accepted_at": now,
    }
    result_content = {
        "command_id": command_id,
        "status": "accepted",
        "data": {
            "command_id": command_id,
            "commandId": command_id,
            "command": command_type.value,
            "status": "accepted",
            "receipt_id": command_id,
            "target": {"type": target_type.value, "id": target_id},
            "receipt": receipt,
        },
        "meta": {
            "durable": True,
            "liveCapitalSideEffects": False,
            "idempotency": {
                "key": clean_key,
                "idempotencyKey": clean_key,
                "replayed": False,
            },
            "snapshot_at": now,
        },
    }

    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": clean_key,
            "request_hash": request_hash,
            "status": "succeeded",
        },
        "stored_response": result_content,
    }
    audit_ctx = {
        "actor": identity.operator_id,
        "operator_id": identity.operator_id,
        "command_id": command_id,
        "reason": str(payload.get("reason") or command_type.value),
        "foundation": foundation_ctx,
        "live_capital_side_effects": False,
    }
    if store is not None:
        target_obj = TargetObject(type=target_type, id=target_id)
        store.submit_command(
            command_id=command_id,
            command_type=command_type,
            target=target_obj,
            submitted_at=now,
            params=payload,
            audit_context=audit_ctx,
            foundation_context=foundation_ctx,
        )

    _FINAL_CONTRACT_IDEMPOTENCY[cache_key] = {"request_hash": request_hash, "result": result_content}
    return JSONResponse(status_code=status_code, content=result_content)


def _build_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    service = CommandAdapterService(
        command_store=lambda: command_store,
        extract_identity=_test_extract_identity,
        final_contract_idempotency=_FINAL_CONTRACT_IDEMPOTENCY,
        gov_bff_idempotency=_GOV_BFF_IDEMPOTENCY,
    )
    service.sem_command_response = _test_sem_command_response

    cmd_router = create_command_adapters_router(service=service)
    app.include_router(cmd_router)

    @app.post("/bff/deployments", status_code=201)
    async def _create_deployment(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = _test_extract_identity(authorization)
        client_provided_id = payload.get("deployment_id") or payload.get("deploymentId") or payload.get("id")
        deployment_id = str(client_provided_id or f"deployment-{uuid.uuid4().hex[:8]}")
        return _test_sem_command_response(
            command_type=CommandType.DEPLOYMENT_CREATE,
            target_type=ObjectType.DEPLOYMENT,
            target_id=deployment_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=201,
            server_generated_target=not client_provided_id,
        )

    @app.post("/bff/audit/export", status_code=202)
    async def _audit_export(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = _test_extract_identity(authorization)
        return _test_sem_command_response(
            command_type=CommandType.AUDIT_EXPORT,
            target_type=ObjectType.AUDIT_EXPORT,
            target_id=str(payload.get("target_type") or payload.get("targetType") or "audit-export"),
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=202,
        )

    @app.post("/bff/v5/interventions/{id}/decide", status_code=202)
    async def _intervention_decide(
        id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = _test_extract_identity(authorization)
        return _test_sem_command_response(
            command_type=CommandType.DECIDE_V5_INTERVENTION,
            target_type=ObjectType.SENTINEL_INTERVENTION,
            target_id=id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=202,
        )

    @app.post("/bff/v5/sentinel/findings/{id}/status", status_code=202)
    async def _sentinel_finding_status(
        id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = _test_extract_identity(authorization)
        return _test_sem_command_response(
            command_type=CommandType.SENTINEL_FINDING_STATUS,
            target_type=ObjectType.SENTINEL_FINDING,
            target_id=id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=202,
        )

    @app.post("/bff/v5/sentinel/remediation/build", status_code=202)
    async def _sentinel_remediation_build(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = _test_extract_identity(authorization)
        provided_finding = payload.get("finding_id") or payload.get("findingId")
        target_id = str(provided_finding or f"remediation-{uuid.uuid4().hex[:8]}")
        return _test_sem_command_response(
            command_type=CommandType.SENTINEL_REMEDIATION_BUILD,
            target_type=ObjectType.SENTINEL_REMEDIATION,
            target_id=target_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=202,
            server_generated_target=not provided_finding,
        )

    @app.post("/bff/v5/sentinel/remediation/{id}/execute", status_code=202)
    async def _sentinel_remediation_execute(
        id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = _test_extract_identity(authorization)
        return _test_sem_command_response(
            command_type=CommandType.SENTINEL_REMEDIATION_EXECUTE,
            target_type=ObjectType.SENTINEL_REMEDIATION,
            target_id=id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=202,
        )

    return app


@contextmanager
def _isolated_command_bridge() -> Iterator[TestClient]:
    global command_store
    with tempfile.TemporaryDirectory() as td:
        command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        _FINAL_CONTRACT_IDEMPOTENCY.clear()
        _CAPITAL_BFF_IDEMPOTENCY.clear()
        _GOV_BFF_IDEMPOTENCY.clear()
        app = _build_test_app()
        try:
            yield TestClient(app)
        finally:
            command_store = None
            _FINAL_CONTRACT_IDEMPOTENCY.clear()
            _CAPITAL_BFF_IDEMPOTENCY.clear()
            _GOV_BFF_IDEMPOTENCY.clear()


def _receipt_id(payload: dict) -> str:
    assert payload["status"] == "accepted"
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["receipt"]["status"] == "accepted"
    assert payload["meta"]["durable"] is True
    assert payload["meta"]["liveCapitalSideEffects"] is False
    return payload["data"]["receipt_id"]


def test_deployment_create_writes_command_store_and_replays_from_durable_idempotency() -> None:
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-deploy-create"}
        body = {"deployment_id": "dep-sem-002", "stage": "paper"}

        first = client.post("/bff/deployments", headers=headers, json=body)
        second = client.post("/bff/deployments", headers=headers, json=body)
        conflict = client.post("/bff/deployments", headers=headers, json={**body, "stage": "live"})

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert conflict.status_code == 409, conflict.text
        command_id = _receipt_id(first.json())
        assert _receipt_id(second.json()) == command_id
        assert second.json()["meta"]["idempotency"]["replayed"] is True

        records = command_store._get_all_commands()
        assert len(records) == 1
        assert records[0]["command_id"] == command_id
        assert records[0]["type"] == "CreateDeployment"
        assert records[0]["target"] == {"type": "Deployment", "id": "dep-sem-002"}
        assert records[0]["foundation"]["idempotency_record"]["status"] == "succeeded"
        assert records[0]["audit"]["live_capital_side_effects"] is False

        status = client.get(f"/api/v1/operator/commands/{command_id}", headers=HEADERS)
        assert status.status_code == 200, status.text
        assert status.json()["type"] == "CreateDeployment"


def test_command_routes_require_header_idempotency_and_reject_body_key() -> None:
    with _isolated_command_bridge() as client:
        body = {"deployment_id": "dep-sem-002-idempotency", "stage": "paper"}

        missing = client.post("/bff/deployments", headers=HEADERS, json=body)
        body_key = client.post(
            "/bff/deployments",
            headers=HEADERS,
            json={**body, "idempotencyKey": "body-key-is-invalid"},
        )
        alias = client.post(
            "/bff/deployments",
            headers={**HEADERS, "X-Idempotency-Key": "sem-002-deploy-alias"},
            json=body,
        )

        assert missing.status_code == 400, missing.text
        assert missing.json()["error"]["code"] == "VALIDATION_FAILED"
        assert body_key.status_code == 400, body_key.text
        assert body_key.json()["error"]["code"] == "VALIDATION_FAILED"
        assert alias.status_code == 201, alias.text
        assert alias.json()["meta"]["idempotency"]["idempotencyKey"] == "sem-002-deploy-alias"
        assert len(command_store._get_all_commands()) == 1


def test_canonical_action_replay_uses_command_store_not_generic_memory_receipt() -> None:
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-action"}
        command_envelope = {
            "command": "StrategyAction",
            "target": {"type": "Strategy", "id": "stg-sem-002"},
            "action": "submit",
            "params": {
                "action_id": "submit",
                "entity_type": "strategy",
                "entity_id": "stg-sem-002",
                "reason": "submit for semantic bridge proof",
            },
            "audit_context": {"reason": "submit for semantic bridge proof"},
        }

        first = client.post("/bff/v1/commands", headers=headers, json=command_envelope)
        _CAPITAL_BFF_IDEMPOTENCY.clear()
        replay = client.post("/bff/v1/commands", headers=headers, json=command_envelope)

        assert first.status_code == 202, first.text
        assert replay.status_code == 202, replay.text
        assert _receipt_id(replay.json()) == _receipt_id(first.json())
        assert replay.json()["meta"]["idempotency"]["replayed"] is True
        records = command_store._get_all_commands()
        assert len(records) == 1
        assert records[0]["type"] == "StrategyAction"


def test_confirm_token_create_read_redeem_delete_are_command_store_backed() -> None:
    with _isolated_command_bridge() as client:
        create = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "sem-002-token-create"},
            json={"tokenId": "ct-sem-002", "reason": "guarded action"},
        )
        assert create.status_code == 201, create.text
        assert create.json()["data"]["tokenId"] == "ct-sem-002"

        read_created = client.get("/bff/confirm-tokens/ct-sem-002", headers=HEADERS)
        assert read_created.status_code == 200, read_created.text
        assert read_created.json()["data"]["status"] == "created"

        redeem = client.post(
            "/bff/confirm-tokens/ct-sem-002/redeem",
            headers={**HEADERS, "Idempotency-Key": "sem-002-token-redeem"},
            json={"reason": "operator confirmed"},
        )
        assert redeem.status_code == 202, redeem.text

        delete = client.request(
            "DELETE",
            "/bff/confirm-tokens/ct-sem-002",
            headers={**HEADERS, "Idempotency-Key": "sem-002-token-delete"},
            json={"reason": "cleanup"},
        )
        assert delete.status_code == 202, delete.text

        read_deleted = client.get("/bff/confirm-tokens/ct-sem-002", headers=HEADERS)
        assert read_deleted.status_code == 200, read_deleted.text
        assert read_deleted.json()["data"]["status"] == "deleted"
        assert [record["type"] for record in command_store._get_all_commands()] == [
            "CreateConfirmToken",
            "RedeemConfirmToken",
            "DeleteConfirmToken",
        ]


def test_deployment_create_server_generated_id_replays_on_retry() -> None:
    """Regression: POST /bff/deployments with no client id must replay on same Idempotency-Key."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "edge-no-id"}
        body = {"stage": "paper"}  # no deployment_id — server will generate it

        first = client.post("/bff/deployments", headers=headers, json=body)
        second = client.post("/bff/deployments", headers=headers, json=body)

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text  # must NOT be 409
        assert _receipt_id(second.json()) == _receipt_id(first.json())
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        # Only one command record created
        assert len(command_store._get_all_commands()) == 1


def test_deployment_create_server_generated_id_conflicts_on_different_payload() -> None:
    """Different payload with same Idempotency-Key must still 409 even with server-generated id."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "edge-no-id-conflict"}
        first = client.post("/bff/deployments", headers=headers, json={"stage": "paper"})
        conflict = client.post("/bff/deployments", headers=headers, json={"stage": "live"})

        assert first.status_code == 201, first.text
        assert conflict.status_code == 409, conflict.text


def test_sentinel_remediation_build_server_generated_id_replays_on_retry() -> None:
    """Regression: POST /bff/v5/sentinel/remediation/build with no finding_id must replay."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sentinel-build-no-id"}
        body = {"reason": "x"}  # no finding_id — server will generate it

        first = client.post("/bff/v5/sentinel/remediation/build", headers=headers, json=body)
        second = client.post("/bff/v5/sentinel/remediation/build", headers=headers, json=body)

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text  # must NOT be 409
        assert _receipt_id(second.json()) == _receipt_id(first.json())
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        assert len(command_store._get_all_commands()) == 1


def test_sentinel_remediation_build_with_client_finding_id_still_works() -> None:
    """When finding_id is provided the normal idempotency hash includes target_id."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sentinel-build-with-id"}
        body = {"finding_id": "finding-sem-002"}

        first = client.post("/bff/v5/sentinel/remediation/build", headers=headers, json=body)
        second = client.post("/bff/v5/sentinel/remediation/build", headers=headers, json=body)
        conflict = client.post(
            "/bff/v5/sentinel/remediation/build",
            headers=headers,
            json={"finding_id": "finding-different"},
        )

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert _receipt_id(second.json()) == _receipt_id(first.json())
        assert conflict.status_code == 409, conflict.text


def test_durable_idempotency_conflict_detected_after_memory_clear() -> None:
    """Regression: after _FINAL_CONTRACT_IDEMPOTENCY is cleared, a retry with a different
    payload must still return 409 — the command_store existing_record path must compare
    stored request_hash and not blindly replay."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-durable-conflict"}
        first = client.post("/bff/deployments", headers=headers, json={"stage": "paper"})
        assert first.status_code == 201, first.text

        # Simulate process restart / memory eviction
        _FINAL_CONTRACT_IDEMPOTENCY.clear()

        # Same key, different payload — must conflict even after memory clear
        conflict = client.post("/bff/deployments", headers=headers, json={"stage": "live"})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        # Same key, same payload — must replay even after memory clear
        replay = client.post("/bff/deployments", headers=headers, json={"stage": "paper"})
        assert replay.status_code == 201, replay.text
        assert replay.json()["meta"]["idempotency"]["replayed"] is True
        assert _receipt_id(replay.json()) == _receipt_id(first.json())


def test_confirm_token_server_generated_id_replays_on_same_key_retry() -> None:
    """Regression: POST /bff/confirm-tokens without client tokenId must replay on same
    Idempotency-Key retry (not 409) and return the original tokenId from the durable record.
    Also: after memory clear, a retry with a different payload must still 409."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-ct-no-id"}
        body = {"reason": "guarded action"}  # no tokenId — server will generate

        first = client.post("/bff/confirm-tokens", headers=headers, json=body)
        assert first.status_code == 201, first.text
        original_token_id = first.json()["data"]["tokenId"]
        assert original_token_id.startswith("ct-")

        # Immediate retry with same key — must replay with same tokenId (not 409)
        second = client.post("/bff/confirm-tokens", headers=headers, json=body)
        assert second.status_code == 201, second.text
        assert second.json()["data"]["tokenId"] == original_token_id
        assert second.json()["meta"]["idempotency"]["replayed"] is True

        # After memory clear, same key + same payload → still replay with original tokenId
        _FINAL_CONTRACT_IDEMPOTENCY.clear()
        third = client.post("/bff/confirm-tokens", headers=headers, json=body)
        assert third.status_code == 201, third.text
        assert third.json()["data"]["tokenId"] == original_token_id

        # After memory clear, same key + different payload → 409
        _FINAL_CONTRACT_IDEMPOTENCY.clear()
        conflict = client.post("/bff/confirm-tokens", headers=headers, json={"reason": "different"})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        # Only one command record created
        assert len(command_store._get_all_commands()) == 1


def test_audit_and_v5_command_routes_write_domain_command_records() -> None:
    with _isolated_command_bridge() as client:
        routes = [
            (
                "POST",
                "/bff/audit/export",
                "sem-002-audit-export",
                {"target_type": "Deployment"},
                "AuditExport",
            ),
            (
                "POST",
                "/bff/v5/interventions/intv-sem-002/decide",
                "sem-002-v5-decide",
                {"decision": "dismiss"},
                "DecideV5Intervention",
            ),
            (
                "POST",
                "/bff/v5/sentinel/findings/finding-sem-002/status",
                "sem-002-sentinel-status",
                {"status": "acknowledged"},
                "SentinelFindingStatus",
            ),
            (
                "POST",
                "/bff/v5/sentinel/remediation/build",
                "sem-002-sentinel-build",
                {"finding_id": "finding-sem-002"},
                "SentinelRemediationBuild",
            ),
            (
                "POST",
                "/bff/v5/sentinel/remediation/rem-sem-002/execute",
                "sem-002-sentinel-execute",
                {"reason": "dry run remediation command"},
                "SentinelRemediationExecute",
            ),
        ]

        for method, path, key, body, command_type in routes:
            response = client.request(
                method,
                path,
                headers={**HEADERS, "Idempotency-Key": key},
                json=body,
            )
            assert response.status_code == 202, response.text
            assert response.json()["data"]["command"] == command_type
            assert response.json()["meta"]["liveCapitalSideEffects"] is False

        assert [record["type"] for record in command_store._get_all_commands()] == [
            "AuditExport",
            "DecideV5Intervention",
            "SentinelFindingStatus",
            "SentinelRemediationBuild",
            "SentinelRemediationExecute",
        ]
