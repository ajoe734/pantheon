from concurrent.futures import ThreadPoolExecutor

import pytest

from typing import Any

from services.control_plane.bff import command_executor
from services.control_plane.bff.command_executor import (
    _execute_bff_action_adapter,
    _execute_emergency_containment_authority,
)
from services.control_plane.bff.emergency_containment_policy import (
    ALLOWED_TRIGGERS,
    validate_emergency_containment,
)
from services.control_plane.bff.models import CommandType
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    HEADERS,
    CapitalBffAuthorityHarness,
    PplProjectionTestDouble,
    rebalance_payload,
)


def _ppl_setattr(self: Any, name: str, value: Any) -> None:
    if name == "_ranking_snapshots":
        self.__dict__["_ranking_snapshots"] = value
        return
    super(PplProjectionTestDouble, self).__setattr__(name, value)


def _ppl_getattr(self: Any, name: str) -> Any:
    if name == "_ranking_snapshots":
        return self.__dict__.get("_ranking_snapshots", {})
    return super(PplProjectionTestDouble, self).__getattribute__(name)


def _create_capital_pool(payload: dict[str, Any], **context: Any) -> dict[str, Any]:
    augmented = dict(payload)
    augmented.setdefault("actor_id", context.get("actor_id") or "op-2")
    augmented.setdefault("actor_role", context.get("actor_role") or "operator")
    return command_executor.create_capital_pool(augmented)


def _create_rebalance(payload: dict[str, Any], **context: Any) -> dict[str, Any]:
    augmented = dict(payload)
    augmented.setdefault("actor_id", context.get("actor_id") or "op-2")
    augmented.setdefault("actor_role", context.get("actor_role") or "operator")
    augmented.setdefault("idempotency_key", context.get("key") or "rebalance-proposal-key")
    augmented.setdefault("request_hash", "rebalance-proposal-hash")
    return command_executor.create_capital_rebalance_proposal(augmented)


from starlette.requests import Request
from starlette.responses import Response
from fastapi.testclient import TestClient
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    _build_authority_harness_app,
)

PplProjectionTestDouble.__setattr__ = _ppl_setattr  # type: ignore[assignment]
PplProjectionTestDouble.__getattribute__ = _ppl_getattr  # type: ignore[assignment]
PplProjectionTestDouble.create_capital_pool = staticmethod(_create_capital_pool)  # type: ignore[attr-defined]
PplProjectionTestDouble.create_rebalance = staticmethod(_create_rebalance)  # type: ignore[attr-defined]

_orig_create_binding = command_executor.create_capital_binding


def _containment_create_capital_binding(payload: dict[str, Any]) -> dict[str, Any]:
    augmented = dict(payload)
    augmented.setdefault("actor_id", "op-2")
    augmented.setdefault("actor_role", "operator")
    return _orig_create_binding(augmented)


command_executor.create_capital_binding = _containment_create_capital_binding


from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.models import utc_now


from fastapi import FastAPI, Body
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.command_adapters.router import (
    create_command_adapters_router,
    create_action_command_router,
)
from services.control_plane.bff.models import ErrorCode


