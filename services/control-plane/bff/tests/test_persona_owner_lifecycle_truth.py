"""Owner lifecycle truth: BFF reads keep the owner lifecycle_state verbatim and
keep the provisioning projection in provisioning_lifecycle_state."""

from __future__ import annotations

import pytest

from services.control_plane.bff.persona_provisioning import ProvisioningRecord
from services.control_plane.bff.personas import service as svc
from services.control_plane.bff.personas.cycle_views import cycle_owner_lifecycle

TENANT = "tenant-a"


@pytest.fixture(autouse=True)
def _persona_service_context():
    from types import SimpleNamespace
    from services.control_plane.bff.ports import create_in_memory_read_surface_ports
    service = svc.PersonaService(
        read_store=create_in_memory_read_surface_ports(), write_owner=SimpleNamespace(),
        ranking_write_owner=SimpleNamespace(), command_store=SimpleNamespace(),
    )
    token = svc._current_persona_service.set(service)
    try:
        yield
    finally:
        svc._current_persona_service.reset(token)


def _succeeded(persona_id: str) -> ProvisioningRecord:
    return ProvisioningRecord(
        tenant_id=TENANT,
        idempotency_key="k-" + persona_id,
        request_hash="h",
        normalized_name=persona_id,
        persona_id=persona_id,
        request_payload={"name": persona_id, "requested_by": "op"},
        state="succeeded",
        current_step="succeeded",
        references={
            "runtime_binding_id": "rb-1",
            "runtime_id": "rt-1",
            "authoritative_readback": {"ok": True},
        },
        result={"paper_running": True, "status": "paper_running"},
    )


def _owner(persona_id: str, state: str, scope: str = "test") -> dict:
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "name": persona_id,
        "tenant_id": TENANT,
        "status": "active",
        "lifecycle_state": state,
        "metadata": {"tenant_id": TENANT, "data_scope": scope},
    }


def _project(persona_id: str, owner_state: str, scope: str = "test") -> dict:
    record = _succeeded(persona_id)
    persona, _ = svc._persona_record_for_provisioning(
        record, payload=record.request_payload, owner="op",
        existing=_owner(persona_id, owner_state, scope),
    )
    return persona


def test_research_only_owner_with_succeeded_provisioning_stays_research_only():
    persona = _project("persona-d64835", "research_only")
    assert persona["lifecycle_state"] == "paper_running"  # legacy projection, unchanged
    assert persona["provisioning_lifecycle_state"] == "paper_running"
    assert persona["owner_lifecycle_state"] == "research_only"

    row = svc._project_persona_league_row(persona)
    assert row["owner_lifecycle_state"] == "research_only"
    assert row["provisioning_lifecycle_state"] == "paper_running"

    dto = svc._project_persona_dto(persona)
    assert dto["ownerLifecycleState"] == "research_only"
    assert dto["provisioningLifecycleState"] == "paper_running"
    assert dto["dataScope"] == "test"
    assert "lifecycleStatus" in dto and "state" in dto  # frontend fields kept


def test_paper_owner_trading_persona_reports_paper_owner():
    persona = _project("persona-trader", "paper_owner", scope="regular")
    row = svc._project_persona_league_row(persona)
    assert row["owner_lifecycle_state"] == "paper_owner"
    assert svc._project_persona_dto(persona)["ownerLifecycleState"] == "paper_owner"
    assert svc._project_persona_dto(persona)["dataScope"] == "regular"


def test_unprojected_owner_record_uses_its_own_lifecycle():
    row = svc._project_persona_league_row(_owner("p", "paper_owner"))
    assert row["owner_lifecycle_state"] == "paper_owner"
    assert row["provisioning_lifecycle_state"] is None


@pytest.mark.parametrize("owner_state,expected", [
    ("research_only", "research_only"),
    ("draft", "draft"),
    ("paper_owner", "paper_owner"),
    ("paper_running", "paper_running"),
    ("consultable", "consultable"),
])
def test_cycle_day_check_uses_owner_state(owner_state, expected):
    from acceptance.persona_day import DAILY_CYCLE_STATES
    persona = _project("persona-x", owner_state)
    assert cycle_owner_lifecycle(persona) == expected
    # registrar rule is paper_running/paper_owner/consultable; the evaluator set
    # omits consultable by design, so check the registrar set explicitly.
    eligible = expected in {"paper_running", "paper_owner", "consultable"}
    assert (expected in DAILY_CYCLE_STATES) == (eligible and expected != "consultable")


def test_cycle_day_check_rejects_inactive_owner():
    persona = _project("persona-y", "paper_owner")
    persona["status"] = "suspended"
    assert cycle_owner_lifecycle(persona) == "paper_owner:status=suspended"


@pytest.mark.parametrize("owner_state", ["research_only", "paper_owner"])
def test_read_persona_cycle_passes_owner_state_not_projection(monkeypatch, owner_state):
    from types import SimpleNamespace
    from services.control_plane.bff.personas import cycle_views

    seen = {}
    monkeypatch.setattr(cycle_views, "read_cycle_views",
                        lambda *a, **k: seen.update(k) or ({}, {}))
    store = SimpleNamespace(get_persona=lambda pid: _owner(pid, owner_state))
    service = svc.PersonaService(read_store=store, write_owner=SimpleNamespace(),
                                 ranking_write_owner=SimpleNamespace(), command_store=SimpleNamespace())
    service.read_persona_cycle("p1", TENANT, identity=None, authorization=None,
                               snapshot_at="t", lifecycle_state="paper_running")
    assert seen["lifecycle_state"] == owner_state
