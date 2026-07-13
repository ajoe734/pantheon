import os
import tempfile

from fastapi.testclient import TestClient

import main as bff_main
from read_store import ReadSurfaceStore

HEADERS = {"Authorization": "Bearer op-2:operator"}


def _client(td):
    bff_main.read_store = ReadSurfaceStore(os.path.join(td, "read.json"), allow_local_snapshot_fallback=True)
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app)


def _proposal(**overrides):
    payload = {
        "capital_pool_id": "pool-real", "ranking_snapshot_id": "rank-q3", "reason": "quarterly",
        "lines": [{"persona_id": "p-live", "stage": "live_running", "capital_scope": "pool",
                   "ranking_snapshot_id": "rank-q3",
                   "capital_pool_id": "pool-real", "current_weight": .1, "target_weight": .12,
                   "delta": .02, "cap_reasons": ["quarterly_increase_cap_25pct"], "evidence_refs": ["ev-1"]}],
        "simulation": {"status": "passed"}, "constraints": {"pool_total_max": 1},
        "rollback_target": {"snapshot_id": "allocation-before-q3"}, "audit_refs": ["audit-ranking-q3"],
    }
    payload.update(overrides)
    return payload


def test_create_and_read_auditable_proposal_does_not_apply_capital():
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _client(td)
            response = client.post("/bff/rebalances", json=_proposal(), headers={**HEADERS, "Idempotency-Key": "rb-proposal-1"})
            assert response.status_code == 202, response.text
            assert response.json()["ranking_snapshot_id"] == "rank-q3"
            detail = client.get(f"/bff/rebalances/{response.json()['rebalance_id']}", headers=HEADERS).json()["data"]
            assert detail["ranking_snapshot_id"] == "rank-q3"
            assert detail["lines"][0]["ranking_snapshot_id"] == "rank-q3"
            assert detail["lines"][0]["delta"] == .02
            assert detail["simulation"]["status"] == "passed"
            assert detail["rollback_target"]["snapshot_id"] == "allocation-before-q3"
            assert detail["applied"] is False
        finally:
            bff_main.read_store = original


def test_rebalance_proposal_rejects_mixed_ranking_snapshots():
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _client(td)
            payload = _proposal()
            payload["lines"][0]["ranking_snapshot_id"] = "rank-other"
            response = client.post(
                "/bff/rebalances",
                json=payload,
                headers={**HEADERS, "Idempotency-Key": "rb-proposal-mixed-snapshot"},
            )
            assert response.status_code == 422, response.text
        finally:
            bff_main.read_store = original


def test_live_increase_apply_requires_human_approval_reference():
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _client(td)
            created = client.post("/bff/rebalances", json=_proposal(), headers={**HEADERS, "Idempotency-Key": "rb-proposal-2"})
            rebalance_id = created.json()["rebalance_id"]
            denied = client.post(f"/bff/rebalances/{rebalance_id}/apply", json={}, headers={**HEADERS, "Idempotency-Key": "rb-apply-1"})
            assert denied.status_code == 409
            accepted = client.post(f"/bff/rebalances/{rebalance_id}/apply", json={"approval_ref": "approval-human-1"}, headers={**HEADERS, "Idempotency-Key": "rb-apply-2"})
            assert accepted.status_code == 202, accepted.text
            command_id = accepted.json()["data"]["command_id"]
            command = client.get(
                f"/api/v1/operator/commands/{command_id}",
                headers=HEADERS,
            )
            assert command.status_code == 200, command.text
            receipt = command.json()
            assert receipt["status"] == "executed"
            assert receipt["result"]["action_id"] == "apply"
            assert receipt["result"]["entity_type"] == "Rebalance"
            assert receipt["result"]["entity_id"] == rebalance_id
            assert receipt["result"]["live_capital_side_effects"] is False
        finally:
            bff_main.read_store = original


def test_emergency_proposal_rejects_increase_and_promotion():
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _client(td)
            response = client.post("/bff/rebalances", json=_proposal(emergency=True), headers={**HEADERS, "Idempotency-Key": "rb-emergency-1"})
            assert response.status_code == 422
            payload = _proposal(emergency=True)
            payload["lines"][0].update(target_weight=.05, delta=-.05, recommendation="containment")
            accepted = client.post("/bff/rebalances", json=payload, headers={**HEADERS, "Idempotency-Key": "rb-emergency-2"})
            assert accepted.status_code == 202, accepted.text
        finally:
            bff_main.read_store = original
