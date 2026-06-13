from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from command_queue import CommandStore
from models import CommandStatus
from read_store import ReadSurfaceStore


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
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.read_store._data = {"approval_decisions": {}}
        bff_main.read_store.get_approval_decision = (  # type: ignore[method-assign]
            lambda decision_id: bff_main.read_store._data["approval_decisions"].get(decision_id)
        )
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
    response = client.post(
        f"/bff/v5/interventions/{target_id}/two-man-sign",
        headers={**PRIMARY_HEADERS, "Idempotency-Key": f"sign-{signature_id}"},
        json={
            "twoManSignatureId": signature_id,
            "command": "RemediateSentinelIntervention",
            "target": {"type": "SentinelIntervention", "id": target_id},
            "signerOperatorIds": signers or ["op-primary", "op-secondary"],
            "reason": "second operator signed the guarded command",
        },
    )
    assert response.status_code == 202, response.text
    command_id = response.json()["data"]["command_id"]
    bff_main.command_store.update_status(command_id, CommandStatus.EXECUTED)
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


def test_concurrent_two_man_signatures_are_operator_scoped_and_remain_usable() -> None:
    with _isolated_security_client() as client:
        def sign(args: tuple[dict[str, str], str]) -> dict:
            headers, signature_id = args
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
                (
                    (PRIMARY_HEADERS, "tms-race-primary"),
                    (SECONDARY_HEADERS, "tms-race-secondary"),
                ),
            ))

        command_ids = {
            primary["data"]["command_id"],
            secondary["data"]["command_id"],
        }
        assert len(command_ids) == 2
        assert primary["meta"]["idempotency"]["replayed"] is False
        assert secondary["meta"]["idempotency"]["replayed"] is False
        for command_id in command_ids:
            bff_main.command_store.update_status(command_id, CommandStatus.EXECUTED)

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
        } == {"tms-race-primary", "tms-race-secondary"}

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
                signature_id="tms-race-primary",
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
