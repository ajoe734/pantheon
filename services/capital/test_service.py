"""
Unit tests for the deployable capital service boundary.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="capital_service_")
    env_backup = {
        "CAPITAL_DATA_DIR": os.environ.get("CAPITAL_DATA_DIR"),
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "CAPITAL_STORE_BACKEND": os.environ.get("CAPITAL_STORE_BACKEND"),
        "CAPITAL_AUDIT_BACKEND": os.environ.get("CAPITAL_AUDIT_BACKEND"),
    }
    os.environ["CAPITAL_DATA_DIR"] = tempdir
    os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = tempdir
    os.environ["CAPITAL_STORE_BACKEND"] = "json"
    os.environ["CAPITAL_AUDIT_BACKEND"] = "jsonl"

    sys.modules.pop("services.capital.main", None)
    module = importlib.import_module("services.capital.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app), Path(tempdir)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _pool_payload(**overrides):
    payload = {
        "actor_id": "capital-admin-1",
        "actor_role": "capital.admin",
        "pool_id": "pool-001",
        "name": "Main Pool",
        "owner_id": "fund-001",
        "owner_type": "fund",
        "risk_policy_ref": "risk-main",
    }
    payload.update(overrides)
    return payload


def _binding_payload(**overrides):
    payload = {
        "actor_id": "persona-admin-1",
        "actor_role": "persona.admin",
        "binding_id": "binding-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "role": "live_owner",
        "allowed_deployment_scope": "canary",
    }
    payload.update(overrides)
    return payload


def _rebalance_payload(*, rebalance_id="rb-001", lines=None, **overrides):
    payload = {
        "actor_id": "operator-1",
        "actor_role": "operator",
        "idempotency_key": f"create-{rebalance_id}",
        "request_hash": f"request-create-{rebalance_id}",
        "rebalance_id": rebalance_id,
        "capital_pool_id": "pool-001",
        "ranking_snapshot_id": "ranking-q3",
        "reason": "quarterly",
        "lines": lines
        or [
            {
                "persona_id": "persona-alpha",
                "stage": "live_running",
                "capital_scope": "pool",
                "capital_pool_id": "pool-001",
                "capital_sleeve_id": "sleeve-alpha",
                "current_weight": 0.10,
                "target_weight": 0.12,
                "delta": 0.02,
                "cap_reasons": ["quarterly_increase_cap_25pct"],
                "evidence_refs": ["evidence:ranking-q3"],
            }
        ],
        "simulation": {"status": "passed"},
        "constraints": {"pool_total_max": 1.0},
        "rollback_target": {"snapshot_id": "allocation-before-q3"},
        "audit_refs": ["audit:ranking-q3"],
    }
    payload.update(overrides)
    return payload


def _apply_payload(*, rebalance_id="rb-001", command_id="cmd-apply-001", **overrides):
    payload = {
        "actor_id": "approver-1",
        "actor_role": "approver",
        "idempotency_key": f"apply-{rebalance_id}",
        "request_hash": f"request-apply-{rebalance_id}",
        "command_id": command_id,
        "approval_ref": f"approval-{rebalance_id}",
    }
    payload.update(overrides)
    return payload


def _reload_capital_module():
    module = importlib.import_module("services.capital.main")
    return importlib.reload(module)


def test_health(client):
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pantheon-capital"


def test_write_authority_matrix(client):
    test_client, _ = client
    response = test_client.get("/api/capital/write-authority")
    assert response.status_code == 200
    matrix = response.json()["matrix"]
    expected = {
        ("CapitalPool", "create"),
        ("CapitalPool", "update_status"),
        ("PersonaCapitalBinding", "create"),
        ("PersonaCapitalBinding", "activate"),
        ("PersonaCapitalBinding", "update_status"),
        ("Rebalance", "create"),
        ("Rebalance", "apply"),
        ("Containment", "create"),
    }
    found = {(entry["resource_type"], entry["operation"]) for entry in matrix}
    assert expected == found
    roles_by_operation = {
        (entry["resource_type"], entry["operation"]): set(entry["authorized_roles"])
        for entry in matrix
    }
    assert "reviewer" in roles_by_operation[("Containment", "create")]
    assert "reviewer" not in roles_by_operation[("Rebalance", "create")]
    assert "reviewer" not in roles_by_operation[("Rebalance", "apply")]


def test_create_pool_and_list(client):
    test_client, data_dir = client
    create = test_client.post("/api/capital-pools", json=_pool_payload())
    assert create.status_code == 201, create.text
    assert create.json()["single_runtime_enforced"] is True

    listing = test_client.get("/api/capital-pools")
    assert listing.status_code == 200
    assert [pool["pool_id"] for pool in listing.json()] == ["pool-001"]

    persisted = (data_dir / "capital_pools.json").read_text(encoding="utf-8")
    assert "pool-001" in persisted


def test_create_pool_rejects_unauthorized_writer(client):
    test_client, _ = client
    response = test_client.post(
        "/api/capital-pools",
        json=_pool_payload(actor_role="persona.admin"),
    )
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"]


def test_binding_requires_existing_pool(client):
    test_client, _ = client
    response = test_client.post("/api/bindings", json=_binding_payload())
    assert response.status_code == 404
    assert "Pool not found" in response.json()["detail"]


def test_binding_lifecycle_and_admissibility_read_path(client):
    test_client, data_dir = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201

    create = test_client.post("/api/bindings", json=_binding_payload())
    assert create.status_code == 201, create.text
    assert create.json()["status"] == "pending"

    activate = test_client.post(
        "/api/bindings/binding-001/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-001",
        },
    )
    assert activate.status_code == 200, activate.text
    assert activate.json()["status"] == "active"

    admissibility = test_client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
        },
    )
    assert admissibility.status_code == 200
    body = admissibility.json()
    assert body["permitted"] is True
    assert body["binding_id"] == "binding-001"
    assert body["allowed_deployment_scope"] == "canary"
    assert body["active_live_owner_binding_id"] == "binding-001"

    too_high = test_client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-001",
            "target_stage": "live",
        },
    )
    assert too_high.status_code == 200
    assert too_high.json()["permitted"] is False

    persisted = (data_dir / "persona_capital_bindings.json").read_text(encoding="utf-8")
    assert "binding-001" in persisted


def test_binding_rejects_role_scope_mismatch(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    response = test_client.post(
        "/api/bindings",
        json=_binding_payload(role="paper_owner", allowed_deployment_scope="canary"),
    )
    assert response.status_code == 400
    assert "deployment ceiling" in response.json()["detail"]


def test_second_live_owner_same_pool_rejected(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload(binding_id="binding-001")).status_code == 201
    assert test_client.post(
        "/api/bindings/binding-001/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-001",
        },
    ).status_code == 200

    second = test_client.post(
        "/api/bindings",
        json=_binding_payload(binding_id="binding-002", persona_id="persona-beta"),
    )
    assert second.status_code == 201

    activate_second = test_client.post(
        "/api/bindings/binding-002/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-002",
        },
    )
    assert activate_second.status_code == 400
    assert "Single-live-owner rule" in activate_second.json()["detail"]


def test_suspended_pool_blocks_activation_and_admissibility(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload()).status_code == 201

    suspend = test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": "suspended",
        },
    )
    assert suspend.status_code == 200

    activate = test_client.post(
        "/api/bindings/binding-001/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-003",
        },
    )
    assert activate.status_code == 400
    assert "must be active" in activate.json()["detail"]

    admissibility = test_client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
        },
    )
    assert admissibility.status_code == 200
    assert admissibility.json()["permitted"] is False
    assert admissibility.json()["pool_status"] == "suspended"


def test_audit_log_records_mutations(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload()).status_code == 201

    events = test_client.get("/api/capital/audit").json()
    event_types = {event["event_type"] for event in events}
    assert "capital_pool_created" in event_types
    assert "persona_capital_binding_created" in event_types


def test_rebalance_apply_updates_authoritative_allocations_and_replays_once(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201

    proposal_payload = _rebalance_payload()
    created = test_client.post("/api/rebalances", json=proposal_payload)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"
    assert created.json()["applied"] is False

    denied_payload = _apply_payload(approval_ref=None)
    denied = test_client.post("/api/rebalances/rb-001/apply", json=denied_payload)
    assert denied.status_code == 409
    assert "approval reference" in denied.json()["detail"]

    apply_payload = _apply_payload()
    applied = test_client.post("/api/rebalances/rb-001/apply", json=apply_payload)
    assert applied.status_code == 200, applied.text
    receipt = applied.json()
    assert receipt["status"] == "applied"
    assert receipt["rebalance_id"] == "rb-001"
    assert receipt["command_id"] == "cmd-apply-001"
    assert receipt["approval_ref"] == "approval-rb-001"
    assert receipt["receipt_ref"]
    assert receipt["audit_ref"]
    assert receipt["authoritative_capital_readback"] is True
    assert receipt["authoritative_capital_state_applied"] is True
    assert receipt["live_capital_side_effects"] is False
    assert receipt["allocation_readback"][0]["current_weight"] == 0.12
    assert receipt["allocation_readback"][0]["target_weight"] == 0.12
    assert receipt["allocation_readback"][0]["allocation_version"] == 1
    assert receipt["allocation_readback"][0]["authoritative_capital_readback"] is True

    detail = test_client.get("/api/rebalances/rb-001")
    assert detail.status_code == 200
    assert detail.json()["status"] == "applied"
    assert detail.json()["applied"] is True
    assert detail.json()["approval_ref"] == "approval-rb-001"
    assert detail.json()["apply_command_id"] == "cmd-apply-001"
    assert detail.json()["apply_receipt_ref"] == receipt["receipt_ref"]

    global_read = test_client.get(
        "/api/allocations",
        params={"capital_pool_id": "pool-001", "persona_id": "persona-alpha"},
    )
    assert global_read.status_code == 200
    assert global_read.json()["authoritative_capital_readback"] is True
    assert global_read.json()["source"] == "capital_service"
    assert global_read.json()["count"] == 1
    allocation = global_read.json()["items"][0]
    assert allocation["authoritative_capital_readback"] is True
    assert allocation["capital_sleeve_id"] == "sleeve-alpha"
    assert allocation["current_weight"] == 0.12
    assert allocation["last_rebalance_id"] == "rb-001"

    pool_read = test_client.get("/api/capital-pools/pool-001/allocations")
    assert pool_read.status_code == 200
    assert pool_read.json()["items"] == global_read.json()["items"]

    replay = test_client.post("/api/rebalances/rb-001/apply", json=apply_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipt_ref"] == receipt["receipt_ref"]
    assert replay.json()["idempotent_replay"] is True
    allocation_after_replay = test_client.get("/api/allocations").json()["items"][0]
    assert allocation_after_replay["allocation_version"] == 1

    create_replay = test_client.post("/api/rebalances", json=proposal_payload)
    assert create_replay.status_code == 201
    assert create_replay.json()["rebalance_id"] == "rb-001"
    conflicting_payload = {**proposal_payload, "reason": "different body"}
    conflict = test_client.post("/api/rebalances", json=conflicting_payload)
    assert conflict.status_code == 409


def test_rebalance_proposal_receipt_and_allocations_survive_store_restart(client):
    test_client, data_dir = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    applied = test_client.post(
        "/api/rebalances/rb-001/apply",
        json=_apply_payload(),
    )
    assert applied.status_code == 200, applied.text
    receipt_ref = applied.json()["receipt_ref"]

    persisted_path = data_dir / "capital_allocation_authority.json"
    assert persisted_path.exists()
    persisted = persisted_path.read_text(encoding="utf-8")
    assert "rb-001" in persisted
    assert receipt_ref in persisted

    restarted_module = _reload_capital_module()
    restarted = TestClient(restarted_module.app)
    proposal = restarted.get("/api/rebalances/rb-001")
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["status"] == "applied"
    assert proposal.json()["apply_receipt"]["receipt_ref"] == receipt_ref
    allocation = restarted.get("/api/allocations").json()["items"][0]
    assert allocation["current_weight"] == 0.12
    assert allocation["last_rebalance_id"] == "rb-001"

    replay = restarted.post(
        "/api/rebalances/rb-001/apply",
        json=_apply_payload(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipt_ref"] == receipt_ref
    assert replay.json()["idempotent_replay"] is True
    assert restarted.get("/api/allocations").json()["items"][0]["allocation_version"] == 1


def test_stale_rebalance_fails_terminal_without_partial_allocation_update(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    initial_lines = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.10,
            "target_weight": 0.15,
            "delta": 0.05,
        },
        {
            "persona_id": "persona-beta",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.20,
            "target_weight": 0.25,
            "delta": 0.05,
        },
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-initial", lines=initial_lines),
    ).status_code == 201
    assert test_client.post(
        "/api/rebalances/rb-initial/apply",
        json=_apply_payload(rebalance_id="rb-initial", command_id="cmd-initial"),
    ).status_code == 200

    stale_lines = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.15,
            "target_weight": 0.16,
            "delta": 0.01,
        },
        {
            "persona_id": "persona-beta",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.20,
            "target_weight": 0.21,
            "delta": 0.01,
        },
    ]
    stale_create = test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-stale", lines=stale_lines),
    )
    assert stale_create.status_code == 201, stale_create.text
    stale_apply = test_client.post(
        "/api/rebalances/rb-stale/apply",
        json=_apply_payload(rebalance_id="rb-stale", command_id="cmd-stale"),
    )
    assert stale_apply.status_code == 409
    assert "no longer match" in stale_apply.json()["detail"]

    failed = test_client.get("/api/rebalances/rb-stale").json()
    assert failed["status"] == "failed"
    assert failed["applied"] is False
    assert failed["failure"]["code"] == "STALE_CURRENT_WEIGHT"
    allocations = {
        item["persona_id"]: item
        for item in test_client.get("/api/allocations").json()["items"]
    }
    assert allocations["persona-alpha"]["current_weight"] == 0.15
    assert allocations["persona-alpha"]["allocation_version"] == 1
    assert allocations["persona-beta"]["current_weight"] == 0.25
    assert allocations["persona-beta"]["allocation_version"] == 1


def test_risk_decreasing_rebalance_does_not_require_approval(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    decrease_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.10,
            "target_weight": 0.05,
            "delta": -0.05,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-decrease", lines=decrease_line),
    ).status_code == 201
    applied = test_client.post(
        "/api/rebalances/rb-decrease/apply",
        json=_apply_payload(
            rebalance_id="rb-decrease",
            command_id="cmd-decrease",
            approval_ref=None,
        ),
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["approval_ref"] is None
    assert applied.json()["allocation_readback"][0]["current_weight"] == 0.05


def test_containment_is_durable_frozen_and_cannot_promote_or_increase(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    flat_line = [
        {
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-001",
            "current_weight": 0.10,
            "target_weight": 0.10,
            "delta": 0.0,
        }
    ]
    assert test_client.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-baseline", lines=flat_line),
    ).status_code == 201
    assert test_client.post(
        "/api/rebalances/rb-baseline/apply",
        json=_apply_payload(
            rebalance_id="rb-baseline",
            command_id="cmd-baseline",
            approval_ref=None,
        ),
    ).status_code == 200

    containment_payload = {
        "actor_id": "operator-1",
        "actor_role": "operator",
        "idempotency_key": "containment-freeze-1",
        "request_hash": "request-containment-freeze-1",
        "containment_id": "containment-freeze-1",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "action": "freeze",
        "trigger": "hard_risk_breach",
        "evidence_refs": ["risk-event:42"],
        "current_weight": 0.10,
        "target_weight": 0.10,
        "command_id": "cmd-containment-1",
        "two_man_signature_id": "two-man-1",
    }
    frozen = test_client.post("/api/containments", json=containment_payload)
    assert frozen.status_code == 201, frozen.text
    assert frozen.json()["state"] == "frozen"
    assert frozen.json()["containment_state"] == "frozen"
    assert frozen.json()["status"] == "executed"
    assert frozen.json()["authoritative_containment_readback"] is True
    assert frozen.json()["authoritative_capital_state_applied"] is True
    assert frozen.json()["live_capital_side_effects"] is False

    listed = test_client.get(
        "/api/containments",
        params={"persona_id": "persona-alpha"},
    )
    assert listed.status_code == 200
    assert [item["containment_id"] for item in listed.json()] == ["containment-freeze-1"]
    allocation = test_client.get(
        "/api/allocations",
        params={"persona_id": "persona-alpha"},
    ).json()["items"][0]
    assert allocation["containment_state"] == "frozen"
    assert allocation["current_weight"] == 0.10

    replay = test_client.post("/api/containments", json=containment_payload)
    assert replay.status_code == 201
    assert replay.json()["containment_id"] == "containment-freeze-1"
    assert replay.json()["idempotent_replay"] is True

    forbidden = test_client.post(
        "/api/containments",
        json={
            **containment_payload,
            "containment_id": "containment-promote",
            "idempotency_key": "containment-promote",
            "request_hash": "request-containment-promote",
            "command_id": "cmd-containment-promote",
            "action": "promote_to_live",
        },
    )
    assert forbidden.status_code == 422
    assert "cannot promote or increase" in forbidden.json()["detail"]

    increase = test_client.post(
        "/api/containments",
        json={
            **containment_payload,
            "containment_id": "containment-increase",
            "idempotency_key": "containment-increase",
            "request_hash": "request-containment-increase",
            "command_id": "cmd-containment-increase",
            "target_weight": 0.11,
        },
    )
    assert increase.status_code == 422
    assert len(test_client.get("/api/containments").json()) == 1

    restarted = TestClient(_reload_capital_module().app)
    assert restarted.get("/api/containments").json()[0]["state"] == "frozen"
