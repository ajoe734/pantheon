"""D13 production persona routes -> real owner clients/APIs/stores.

Only network transport is bridged to local ASGI owners. Fixture records are
local test data; these tests make no claim of hosted deployment evidence.
"""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import extract_identity
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.ports.ooda_management import OodaPacketsPort
from services.control_plane.ooda.ooda_loop_packet import OodaLoopPacket
from services.persona.cron_ooda_closure import append_ooda_packet
from services.persona.harness import HarnessSurface
from services.persona.test_harness import BASE, system  # noqa: F401
from services.persona.write_owner import CreatePersonaRequest
from services.control_plane.bff.tests.test_agora_committee_memory_owners import (  # noqa: F401
    SECRET, headers, stack,
)

PID, TENANT = "persona-a", "tenant-a"
PATHS = [f"/bff/personas/{PID}", f"/api/v1/operator/persona-management/{PID}"]


@pytest.fixture
def cycle_stack(stack, system, tmp_path, monkeypatch):  # noqa: F811
    _, calls, control, _, owners, consultation_store = stack
    owner_client, owner, harness, *_ = system
    monkeypatch.setenv("PERSONA_JWT_SECRET", SECRET)
    owner.create(CreatePersonaRequest(actor_id="alice", persona_id=PID, name="Cycle persona",
                                     mandate="Paper analysis", tenant_id=TENANT))
    raw = owner._records.get(PID)
    assert owner._records.compare_and_set(PID, raw, {**raw, "lifecycle_state": "paper_owner"})[0]
    harness.initialize(PID, HarnessSurface.model_validate(BASE), "alice")
    owners["persona.test"] = owner_client
    monkeypatch.setenv("PANTHEON_PERSONA_API_URL", "http://persona.test")
    for name in ("PANTHEON_OODA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "controller.jsonl"
    packet = OodaLoopPacket(packet_id="cycle-test", loop_type="paper_strategy", status="observing",
        environment="paper", persona_ids=[PID], created_at="2026-11-13T10:00:00Z",
        updated_at="2026-11-13T10:00:00Z").to_dict()
    packet.update(tenant_id=TENANT, producer={"cron_run_at_ms": 20}, persona_cycle={
        "phase": "terminal", "failure_reason": "agent_turn_unavailable", "harness_version": 1,
        "risk_result": {"status": "completed"}, "case_files": {"prompt": "PRIVATE_CASE_FILE"}})
    append_ooda_packet(path, packet)
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(path))
    # Use the real memory owner writer, including the harness-experience mapping.
    r = owners["memory.test"].post("/api/memory/writebacks/learn-feedback", headers=headers(), json={
        "source_event_type": "harness_experience", "source_event_id": "fixture-harness-experience",
        "tenant_id": TENANT, "write_authority": "persona-svc", "sponsor_persona_id": PID,
        "contributing_persona_ids": [PID], "summary": "Recorded paper outcome",
        "cue": "threshold change", "lesson": "Keep the evidence", "outcome": "helped",
        "confidence": .8, "harness_version": 1, "evidence_refs": ["cycle-test"],
        "tags": ["harness_experience"], "written_at": "2026-11-13T10:00:00Z",
    })
    assert r.status_code == 201, r.text
    store = create_in_memory_read_surface_ports(persona_capital_runtime_kwargs={"personas": [{
        "persona_id": PID, "tenant_id": TENANT, "name": "Cycle persona", "status": "active",
        "lifecycle_state": "paper_running", "metadata": {"tenant_id": TENANT}}]},
        ooda_management_kwargs={"ooda_port": OodaPacketsPort()})
    service = PersonaService(write_owner=store, read_store=store, ranking_write_owner=store,
        command_store=CommandStore(str(tmp_path / "commands.jsonl")),
        provisioning_store=MemoryPersonaProvisioningStore())
    app = FastAPI()
    app.include_router(create_personas_router(service=service, extract_identity_fn=extract_identity))
    calls.clear()
    return TestClient(app), calls, control, owners, path, packet, store, consultation_store


