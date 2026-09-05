from __future__ import annotations

import http.client
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

import command_executor
from services.control_plane.bff import main as bff_main
from rebalance_authority_test_support import (
    APPROVER_HEADERS,
    HEADERS,
    CapitalBffAuthorityHarness,
    rebalance_payload,
)


def _create_proposal(
    harness: CapitalBffAuthorityHarness,
    *,
    key: str,
    payload: dict | None = None,
):
    assert harness.client is not None
    request_payload = payload or rebalance_payload()
    harness.admit_rebalance_payload(request_payload)
    response = harness.client.post(
        "/bff/rebalances",
        json=request_payload,
        headers={**HEADERS, "Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response


def _command_receipt(harness: CapitalBffAuthorityHarness, command_id: str) -> dict:
    assert harness.client is not None
    response = harness.client.get(
        f"/api/v1/operator/commands/{command_id}",
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_complete_proposal_is_durable_and_does_not_apply_capital(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-complete")
        created_body = created.json()
        rebalance_id = created_body["rebalance_id"]
        proposal_command_id = created_body["data"]["command_id"]

        assert harness.client is not None
        response = harness.client.get(f"/bff/rebalances/{rebalance_id}", headers=HEADERS)
        assert response.status_code == 200, response.text
        detail = response.json()["data"]
        expected = rebalance_payload()
        assert detail["capital_pool_id"] == expected["capital_pool_id"]
        assert detail["ranking_snapshot_id"] == expected["ranking_snapshot_id"]
        assert detail["reason"] == expected["reason"]
        line = detail["lines"][0]
        assert {
            key: value
            for key, value in line.items()
            if key != "delta"
        } == {
            **{
                key: value
                for key, value in expected["lines"][0].items()
                if key != "delta"
            },
            "allocation_id": "pool-real|sleeve:sleeve-live",
            "binding_id": "binding-live",
            "binding_state": "pending",
        }
        assert line["delta"] == pytest.approx(0.02)
        assert detail["simulation"] == expected["simulation"]
        assert detail["constraints"] == expected["constraints"]
        assert detail["rollback_target"] == expected["rollback_target"]
        assert detail["audit_refs"] == expected["audit_refs"]
        assert detail["status"] == "pending"
        assert detail["applied"] is False
        assert detail["apply_receipt"] is None
        assert detail["canonical_write_authority"] == "capital_service"
        assert detail["persistence_mode"] == "owner_store"

        proposal_receipt = _command_receipt(harness, proposal_command_id)
        assert proposal_receipt["status"] == "executed"
        assert proposal_receipt["result"]["proposal_persisted"] is True
        assert proposal_receipt["result"]["authoritative_capital_readback"] is True
        assert proposal_receipt["result"]["live_capital_side_effects"] is False

        pool = harness.client.get("/bff/capital-pools/pool-real", headers=HEADERS)
        assert pool.status_code == 200, pool.text
        allocation = pool.json()["data"]["allocations"][0]
        assert allocation["current_weight"] == 0.10
        assert allocation["target_weight"] == 0.10


@pytest.mark.parametrize("stage", ["live", "live_candidate", "live_running"])
def test_live_increase_without_approval_is_409_and_leaves_owner_state_unchanged(
    tmp_path: Path,
    stage: str,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        payload = rebalance_payload()
        payload["lines"][0]["stage"] = stage
        created = _create_proposal(
            harness,
            key=f"rb-proposal-no-approval-{stage}",
            payload=payload,
        )
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None

        before = harness.client.get(f"/bff/rebalances/{rebalance_id}", headers=HEADERS).json()["data"]
        before_allocations = harness.client.get(
            "/bff/capital-pools/pool-real", headers=HEADERS
        ).json()["data"]["allocations"]
        denied = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json={},
            headers={
                **HEADERS,
                "Idempotency-Key": f"rb-apply-without-approval-{stage}",
            },
        )

        assert denied.status_code == 409, denied.text
        after = harness.client.get(f"/bff/rebalances/{rebalance_id}", headers=HEADERS).json()["data"]
        after_allocations = harness.client.get(
            "/bff/capital-pools/pool-real", headers=HEADERS
        ).json()["data"]["allocations"]
        assert after == before
        assert after["status"] == "pending"
        assert after["applied"] is False
        assert after_allocations == before_allocations
        assert after_allocations[0]["current_weight"] == 0.10


def test_approved_apply_is_terminal_authoritative_and_ignores_body_tampering(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-approved")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="approved",
        )
        apply_payload = {
            **evidence,
            "rebalance_id": "rb-attacker",
            "entity_type": "Persona",
            "entity_id": "p-attacker",
            "action_id": "promote_to_live",
            "actor_id": "attacker",
            "actor_role": "admin",
            "capital_pool_id": "pool-attacker",
            "lines": [
                {
                    "persona_id": "p-attacker",
                    "current_weight": 0,
                    "target_weight": 1,
                }
            ],
        }
        apply_request_headers = {
            **apply_headers,
            "Idempotency-Key": "rb-apply-approved",
        }
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_payload,
            headers=apply_request_headers,
        )
        assert accepted.status_code == 202, accepted.text
        command_id = accepted.json()["data"]["command_id"]

        token_id = apply_headers["X-Confirm-Token"]
        token_state = harness.client.get(
            f"/bff/confirm-tokens/{token_id}",
            headers=HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "redeemed"

        replay = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_payload,
            headers=apply_request_headers,
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["data"]["command_id"] == command_id
        assert replay.json()["meta"]["idempotency"]["replayed"] is True

        reused = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_payload,
            headers={
                **apply_headers,
                "Idempotency-Key": "rb-apply-approved-reused-token",
            },
        )
        assert reused.status_code == 428, reused.text
        assert reused.json()["error"]["details"]["reason"] == "CONFIRM_TOKEN_INVALID"

        receipt = _command_receipt(harness, command_id)
        assert receipt["status"] == "executed"
        result = receipt["result"]
        assert result["status"] == "applied"
        assert result["command_id"] == command_id
        assert result["entity_type"] == "Rebalance"
        assert result["entity_id"] == rebalance_id
        assert result["action_id"] == "apply"
        assert result["approval_ref"] == evidence["approval_decision_id"]
        assert result["receipt_ref"].startswith("capital-rebalance-receipt:")
        assert result["audit_ref"].startswith("capital-audit:")
        assert result["authoritative_capital_readback"] is True
        assert result["authoritative_capital_state_applied"] is True
        assert result["canonical_write_authority"] == "capital_service"
        assert result["live_capital_side_effects"] is False
        assert [item["persona_id"] for item in result["allocation_readback"]] == ["p-live"]
        assert result["allocation_readback"][0]["current_weight"] == 0.12

        stored = bff_main.command_store.get_command(command_id)
        assert stored is not None
        assert stored["target"] == {"type": "Rebalance", "id": rebalance_id}
        assert stored["params"]["entity_type"] == "Rebalance"
        assert stored["params"]["entity_id"] == rebalance_id
        assert stored["params"]["action_id"] == "ApprovedApply"
        assert stored["params"]["actor_id"] == "op-2"
        assert "lines" not in stored["params"]

        proposal = harness.client.get(
            f"/bff/rebalances/{rebalance_id}", headers=HEADERS
        ).json()["data"]
        assert proposal["status"] == "applied"
        assert proposal["applied"] is True
        assert proposal["apply_command_id"] == command_id
        assert proposal["apply_receipt"]["receipt_ref"] == result["receipt_ref"]
        assert proposal["lines"][0]["persona_id"] == "p-live"
        assert proposal["lines"][0]["target_weight"] == 0.12

        pool = harness.client.get("/bff/capital-pools/pool-real", headers=HEADERS)
        assert pool.status_code == 200, pool.text
        pool_data = pool.json()["data"]
        assert pool_data["authoritative_capital_readback"] is True
        assert len(pool_data["allocations"]) == 1
        allocation = pool_data["allocations"][0]
        assert allocation["persona_id"] == "p-live"
        assert allocation["current_weight"] == 0.12
        assert allocation["target_weight"] == 0.12
        assert allocation["last_rebalance_id"] == rebalance_id


def test_bff_apply_bootstraps_zero_weight_owner_allocation(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        payload = rebalance_payload()
        payload["lines"][0].update(current_weight=0.0, target_weight=0.12, delta=0.12)
        created = _create_proposal(
            harness,
            key="rb-proposal-zero-bootstrap",
            payload=payload,
        )
        rebalance_id = created.json()["rebalance_id"]
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="zero-bootstrap",
        )
        assert harness.client is not None
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=evidence,
            headers={**apply_headers, "Idempotency-Key": "rb-apply-zero-bootstrap"},
        )
        assert accepted.status_code == 202, accepted.text
        receipt = _command_receipt(harness, accepted.json()["data"]["command_id"])
        assert receipt["status"] == "executed"
        allocation = receipt["result"]["allocation_readback"][0]
        assert allocation["current_weight"] == 0.12
        assert allocation["authoritative_capital_readback"] is True


def test_pre_auto_redeem_guarded_record_keeps_token_consumed_after_upgrade_restart(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-upgrade-consumption")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        apply_body, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="upgrade-consumption",
        )
        request_headers = {
            **apply_headers,
            "Idempotency-Key": "rb-apply-upgrade-consumption",
        }
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers=request_headers,
        )
        assert accepted.status_code == 202, accepted.text
        command_id = accepted.json()["data"]["command_id"]
        token_id = apply_headers["X-Confirm-Token"]

        # Simulate a command admitted before automatic redemption was deployed:
        # its guarded command/audit evidence is durable, but no explicit redeem
        # record exists yet.
        records = [
            record
            for record in bff_main.command_store._get_all_commands()
            if not (
                record.get("type")
                == bff_main.CommandType.CONFIRM_TOKEN_REDEEM.value
                and record.get("target", {}).get("id") == token_id
            )
        ]
        bff_main.command_store._update_commands(records)
        assert (
            harness.client.get(
                f"/bff/confirm-tokens/{token_id}",
                headers=HEADERS,
            ).json()["data"]["status"]
            == "redeemed"
        )

        harness.restart()
        assert harness.client is not None
        token_state = harness.client.get(
            f"/bff/confirm-tokens/{token_id}",
            headers=HEADERS,
        )
        assert token_state.status_code == 200, token_state.text
        assert token_state.json()["data"]["status"] == "redeemed"

        replay = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers=request_headers,
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["data"]["command_id"] == command_id

        reused = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers={
                **apply_headers,
                "Idempotency-Key": "rb-apply-upgrade-reused-token",
            },
        )
        assert reused.status_code == 428, reused.text
        assert reused.json()["error"]["details"]["reason"] == "CONFIRM_TOKEN_INVALID"


