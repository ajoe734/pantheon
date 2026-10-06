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
from services.control_plane.bff.command_adapters.retired import reject_retired_command
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
    reject_retired_command(command_type.value)
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
        gov_bff_idempotency=_GOV_BFF_IDEMPOTENCY,
        process_command_task=lambda cmd_id: None,
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


def test_deployment_create_routes_are_retired_before_command_admission() -> None:
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-deploy-create"}
        response = client.post(
            "/bff/deployments", headers=headers,
            json={"deployment_id": "dep-sem-002", "stage": "paper"},
        )

        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "ACTION_RETIRED"
        assert not command_store._get_all_commands()


def test_create_deployment_command_is_retired_without_receipt() -> None:
    with _isolated_command_bridge() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "sem-002-create-command"},
            json={
                "command": "CreateDeployment",
                "target": {"type": "Deployment", "id": "dep-sem-002"},
                "params": {"deployment_id": "dep-sem-002", "stage": "paper"},
            },
        )

        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "ACTION_RETIRED"
        assert not command_store._get_all_commands()


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


def test_command_routes_require_header_idempotency_and_reject_body_key() -> None:
    with _isolated_command_bridge() as client:
        body = {"tokenId": "ct-idempotency", "reason": "guarded action"}

        missing = client.post("/bff/confirm-tokens", headers=HEADERS, json=body)
        assert missing.status_code == 400, missing.text
        assert missing.json()["error"]["code"] == "VALIDATION_FAILED"

        body_key = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-body-key"},
            json={**body, "idempotencyKey": "body-key"},
        )
        assert body_key.status_code == 400, body_key.text
        assert body_key.json()["error"]["code"] == "VALIDATION_FAILED"

        alias = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "X-Idempotency-Key": "ct-alias-key"},
            json=body,
        )
        assert alias.status_code == 201, alias.text
        assert alias.json()["meta"]["idempotency"]["idempotencyKey"] == "ct-alias-key"
        assert len(command_store._get_all_commands()) == 1


def test_confirm_token_create_persists_command_record_shape_and_status() -> None:
    with _isolated_command_bridge() as client:
        response = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "ct-record-shape"},
            json={"tokenId": "ct-record-shape", "reason": "guarded action"},
        )
        assert response.status_code == 201, response.text
        record = command_store.get_command_by_idempotency_key(
            "ct-record-shape", operator_id="op-sem-002"
        )
        assert record is not None
        assert record["command_id"] == response.json()["command_id"]
        assert record["type"] == "CreateConfirmToken"
        assert record["target"] == {"type": "ConfirmToken", "id": "ct-record-shape"}
        assert record["foundation"]["idempotency_record"]["status"] == "succeeded"
        assert record["audit"]["live_capital_side_effects"] is False

        status = client.get(
            f"/api/v1/operator/commands/{record['command_id']}", headers=HEADERS
        )
        assert status.status_code == 200, status.text
        assert status.json()["type"] == "CreateConfirmToken"


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


def test_durable_idempotency_conflict_detected_after_memory_clear() -> None:
    """Regression: after _FINAL_CONTRACT_IDEMPOTENCY is cleared, a retry with a different
    payload must still return 409 — the command_store existing_record path must compare
    stored request_hash and not blindly replay."""
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-durable-conflict"}
        payload = {
            "command": "StrategyAction",
            "target": {"type": "Strategy", "id": "stg-durable-conflict"},
            "action": "submit",
            "params": {"action_id": "submit", "entity_type": "strategy", "entity_id": "stg-durable-conflict"},
            "audit_context": {"reason": "durable idempotency regression"},
        }
        first = client.post("/bff/v1/commands", headers=headers, json=payload)
        assert first.status_code == 202, first.text

        # Simulate process restart / memory eviction
        _FINAL_CONTRACT_IDEMPOTENCY.clear()

        # Same key, different payload — must conflict even after memory clear
        conflict = client.post("/bff/v1/commands", headers=headers, json={**payload, "params": {**payload["params"], "reason": "changed"}})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        # Same key, same payload — must replay even after memory clear
        replay = client.post("/bff/v1/commands", headers=headers, json=payload)
        assert replay.status_code == 202, replay.text
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
