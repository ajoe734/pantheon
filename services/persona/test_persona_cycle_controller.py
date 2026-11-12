"""Real controller/registrar/owner stores, with only OpenClaw HTTP simulated.

These local fixtures are not hosted, model or trading evidence.
"""
from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from integrations.openclaw.persona_ooda_runtime import CycleTurnOutput
from services.control_plane.cron.persona_cron_registrar import AdapterCronRuntime, PersonaCronRegistrar
from services.control_plane.cron.workflows import PERSONA_DAILY_CYCLE_WORKFLOW_ID as WORKFLOW_ID
from services.control_plane.persona.persona_strategy_pool import (
    AppendPoolVersionRequest, PersonaStrategyPoolStore,
)
from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.persona.cron_ooda_closure import append_ooda_packet
from services.persona.harness import HarnessSurface, PersistentHarnessOwner
from services.persona.persona_cycle_controller import (
    CycleOwners, PersonaCycleController, packet_identity, read_packets,
)
from services.persona.write_owner import CreatePersonaRequest, PersistentPersonaOwner
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.agora.store import AgoraEvidenceStore
from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
from services.agora.service import AgoraEvidenceWriteService
from services.control_plane.bff.agora.evidence.router import HumanEvidenceRequest


SURFACE = {
    "identity_inputs": ["Propose only, cite evidence."],
    "guardrails": {"allowed_tools": ["read"], "forbidden_solo_actions": ["admit", "harness_patch"]},
    "consult_policy": [{"action": "admit", "required_roles": ["risk_persona"]}],
    "tool_guidance": {"read": "Read governed evidence."}, "skills": [],
    "workflow": {"cadence_seconds": 86400, "steps": ["observe", "orient", "decide", "act", "learn"]},
    "cycle_thresholds": {"minimum_evidence_count": 1, "minimum_confidence": 0.8},
    "memory_retrieval_policy": {"scopes": ["own_private", "institutional"], "max_items": 8,
                                "minimum_confidence": 0.8},
}
OUTPUT = {"orient": "Insufficient paper market inputs; propose no action.",
          "proposals": [{"kind": "no_action", "reason": "No validated signal", "evidence_refs": []}],
          "memory_candidates": []}


@pytest.fixture
def system(tmp_path, monkeypatch):
    state = {"jobs": [], "runs": {}, "turns": 0, "outage": False, "paths": [], "answer": OUTPUT}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["paths"].append(self.path)
            status = 200
            if self.path == "/api/openclaw-adapter/gateway/cron":
                assert self.headers.get("X-Pantheon-Service-Token") == "fixture-adapter-token"
                method, params = body["method"], body["params"]
                if method == "cron.list":
                    data = {"jobs": copy.deepcopy(state["jobs"]), "hasMore": False}
                elif method == "cron.add":
                    data = {**params, "id": f"job-{len(state['jobs']) + 1}"}
                    state["jobs"].append(data)
                elif method == "cron.update":
                    job = next(j for j in state["jobs"] if j["id"] == params["id"])
                    job.update(params["patch"])
                    data = job
                elif method == "cron.remove":
                    state["jobs"] = [j for j in state["jobs"] if j["id"] != params["id"]]
                    data = {"removed": True}
                elif method == "cron.runs":
                    data = {"entries": copy.deepcopy(state["runs"].get(params["id"], [])), "hasMore": False}
                else:
                    raise AssertionError(method)
                result = {"status": "ok", "data": data}
            elif self.path == "/api/openclaw-adapter/agents/persona/memory/materialize":
                assert body["persona_id"] == "persona-cycle-test"
                result = {"status": "ok", "memory": {"persona_id": body["persona_id"],
                          "hit_count": 0, "source_refs": [], "generated_at": "2026-11-12T10:00:00Z"}}
            elif self.path == "/v1/responses":
                state["turns"] += 1
                assert body["model"] == "openclaw/persona-cycle-test"
                assert '"harness_version": 1' in body["input"]
                status = 503 if state["outage"] else 200
                result = {"output": [{"content": [{"type": "output_text", "text": json.dumps(state["answer"])}]}]}
            else:
                raise AssertionError(self.path)
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", base)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "fixture-gateway-token")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    owner.create(CreatePersonaRequest(actor_id="fixture-operator", persona_id="persona-cycle-test",
        name="Cycle", mandate="Consult only", tenant_id="tenant-a", workspace_ref="/fixture/persona-cycle-test"))
    raw = owner._records.get("persona-cycle-test")
    assert owner._records.compare_and_set("persona-cycle-test", raw, {**raw, "lifecycle_state": "paper_owner"})[0]
    PersistentHarnessOwner(owner).initialize("persona-cycle-test", HarnessSurface.model_validate(SURFACE), "fixture-operator")
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "control-plane" / "persona"))
    # Approved registry and telemetry owner fixtures, persisted through real
    # record/projection stores. They are test inputs, not generated gate proof.
    entries = AtomicJsonRecordStore(tmp_path / "registry.json")
    checksum = "sha256:" + "a" * 64
    entry = {"registry_id": "artifact-cycle", "artifact_state": "approved", "checksum": checksum,
             "owner_tenant": "tenant-a", "approval_decision_id": "fixture-baseline", "approved_at": "2026-11-01T00:00:00Z",
             "metadata": {"persona_id": "persona-cycle-test", "execution_context": "paper",
                          "persona_capital_binding_id": "pcb-cycle"}}
    assert entries.compare_and_set("artifact-cycle", None, entry)[0]
    pool = PersonaStrategyPoolStore(AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda rid, tenant: entries.get(rid), owner_lister=lambda pid, tenant: entries.list_all())
    pool.seed_from_owner_records("persona-cycle-test", "tenant-a", actor_id="fixture-operator")
    telemetry = RuntimeSummaryProjectionStore(path=str(tmp_path / "telemetry.json"))
    book = {"member_id": "artifact-cycle", "checksum": checksum,
            "source_ref": "fixture:owner-book", "since_inception": True,
            "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
            "dates": [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(30)],
            "returns": [.001, -.0005] * 15}
    assert telemetry.project_event({"event_id": "fixture-telemetry", "event_type": "heartbeat",
        "created_at": datetime.now(timezone.utc).isoformat(), "tenant_id": "tenant-a", "runtime_id": "runtime-cycle",
        "deployment_stage": "paper", "binding_id": "rb-cycle", "persona_capital_binding_id": "pcb-cycle",
        "capital_pool_id": "pool-cycle", "artifact_id": "artifact-cycle", "metrics": {"heartbeat": 1},
        "metadata": {"member_virtual_books": [book]}})
    state["telemetry"] = telemetry
    state["book"] = book
    owners = CycleOwners(owner, pool, env={"PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": base,
                                         "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": "fixture-adapter-token"},
                         telemetry_reader=lambda tenant: telemetry.list(tenant_id=tenant),
                         feedback_owner=AgoraEvidenceStore(AgoraDatasetStore(backend="memory")))
    registrar = PersonaCronRegistrar(AdapterCronRuntime(base, service_token="fixture-adapter-token"))
    controller = PersonaCycleController(registrar, owners, store_path=tmp_path / "packets.jsonl")
    thread.start()
    try:
        yield controller, state, owner, pool
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()