@pytest.mark.parametrize(
    "failure_kind",
    ["url_disconnect", "json_decode", "truncated_response"],
)
def test_ambiguous_owner_apply_response_reconciles_committed_receipt(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        payload = rebalance_payload()
        payload["lines"][0].update(current_weight=0.0, target_weight=0.12, delta=0.12)
        created = _create_proposal(harness, key="rb-proposal-ambiguous", payload=payload)
        rebalance_id = created.json()["rebalance_id"]
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix=f"ambiguous-{failure_kind}",
        )

        owner_post = command_executor._post_json
        raised = False

        def commit_then_disconnect(url, body, auth_token=None, mfa_token=None):
            nonlocal raised
            result = owner_post(url, body, auth_token, mfa_token)
            if url.endswith("/apply") and not raised:
                raised = True
                if failure_kind == "json_decode":
                    raise json.JSONDecodeError("truncated JSON after commit", "{", 1)
                if failure_kind == "truncated_response":
                    raise http.client.IncompleteRead(b'{"status":', 12)
                raise URLError("connection lost after owner commit")
            return result

        command_executor._post_json = commit_then_disconnect
        try:
            assert harness.client is not None
            accepted = harness.client.post(
                f"/bff/rebalances/{rebalance_id}/apply",
                json=evidence,
                headers={
                    **apply_headers,
                    "Idempotency-Key": f"rb-apply-ambiguous-{failure_kind}",
                },
            )
        finally:
            command_executor._post_json = owner_post
        assert accepted.status_code == 202, accepted.text
        receipt = _command_receipt(harness, accepted.json()["data"]["command_id"])
        assert receipt["status"] == "executed"
        assert receipt["result"]["owner_receipt_reconciled"] is True
        assert receipt["result"]["authoritative_capital_state_applied"] is True


