"""Route-level security invariants for automatic Persona provisioning."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

import main as bff_main
from persona_provisioning import MemoryPersonaProvisioningStore
from read_store import ReadSurfaceStore
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt


@dataclass
class _RouteHarness:
    client: TestClient
    transport: FakeOwnerTransport
    store: MemoryPersonaProvisioningStore


@pytest.fixture()
def route_harness(tmp_path, monkeypatch: pytest.MonkeyPatch) -> _RouteHarness:
    read_store = ReadSurfaceStore(
        str(tmp_path / "read-surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    transport = FakeOwnerTransport()
    store = MemoryPersonaProvisioningStore()
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setattr(bff_main, "read_store", read_store)
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", store)
    monkeypatch.setattr(bff_main, "_PersonaOwnerHttpTransport", lambda: transport)
    monkeypatch.setattr(bff_main, "_register_persona_cron_required", _schedule_receipt)
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
