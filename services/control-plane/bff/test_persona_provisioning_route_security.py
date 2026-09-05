"""Route-level security invariants for automatic Persona provisioning."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.ports import ReadSurfacePorts
from test_loop_prod_per_001_provisioning import _provisioning_read_surface_double
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt


@dataclass
class _RouteHarness:
    client: TestClient
    transport: FakeOwnerTransport
    store: MemoryPersonaProvisioningStore


@pytest.fixture()
def route_harness(tmp_path, monkeypatch: pytest.MonkeyPatch) -> _RouteHarness:
    read_store = _provisioning_read_surface_double()
    transport = FakeOwnerTransport()
    store = MemoryPersonaProvisioningStore()
    from services.persona.runtime_profile import build_persona_runtime_profile

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_RECONCILER_ENABLED", "false")
    monkeypatch.setattr(bff_main, "read_store", read_store)
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", store)
    monkeypatch.setattr(bff_main, "_PersonaOwnerHttpTransport", lambda: transport)
    monkeypatch.setattr(bff_main, "_register_persona_cron_required", _schedule_receipt)
    monkeypatch.setattr(
        bff_main,
        "build_persona_runtime_profile",
        build_persona_runtime_profile,
        raising=False,
    )
    monkeypatch.setattr(bff_main, "_PERSONA_BFF_OVERLAY", {})
    monkeypatch.setattr(bff_main, "_STRATEGY_PERSONA_BFF_IDEMPOTENCY", {})
    return _RouteHarness(TestClient(bff_main.app), transport, store)


def _headers(operator: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {operator}:operator",
        "Idempotency-Key": key,
    }


def test_authenticated_actor_replaces_all_client_actor_assertions(
    route_harness: _RouteHarness,
) -> None:
    response = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-real", "persona-actor-1"),
        json={
            "name": "Actor Bound Persona",
            "risk": "low",
            "requested_by": "operator-evil",
            "created_by": "operator-evil",
            "actor_id": "operator-evil",
        },
    )

    assert response.status_code == 201, response.text
    persona_id = response.json()["data"]["id"]
    durable = route_harness.store.get("pantheon-dev", "persona-actor-1")
    assert durable is not None
    assert durable.request_payload["requested_by"] == "operator-real"
    assert "operator-evil" not in json.dumps(durable.request_payload, sort_keys=True)
    assert "operator-evil" not in json.dumps(route_harness.transport.calls, sort_keys=True)
    persona = bff_main.read_store.get_persona(persona_id)
    assert persona is not None
    assert persona["created_by"] == "operator-real"
    assert persona["metadata"]["owner"] == "operator-real"


def test_cross_operator_idempotency_replay_conflicts_without_owner_overwrite(
    route_harness: _RouteHarness,
) -> None:
    payload = {"name": "Immutable Owner Persona", "risk": "low"}
    first = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-owner-1"),
        json=payload,
    )
    assert first.status_code == 201, first.text
    persona_id = first.json()["data"]["id"]

    replay = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-b", "persona-owner-1"),
        json=payload,
    )

    assert replay.status_code == 409, replay.text
    persona = bff_main.read_store.get_persona(persona_id)
    assert persona is not None
    assert persona["created_by"] == "operator-a"
    assert persona["metadata"]["owner"] == "operator-a"


@pytest.mark.parametrize("risk", ["medium", "high", "critical"])
def test_non_low_risk_is_rejected_before_any_owner_mutation(
    route_harness: _RouteHarness,
    risk: str,
) -> None:
    response = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", f"persona-risk-{risk}"),
        json={"name": f"Risk {risk}", "risk": risk},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["precondition_failed"] == "risk_level"
    assert route_harness.transport.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "paper_running"),
        ("lifecycle_state", "paper_running"),
        ("runtimeBindingId", "rb-forged"),
        ("capitalMode", "live"),
        ("owner", "operator-evil"),
    ],
)
def test_patch_cannot_bypass_server_managed_lifecycle(
    route_harness: _RouteHarness,
    field: str,
    value: str,
) -> None:
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", f"persona-patch-create-{field}"),
        json={"name": f"Patch Guard {field}", "risk": "low"},
    )
    assert created.status_code == 201, created.text
    persona_id = created.json()["data"]["id"]

    patched = route_harness.client.patch(
        f"/bff/personas/{persona_id}",
        headers=_headers("operator-a", f"persona-patch-{field}"),
        json={field: value},
    )

    assert patched.status_code == 422, patched.text
    persona = bff_main.read_store.get_persona(persona_id)
    assert persona is not None
    assert persona["lifecycle_state"] == "provisioning"
    assert persona["metadata"]["owner"] == "operator-a"


def test_persona_get_and_list_are_pure_reads(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-pure-read-create"),
        json={"name": "Pure Read Persona", "risk": "low"},
    )
    assert created.status_code == 201, created.text
    persona_id = created.json()["data"]["id"]
    before = bff_main.read_store.get_persona(persona_id)

    def forbidden_reconcile(*_args, **_kwargs):
        raise AssertionError("read route must not reconcile or mutate Persona lifecycle")

    monkeypatch.setattr(
        bff_main,
        "_evaluate_persona_provisioning_status",
        forbidden_reconcile,
    )
    detail = route_harness.client.get(
        f"/bff/personas/{persona_id}",
        headers={"Authorization": "Bearer viewer-a:viewer"},
    )
    listed = route_harness.client.get(
        "/bff/personas?page_size=1",
        headers={"Authorization": "Bearer viewer-a:viewer"},
    )

    assert detail.status_code == 200, detail.text
    assert listed.status_code == 200, listed.text
    assert bff_main.read_store.get_persona(persona_id) == before


def test_persona_list_projects_only_requested_page(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(5):
        bff_main.read_store.create_persona(
            persona_id=f"persona-page-{index}",
            name=f"Page Persona {index}",
            actor_id="operator-a",
            lifecycle_state="provisioning",
            metadata={"tenant_id": "pantheon-dev", "archetype": "generalist"},
        )
    original = bff_main._project_persona_list_records
    projected_sizes: list[int] = []

    def capture(records):
        projected_sizes.append(len(records))
        return original(records)

    monkeypatch.setattr(bff_main, "_project_persona_list_records", capture)
    response = route_harness.client.get(
        "/bff/personas?page_size=1",
        headers={"Authorization": "Bearer viewer-a:viewer"},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 1
    assert projected_sizes == [1]


def test_patch_cache_is_namespaced_after_tenant_authorization(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bff_main,
        "_bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {
            "id": "tenant-a" if identity.operator_id == "operator-a" else "tenant-b"
        },
    )
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-tenant-cache-create"),
        json={"name": "Tenant Cache Persona", "risk": "low"},
    )
    assert created.status_code == 201, created.text
    persona_id = created.json()["data"]["id"]

    first = route_harness.client.patch(
        f"/bff/personas/{persona_id}",
        headers=_headers("operator-a", "shared-patch-key"),
        json={"successRate": 0.25},
    )
    foreign = route_harness.client.patch(
        f"/bff/personas/{persona_id}",
        headers=_headers("operator-b", "shared-patch-key"),
        json={"successRate": 0.25},
    )

    assert first.status_code == 200, first.text
    assert foreign.status_code == 404, foreign.text


def test_patch_overlay_and_cached_replay_preserve_tenant_snapshot(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bff_main,
        "_bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {"id": "tenant-a"},
    )
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-overlay-tenant-create"),
        json={"name": "Overlay Tenant Persona", "risk": "low"},
    )
    persona_id = created.json()["data"]["id"]

    first = route_harness.client.patch(
        f"/bff/personas/{persona_id}",
        headers=_headers("operator-a", "persona-overlay-tenant-patch"),
        json={"successRate": 0.5},
    )
    first_body = first.json()
    assert first_body["data"]["tenantId"] == "tenant-a"
    assert bff_main._PERSONA_BFF_OVERLAY[persona_id]["tenantId"] == "tenant-a"

    bff_main._PERSONA_BFF_OVERLAY[persona_id]["state"] = "failed"
    replay = route_harness.client.patch(
        f"/bff/personas/{persona_id}",
        headers=_headers("operator-a", "persona-overlay-tenant-patch"),
        json={"successRate": 0.5},
    )

    assert replay.status_code == 200, replay.text
    assert replay.json() == first_body
    assert replay.json()["data"]["state"] != "failed"


def test_patch_preserves_newer_canonical_lifecycle_over_stale_overlay(
    route_harness: _RouteHarness,
) -> None:
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-stale-overlay-create"),
        json={"name": "Stale Overlay Guard", "risk": "low"},
    )
    assert created.status_code == 201, created.text
    persona_id = created.json()["data"]["id"]
    bff_main.read_store.update_persona(
        persona_id,
        lifecycle_state="paper_running",
        metadata={"paper_runtime_state": "running"},
    )
    bff_main._PERSONA_BFF_OVERLAY[persona_id]["state"] = "provisioning"

    patched = route_harness.client.patch(
        f"/bff/personas/{persona_id}",
        headers=_headers("operator-a", "persona-stale-overlay-patch"),
        json={"successRate": 0.75},
    )

    assert patched.status_code == 200, patched.text
    canonical = bff_main.read_store.get_persona(persona_id)
    assert canonical is not None
    assert canonical["lifecycle_state"] == "paper_running"


def test_reconcile_route_requires_operator_and_tenant_scope(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bff_main,
        "_bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {
            "id": "tenant-a" if identity.operator_id != "operator-b" else "tenant-b"
        },
    )
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-reconcile-security-create"),
        json={"name": "Reconcile Security", "risk": "low"},
    )
    assert created.status_code == 201, created.text
    persona_id = created.json()["data"]["id"]

    unauthenticated = route_harness.client.post(
        f"/bff/personas/{persona_id}/provisioning/reconcile"
    )
    viewer = route_harness.client.post(
        f"/bff/personas/{persona_id}/provisioning/reconcile",
        headers={"Authorization": "Bearer viewer-a:viewer"},
    )
    foreign = route_harness.client.post(
        f"/bff/personas/{persona_id}/provisioning/reconcile",
        headers={"Authorization": "Bearer operator-b:operator"},
    )

    assert unauthenticated.status_code == 401
    assert viewer.status_code == 403
    assert foreign.status_code == 404


def test_reconcile_route_reports_degraded_owner_dependency(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = route_harness.client.post(
        "/bff/personas",
        headers=_headers("operator-a", "persona-reconcile-degraded-create"),
        json={"name": "Reconcile Degraded", "risk": "low"},
    )
    assert created.status_code == 201, created.text
    persona_id = created.json()["data"]["id"]
    monkeypatch.setattr(
        bff_main,
        "_get_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("deployment unavailable")
        ),
    )

    reconciled = route_harness.client.post(
        f"/bff/personas/{persona_id}/provisioning/reconcile",
        headers={"Authorization": "Bearer operator-a:operator"},
    )

    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["meta"]["status"] == "degraded"
    assert "deployment" in reconciled.json()["meta"]["degraded_dependencies"]


def test_controller_isolates_one_malformed_persona_from_later_records(
    route_harness: _RouteHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {"persona_id": "persona-bad", "lifecycle_state": "provisioning"},
        {"persona_id": "persona-good", "lifecycle_state": "provisioning"},
    ]
    evaluated: list[str] = []
    monkeypatch.setattr(bff_main, "_list_persona_records", lambda: records)
    monkeypatch.setattr(
        bff_main,
        "_persona_readback_snapshot",
        lambda: ({}, None, []),
    )

    def evaluate(persona_id, *_args, **_kwargs):
        evaluated.append(persona_id)
        if persona_id == "persona-bad":
            raise ValueError("malformed metadata")
        return "provisioning"

    monkeypatch.setattr(bff_main, "_evaluate_persona_provisioning_status", evaluate)

    assert bff_main._reconcile_persona_provisioning_once() == 1
    assert evaluated == ["persona-bad", "persona-good"]