@pytest.mark.parametrize(
    ("wrong_field", "wrong_value"),
    [
        ("command_id", "cmd-owner-other"),
        ("rebalance_id", "rb-owner-other"),
        ("approval_ref", "approval-owner-other"),
    ],
)
def test_normal_owner_apply_receipt_fails_closed_on_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    wrong_field: str,
    wrong_value: str,
) -> None:
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital.test")
    owner_receipt = {
        "command_id": "cmd-expected",
        "rebalance_id": "rb-expected",
        "approval_ref": "approval-expected",
        "status": "applied",
        "authoritative_capital_readback": True,
        "authoritative_capital_state_applied": True,
    }
    owner_receipt[wrong_field] = wrong_value
    monkeypatch.setattr(command_executor, "_post_json", lambda *args, **kwargs: owner_receipt)

    with pytest.raises(RuntimeError, match="wrong"):
        command_executor._execute_approved_rebalance_apply(
            "cmd-expected",
            {
                "entity_type": "Rebalance",
                "entity_id": "rb-expected",
                "rebalance_id": "rb-expected",
                "approval_required": True,
                "approval_ref": "approval-expected",
            },
        )


def test_same_key_retries_only_retryable_terminal_capital_command(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        payload = rebalance_payload()
        payload["lines"][0].update(current_weight=0.0, target_weight=0.12, delta=0.12)
        created = _create_proposal(harness, key="rb-proposal-retry", payload=payload)
        rebalance_id = created.json()["rebalance_id"]
        evidence, apply_headers = harness.apply_evidence(rebalance_id, suffix="retry")

        owner_post = command_executor._post_json

        def disconnect_before_commit(url, body, auth_token=None, mfa_token=None):
            if url.endswith("/apply"):
                raise URLError("owner connection unavailable")
            return owner_post(url, body, auth_token, mfa_token)

        command_executor._post_json = disconnect_before_commit
        assert harness.client is not None
        first = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=evidence,
            headers={**apply_headers, "Idempotency-Key": "rb-apply-retry"},
        )
        assert first.status_code == 202, first.text
        command_id = first.json()["data"]["command_id"]
        failed = _command_receipt(harness, command_id)
        assert failed["status"] == "failed"
        assert failed["error"]["retryable"] is True

        command_executor._post_json = owner_post
        retried = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=evidence,
            headers={**apply_headers, "Idempotency-Key": "rb-apply-retry"},
        )
        assert retried.status_code == 202, retried.text
        assert retried.json()["data"]["command_id"] == command_id
        executed = _command_receipt(harness, command_id)
        assert executed["status"] == "executed"
        assert executed["error"] is None