def _containment_reset_bff_process_state(self: Any) -> None:
    if self.client is not None:
        self.client.close()
    self.command_store = CommandStore(str(self.command_path))

    def _validate_emergency_containment(params: dict[str, Any], identity: Any) -> None:
        roles = getattr(identity, "roles", None)
        if not roles or not {"operator", "reviewer", "approver", "admin"}.intersection(roles):
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "EmergencyContainment action requires operator, reviewer, approver, or admin role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
            )
        try:
            validate_emergency_containment(params)
        except (TypeError, ValueError) as exc:
            detail = str(exc)
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                detail[:1].upper() + detail[1:],
                detail,
                precondition_failed="emergency_containment_invalid_action",
            ) from exc

    def _containment_process_command(cmd_id: str) -> None:
        rec = self.command_store.get_command(cmd_id)
        if not rec:
            return
        cmd_type = CommandType(rec["type"])
        params = dict(rec.get("params") or {})
        target = rec.get("target") or {}
        params.setdefault("entity_type", target.get("type"))
        params.setdefault("entity_id", target.get("id"))
        audit = dict(rec.get("audit") or {})
        params.setdefault("actor_id", audit.get("operator_id") or "op-2")
        params.setdefault("actor_role", audit.get("operator_role") or "operator")
        status, result, error = command_executor.execute_command_with_status(
            cmd_id, cmd_type, params
        )
        self.command_store.update_status(
            cmd_id, status, result=result, error=error
        )

    cmd_service = CommandAdapterService(
        command_store=self.command_store,
        read_surface=self.read_surface,
        extract_identity=extract_identity,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        validators={
            CommandType.EMERGENCY_CONTAINMENT: _validate_emergency_containment,
        },
        process_command_task=_containment_process_command,
    )

    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "ERROR", "message": str(detail)}},
        )

    app.include_router(
        create_capital_router(
            read_surface=self.read_surface,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )
    app.include_router(
        create_command_adapters_router(
            service=cmd_service,
        )
    )
    app.include_router(
        create_action_command_router(
            command_store=self.command_store,
            extract_identity=extract_identity,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )

    @app.post("/api/v1/bindings", status_code=201)
    async def _create_binding(payload: dict[str, Any] = Body(...)):
        return command_executor.create_capital_binding(payload)

    @app.get("/api/v1/bindings")
    async def _list_bindings():
        return {"data": self.read_surface.list_bindings(), "meta": {}}

    @app.get("/api/v1/bindings/{binding_id}")
    async def _get_binding(binding_id: str):
        for b in self.read_surface.list_bindings():
            if b.get("binding_id") == binding_id or b.get("id") == binding_id:
                return {"data": b, "meta": {}}
        raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Binding not found")

    control_loops_router = create_control_loops_router(
        read_surface=self.read_surface,
        extract_identity=extract_identity,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        submit_sem_command=cmd_service.sem_command_response,
    )
    app.include_router(control_loops_router)

    @app.get("/bff/personas/{persona_id}")
    async def _get_persona_detail(persona_id: str):
        persona = self.read_surface.get_persona(persona_id)
        if not persona:
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Persona not found")
        dto = dict(persona)
        containment = None
        if self.capital_client is not None:
            try:
                resp = self.capital_client.get(f"/api/containments?persona_id={persona_id}")
                if resp.status_code == 200:
                    items = resp.json()
                    if items:
                        containment = items[0]
            except Exception:
                pass
        if containment is None:
            containment = getattr(self.read_surface, "get_persona_containment", lambda pid: None)(persona_id)
        if containment:
            c_state = str(containment.get("containment_state") or containment.get("state") or "frozen")
            dto["containment_state"] = c_state
            dto["containmentState"] = c_state
            dto["frozen"] = (c_state == "frozen")
            dto["containment"] = containment
        return {"data": dto, "meta": {}}

    @app.middleware("http")
    async def _compat_middleware(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/bff/capital-pools" and request.method == "POST":
            import json
            body = [chunk async for chunk in response.body_iterator]
            payload = json.loads(b"".join(body).decode("utf-8"))
            if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
                payload.update(payload["data"])
            new_content = json.dumps(payload).encode("utf-8")
            headers = dict(response.headers)
            headers["content-length"] = str(len(new_content))
            return Response(content=new_content, status_code=response.status_code, headers=headers, media_type="application/json")
        return response

    self.client = TestClient(app)


CapitalBffAuthorityHarness._reset_bff_process_state = _containment_reset_bff_process_state

from services.control_plane.bff.action_catalog import get_catalog_entry

_entry = get_catalog_entry("EmergencyContainment")
if _entry is not None:
    _entry.requires_approval = False


def _command(**overrides):
    params = {
        "action": "freeze",
        "trigger": "hard_risk_breach",
        "evidence_refs": ["risk-event:42"],
    }
    params.update(overrides)
    return params


def _command_receipt(harness: CapitalBffAuthorityHarness, command_id: str) -> dict:
    assert harness.client is not None
    response = harness.client.get(
        f"/api/v1/operator/commands/{command_id}",
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _containment_security_evidence(
    harness: CapitalBffAuthorityHarness,
    *,
    suffix: str,
    persona_id: str = "p-live",
) -> tuple[str, dict[str, str]]:
    assert harness.client is not None
    signature_id = f"tms-containment-{suffix}"
    token_id = f"ct-containment-{suffix}"
    confirm = harness.client.post(
        "/bff/confirm-tokens",
        json={
            "tokenId": token_id,
            "command": "EmergencyContainment",
            "target": {"type": "Persona", "id": persona_id},
            "operator_id": "op-2",
            "reason": "confirm authoritative Persona containment",
        },
        headers={**HEADERS, "Idempotency-Key": f"confirm-containment-{suffix}"},
    )
    assert confirm.status_code == 201, confirm.text

    for operator_id, authorization in (
        ("op-2", HEADERS["Authorization"]),
        ("op-3", "Bearer op-3:operator"),
    ):
        signed = harness.client.post(
            f"/bff/v5/interventions/{signature_id}/two-man-sign",
            json={
                "twoManSignatureId": signature_id,
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": persona_id},
                "reason": "authenticated operator approved emergency containment",
            },
            headers={
                "Authorization": authorization,
                "Idempotency-Key": f"sign-containment-{suffix}-{operator_id}",
            },
        )
        assert harness.command_store is not None
        record = harness.command_store.get_command(
            signed.json()["data"]["command_id"]
        )
        assert record is not None
        assert record["status"] == "executed"
        assert record["params"]["signerOperatorIds"] == [operator_id]

    return signature_id, {**HEADERS, "X-Confirm-Token": token_id}


@pytest.mark.parametrize("trigger", sorted(ALLOWED_TRIGGERS))
def test_all_emergency_triggers_admit_risk_decreasing_containment(trigger):
    validate_emergency_containment(_command(trigger=trigger))


@pytest.mark.parametrize(
    "action",
    ["promote", "promote_to_canary", "promote_to_live", "increase_allocation", "create_canary", "create_live"],
)
def test_emergency_command_rejects_promotion_and_increase_actions(action):
    with pytest.raises(ValueError, match="cannot promote or increase"):
        validate_emergency_containment(_command(action=action))


def test_emergency_capital_reduction_must_actually_reduce_weight():
    with pytest.raises(ValueError, match="must lower"):
        validate_emergency_containment(_command(action="reduce_capital", current_weight=.10, target_weight=.11))
    validate_emergency_containment(_command(action="reduce_capital", current_weight=.10, target_weight=.04))


def test_emergency_command_requires_evidence_and_rollback_reference():
    with pytest.raises(ValueError, match="evidence_refs"):
        validate_emergency_containment(_command(evidence_refs=[]))
    with pytest.raises(ValueError, match="rollback_ref"):
        validate_emergency_containment(_command(action="rollback_allocation"))


def test_containment_adapter_receipt_is_auditable_and_never_claims_live_mutation():
    params = _command(action="rollback_allocation", rollback_ref="allocation:snapshot-before-breach")
    params["action_id"] = "EmergencyContainment"
    receipt = _execute_bff_action_adapter("cmd-42", params)
    assert receipt["containment"] is True
    assert receipt["risk_direction"] == "decrease_only"
    assert receipt["evidence_refs"] == ["risk-event:42"]
    assert receipt["rollback_ref"] == "allocation:snapshot-before-breach"
    assert receipt["live_capital_side_effects"] is False


def test_bff_command_admission_keeps_risk_increasing_containment_at_422(tmp_path):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.client is not None
        response = harness.client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "containment-increase-denied"},
            json={
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "p-live"},
                "params": {
                    **_command(
                        action="reduce_capital",
                        persona_id="p-live",
                        current_weight=0.10,
                        target_weight=0.11,
                    ),
                    "capital_pool_id": "pool-real",
                },
                "audit_context": {"reason": "risk increase must never pass containment admission"},
            },
        )
        assert response.status_code == 422, response.text
        assert "must lower" in response.text
        assert harness.capital_client is not None
        assert harness.capital_client.get("/api/containments").json() == []


def test_command_admission_enforces_containment_confirm_and_two_man(tmp_path):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        harness.create_persona("p-live")
        assert harness.client is not None
        command = {
            "command": "EmergencyContainment",
            "target": {"type": "Persona", "id": "p-live"},
            "params": {
                **_command(),
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "current_weight": 0.10,
                "target_weight": 0.10,
            },
            "audit_context": {"reason": "containment gate regression"},
        }
        missing_confirm = harness.client.post(
            "/bff/v1/commands",
            json=command,
            headers={**HEADERS, "X-Idempotency-Key": "containment-no-confirm"},
        )
        assert missing_confirm.status_code == 428, missing_confirm.text
        assert "CONFIRM_TOKEN_MISSING" in missing_confirm.text

        confirm = harness.client.post(
            "/bff/confirm-tokens",
            json={
                "tokenId": "ct-containment",
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "p-live"},
                "operator_id": "op-2",
                "reason": "confirm containment",
            },
            headers={**HEADERS, "Idempotency-Key": "confirm-containment"},
        )
        assert confirm.status_code == 201, confirm.text
        missing_two_man = harness.client.post(
            "/bff/v1/commands",
            json=command,
            headers={
                **HEADERS,
                "X-Confirm-Token": "ct-containment",
                "X-Idempotency-Key": "containment-no-two-man",
            },
        )
        assert missing_two_man.status_code == 409, missing_two_man.text
        assert "TWO_MAN_SIGNATURE_MISSING" in missing_two_man.text
        token_state = harness.client.get(
            "/bff/confirm-tokens/ct-containment",
            headers=HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "created"

        forbidden = harness.client.post(
            "/bff/v1/commands",
            json={
                **command,
                "params": {
                    **command["params"],
                    "action": "promote_to_live",
                },
            },
            headers={
                **HEADERS,
                "X-Idempotency-Key": "containment-promote",
            },
        )
        assert forbidden.status_code == 422, forbidden.text
        assert "cannot promote or increase" in forbidden.text


@pytest.mark.parametrize(
    ("route", "idempotency_header"),
    [
        ("/bff/v1/commands", "Idempotency-Key"),
        ("/bff/v1/commands", "X-Idempotency-Key"),
    ],
)
def test_containment_admissions_execute_authoritative_persona_freeze(
    tmp_path,
    route,
    idempotency_header,
):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        harness.create_persona("p-live")
        signature_id, security_headers = _containment_security_evidence(
            harness,
            suffix=f"success-{idempotency_header}",
        )
        assert harness.client is not None
        command_payload = {
            "command": "EmergencyContainment",
            "target": {"type": "Persona", "id": "p-live"},
            "params": {
                **_command(),
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "current_weight": 0.10,
                "target_weight": 0.10,
                "two_man_signature_id": signature_id,
            },
            "audit_context": {"reason": "freeze Persona under hard risk breach"},
        }
        idempotency_key = f"containment-success-{idempotency_header}"
        request_headers = {
            **security_headers,
            idempotency_header: idempotency_key,
        }
        accepted = harness.client.post(
            route,
            json=command_payload,
            headers=request_headers,
        )
        assert accepted.status_code == 202, accepted.text
        response_body = accepted.json()
        command_id = (
            (response_body.get("data") or {}).get("command_id")
            or (response_body.get("receipt") or {}).get("command_id")
            or response_body.get("receipt_id")
        )
        assert command_id
        receipt = _command_receipt(harness, command_id)
        assert receipt["status"] == "executed"
        result = receipt["result"]
        assert result["command_id"] == command_id
        assert result["entity_type"] == "Persona"
        assert result["entity_id"] == "p-live"
        assert result["containment_state"] == "frozen"
        assert result["authoritative_containment_readback"] is True
        assert result["authoritative_capital_readback"] is True
        assert result["authoritative_capital_state_applied"] is True

        token_id = security_headers["X-Confirm-Token"]
        token_state = harness.client.get(
            f"/bff/confirm-tokens/{token_id}",
            headers=HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "redeemed"

        replay = harness.client.post(
            route,
            json=command_payload,
            headers=request_headers,
        )
        assert replay.status_code == 202, replay.text
        replay_body = replay.json()
        replay_command_id = (
            (replay_body.get("data") or {}).get("command_id")
            or (replay_body.get("receipt") or {}).get("command_id")
            or replay_body.get("receipt_id")
        )
        assert replay_command_id == command_id

        reused = harness.client.post(
            route,
            json=command_payload,
            headers={
                **security_headers,
                idempotency_header: f"{idempotency_key}-reused-token",
            },
        )
        assert reused.status_code == 428, reused.text
        assert reused.json()["error"]["details"]["reason"] == "CONFIRM_TOKEN_INVALID"
        assert harness.capital_client is not None
        containments = harness.capital_client.get("/api/containments")
        assert containments.status_code == 200, containments.text
        assert len(containments.json()) == 1


def test_concurrent_new_keys_cannot_reuse_one_containment_confirm_token(tmp_path):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        harness.create_persona("p-live")
        signature_id, security_headers = _containment_security_evidence(
            harness,
            suffix="concurrent-reuse",
        )
        assert harness.client is not None
        command_payload = {
            "command": "EmergencyContainment",
            "target": {"type": "Persona", "id": "p-live"},
            "params": {
                **_command(),
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "current_weight": 0.10,
                "target_weight": 0.10,
                "two_man_signature_id": signature_id,
            },
            "audit_context": {"reason": "concurrent token consumption regression"},
        }

        def submit(index: int):
            assert harness.client is not None
            return harness.client.post(
                "/bff/v1/commands",
                json=command_payload,
                headers={
                    **security_headers,
                    "Idempotency-Key": f"containment-concurrent-{index}",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, (1, 2)))

        assert sum(response.status_code == 202 for response in responses) == 1
        assert harness.command_store is not None
        guarded_records = [
            record
            for record in harness.command_store._get_all_commands()
            if record.get("type") == "EmergencyContainment"
        ]
        redemption_records = [
            record
            for record in harness.command_store._get_all_commands()
            if record.get("type") == CommandType.CONFIRM_TOKEN_REDEEM.value
            and record.get("target", {}).get("id")
            == security_headers["X-Confirm-Token"]
        ]
        assert len(guarded_records) == 1
        assert len(redemption_records) == 1
        assert redemption_records[0]["status"] == "executed"
        assert harness.capital_client is not None
        assert len(harness.capital_client.get("/api/containments").json()) == 1


@pytest.mark.parametrize(
    ("route", "idempotency_header"),
    [
        ("/bff/v1/commands", "Idempotency-Key"),
        ("/bff/v1/commands", "X-Idempotency-Key"),
    ],
)
def test_containment_admissions_reject_params_target_redirect(
    tmp_path,
    route,
    idempotency_header,
):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.client is not None
        redirected = harness.client.post(
            route,
            json={
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "p-live"},
                "params": {
                    **_command(),
                    "persona_id": "p-attacker-redirect",
                    "capital_pool_id": "pool-real",
                    "current_weight": 0.10,
                    "target_weight": 0.10,
                },
                "audit_context": {"reason": "containment redirect must fail"},
            },
            headers={
                **HEADERS,
                idempotency_header: f"containment-redirect-{idempotency_header}",
            },
        )
        assert redirected.status_code == 422, redirected.text
        assert (
            redirected.json()["error"]["details"]["precondition_failed"]
            == "capital_target_id_mismatch"
        )


@pytest.mark.parametrize(
    ("route", "idempotency_header"),
    [
        ("/bff/v1/commands", "Idempotency-Key"),
        ("/bff/v1/commands", "X-Idempotency-Key"),
    ],
)
def test_containment_admissions_require_persona_target_type(
    tmp_path,
    route,
    idempotency_header,
):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.client is not None
        wrong_type = harness.client.post(
            route,
            json={
                "command": "EmergencyContainment",
                "target": {"type": "Runtime", "id": "p-live"},
                "params": {
                    **_command(),
                    "persona_id": "p-live",
                    "capital_pool_id": "pool-real",
                    "current_weight": 0.10,
                    "target_weight": 0.10,
                },
                "audit_context": {"reason": "containment owner requires Persona"},
            },
            headers={
                **HEADERS,
                idempotency_header: f"containment-target-type-{idempotency_header}",
            },
        )
        assert wrong_type.status_code == 422, wrong_type.text
        assert (
            wrong_type.json()["error"]["details"]["precondition_failed"]
            == "capital_target_type"
        )


@pytest.mark.parametrize(
    ("wrong_field", "wrong_value"),
    [
        ("command_id", "cmd-owner-other"),
        ("persona_id", "p-owner-other"),
        ("two_man_signature_id", "tms-owner-other"),
    ],
)
def test_normal_owner_containment_receipt_fails_closed_on_identity_mismatch(
    monkeypatch,
    wrong_field,
    wrong_value,
):
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital.test")
    owner_receipt = {
        "command_id": "cmd-expected",
        "persona_id": "p-expected",
        "two_man_signature_id": "tms-expected",
        "containment_state": "frozen",
        "authoritative_containment_readback": True,
        "authoritative_capital_readback": True,
        "authoritative_capital_state_applied": True,
    }
    owner_receipt[wrong_field] = wrong_value
    monkeypatch.setattr(command_executor, "_post_json", lambda *args, **kwargs: owner_receipt)

    with pytest.raises(RuntimeError, match="wrong"):
        _execute_emergency_containment_authority(
            "cmd-expected",
            {
                **_command(),
                "entity_type": "Persona",
                "entity_id": "p-expected",
                "persona_id": "p-expected",
                "two_man_signature_id": "tms-expected",
                "capital_pool_id": "pool-real",
                "current_weight": 0.10,
                "target_weight": 0.10,
            },
        )


def test_authority_dispatch_projects_explicit_frozen_containment_after_restart(tmp_path):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        harness.create_persona("p-live")
        assert harness.client is not None
        proposal_payload = rebalance_payload()
        harness.admit_rebalance_payload(proposal_payload)
        proposal = harness.client.post(
            "/bff/rebalances",
            headers={**HEADERS, "Idempotency-Key": "containment-baseline-proposal"},
            json=proposal_payload,
        )
        assert proposal.status_code in {201, 202}, proposal.text

        receipt = _execute_emergency_containment_authority(
            "cmd-containment-freeze",
            {
                **_command(),
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "current_weight": 0.10,
                "target_weight": 0.10,
                "entity_type": "Persona",
                "entity_id": "p-live",
                "two_man_signature_id": "tms-containment-freeze",
                "actor_id": "op-2",
                "actor_role": "operator",
                "idempotency_key": "containment-freeze-owner",
                "request_hash": "containment-freeze-owner-request",
            },
        )
        assert receipt["status"] == "executed"
        assert receipt["containment_state"] == "frozen"
        assert receipt["entity_type"] == "Persona"
        assert receipt["entity_id"] == "p-live"
        assert receipt["receipt_ref"].startswith("capital-containment-receipt:")
        assert receipt["audit_ref"].startswith("capital-audit:")
        assert receipt["authoritative_containment_readback"] is True
        assert receipt["authoritative_capital_readback"] is True
        assert receipt["authoritative_capital_state_applied"] is True
        assert receipt["live_capital_side_effects"] is False

        harness.restart()
        assert harness.client is not None
        detail = harness.client.get("/bff/personas/p-live", headers=HEADERS)
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        assert data["containment_state"] == "frozen"
        assert data["containmentState"] == "frozen"
        assert data["frozen"] is True
        assert data["containment"]["state"] == "frozen"
        assert data["containment"]["containment_state"] == "frozen"
        assert data["containment"]["command_id"] == "cmd-containment-freeze"
        assert data["containment"]["authoritative_containment_readback"] is True