def run(number, status="ok"):
    return {"runId": f"run-{number}", "runAtMs": 1794477600000 + number * 86400000,
            "jobId": "job-1", "status": status}


def test_two_consecutive_cron_cycles_duplicate_provider_outage_and_restart(system):
    controller, gateway, owner, _ = system
    initial = controller.reconcile_once()
    assert initial["failures"] == []
    assert len(gateway["jobs"]) == 1
    assert gateway["jobs"][0]["schedule"]["expr"] == "0 10 * * 1-5"
    for number in (1, 2):
        gateway["runs"]["job-1"] = [run(number), *gateway["runs"].get("job-1", [])]
        result = controller.reconcile_once()
        assert result["failures"] == []
        assert result["personas"]["persona-cycle-test"]["stage"] == "closed"
        controller.reconcile_once()
        assert gateway["turns"] == number
    restarted = PersonaCycleController(controller.registrar, controller.owners, store_path=controller.path)
    restarted.reconcile_once()
    assert gateway["turns"] == 2
    gateway["outage"] = True
    gateway["runs"]["job-1"].insert(0, run(3))
    failed = restarted.reconcile_once()
    assert failed["personas"]["persona-cycle-test"]["stage"] == "observing"
    assert failed["personas"]["persona-cycle-test"]["status"] == "failed"
    restarted.reconcile_once()
    assert gateway["turns"] == 3
    packets = read_packets(controller.path)
    assert len(packets) == 3
    assert all(p["tenant_id"] == "tenant-a" and not p["act"]["live_capital_side_effects"] for p in packets.values())
    assert not any(any(word in path for word in ("broker", "order", "capital")) for path in gateway["paths"])