def test_ambiguous_commit_and_receipt_outage_remain_same_key_retryable(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        payload = rebalance_payload()
        payload["lines"][0].update(current_weight=0.0, target_weight=0.12, delta=0.12)
        created = _create_proposal(harness, key="rb-proposal-ambiguous-retry", payload=payload)
        rebalance_id = created.json()["rebalance_id"]
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="ambiguous-retry",
        )
        owner_post = command_executor._post_json
        owner_get = command_executor._get_json
        post_failed = False

        def commit_then_parse_failure(url, body, auth_token=None, mfa_token=None):
            nonlocal post_failed
            result = owner_post(url, body, auth_token, mfa_token)
            if url.endswith("/apply") and not post_failed:
                post_failed = True
                raise json.JSONDecodeError("owner response truncated after commit", "{", 1)
            return result

        def receipt_temporarily_unavailable(url, auth_token=None, mfa_token=None):
            if "/receipts/" in url:
                raise URLError("receipt endpoint temporarily unavailable")
            return owner_get(url, auth_token, mfa_token)

        command_executor._post_json = commit_then_parse_failure
        command_executor._get_json = receipt_temporarily_unavailable
        assert harness.client is not None
        try:
            first = harness.client.post(
                f"/bff/rebalances/{rebalance_id}/apply",
                json=evidence,
                headers={
                    **apply_headers,
                    "Idempotency-Key": "rb-apply-ambiguous-retry",
                },
            )
        finally:
            command_executor._post_json = owner_post
            command_executor._get_json = owner_get
        assert first.status_code == 202, first.text
        command_id = first.json()["data"]["command_id"]
        failed = _command_receipt(harness, command_id)
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "DOWNSTREAM_AMBIGUOUS"
        assert failed["error"]["retryable"] is True

        retried = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=evidence,
            headers={
                **apply_headers,
                "Idempotency-Key": "rb-apply-ambiguous-retry",
            },
        )
        assert retried.status_code == 202, retried.text
        assert retried.json()["data"]["command_id"] == command_id
        executed = _command_receipt(harness, command_id)
        assert executed["status"] == "executed"
        assert executed["error"] is None


def test_owner_http_409_semantic_conflict_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflict = HTTPError("http://capital.test/apply", 409, "conflict", {}, None)
    monkeypatch.setattr(
        command_executor,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(conflict),
    )
    monkeypatch.setattr(command_executor, "_get_json", lambda *args, **kwargs: None)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital.test")

    status, result, error = command_executor.execute_command_with_status(
        "cmd-conflict",
        bff_main.CommandType.APPROVED_APPLY,
        {
            "entity_type": "Rebalance",
            "entity_id": "rb-conflict",
            "rebalance_id": "rb-conflict",
            "approval_required": True,
            "approval_ref": "approval-conflict",
        },
    )
    assert status.value == "failed"
    assert result is None
    assert error is not None
    assert error["downstream_status"] == 409
    assert error["retryable"] is False


def test_concurrent_single_sign_records_aggregate_to_valid_two_man_evidence(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        payload = rebalance_payload()
        payload["lines"][0].update(current_weight=0.0, target_weight=0.12, delta=0.12)
        created = _create_proposal(harness, key="rb-proposal-concurrent-sign", payload=payload)
        rebalance_id = created.json()["rebalance_id"]
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="concurrent-sign",
        )

        records = bff_main.command_store._get_all_commands()
        for record in records:
            if (
                record.get("type") == "RebalanceTwoManSign"
                and (record.get("audit") or {}).get("operator_id") == "op-3"
            ):
                record["params"].update(
                    signer_operator_ids=["op-3"],
                    first_operator_id="op-3",
                    second_operator_id=None,
                    complete=False,
                )
        bff_main.command_store._update_commands(records)

        assert harness.client is not None
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=evidence,
            headers={**apply_headers, "Idempotency-Key": "rb-apply-concurrent-sign"},
        )
        assert accepted.status_code == 202, accepted.text
        receipt = _command_receipt(harness, accepted.json()["data"]["command_id"])
        assert receipt["status"] == "executed"


@pytest.mark.parametrize(
    ("route", "idempotency_header"),
    [
        ("/bff/v1/commands", "Idempotency-Key"),
        ("/api/v1/operator/commands", "X-Idempotency-Key"),
    ],
)
@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("RebalanceApproval", {"type": "ApprovalDecision", "id": "approval-forged"}),
        ("RebalanceTwoManSign", {"type": "Review", "id": "tms-forged"}),
    ],
)
def test_public_command_admissions_reject_forged_rebalance_evidence(
    tmp_path: Path,
    route: str,
    idempotency_header: str,
    command: str,
    target: Dict[str, str],
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key=f"rb-proposal-{command}-{idempotency_header}")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        forged = harness.client.post(
            route,
            json={
                "command": command,
                "target": target,
                "params": {
                    "approval_decision_id": "approval-forged",
                    "two_man_signature_id": "tms-forged",
                    "outcome": "approved",
                    "state": "approved",
                    "signer_operator_ids": ["op-a", "op-b"],
                    "command": "ApprovedApply",
                    "target": {"type": "Rebalance", "id": rebalance_id},
                    "target_type": "Rebalance",
                    "target_id": rebalance_id,
                },
                "audit_context": {"reason": "attempt forged evidence"},
            },
            headers={**HEADERS, idempotency_header: f"forged-{command}-{route}"},
        )
        assert forged.status_code == 403, forged.text
        assert "server-managed" in forged.text


