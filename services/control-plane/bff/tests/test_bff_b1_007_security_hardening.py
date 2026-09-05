from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
import pytest
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus
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


async def _noop_process_command(_command_id: str) -> None:
    return None


@contextmanager
def _isolated_security_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        store = create_in_memory_read_surface_ports()
        store._data = {"approval_decisions": {}}
        store.get_approval_decision = (  # type: ignore[method-assign]
            lambda decision_id: store._data["approval_decisions"].get(decision_id)
        )
        bff_main.read_store = store
        bff_main._process_command_stub = _noop_process_command
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_main._COMMAND_AUTH_CONTEXT.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._COMMAND_AUTH_CONTEXT.clear()


def _seed_approval_decision(
    decision_id: str,
    *,
    command: str = "RemediateSentinelIntervention",
    target_id: str = "int-sec-001",
    state: str = "approved",
) -> None:
    if not hasattr(bff_main.read_store, "_data"):
        bff_main.read_store._data = {}
    bff_main.read_store._data.setdefault("approval_decisions", {})[decision_id] = {
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
        stored = bff_main.command_store.get_command(command_id)
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
        records = [
            record
            for record in bff_main.command_store._get_all_commands()
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
        guarded_records = [
            record
            for record in bff_main.command_store._get_all_commands()
            if record["type"] == "RemediateSentinelIntervention"
        ]
        redemption_records = [
            record
            for record in bff_main.command_store._get_all_commands()
            if record["type"] == bff_main.CommandType.CONFIRM_TOKEN_REDEEM.value
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
        identity = bff_main._extract_identity(PRIMARY_HEADERS["Authorization"])
        cmd = bff_main.OperatorCommand(
            command=bff_main.CommandType.REMEDIATE_SENTINEL_INTERVENTION,
            target=bff_main.TargetObject(
                type=bff_main.ObjectType.SENTINEL_INTERVENTION,
                id=target_id,
            ),
            action="remediate_sentinel_intervention",
            params=merged_params,
            audit_context=bff_main.AuditContext(reason=payload["reason"]),
        )
        foundation = bff_main._build_foundation_command_context(
            cmd=cmd,
            identity=identity,
            raw_payload={**payload, "intervention_id": target_id},
            trace_id=PRIMARY_HEADERS["X-Trace-Id"],
            correlation_id=PRIMARY_HEADERS["X-Correlation-Id"],
            request_id=PRIMARY_HEADERS["X-Request-Id"],
            idempotency_key=idempotency_key,
        )
        command_id = "cmd-specialized-preupgrade"
        foundation["idempotency_record"] = foundation["idempotency_record"].with_status(
            "succeeded",
            result_ref=f"command:{command_id}",
        )
        submitted_at = bff_main.utc_now()
        stored_params = bff_main._stored_command_params(cmd, identity)
        serialized_foundation = bff_main._serialize_foundation_context(foundation)
        bff_main.command_store.submit_command(
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
        bff_main.command_store.update_status(command_id, CommandStatus.EXECUTED)

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
            for record in bff_main.command_store._get_all_commands()
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
        record = bff_main.command_store.get_command(
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
        *bff_main._TWO_MAN_SIGNER_LIST_FIELDS,
        *bff_main._TWO_MAN_SIGNER_FIELDS,
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
            if signer_alias in bff_main._TWO_MAN_SIGNER_LIST_FIELDS
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
        record = bff_main.command_store.get_command(
            signed.json()["data"]["command_id"]
        )
        assert record is not None
        assert bff_main._two_man_signers(record) == {"op-primary"}
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
        bff_main.command_store.update_status(
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
        bff_main.command_store.update_status(
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
            local_client = TestClient(bff_main.app)
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
        sign_records = [
            record
            for record in bff_main.command_store._get_all_commands()
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
        bff_main.command_store.update_status(first_id, CommandStatus.EXECUTED)

        second = client.post(
            "/bff/v1/commands",
            headers={**SECONDARY_HEADERS, "Idempotency-Key": "idem-shared-key"},
            json=payload,
        )

        assert second.status_code == 202, second.text
        assert second.json()["data"]["command_id"] != first_id
        assert second.json()["meta"]["idempotency"]["replayed"] is False