def test_registration_repairs_duplicates_drift_and_orphans_preserves_first_evaluation(system):
    controller, state, owner, _ = system
    controller.reconcile_once()
    original = copy.deepcopy(state["jobs"][0])
    state["jobs"][0]["schedule"]["expr"] = "* * * * *"
    state["jobs"].append({**copy.deepcopy(original), "id": "duplicate"})
    orphan = copy.deepcopy(original)
    event = json.loads(orphan["payload"]["text"])
    event["persona_id"] = "removed-persona"
    orphan.update(id="orphan", payload={"kind": "systemEvent", "text": json.dumps(event)})
    state["jobs"].append(orphan)
    controller.registrar.register_for_persona("persona-cycle-test", workflow_ids=["pantheon.persona.first-evaluation", "pantheon.review"])
    report = controller.reconcile_once()
    assert report["failures"] == []
    events = [json.loads(j["payload"]["text"]) for j in state["jobs"]]
    assert sorted(e["workflow_id"] for e in events) == [WORKFLOW_ID, "pantheon.persona.first-evaluation"]
    assert len([j for j in state["jobs"] if j["schedule"]["expr"] == "0 10 * * 1-5"]) == 1


def test_concurrent_run_consumers_buy_only_one_turn(system):
    controller, state, owner, _ = system
    persona = owner.list_json()[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        packets = list(executor.map(lambda _: controller.consume_run(persona, "job-1", run(1)), range(2)))
    assert packets[0] == packets[1]
    assert state["turns"] == 1
    assert len(read_packets(controller.path)) == 1


def test_restart_does_not_retry_an_uncertain_provider_or_risk_attempt(system):
    controller, state, owner, _ = system
    persona = owner.list_json()[0]

    class ProcessDeath(BaseException):
        pass

    def interrupted(*args):
        raise ProcessDeath()

    controller.turn_runner = interrupted
    with pytest.raises(ProcessDeath):
        controller.consume_run(persona, "job-1", run(1))
    restarted = PersonaCycleController(controller.registrar, controller.owners, store_path=controller.path)
    packet = restarted.consume_run(persona, "job-1", run(1))
    assert packet["status"] == "observing"
    assert packet["persona_cycle"]["failure_reason"] == "interrupted_cycle_outcome_unknown"
    assert state["turns"] == 0


def test_schema_failure_stops_at_observing_and_never_executes_model_commands(system):
    controller, state, owner, _ = system
    state["answer"] = {**OUTPUT, "proposals": [{"kind": "broker_order", "parameters": {"symbol": "2330"}}]}
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["status"] == "observing"
    assert "ValidationError" in packet["persona_cycle"]["failure_reason"]


def test_trading_pool_risk_owner_runs_when_observe_and_agent_are_unavailable(system):
    controller, state, owner, pool = system
    persona = {**owner.list_json()[0], "lifecycle_state": "paper_owner"}
    # Drop a required Observe input while keeping actual telemetry/risk books.
    raw = owner._records.get(persona["persona_id"])
    owner._records.compare_and_set(persona["persona_id"], raw, {**raw, "metadata": {}})
    persona = owner.list_json()[0]
    packet = controller.consume_run(persona, "job-1", run(1))
    assert packet["status"] == "observing"
    assert packet["persona_cycle"]["risk_result"]["receipt_ref"].endswith("/versions/2")
    assert len(pool.versions(persona["persona_id"], persona["tenant_id"])) == 2
    assert state["turns"] == 0
    controller.consume_run(persona, "job-1", run(1))
    assert len(pool.versions(persona["persona_id"], persona["tenant_id"])) == 2


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_packet_claim_storage_replays_both_hosted_shapes(system, tmp_path, suffix):
    controller, state, owner, _ = system
    controller.path = tmp_path / ("shape" + suffix)
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert read_packets(controller.path)[packet["packet_id"]] == packet
    controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert state["turns"] == 1


def test_run_identity_is_tenant_scoped_and_requires_real_identity(system):
    _, _, owner, _ = system
    persona = owner.list_json()[0]
    assert packet_identity(persona, "job-1", run(1)) != packet_identity({**persona, "tenant_id": "tenant-b"}, "job-1", run(1))
    with pytest.raises(ValueError):
        packet_identity(persona, "job-1", {"status": "ok"})


def test_human_teaching_refs_use_actual_owner_consent_and_scope_without_private_content(system):
    controller, _, owner, _ = system
    evidence_owner = controller.owners.feedback_owner
    service = AgoraEvidenceWriteService(evidence_owner)
    now = datetime.now(timezone.utc)
    for eid, tenant, pid in (("own", "tenant-a", "persona-cycle-test"),
                              ("foreign", "tenant-b", "persona-cycle-test"),
                              ("other-persona", "tenant-a", "another-persona")):
        service.submit("signal_feedback", HumanEvidenceRequest(
            evidence_id=eid, persona_id=pid, subject_ref="signal://fixture",
            content={"text": "Private human correction"}, captured_at=now, consent_granted=True),
            scope=SimpleNamespace(roles=["operator"], operator_id="human", user_id="human", tenant_id=tenant),
            idempotency_key=eid, admitted_at=now.isoformat())
    refs = evidence_owner.cycle_references(tenant_id="tenant-a", persona_id="persona-cycle-test")
    assert [r["evidence_id"] for r in refs] == ["own"]
    assert all(set(r) == {"evidence_id", "created_at"} for r in refs)
    assert evidence_owner.cycle_references(tenant_id="tenant-a", persona_id="persona-cycle-test", since=now + timedelta(seconds=1)) == []
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["observe"]["human_feedback_refs"] == ["/bff/agora/human-evidence/own"]
    assert "Private human correction" not in json.dumps(packet)


def test_teaching_cursor_preserves_same_timestamp_remainder(system):
    controller, _, owner, _ = system
    evidence_owner = controller.owners.feedback_owner
    service = AgoraEvidenceWriteService(evidence_owner)
    now = datetime.now(timezone.utc)
    for number in range(101):
        eid = f"same-time-{number:03d}"
        service.submit("signal_feedback", HumanEvidenceRequest(
            evidence_id=eid, persona_id="persona-cycle-test", subject_ref="signal://fixture",
            content={"text": "Learning fixture"}, captured_at=now, consent_granted=True),
            scope=SimpleNamespace(roles=["operator"], operator_id="human", user_id="human", tenant_id="tenant-a"),
            idempotency_key=eid, admitted_at=now.isoformat())
    # Model a transaction whose admissions share one database timestamp.
    for row in evidence_owner.inbox._inbox.values():
        row["created_at"] = now.isoformat()
    first = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert len(first["observe"]["human_feedback_refs"]) == 100
    assert first["persona_cycle"]["observe_bundle"]["human_feedback"]["status"] == "truncated"
    second = controller.consume_run(owner.list_json()[0], "job-1", run(2))
    assert second["observe"]["human_feedback_refs"] == ["/bff/agora/human-evidence/same-time-100"]


def test_management_cycle_health_and_private_packet_tenant_fencing(system):
    from services.control_plane.bff.core.owner_reads import selected_tenant
    from services.control_plane.bff.loop_inventory import list_loop_health_entries, project_persona_cycle_health
    from services.control_plane.bff.ports.ooda_management import OodaPacketsPort
    controller, _, owner, _ = system
    persona = owner.list_json()[0]
    packet = controller.consume_run(persona, "job-1", run(1))
    assert len(list_loop_health_entries()) == 12
    assert project_persona_cycle_health(persona, [packet])["stage"] == "closed"
    assert project_persona_cycle_health({**persona, "tenant_id": "tenant-b"}, [packet])["status"] == "unobserved"
    port = OodaPacketsPort(records_provider=lambda: [packet])
    for tenant, count in ((None, 0), ("tenant-b", 0), ("tenant-a", 1)):
        token = selected_tenant.set(tenant)
        try:
            assert len(port.list_ooda_packets()) == count
        finally:
            selected_tenant.reset(token)


def test_private_packet_routes_do_not_trust_foreign_tenant_header(system):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.control_loops.router import create_control_loops_router
    from services.control_plane.bff.ports.ooda_management import OodaPacketsPort
    controller, _, owner, _ = system
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(create_control_loops_router(read_surface=OodaPacketsPort(records_provider=lambda: [packet])))
    with TestClient(app) as client:
        assert len(client.get("/bff/ooda/packets", headers={"Authorization": "Bearer reader:viewer:mfa::tenant-a"}).json()["items"]) == 1
        foreign = {"Authorization": "Bearer reader:viewer:mfa::tenant-b", "X-Tenant-Id": "tenant-a"}
        assert client.get("/bff/ooda/packets", headers=foreign).json()["items"] == []
        detail = client.get("/bff/ooda/packets/" + packet["packet_id"], headers=foreign)
        assert detail.status_code in {200, 404}
        assert "persona_cycle" not in detail.json().get("data", {})


def test_provider_outage_still_removes_member_using_real_deterministic_drawdown_rule(system):
    controller, state, owner, pool = system
    book = {**state["book"], "returns": [.001, -.0005] * 14 + [.001, -.2]}
    state["telemetry"].project_event({"event_id": "drawdown-event", "event_type": "heartbeat",
        "created_at": datetime.now(timezone.utc).isoformat(), "tenant_id": "tenant-a", "runtime_id": "runtime-cycle",
        "deployment_stage": "paper", "binding_id": "rb-cycle", "persona_capital_binding_id": "pcb-cycle",
        "capital_pool_id": "pool-cycle", "artifact_id": "artifact-cycle", "metrics": {"heartbeat": 1},
        "metadata": {"member_virtual_books": [book]}})
    state["outage"] = True
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["status"] == "observing"
    assert state["turns"] == 1
    member = pool.current("persona-cycle-test", "tenant-a")["members"][0]
    assert member["stage"] == "removed"
    assert member["risk_weight"] == 0
    assert member["removal_reason"] == "own_high_water_drawdown"