def test_legacy_operator_admission_cannot_bypass_approved_apply_gates(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-legacy-bypass")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        bypass = harness.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "params": {
                    "rebalance_id": rebalance_id,
                    "approval_ref": "approval-unverified",
                    "approval_decision_id": "approval-unverified",
                    "approval_required": True,
                    "two_man_signature_id": "tms-unverified",
                },
                "audit_context": {"reason": "attempt legacy apply bypass"},
            },
            headers={**HEADERS, "X-Idempotency-Key": "legacy-apply-bypass"},
        )
        assert bypass.status_code == 428, bypass.text
        assert "CONFIRM_TOKEN_MISSING" in bypass.text


@pytest.mark.parametrize(
    ("route", "idempotency_header"),
    [
        ("/bff/v1/commands", "Idempotency-Key"),
        ("/api/v1/operator/commands", "X-Idempotency-Key"),
    ],
)
def test_approved_apply_admissions_reject_params_target_redirect(
    tmp_path: Path,
    route: str,
    idempotency_header: str,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key=f"rb-proposal-redirect-{idempotency_header}")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        redirected = harness.client.post(
            route,
            json={
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "params": {"rebalance_id": "rb-attacker-redirect"},
                "audit_context": {"reason": "redirect must fail before owner dispatch"},
            },
            headers={
                **HEADERS,
                idempotency_header: f"rb-redirect-{idempotency_header}",
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
def test_validated_apply_evidence_overwrites_conflicting_param_aliases(
    tmp_path: Path,
    route: str,
    idempotency_header: str,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key=f"rb-proposal-alias-{idempotency_header}")
        rebalance_id = created.json()["rebalance_id"]
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix=f"alias-{idempotency_header}",
        )
        approval_id = evidence["approval_decision_id"]
        signature_id = evidence["two_man_signature_id"]
        assert harness.client is not None
        accepted = harness.client.post(
            route,
            json={
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "approvalDecisionId": approval_id,
                "twoManSignatureId": signature_id,
                "params": {
                    "rebalance_id": rebalance_id,
                    "approval_ref": "approval-attacker",
                    "approval_decision_id": "approval-attacker",
                    "approvalId": "approval-attacker",
                    "two_man_signature_id": "tms-attacker",
                    "twoManApprovalId": "tms-attacker",
                },
                "audit_context": {"reason": "validated evidence must be canonical"},
            },
            headers={
                **apply_headers,
                idempotency_header: f"rb-apply-alias-{idempotency_header}",
            },
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
        assert receipt["result"]["approval_ref"] == approval_id

        stored = bff_main.command_store.get_command(command_id)
        assert stored is not None
        assert stored["params"]["approval_decision_id"] == approval_id
        assert stored["params"]["approval_ref"] == approval_id
        assert stored["params"]["two_man_signature_id"] == signature_id
        for forged_alias in ("approvalId", "twoManApprovalId"):
            assert forged_alias not in stored["params"]
        assert stored["audit"]["precondition_evidence"] == {
            "confirm_token_id": apply_headers["X-Confirm-Token"],
            "approval_decision_id": approval_id,
            "two_man_signature_id": signature_id,
        }


def test_approval_record_cannot_be_aggregated_as_second_signer(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-evidence-collision")
        rebalance_id = created.json()["rebalance_id"]
        evidence_id = "evidence-collision"
        assert harness.client is not None
        approval = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/approve",
            json={"approval_decision_id": evidence_id},
            headers={**APPROVER_HEADERS, "Idempotency-Key": "approve-evidence-collision"},
        )
        assert approval.status_code == 201, approval.text
        signature = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/two-man-sign",
            json={"two_man_signature_id": evidence_id},
            headers={**HEADERS, "Idempotency-Key": "sign-evidence-collision"},
        )
        assert signature.status_code == 202, signature.text
        assert signature.json()["data"]["complete"] is False
        confirm = harness.client.post(
            "/bff/confirm-tokens",
            json={
                "tokenId": "ct-evidence-collision",
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "operator_id": "op-2",
                "reason": "confirm collision regression",
            },
            headers={**HEADERS, "Idempotency-Key": "confirm-evidence-collision"},
        )
        assert confirm.status_code == 201, confirm.text
        apply = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json={
                "approval_decision_id": evidence_id,
                "two_man_signature_id": evidence_id,
            },
            headers={
                **HEADERS,
                "X-Confirm-Token": "ct-evidence-collision",
                "Idempotency-Key": "apply-evidence-collision",
            },
        )
        assert apply.status_code == 409, apply.text
        assert "TWO_MAN_SIGNATURE_SIGNER_MISMATCH" in apply.text


def test_reviewer_cannot_produce_two_man_rebalance_evidence(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-reviewer-sign")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        denied = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/two-man-sign",
            json={"two_man_signature_id": "tms-reviewer"},
            headers={
                "Authorization": "Bearer op-reviewer:reviewer",
                "Idempotency-Key": "sign-reviewer",
            },
        )
        assert denied.status_code == 403, denied.text


