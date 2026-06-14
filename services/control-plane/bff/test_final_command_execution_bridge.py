from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore


HEADERS = {"Authorization": "Bearer op-sem-002:operator,reviewer,admin:mfa"}


@contextmanager
def _isolated_command_bridge() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.clear()


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

        records = bff_main.command_store._get_all_commands()
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
        assert missing.json()["error"]["code"] == "INVALID_PARAMS"
        assert body_key.status_code == 400, body_key.text
        assert body_key.json()["error"]["code"] == "INVALID_REQUEST"
        assert alias.status_code == 201, alias.text
        assert alias.json()["meta"]["idempotency"]["idempotencyKey"] == "sem-002-deploy-alias"
        assert len(bff_main.command_store._get_all_commands()) == 1


def test_canonical_action_replay_uses_command_store_not_generic_memory_receipt() -> None:
    with _isolated_command_bridge() as client:
        headers = {**HEADERS, "Idempotency-Key": "sem-002-action"}
        body = {"reason": "submit for semantic bridge proof"}

        first = client.post("/bff/actions/strategy/stg-sem-002/submit", headers=headers, json=body)
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        replay = client.post("/bff/actions/strategy/stg-sem-002/submit", headers=headers, json=body)

        assert first.status_code == 202, first.text
        assert replay.status_code == 202, replay.text
        assert _receipt_id(replay.json()) == _receipt_id(first.json())
        assert replay.json()["meta"]["idempotency"]["replayed"] is True
        records = bff_main.command_store._get_all_commands()
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
        assert [record["type"] for record in bff_main.command_store._get_all_commands()] == [
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
        assert len(bff_main.command_store._get_all_commands()) == 1


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
        assert len(bff_main.command_store._get_all_commands()) == 1


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
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()

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
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        third = client.post("/bff/confirm-tokens", headers=headers, json=body)
        assert third.status_code == 201, third.text
        assert third.json()["data"]["tokenId"] == original_token_id

        # After memory clear, same key + different payload → 409
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        conflict = client.post("/bff/confirm-tokens", headers=headers, json={"reason": "different"})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        # Only one command record created
        assert len(bff_main.command_store._get_all_commands()) == 1


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

        assert [record["type"] for record in bff_main.command_store._get_all_commands()] == [
            "AuditExport",
            "DecideV5Intervention",
            "SentinelFindingStatus",
            "SentinelRemediationBuild",
            "SentinelRemediationExecute",
        ]
