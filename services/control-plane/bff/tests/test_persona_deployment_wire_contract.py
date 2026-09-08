"""DP-1: the coordinator's Deployment wire contract against the real owner.

The plan and dispatch bodies here are the exact bodies
``PersonaProvisioningCoordinator`` composes for a real provisioning record -- not
hand-written dictionaries.  They are then posted through the real Deployment
FastAPI routes, request models and handlers, with isolated Registry/Governance
reader fixtures injected as transport only, so Deployment keeps its own owner
read authority.  Durable owner state is re-read with fresh store instances.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.persona_provisioning_coordinator import (
    deterministic_provisioning_ids,
)
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _coordinator,
    _post_payload,
    _record_and_store,
    _schedule_receipt,
)
from services.governance.test_approval_authority import (
    SnapshotApprovalReader,
    approval_snapshot,
)


_TENANT_ID = "tenant-a"
_AUTH_HEADERS = {
    "Authorization": "Bearer deployment-wire-test:operator,service",
    "X-Tenant-Id": _TENANT_ID,
}


def _iso(delta: timedelta) -> str:
    return (
        (datetime.now(timezone.utc) + delta)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class _CoordinatorWire:
    """The real coordinator payloads plus the owner objects they reference."""

    def __init__(self) -> None:
        store, record = _record_and_store()
        transport = FakeOwnerTransport()
        _coordinator(store, transport, _schedule_receipt).coordinate(record)
        ids = deterministic_provisioning_ids(record)

        self.ids = ids
        self.record = record
        self.transport = transport
        self.plan_payload = _post_payload(transport, "/api/deployment/plans")
        self.dispatch_payload = _post_payload(
            transport,
            f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch",
        )
        entry = transport._owner_registry_entry(ids.strategy_artifact_id)
        # Deployment's Registry reader returns the owner-side entry, which
        # carries the owning tenant.  The coordinator never supplies it.
        entry["owner_tenant"] = _TENANT_ID
        self.registry_entry = entry
        self.approval = approval_snapshot(
            decision_id=ids.strategy_artifact_approval_decision_id,
            tenant_id=_TENANT_ID,
            target_type="registry_entry",
            target_id=ids.strategy_artifact_id,
            target_version=entry["version"],
            candidate_digest=entry["checksum"],
            capital_pool_id=ids.capital_pool_id,
            persona_id=record.persona_id,
            expires_at=_iso(timedelta(days=30)),
        )


@pytest.fixture()
def deployment_owner(tmp_path: Path):
    wire = _CoordinatorWire()
    tempdir = tmp_path
    governance_dir = tempdir / "governance"
    governance_dir.mkdir(parents=True, exist_ok=True)

    env_names = (
        "CAPITAL_DATA_DIR",
        "DEPLOYMENT_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_BINDING_STORE_PATH",
        "PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED",
        "PANTHEON_DEPLOYMENT_AUTH_MODE",
    )
    backup = {name: os.environ.get(name) for name in env_names}
    os.environ.update(
        {
            "CAPITAL_DATA_DIR": str(governance_dir),
            "DEPLOYMENT_DATA_DIR": str(governance_dir),
            "PANTHEON_GOVERNANCE_DATA_DIR": str(governance_dir),
            "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(
                tempdir / "runtime_bindings.json"
            ),
            "PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED": "false",
            "PANTHEON_DEPLOYMENT_AUTH_MODE": "permissive",
        }
    )

    sys.modules.pop("services.deployment.service", None)
    module = importlib.reload(importlib.import_module("services.deployment.service"))

    # Owner reads stay Deployment's own authority; only the transport is
    # injected, and only with objects an owner would actually return.
    registry_owner: dict[str, dict[str, Any]] = {
        wire.registry_entry["registry_id"]: deepcopy(wire.registry_entry)
    }
    governance_owner: dict[str, dict[str, Any]] = {
        wire.approval["decision_id"]: deepcopy(wire.approval)
    }

    def registry_reader(identity: str) -> dict[str, Any]:
        return deepcopy(registry_owner[identity])

    module.planner_service.registry_reader = registry_reader
    module.planner_service.approval_reader = SnapshotApprovalReader(
        lambda: deepcopy(governance_owner[wire.approval["decision_id"]])
    )

    client = TestClient(module.app, headers=_AUTH_HEADERS)
    try:
        yield {
            "wire": wire,
            "client": client,
            "module": module,
            "governance_dir": governance_dir,
            "registry_owner": registry_owner,
            "governance_owner": governance_owner,
        }
    finally:
        client.close()
        sys.modules.pop("services.deployment.service", None)
        for name, value in backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _fresh_plan_store(governance_dir: Path):
    from services.deployment.service import DeploymentPlanStore

    return DeploymentPlanStore(str(governance_dir / "deployment_plans.json"))


def _fresh_saga_store(governance_dir: Path):
    from services.deployment.service import DeploymentSagaStore

    return DeploymentSagaStore(str(governance_dir / "deployment_sagas.json"))


def test_coordinator_plan_and_dispatch_bodies_carry_no_owner_snapshots(
    deployment_owner,
) -> None:
    wire = deployment_owner["wire"]

    assert "registry_entry" not in wire.plan_payload
    assert "approval_decision" not in wire.plan_payload
    assert "registry_entry" not in wire.dispatch_payload

    # Exact identities, paper stage, rollback identity and receipts are kept.
    assert wire.plan_payload["plan_id"] == wire.ids.deployment_plan_id
    assert wire.plan_payload["registry_id"] == wire.ids.strategy_artifact_id
    assert wire.plan_payload["approval_decision_id"] == (
        wire.ids.strategy_artifact_approval_decision_id
    )
    assert wire.plan_payload["capital_pool_id"] == wire.ids.capital_pool_id
    assert wire.plan_payload["target_stage"] == "paper"
    assert wire.plan_payload["current_stage"] == "none"
    assert wire.plan_payload["rollback"] == {
        "target_artifact_id": wire.ids.baseline_strategy_artifact_id,
        "target_version": wire.ids.baseline_version,
        "action_type": "pause_then_replace",
        "reason": "Fail closed to the approved zero-capital paper baseline",
    }
    assert wire.plan_payload["metadata"]["requested_by"] == "operator-a"
    assert wire.plan_payload["metadata"]["provisioning_request_hash"] == (
        wire.record.request_hash
    )
    assert wire.dispatch_payload["saga_id"] == wire.ids.deployment_saga_id
    assert wire.dispatch_payload["metadata"]["persona_capital_binding_id"] == (
        wire.ids.persona_capital_binding_id
    )


def test_real_deployment_create_and_dispatch_persist_owner_state(
    deployment_owner,
) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]

    created = client.post("/api/deployment/plans", json=wire.plan_payload)
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["plan_id"] == wire.ids.deployment_plan_id
    assert plan["artifact_id"] == wire.ids.strategy_artifact_id
    assert plan["artifact_version"] == wire.registry_entry["version"]
    assert plan["strategy_id"] == wire.ids.strategy_id
    assert plan["capital_pool_id"] == wire.ids.capital_pool_id
    assert plan["target_stage"] == "paper"
    assert plan["rollback"]["target_artifact_id"] == (
        wire.ids.baseline_strategy_artifact_id
    )
    # The owner stamps its own authenticated actor and tenant, not the caller's
    # snapshot of them.
    assert plan["metadata"]["tenant_id"] == _TENANT_ID
    assert plan["metadata"]["authenticated_actor_id"] == "deployment-wire-test"

    dispatched = client.post(
        f"/api/deployment/plans/{wire.ids.deployment_plan_id}/dispatch",
        json=wire.dispatch_payload,
    )
    assert dispatched.status_code == 200, dispatched.text
    body = dispatched.json()
    assert body["replayed"] is False
    assert body["target_stage"] == "paper"
    assert body["execution_context"] == "paper"
    saga = body["deployment_saga"]["saga"]
    assert saga["saga_id"] == wire.ids.deployment_saga_id
    assert saga["plan_id"] == wire.ids.deployment_plan_id

    # Durable owner readback through fresh store instances.
    persisted_plan = _fresh_plan_store(governance_dir).get(wire.ids.deployment_plan_id)
    assert persisted_plan is not None
    assert persisted_plan.artifact_id == wire.ids.strategy_artifact_id
    assert persisted_plan.capital_pool_id == wire.ids.capital_pool_id
    assert str(persisted_plan.target_stage).endswith("paper")
    persisted_saga = _fresh_saga_store(governance_dir).get(wire.ids.deployment_saga_id)
    assert persisted_saga is not None
    assert persisted_saga.plan_id == wire.ids.deployment_plan_id


def test_dispatch_replay_is_idempotent_on_the_real_owner(deployment_owner) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]

    assert client.post("/api/deployment/plans", json=wire.plan_payload).status_code == 201
    path = f"/api/deployment/plans/{wire.ids.deployment_plan_id}/dispatch"
    first = client.post(path, json=wire.dispatch_payload)
    second = client.post(path, json=wire.dispatch_payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert len(_fresh_saga_store(governance_dir).list_all()) == 1


@pytest.mark.parametrize(
    ("route", "field"),
    [
        ("plans", "registry_entry"),
        ("plans", "approval_decision"),
        ("dispatch", "registry_entry"),
    ],
)
def test_embedded_owner_snapshots_are_rejected_with_no_owner_write(
    deployment_owner,
    route: str,
    field: str,
) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]
    snapshot = (
        deepcopy(wire.registry_entry)
        if field == "registry_entry"
        else deepcopy(wire.approval)
    )

    if route == "plans":
        response = client.post(
            "/api/deployment/plans",
            json={**wire.plan_payload, field: snapshot},
        )
    else:
        assert (
            client.post("/api/deployment/plans", json=wire.plan_payload).status_code
            == 201
        )
        response = client.post(
            f"/api/deployment/plans/{wire.ids.deployment_plan_id}/dispatch",
            json={**wire.dispatch_payload, field: snapshot},
        )

    assert response.status_code == 422, response.text
    detail = json.dumps(response.json())
    assert "extra_forbidden" in detail
    assert field in detail
    assert _fresh_saga_store(governance_dir).list_all() == []
    assert _fresh_saga_store(governance_dir).outbox_records() == []
    if route == "plans":
        assert _fresh_plan_store(governance_dir).get(wire.ids.deployment_plan_id) is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"expires_at": "2020-01-01T00:00:00Z"},
        {"revoked_at": "2026-09-01T00:00:00Z"},
        {"candidate_digest": "sha256:not-the-approved-artifact"},
        {"decision": "rejected"},
        {"superseded_by": "apv-some-later-decision"},
    ],
    ids=["expired", "revoked", "wrong_digest", "not_approved", "superseded"],
)
def test_plan_create_fails_closed_on_invalid_owner_approval(
    deployment_owner,
    mutation: dict[str, Any],
) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]
    deployment_owner["governance_owner"][wire.approval["decision_id"]].update(mutation)

    response = client.post("/api/deployment/plans", json=wire.plan_payload)

    assert response.status_code == 422, response.text
    assert _fresh_plan_store(governance_dir).get(wire.ids.deployment_plan_id) is None


def test_plan_create_fails_closed_on_cross_tenant_owner_objects(
    deployment_owner,
) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]

    foreign_registry = client.post(
        "/api/deployment/plans",
        json=wire.plan_payload,
        headers={**_AUTH_HEADERS, "X-Tenant-Id": "tenant-foreign"},
    )
    assert foreign_registry.status_code == 422, foreign_registry.text
    assert _fresh_plan_store(governance_dir).get(wire.ids.deployment_plan_id) is None

    # A Registry artifact owned by another tenant is refused for this tenant.
    deployment_owner["registry_owner"][wire.registry_entry["registry_id"]][
        "owner_tenant"
    ] = "tenant-foreign"
    foreign_artifact = client.post("/api/deployment/plans", json=wire.plan_payload)
    assert foreign_artifact.status_code == 422, foreign_artifact.text
    assert _fresh_plan_store(governance_dir).get(wire.ids.deployment_plan_id) is None


def test_plan_create_fails_closed_when_registry_state_is_not_approved(
    deployment_owner,
) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]
    deployment_owner["registry_owner"][wire.registry_entry["registry_id"]].update(
        artifact_state="candidate"
    )

    response = client.post("/api/deployment/plans", json=wire.plan_payload)

    assert response.status_code == 422, response.text
    assert _fresh_plan_store(governance_dir).get(wire.ids.deployment_plan_id) is None


@pytest.mark.parametrize("change", [
    {"expires_at": "2020-01-01T00:00:00Z"},
    {"revoked_at": "2026-09-01T00:00:00Z"},
    {"candidate_digest": "sha256:changed-after-plan"},
    {"tenant_id": "tenant-foreign"},
], ids=["expired", "revoked", "wrong-digest", "foreign-tenant"])
def test_dispatch_revalidates_current_approval_after_plan_creation(deployment_owner, change):
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    assert client.post("/api/deployment/plans", json=wire.plan_payload).status_code == 201
    deployment_owner["governance_owner"][wire.approval["decision_id"]].update(change)

    response = client.post(
        f"/api/deployment/plans/{wire.ids.deployment_plan_id}/dispatch",
        json=wire.dispatch_payload,
    )

    # Dispatch deliberately reports owner-authority refusal as 400; plan create
    # maps the same domain error to 422. Preserve the existing owner contract.
    assert response.status_code == 400, response.text
    assert _fresh_saga_store(deployment_owner["governance_dir"]).list_all() == []
    assert _fresh_saga_store(deployment_owner["governance_dir"]).outbox_records() == []


@pytest.mark.parametrize("owner", ["registry", "governance"])
def test_missing_reader_principal_fails_before_plan_persistence(
    deployment_owner, monkeypatch: pytest.MonkeyPatch, owner,
):
    """An owner fixture must not hide the known hosted missing-reader blocker."""
    planner = deployment_owner["module"].planner_service
    if owner == "registry":
        planner.registry_reader = None
        monkeypatch.setenv("DEPLOYMENT_REGISTRY_SERVICE_TOKEN", "")
    else:
        planner.approval_reader = None
        monkeypatch.setenv("DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN", "")

    wire = deployment_owner["wire"]
    response = deployment_owner["client"].post("/api/deployment/plans", json=wire.plan_payload)

    assert response.status_code == 422, response.text
    assert "principal" in response.text
    assert _fresh_plan_store(deployment_owner["governance_dir"]).get(
        wire.ids.deployment_plan_id
    ) is None


def test_dispatch_requires_an_authenticated_tenant(deployment_owner) -> None:
    wire = deployment_owner["wire"]
    client = deployment_owner["client"]
    governance_dir = deployment_owner["governance_dir"]
    assert client.post("/api/deployment/plans", json=wire.plan_payload).status_code == 201

    unauthenticated = client.post(
        f"/api/deployment/plans/{wire.ids.deployment_plan_id}/dispatch",
        json=wire.dispatch_payload,
        headers={"Authorization": "", "X-Tenant-Id": _TENANT_ID},
    )
    foreign_tenant = client.post(
        f"/api/deployment/plans/{wire.ids.deployment_plan_id}/dispatch",
        json=wire.dispatch_payload,
        headers={**_AUTH_HEADERS, "X-Tenant-Id": "tenant-foreign"},
    )

    assert unauthenticated.status_code == 401, unauthenticated.text
    assert foreign_tenant.status_code == 404, foreign_tenant.text
    assert _fresh_saga_store(governance_dir).list_all() == []
    assert _fresh_saga_store(governance_dir).outbox_records() == []