def test_concurrent_same_key_approval_produces_one_durable_record(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-concurrent-approval")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None

        def approve() -> Any:
            return harness.client.post(
                f"/bff/rebalances/{rebalance_id}/approve",
                json={"approval_decision_id": "approval-concurrent"},
                headers={
                    **APPROVER_HEADERS,
                    "Idempotency-Key": "approve-concurrent-same-key",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: approve(), range(2)))
        assert [response.status_code for response in responses] == [201, 201]
        command_ids = {response.json()["data"]["command_id"] for response in responses}
        assert len(command_ids) == 1
        replayed = sorted(response.json()["meta"]["idempotency"]["replayed"] for response in responses)
        assert replayed == [False, True]
        records = [
            record
            for record in bff_main.command_store._get_all_commands()
            if record.get("type") == "RebalanceApproval"
            and (record.get("params") or {}).get("approval_decision_id")
            == "approval-concurrent"
        ]
        assert len(records) == 1


def test_restart_replay_heals_trusted_submitted_rebalance_evidence(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-heal-evidence")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        payload = {
            "approval_decision_id": "approval-heal-evidence",
            "memo": "heal interrupted trusted approval persistence",
        }
        first = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/approve",
            json=payload,
            headers={
                **APPROVER_HEADERS,
                "Idempotency-Key": "approve-heal-evidence",
            },
        )
        assert first.status_code == 201, first.text
        command_id = first.json()["data"]["command_id"]

        records = bff_main.command_store._get_all_commands()
        evidence_record = next(
            record for record in records if record.get("command_id") == command_id
        )
        evidence_record["status"] = "submitted"
        evidence_record["result"] = None
        evidence_record["audit"].pop("execution_completed_at", None)
        bff_main.command_store._update_commands(records)

        harness.restart()
        assert harness.client is not None
        replay = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/approve",
            json=payload,
            headers={
                **APPROVER_HEADERS,
                "Idempotency-Key": "approve-heal-evidence",
            },
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["data"]["command_id"] == command_id
        assert replay.json()["meta"]["idempotency"]["replayed"] is True

        healed = bff_main.command_store.get_command(command_id)
        assert healed is not None
        assert healed["status"] == "executed"
        assert healed["result"]["approval_decision_id"] == "approval-heal-evidence"
        assert healed["audit"]["execution_completed_at"] == healed["submitted_at"]


def test_binding_ambiguous_reconciliation_rejects_sleeve_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital.test")
    conflict = HTTPError("http://capital/api/bindings", 409, "conflict", {}, None)
    monkeypatch.setattr(
        command_executor,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(conflict),
    )
    monkeypatch.setattr(
        command_executor,
        "_get_json",
        lambda *args, **kwargs: {
            "binding_id": "binding-stable",
            "persona_id": "p-live",
            "capital_pool_id": "pool-real",
            "capital_sleeve_id": "sleeve-other",
            "role": "live_owner",
            "allowed_deployment_scope": "live",
            "metadata": {"capital_sleeve_id": "sleeve-other"},
        },
    )
    with pytest.raises(HTTPError) as raised:
        command_executor.create_capital_binding(
            {
                "binding_id": "binding-stable",
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "capital_sleeve_id": "sleeve-live",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
                "metadata": {"capital_sleeve_id": "sleeve-live"},
            }
        )
    assert raised.value is conflict


def test_pool_ambiguous_reconciliation_rejects_creator_marker_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", "http://capital.test")
    conflict = HTTPError("http://capital/api/capital-pools", 409, "conflict", {}, None)
    monkeypatch.setattr(
        command_executor,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(conflict),
    )
    monkeypatch.setattr(
        command_executor,
        "_get_json",
        lambda *args, **kwargs: {
            "pool_id": "pool-stable",
            "name": "Stable Pool",
            "owner_id": "fund-real",
            "owner_type": "fund",
            "status": "active",
            "currency": "USD",
            "single_runtime_enforced": True,
            "metadata": {
                "_pantheon_owner_create": {
                    "actor_id": "op-other",
                    "idempotency_key": "same-key",
                    "request_hash": "same-hash",
                }
            },
        },
    )
    with pytest.raises(HTTPError) as raised:
        command_executor.create_capital_pool(
            {
                "pool_id": "pool-stable",
                "name": "Stable Pool",
                "owner_id": "fund-real",
                "owner_type": "fund",
                "status": "active",
                "currency": "USD",
                "single_runtime_enforced": True,
                "metadata": {
                    "_pantheon_owner_create": {
                        "actor_id": "op-requester",
                        "idempotency_key": "same-key",
                        "request_hash": "same-hash",
                    }
                },
            }
        )
    assert raised.value is conflict