@pytest.mark.parametrize("route", PATHS)
def test_detail_reads_real_owners_and_controller_record(cycle_stack, route):
    client, calls, _, _, path, packet, *_ = cycle_stack
    before = path.read_bytes()
    response = client.get(route, headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    view = body["data"]["personaCycle"]
    assert view["cycle"]["health"]["status"] == "failed"
    assert view["cycle"]["health"]["stage"] == "observing"
    assert view["cycle"]["health"]["failure_reason"] == "agent_turn_unavailable"
    assert view["cycle"]["health"]["risk_status"] == "completed"
    assert view["cycle"]["packets"][0]["harness_version"] == 1
    assert "PRIVATE_CASE_FILE" not in response.text
    assert view["memorySummary"]["returned"] >= 2
    assert view["consultationMemos"][0]["memo_id"] == "memo-panel-a"
    assert view["harness"]["active_version"] == 1
    assert view["harness"]["versions"][0]["schema_version"] == "persona_harness.v1"
    assert "surface" not in view["harness"]["versions"][0]
    assert view["harnessExperiences"][0]["experience"]["outcome"] == "helped"
    assert all(body["meta"]["surfaces"][k]["status"] == "ok" for k in (
        "persona_cycles", "persona_memory_summary", "persona_consultation_memos", "persona_harness", "harness_experiences"))
    assert {host for _, host, *_ in calls} >= {"memory.test", "persona.test", "consultation.test", "governance.test"}
    assert all(method == "GET" for method, host, *_ in calls if host != "governance.test")
    assert path.read_bytes() == before


@pytest.mark.parametrize("owner,key", [("memory.test", "memorySummary"), ("persona.test", "harness"),
                                       ("consultation.test", "consultationMemos")])
def test_owner_outage_preserves_other_sections(cycle_stack, owner, key):
    client, _, control, *_ = cycle_stack
    control["offline"].add(owner)
    response = client.get(PATHS[0], headers=headers())
    assert response.status_code == 200, response.text
    view = response.json()["data"]["personaCycle"]
    assert view[key] is None
    assert view["cycle"]["health"]["status"] == "failed"
    surface = {"memorySummary": "persona_memory_summary", "harness": "persona_harness",
               "consultationMemos": "persona_consultation_memos"}[key]
    assert response.json()["meta"]["surfaces"][surface]["status"] == "unavailable"
    assert response.json()["meta"]["surfaces"][surface]["reason"]


@pytest.mark.parametrize("contents", [None, "broken json", ""])
def test_missing_corrupt_or_empty_controller_is_unavailable(cycle_stack, contents):
    client, _, _, _, path, *_ = cycle_stack
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    body = client.get(PATHS[0], headers=headers()).json()
    assert body["data"]["personaCycle"]["cycle"] is None
    assert body["meta"]["surfaces"]["persona_cycles"]["status"] == "unavailable"


def test_empty_owner_results_are_unavailable(cycle_stack):
    client, _, control, *_ = cycle_stack
    control["response"][("memory.test", "/api/memory/retrieve")] = {"hits": [], "authz": {"allowed": True}}
    control["response"][("consultation.test", "/api/consult/requests")] = []
    control["response"][("persona.test", f"/api/personas/{PID}/harness")] = {
        "status": "unavailable", "reason": "harness_not_initialized"}
    result = client.get(PATHS[0], headers=headers()).json()
    assert all(result["data"]["personaCycle"][k] is None for k in (
        "memorySummary", "harnessExperiences", "consultationMemos", "harness"))
    assert result["meta"]["surfaces"]["persona_memory_summary"]["reason"] == "no_memory_records"


@pytest.mark.parametrize("kind", ["memory", "memo", "harness"])
def test_misscoped_owner_response_is_never_exposed(cycle_stack, kind):
    client, _, control, owners, *_ = cycle_stack
    if kind == "memory":
        control["response"][("memory.test", "/api/memory/retrieve")] = {"authz": {"allowed": True}, "hits": [
            {"type": "persona", "entry": {"memory_id": "foreign", "tenant_id": "tenant-other",
             "persona_id": PID, "content": {"summary": "PRIVATE_FOREIGN"}}}]}
        key = "memorySummary"
    elif kind == "memo":
        control["response"][("consultation.test", "/api/consult/memos")] = [
            {"memo_id": "foreign", "request_id": "other-panel", "summary": "PRIVATE_FOREIGN"}]
        key = "consultationMemos"
    else:
        body = owners["persona.test"].get(f"/api/personas/{PID}/harness",
            headers={**headers(), "X-Tenant-Id": TENANT}).json()
        body["versions"][0]["persona_id"] = "PRIVATE_FOREIGN"
        control["response"][("persona.test", f"/api/personas/{PID}/harness")] = body
        key = "harness"
    response = client.get(PATHS[0], headers=headers())
    assert response.json()["data"]["personaCycle"][key] is None
    assert "PRIVATE_FOREIGN" not in response.text


def test_cycles_ignore_other_tenants_personas_and_live(cycle_stack):
    client, _, _, _, path, packet, *_ = cycle_stack
    for field, value in [("tenant_id", "other"), ("persona_ids", ["other"]), ("environment", "live")]:
        other = deepcopy(packet)
        other.update(packet_id="foreign-" + field, **{field: value}, producer={"cron_run_at_ms": 999})
        append_ooda_packet(path, other)
    view = client.get(PATHS[0], headers=headers()).json()["data"]["personaCycle"]
    assert [p["packet_id"] for p in view["cycle"]["packets"]] == ["cycle-test"]


def test_foreign_caller_refused_before_owner_reads(cycle_stack):
    client, calls, *_ = cycle_stack
    response = client.get(PATHS[0], headers=headers("tenant-other"))
    assert response.status_code == 404
    assert calls == []


def test_management_fleet_has_controller_health(cycle_stack):
    client, _, _, _, path, *_ = cycle_stack
    response = client.get("/bff/management/persona-fleet", headers=headers())
    assert response.status_code == 200, response.text
    row = next(r for r in response.json()["data"]["items"] if r["persona_id"] == PID)
    assert row["persona_cycle_health"]["last_packet_id"] == "cycle-test"
    assert row["persona_cycle_health"]["status"] == "failed"
    path.unlink()
    response = client.get("/bff/management/persona-fleet", headers=headers())
    row = next(r for r in response.json()["data"]["items"] if r["persona_id"] == PID)
    assert row["persona_cycle_health"]["status"] == "unavailable"
    assert row["persona_cycle_health"]["failure_reason"]


@pytest.mark.parametrize("phase,status,expected", [("claimed", "observing", "running"),
                                                   ("terminal", "closed", "completed")])
def test_latest_controller_run_controls_detail_and_management(cycle_stack, phase, status, expected):
    client, _, _, _, path, packet, *_ = cycle_stack
    latest = deepcopy(packet)
    latest.update(packet_id="cycle-latest", status=status, producer={"cron_run_at_ms": 100})
    latest["persona_cycle"].update(phase=phase, failure_reason=None)
    append_ooda_packet(path, latest)
    view = client.get(PATHS[0], headers=headers()).json()["data"]["personaCycle"]
    assert view["cycle"]["health"]["last_packet_id"] == "cycle-latest"
    assert view["cycle"]["health"]["status"] == expected
    assert [r["packet_id"] for r in view["cycle"]["packets"]] == ["cycle-latest", "cycle-test"]
    row = client.get("/bff/management/persona-fleet", headers=headers()).json()["data"]["items"][0]
    assert row["persona_cycle_health"] == view["cycle"]["health"]


@pytest.mark.parametrize("host,path,key", [
    ("memory.test", "/api/memory/retrieve", "memorySummary"),
    ("consultation.test", "/api/consult/requests", "consultationMemos"),
    ("persona.test", f"/api/personas/{PID}/harness", "harness"),
])
@pytest.mark.parametrize("payload", [None, {}, {"unexpected": "PRIVATE_INVALID_BODY"}])
def test_malformed_owner_data_fails_closed(cycle_stack, host, path, key, payload):
    client, _, control, *_ = cycle_stack
    control["response"][(host, path)] = payload
    response = client.get(PATHS[0], headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["data"]["personaCycle"][key] is None
    assert "PRIVATE_INVALID_BODY" not in response.text


def test_owner_configuration_missing_is_explicit(cycle_stack, monkeypatch):
    client, calls, *_ = cycle_stack
    for name in ("PANTHEON_PERSONA_API_URL", "PANTHEON_PERSONA_SERVICE_URL", "PANTHEON_MEMORY_API_URL",
                 "PANTHEON_MEMORY_SERVICE_URL", "MEMORY_URL", "CONSULTATION_SERVICE_TOKEN", "CONSULTATION_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    response = client.get(PATHS[0], headers=headers())
    assert response.status_code == 200, response.text
    view = response.json()["data"]["personaCycle"]
    assert view["cycle"] and all(view[k] is None for k in ("memorySummary", "harnessExperiences", "harness", "consultationMemos"))
    assert calls == []
