from __future__ import annotations

import http.client
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff import command_executor
from services.control_plane.bff.core.app_factory import create_core_router, sem_bff_version_default
from services.control_plane.bff.models import CommandType
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    HEADERS,
    CapitalBffAuthorityHarness,
    rebalance_payload,
)

# Mounted proofs for the Capital write path: every REST/stored-command entry reaches the isolated real
# Capital owner through the single CapitalOwnerWriter with the caller's own JWT.  Capital approval is
# decided by the owner's CapitalGuard from the Governance decision supplied as ``approval_ref`` /
# ``approval_decision_id``; the BFF no longer keeps approval or two-man evidence of its own.

REBALANCE = "Rebalance"


def _create_proposal(harness: CapitalBffAuthorityHarness, *, key: str, payload: dict | None = None) -> str:
    assert harness.client is not None
    request_payload = payload or rebalance_payload()
    harness.admit_rebalance_payload(request_payload)
    response = harness.client.post(
        "/bff/rebalances", json=request_payload, headers={**HEADERS, "Idempotency-Key": key}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["rebalance_id"]


def _raw(token: str | None) -> str:
    return str(token or "").removeprefix("Bearer ")


def _apply(harness: CapitalBffAuthorityHarness, rebalance_id: str, *, key: str, approval_ref: str | None = "approval-apply") -> dict:
    params = {"approval_ref": approval_ref} if approval_ref else {}
    return harness.run_command(
        "ApprovedApply", {"type": REBALANCE, "id": rebalance_id}, params, key=key, token={"command": "ApprovedApply"}
    )


def _owner_applies(harness: CapitalBffAuthorityHarness) -> list:
    return [call for call in harness.owner_calls if call[0] == "POST" and call[1].endswith("/apply")]


def _unapplied_zero_weight_payload() -> dict:
    payload = rebalance_payload()
    payload["lines"][0].update(current_weight=0.0, target_weight=0.12, delta=0.12)
    return payload


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
        CommandType.APPROVED_APPLY,
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


@pytest.mark.parametrize(
    "idempotency_header",
    ["Idempotency-Key", "X-Idempotency-Key"],
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
    idempotency_header: str,
    command: str,
    target: Dict[str, str],
) -> None:
    route = "/bff/v1/commands"
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key=f"rb-proposal-{command}-{idempotency_header}")
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


def test_final_command_admission_cannot_bypass_approved_apply_gates(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-legacy-bypass")
        assert harness.client is not None
        bypass = harness.client.post(
            "/bff/v1/commands",
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
    "idempotency_header",
    ["Idempotency-Key", "X-Idempotency-Key"],
)
def test_approved_apply_admissions_reject_params_target_redirect(
    tmp_path: Path,
    idempotency_header: str,
) -> None:
    route = "/bff/v1/commands"
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key=f"rb-proposal-redirect-{idempotency_header}")
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


def test_bff_version_reports_configured_source_sha(monkeypatch) -> None:
    source_sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("BFF_COMMIT", source_sha)
    monkeypatch.setenv("BFF_IMAGE_DIGEST", "sha256:123456")
    monkeypatch.setenv("BFF_BUILD_TIME", "2026-07-14T00:00:00Z")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    app = FastAPI()
    app.include_router(
        create_core_router({"sem_bff_version": sem_bff_version_default})
    )
    response = TestClient(app).get("/bff/version")
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


