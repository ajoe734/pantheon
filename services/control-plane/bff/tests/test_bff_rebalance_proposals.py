from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as bff_main
from rebalance_authority_test_support import (
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
    response = harness.client.post(
        "/bff/rebalances",
        json=payload or rebalance_payload(),
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
        assert allocation["last_rebalance_id"] is None


def test_live_increase_without_approval_is_409_and_leaves_owner_state_unchanged(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-no-approval")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None

        before = harness.client.get(f"/bff/rebalances/{rebalance_id}", headers=HEADERS).json()["data"]
        before_allocations = harness.client.get(
            "/bff/capital-pools/pool-real", headers=HEADERS
        ).json()["data"]["allocations"]
        denied = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json={},
            headers={**HEADERS, "Idempotency-Key": "rb-apply-without-approval"},
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
        assert after_allocations[0]["last_rebalance_id"] is None


def test_approved_apply_is_terminal_authoritative_and_ignores_body_tampering(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        created = _create_proposal(harness, key="rb-proposal-approved")
        rebalance_id = created.json()["rebalance_id"]
        assert harness.client is not None
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json={
                "approval_ref": "approval-human-1",
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
            },
            headers={**HEADERS, "Idempotency-Key": "rb-apply-approved"},
        )
        assert accepted.status_code == 202, accepted.text
        command_id = accepted.json()["data"]["command_id"]

        receipt = _command_receipt(harness, command_id)
        assert receipt["status"] == "executed"
        result = receipt["result"]
        assert result["status"] == "applied"
        assert result["command_id"] == command_id
        assert result["entity_type"] == "Rebalance"
        assert result["entity_id"] == rebalance_id
        assert result["action_id"] == "apply"
        assert result["approval_ref"] == "approval-human-1"
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
        assert stored["params"]["action_id"] == "apply"
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
        apply_body = {"approval_ref": "approval-restart-1"}
        assert harness.client is not None
        accepted = harness.client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            json=apply_body,
            headers={**HEADERS, "Idempotency-Key": "rb-apply-restart"},
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
            headers={**HEADERS, "Idempotency-Key": "rb-apply-restart"},
        )
        assert apply_replay.status_code == 202, apply_replay.text
        assert apply_replay.json()["data"]["command_id"] == apply_command_id
        assert _command_receipt(harness, apply_command_id)["result"] == before_result


def test_emergency_proposal_rejects_increase_and_accepts_containment(
    tmp_path: Path,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.client is not None
        rejected = harness.client.post(
            "/bff/rebalances",
            json=rebalance_payload(emergency=True),
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
    response = TestClient(bff_main.app).get("/bff/version")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "service": "operator-bff",
        "version": "0.2.0",
        "source_commit_sha": source_sha,
        "commit": source_sha,
        "source_commit_known": True,
    }