def test_restart_preserves_proposal_receipt_readback_and_same_key_command_replay(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        proposal_payload = rebalance_payload()
        created = _create_proposal(
            harness,
            key="rb-proposal-restart",
            payload=proposal_payload,
        )
        rebalance_id = created.json()["rebalance_id"]
        proposal_command_id = created.json()["data"]["command_id"]
        assert harness.client is not None
        apply_body, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="restart",
        )
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers={**apply_headers, "Idempotency-Key": "rb-apply-restart"},
        )
        assert accepted.status_code == 202, accepted.text
        apply_command_id = accepted.json()["data"]["command_id"]
        before_receipt = _command_receipt(harness, apply_command_id)
        before_result = before_receipt["result"]

        harness.restart()
        assert harness.client is not None
        proposal = harness.client.get(
            f"/bff/rebalances/{rebalance_id}", headers=HEADERS
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["data"]["applied"] is True
        assert proposal.json()["data"]["apply_receipt"]["receipt_ref"] == before_result["receipt_ref"]

        restarted_receipt = _command_receipt(harness, apply_command_id)
        assert restarted_receipt["status"] == "executed"
        assert restarted_receipt["result"] == before_result
        allocations = harness.client.get(
            "/bff/capital-pools/pool-real", headers=HEADERS
        ).json()["data"]["allocations"]
        assert allocations[0]["current_weight"] == 0.12
        assert allocations[0]["last_rebalance_id"] == rebalance_id

        proposal_replay = harness.client.post(
            "/bff/rebalances",
            json=proposal_payload,
            headers={**HEADERS, "Idempotency-Key": "rb-proposal-restart"},
        )
        assert proposal_replay.status_code == 202, proposal_replay.text
        assert proposal_replay.json()["rebalance_id"] == rebalance_id
        assert proposal_replay.json()["data"]["command_id"] == proposal_command_id

        apply_replay = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers={**apply_headers, "Idempotency-Key": "rb-apply-restart"},
        )
        assert apply_replay.status_code == 202, apply_replay.text
        assert apply_replay.json()["data"]["command_id"] == apply_command_id
        assert _command_receipt(harness, apply_command_id)["result"] == before_result


def test_startup_replays_submitted_approved_apply_to_terminal_owner_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-startup-replay")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        apply_body, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="startup-replay",
        )

        original_processor = bff_main._process_command_stub

        async def leave_submitted(_command_id: str) -> None:
            return None

        monkeypatch.setattr(bff_main, "_process_command_stub", leave_submitted)
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers={
                **apply_headers,
                "Idempotency-Key": "rb-apply-startup-replay",
            },
        )
        assert accepted.status_code == 202, accepted.text
        command_id = accepted.json()["data"]["command_id"]
        submitted = bff_main.command_store.get_command(command_id)
        assert submitted is not None
        assert submitted["status"] == "submitted"
        assert (
            harness.client.get(
                f"/bff/confirm-tokens/{apply_headers['X-Confirm-Token']}",
                headers=HEADERS,
            ).json()["data"]["status"]
            == "redeemed"
        )

        monkeypatch.setattr(bff_main, "_process_command_stub", original_processor)
        harness.restart()
        assert harness.client is not None
        harness.client.close()

        with TestClient(bff_main.app) as restarted_client:
            harness.client = restarted_client
            deadline = time.monotonic() + 3.0
            receipt = _command_receipt(harness, command_id)
            while receipt["status"] in {"submitted", "processing"} and time.monotonic() < deadline:
                time.sleep(0.02)
                receipt = _command_receipt(harness, command_id)

            assert receipt["status"] == "executed", receipt
            assert receipt["result"]["status"] == "applied"
            assert receipt["result"]["command_id"] == command_id
            proposal = restarted_client.get(
                f"/bff/rebalances/{rebalance_id}",
                headers=HEADERS,
            )
            assert proposal.status_code == 200, proposal.text
            assert proposal.json()["data"]["applied"] is True
            assert proposal.json()["data"]["apply_command_id"] == command_id
            token_id = apply_headers["X-Confirm-Token"]
            token_state = restarted_client.get(
                f"/bff/confirm-tokens/{token_id}",
                headers=HEADERS,
            )
            assert token_state.status_code == 200, token_state.text
            assert token_state.json()["data"]["status"] == "redeemed"
            redemption_records = [
                record
                for record in bff_main.command_store._get_all_commands()
                if record.get("type")
                == bff_main.CommandType.CONFIRM_TOKEN_REDEEM.value
                and record.get("target", {}).get("id") == token_id
            ]
            assert len(redemption_records) == 1
        harness.client = None


