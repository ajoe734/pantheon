from concurrent.futures import ThreadPoolExecutor

import pytest

import command_executor
from services.control_plane.bff import main as bff_main
from command_executor import (
    _execute_bff_action_adapter,
    _execute_emergency_containment_authority,
)
from emergency_containment_policy import ALLOWED_TRIGGERS, validate_emergency_containment
from rebalance_authority_test_support import (
    HEADERS,
    CapitalBffAuthorityHarness,
    rebalance_payload,
)


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
        assert signed.status_code == 202, signed.text
        record = bff_main.command_store.get_command(
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


def test_legacy_command_admission_enforces_containment_confirm_and_two_man(tmp_path):
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
            "audit_context": {"reason": "legacy containment gate regression"},
        }
        missing_confirm = harness.client.post(
            "/api/v1/operator/commands",
            json=command,
            headers={**HEADERS, "X-Idempotency-Key": "legacy-containment-no-confirm"},
        )
        assert missing_confirm.status_code == 428, missing_confirm.text
        assert "CONFIRM_TOKEN_MISSING" in missing_confirm.text

        confirm = harness.client.post(
            "/bff/confirm-tokens",
            json={
                "tokenId": "ct-legacy-containment",
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "p-live"},
                "operator_id": "op-2",
                "reason": "confirm legacy containment",
            },
            headers={**HEADERS, "Idempotency-Key": "confirm-legacy-containment"},
        )
        assert confirm.status_code == 201, confirm.text
        missing_two_man = harness.client.post(
            "/api/v1/operator/commands",
            json=command,
            headers={
                **HEADERS,
                "X-Confirm-Token": "ct-legacy-containment",
                "X-Idempotency-Key": "legacy-containment-no-two-man",
            },
        )
        assert missing_two_man.status_code == 409, missing_two_man.text
        assert "TWO_MAN_SIGNATURE_MISSING" in missing_two_man.text
        token_state = harness.client.get(
            "/bff/confirm-tokens/ct-legacy-containment",
            headers=HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "created"

        forbidden = harness.client.post(
            "/api/v1/operator/commands",
            json={
                **command,
                "params": {
                    **command["params"],
                    "action": "promote_to_live",
                },
            },
            headers={
                **HEADERS,
                "X-Idempotency-Key": "legacy-containment-promote",
            },
        )
        assert forbidden.status_code == 422, forbidden.text
        assert "cannot promote or increase" in forbidden.text


@pytest.mark.parametrize(
    ("route", "idempotency_header"),
    [
        ("/bff/v1/commands", "Idempotency-Key"),
        ("/api/v1/operator/commands", "X-Idempotency-Key"),
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
        assert all(response.status_code in {202, 409, 428} for response in responses)
        guarded_records = [
            record
            for record in bff_main.command_store._get_all_commands()
            if record.get("type") == "EmergencyContainment"
        ]
        redemption_records = [
            record
            for record in bff_main.command_store._get_all_commands()
            if record.get("type") == bff_main.CommandType.CONFIRM_TOKEN_REDEEM.value
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
        ("/api/v1/operator/commands", "X-Idempotency-Key"),
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
        ("/api/v1/operator/commands", "X-Idempotency-Key"),
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
        assert proposal.status_code == 202, proposal.text

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