def test_proposal_is_owner_durable_and_does_not_apply_capital(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-complete")
        assert harness.client is not None
        detail = harness.client.get(f"/bff/rebalances/{rebalance_id}", headers=HEADERS).json()["data"]
        expected = rebalance_payload()
        assert detail["capital_pool_id"] == expected["capital_pool_id"]
        assert detail["status"] == "pending"
        assert detail["applied"] is False
        assert detail["canonical_write_authority"] == "capital_service"
        assert detail["lines"][0]["binding_id"] == "binding-live"
        owner_post = [call for call in harness.owner_calls if call[:2] == ("POST", "http://capital-authority.test/api/rebalances")]
        assert [_raw(call[2]) for call in owner_post] == [_raw(HEADERS["Authorization"])]  # caller JWT, not a service token
        allocation = harness.capital_client.get("/api/allocations").json()["items"][0]
        assert allocation["current_weight"] == 0.10


@pytest.mark.parametrize("stage", ["live", "live_candidate", "live_running"])
def test_live_increase_without_approval_is_owner_rejected_and_state_unchanged(tmp_path: Path, stage: str) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        payload = rebalance_payload()
        payload["lines"][0]["stage"] = stage
        rebalance_id = _create_proposal(harness, key=f"rb-proposal-no-approval-{stage}", payload=payload)
        before = harness.capital_client.get(f"/api/rebalances/{rebalance_id}").json()

        record = _apply(harness, rebalance_id, key=f"rb-apply-no-approval-{stage}", approval_ref=None)

        assert record["status"] == "failed"
        assert record["error"]["downstream_status"] == 403
        assert harness.capital_client.get(f"/api/rebalances/{rebalance_id}").json() == before
        assert before["status"] == "pending" and before["applied"] is False


def test_approved_apply_is_terminal_authoritative_and_forwards_the_caller_jwt(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-approved")
        record = _apply(harness, rebalance_id, key="rb-apply-approved")

        assert record["status"] == "executed"
        result = record["result"]
        assert (result["status"], result["entity_id"], result["approval_ref"]) == ("applied", rebalance_id, "approval-apply")
        assert result["authoritative_capital_readback"] is True
        assert result["authoritative_capital_state_applied"] is True
        assert result["live_capital_side_effects"] is False
        assert result["allocation_readback"][0]["current_weight"] == 0.12
        assert record["params"]["actor_id"] == "op-2"
        assert [_raw(call[2]) for call in _owner_applies(harness)] == [_raw(HEADERS["Authorization"])]
        owner = harness.capital_client.get(f"/api/rebalances/{rebalance_id}").json()
        assert owner["status"] == "applied" and owner["applied"] is True
        assert owner["apply_command_id"] == record["command_id"]

        # Same key + same confirm token replays the stored command: still exactly one owner effect.
        replay = harness.client.post(
            "/bff/v1/commands",
            json={"command": "ApprovedApply", "target": {"type": REBALANCE, "id": rebalance_id},
                  "params": {"approval_ref": "approval-apply"}, "audit_context": {"reason": "rb-apply-approved"}},
            headers={**HEADERS, "Idempotency-Key": "rb-apply-approved", "X-Confirm-Token": "ct-rb-apply-approved"},
        )
        assert replay.status_code == 202, replay.text
        assert len(_owner_applies(harness)) == 1


def test_bff_apply_bootstraps_zero_weight_owner_allocation(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-zero", payload=_unapplied_zero_weight_payload())
        record = _apply(harness, rebalance_id, key="rb-apply-zero")
        assert record["status"] == "executed"
        allocation = record["result"]["allocation_readback"][0]
        assert allocation["current_weight"] == 0.12
        assert allocation["authoritative_capital_readback"] is True


@pytest.mark.parametrize("failure_kind", ["url_disconnect", "json_decode", "truncated_response"])
def test_ambiguous_owner_apply_response_reconciles_committed_receipt(tmp_path: Path, failure_kind: str) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-ambiguous", payload=_unapplied_zero_weight_payload())
        owner_post = command_executor._post_json
        raised = False

        def commit_then_fail(url, body, auth_token=None, mfa_token=None):
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

        command_executor._post_json = commit_then_fail
        try:
            record = _apply(harness, rebalance_id, key=f"rb-apply-ambiguous-{failure_kind}")
        finally:
            command_executor._post_json = owner_post
        assert record["status"] == "executed"
        assert record["result"]["owner_receipt_reconciled"] is True
        assert record["result"]["authoritative_capital_state_applied"] is True
        # The receipt GET used to reconcile also carried the caller's JWT.
        receipt_reads = [call for call in harness.owner_calls if "/receipts/" in call[1]]
        assert receipt_reads and all(_raw(call[2]) == _raw(HEADERS["Authorization"]) for call in receipt_reads)


def test_same_key_retries_only_retryable_terminal_capital_command(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-retry", payload=_unapplied_zero_weight_payload())
        owner_post = command_executor._post_json

        def disconnect_before_commit(url, body, auth_token=None, mfa_token=None):
            if url.endswith("/apply"):
                raise URLError("owner connection unavailable")
            return owner_post(url, body, auth_token, mfa_token)

        command_executor._post_json = disconnect_before_commit
        try:
            failed = _apply(harness, rebalance_id, key="rb-apply-retry")
        finally:
            command_executor._post_json = owner_post
        assert failed["status"] == "failed"
        assert failed["error"]["retryable"] is True

        retried = harness.client.post(
            "/bff/v1/commands",
            json={"command": "ApprovedApply", "target": {"type": REBALANCE, "id": rebalance_id},
                  "params": {"approval_ref": "approval-apply"}, "audit_context": {"reason": "rb-apply-retry"}},
            headers={**HEADERS, "Idempotency-Key": "rb-apply-retry", "X-Confirm-Token": "ct-rb-apply-retry"},
        )
        assert retried.status_code == 202, retried.text
        executed = harness.command_store.get_command(failed["command_id"])
        assert executed["status"] == "executed" and executed["error"] is None


def test_restart_preserves_owner_apply_receipt_and_proposal_readback(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-restart")
        record = _apply(harness, rebalance_id, key="rb-apply-restart")
        receipt_ref = record["result"]["receipt_ref"]

        harness.restart()
        owner = harness.capital_client.get(f"/api/rebalances/{rebalance_id}").json()
        assert owner["applied"] is True
        assert owner["apply_receipt"]["receipt_ref"] == receipt_ref
        assert harness.capital_client.get("/api/allocations").json()["items"][0]["current_weight"] == 0.12


def test_pool_and_binding_creation_are_owner_durable_and_replay_after_restart(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.capital_client.get("/api/capital-pools/pool-real").json()["status"] == "active"
        assert harness.capital_client.get("/api/bindings/binding-live").json()["status"] == "pending"

        harness.restart()
        replay_pool = harness.client.post(
            "/bff/capital-pools",
            json={"pool_id": "pool-real", "name": "Regression Pool", "owner_id": "fund-real", "owner_type": "fund",
                  "risk_policy_ref": "risk-main", "approval_decision_id": "approval-pool-real"},
            headers={**HEADERS, "Idempotency-Key": "create-pool-real"},
        )
        replay_binding = harness.client.post(
            "/api/v1/bindings",
            json={"binding_id": "binding-live", "persona_id": "p-live", "capital_pool_id": "pool-real",
                  "capital_sleeve_id": "sleeve-live", "role": "live_owner", "allowed_deployment_scope": "live"},
            headers={**HEADERS, "Idempotency-Key": "create-binding-live"},
        )
        assert replay_pool.status_code == 201, replay_pool.text
        assert replay_pool.json()["data"]["idempotent_replay"] is True
        assert replay_binding.status_code == 201, replay_binding.text
        assert replay_binding.json()["idempotent_replay"] is True
        assert len(harness.capital_client.get("/api/capital-pools").json()) == 1
        assert len(harness.capital_client.get("/api/bindings").json()) == 1


def test_restart_preserves_pending_proposals_via_write_datasets(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        rebalance_id = _create_proposal(harness, key="rb-proposal-pending-restart")
        harness.restart()
        proposal = harness.client.get(f"/bff/rebalances/{rebalance_id}", headers=HEADERS)
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["data"]["status"] == "pending"
        assert proposal.json()["data"]["applied"] is False