def test_pool_and_binding_creation_are_owner_durable_and_replay_after_restart(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.capital_client is not None
        pool = harness.capital_client.get("/api/capital-pools/pool-real")
        binding = harness.capital_client.get("/api/bindings/binding-live")
        assert pool.status_code == 200, pool.text
        assert pool.json()["status"] == "active"
        assert binding.status_code == 200, binding.text
        assert binding.json()["status"] == "pending"
        assert binding.json()["capital_sleeve_id"] == "sleeve-live"

        harness.restart()
        assert harness.client is not None
        replay_pool = harness.client.post(
            "/bff/capital-pools",
            json={
                "pool_id": "pool-real",
                "name": "Regression Pool",
                "owner_id": "fund-real",
                "owner_type": "fund",
                "risk_policy_ref": "risk-main",
            },
            headers={**HEADERS, "Idempotency-Key": "create-pool-real"},
        )
        replay_binding = harness.client.post(
            "/api/v1/bindings",
            json={
                "binding_id": "binding-live",
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "capital_sleeve_id": "sleeve-live",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
            },
            headers={**HEADERS, "Idempotency-Key": "create-binding-live"},
        )
        assert replay_pool.status_code == 201, replay_pool.text
        assert replay_pool.json()["pool_id"] == "pool-real"
        assert replay_pool.json()["idempotent_replay"] is True
        assert replay_binding.status_code == 201, replay_binding.text
        assert replay_binding.json()["binding_id"] == "binding-live"
        assert replay_binding.json()["idempotent_replay"] is True

        assert harness.capital_client is not None
        assert len(harness.capital_client.get("/api/capital-pools").json()) == 1
        assert len(harness.capital_client.get("/api/bindings").json()) == 1


def test_emergency_proposal_rejects_increase_and_accepts_containment(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.client is not None
        rejected_payload = rebalance_payload(emergency=True)
        harness.admit_rebalance_payload(rejected_payload)
        rejected = harness.client.post(
            "/bff/rebalances",
            json=rejected_payload,
            headers={**HEADERS, "Idempotency-Key": "rb-emergency-increase"},
        )
        assert rejected.status_code == 422, rejected.text

        payload = rebalance_payload(emergency=True)
        payload["lines"][0].update(
            target_weight=0.05,
            delta=-0.05,
            recommendation="containment",
        )
        accepted = _create_proposal(
            harness,
            key="rb-emergency-decrease",
            payload=payload,
        )
        detail = harness.client.get(
            f"/bff/rebalances/{accepted.json()['rebalance_id']}",
            headers=HEADERS,
        ).json()["data"]
        assert detail["lines"][0]["target_weight"] == 0.05
        assert detail["applied"] is False


def test_bff_version_reports_configured_source_sha(monkeypatch) -> None:
    source_sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("BFF_COMMIT", source_sha)
    monkeypatch.setenv("BFF_IMAGE_DIGEST", "sha256:123456")
    monkeypatch.setenv("BFF_BUILD_TIME", "2026-07-14T00:00:00Z")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    response = TestClient(bff_main.app).get("/bff/version")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["service"] == "operator-bff"
    assert data["version"] == "0.2.0"
    assert data["source_commit_sha"] == source_sha
    assert data["commit"] == source_sha
    assert data["source_commit_known"] is True
    assert data["image_digest"] == "sha256:123456"
    assert data["build_time"] == "2026-07-14T00:00:00Z"
    assert data["environment"] == "dev"
    assert "config_posture" in data
    assert "auth_stub" in data["config_posture"]


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

    return signature_id, {**HEADERS, "X-Confirm-Token": token_id}


def test_action_adapter_rebalance_apply_forwarding(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-adapter-apply")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        evidence, apply_headers = harness.apply_evidence(
            rebalance_id,
            suffix="adapter-apply",
        )

        # Call the action adapter endpoint
        response = harness.client.post(
            f"/bff/actions/rebalance/{rebalance_id}/apply",
            json=evidence,
            headers={
                **apply_headers,
                "Idempotency-Key": "rb-apply-adapter-apply",
            },
        )
        assert response.status_code == 202, response.text
        command_id = response.json()["data"]["command_id"]

        receipt = _command_receipt(harness, command_id)
        assert receipt["status"] == "executed"
        result = receipt["result"]
        assert result["status"] == "applied"
        assert result["dispatch_path"] == "capital_service_rebalance_authority"
        assert result["entity_id"] == rebalance_id

        proposal = harness.client.get(
            f"/bff/rebalances/{rebalance_id}", headers=HEADERS
        ).json()["data"]
        assert proposal["status"] == "applied"
        assert proposal["applied"] is True


def test_action_adapter_emergency_containment_forwarding(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        persona_id = "p-containment-forward"
        harness.create_persona(persona_id)
        assert harness.client is not None

        sig_id, apply_headers = _containment_security_evidence(
            harness,
            suffix="containment-forward",
            persona_id=persona_id,
        )

        # Call the action adapter endpoint for persona emergency containment
        response = harness.client.post(
            f"/bff/actions/persona/{persona_id}/EmergencyContainment",
            json={
                "action": "freeze",
                "trigger": "hard_risk_breach",
                "evidence_refs": ["risk-event:42"],
                "two_man_signature_id": sig_id,
            },
            headers={
                **apply_headers,
                "Idempotency-Key": "persona-containment-adapter-apply",
            },
        )
        assert response.status_code == 202, response.text
        command_id = response.json()["data"]["command_id"]

        receipt = _command_receipt(harness, command_id)
        assert receipt["status"] == "executed"
        result = receipt["result"]
        assert result["containment"] is True
        assert result["containment_state"] == "frozen"
        assert result["dispatch_path"] == "capital_service_containment_authority"

        persona = harness.client.get(
            f"/bff/personas/{persona_id}", headers=HEADERS
        ).json()["data"]
        assert persona["containment_state"] == "frozen"
        assert persona["frozen"] is True


def test_restart_preserves_pending_proposals_via_write_datasets(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-pending-restart")
        rebalance_id = created.json()["rebalance_id"]

        harness.restart()
        assert harness.client is not None

        proposal = harness.client.get(
            f"/bff/rebalances/{rebalance_id}", headers=HEADERS
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["data"]["status"] == "pending"
        assert proposal.json()["data"]["applied"] is False
