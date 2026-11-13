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

from integrations.openclaw.persona_memory_bridge import DEFAULT_MEMORY_QUERY, materialize_persona_memory_from_api
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
          "proposals": [{"kind": "no_action", "reason": "No validated signal", "evidence_refs": [],
                         "none_applicable_reason": "No case-file item bears on staying flat today."}],
          "memory_candidates": []}
TENANT_A = "tenant-a"


def _private(mid, pid, summary, tags):
    return {"memory_id": f"pmem-{mid:08d}-1111-1111-1111-111111111111", "persona_id": pid,
            "memory_type": "strategy_lesson", "content": {"summary": summary, "tags": tags},
            "source_event_type": "postmortem_published", "source_event_id": f"PM-{mid}",
            "written_at": "2026-06-09T01:00:00Z", "write_authority": "incident-svc",
            "relevance_scope": "persona_private", "reuse_count": 0, "tenant_id": TENANT_A}


def _institutional(mid, headline, tags, family):
    return {"entry_id": f"mem-{mid:08d}-1111-1111-1111-111111111111", "knowledge_type": "research_finding",
            "content": {"headline": headline, "body": headline, "tags": tags},
            "source_event_type": "research_task_completed", "source_event_id": f"rt-{mid}",
            "written_at": "2026-04-20T04:00:00Z", "write_authority": "research-svc",
            "scope": "strategy_family", "scope_filter": family, "contributing_persona_ids": [],
            "reuse_count": 0}


@pytest.fixture(scope="module")
def memory_service(tmp_path_factory):
    """The production Memory Plane app over real HTTP; entries go in through its write routes."""
    import importlib
    import os
    import socket
    import time
    import uvicorn
    from services.knowledge import case_file as cf
    tmp = tmp_path_factory.mktemp("memory")
    keys = ("PANTHEON_MEMORY_STORE", "PANTHEON_PERSONA_MEMORY_STORE", "PANTHEON_MEMORY_STORE_BACKEND",
            "PANTHEON_PERSONA_MEMORY_STORE_BACKEND", "PANTHEON_MEMORY_AUTHZ_MODE", "PANTHEON_RUNTIME_AUTH_MODE")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update({"PANTHEON_MEMORY_STORE": str(tmp / "inst.json"),
                       "PANTHEON_PERSONA_MEMORY_STORE": str(tmp / "persona.json"),
                       "PANTHEON_MEMORY_STORE_BACKEND": "json", "PANTHEON_PERSONA_MEMORY_STORE_BACKEND": "json",
                       "PANTHEON_MEMORY_AUTHZ_MODE": "local"})
    os.environ.pop("PANTHEON_RUNTIME_AUTH_MODE", None)
    memory = importlib.import_module("services.memory.main")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(memory.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "memory service did not start"
    endpoint = cf.OwnerEndpoint(f"http://127.0.0.1:{port}")
    post = lambda path, body: cf.http_json(endpoint, path, tenant_id=TENANT_A, method="POST", body=body)
    post("/api/memory/entries", _institutional(1, "momentum 2330 sizing institutional lesson",
                                              ["family:momentum", "underlying:2330"], "momentum"))
    post("/api/memory/entries", _institutional(2, "carry 2882 utilisation institutional lesson",
                                              ["family:carry", "underlying:2882"], "carry"))
    post("/api/memory/persona-entries", _private(1, "persona-cycle-test", "ONE-PRIVATE momentum 2330 sizing",
                                                 ["family:momentum", "underlying:2330"]))
    post("/api/memory/persona-entries", _private(2, "persona-cycle-two", "TWO-PRIVATE carry 2882 low utilisation",
                                                 ["family:carry", "underlying:2882", "shortfall:low_risk_budget_utilisation"]))
    # Each persona's private memory matches the OTHER persona's query terms and tags.
    post("/api/memory/persona-entries", _private(3, "persona-cycle-two", "TWO-SECRET momentum 2330 sizing",
                                                 ["family:momentum", "underlying:2330"]))
    post("/api/memory/persona-entries", _private(4, "persona-cycle-test", "ONE-SECRET carry 2882 utilisation",
                                                 ["family:carry", "underlying:2882"]))
    try:
        yield endpoint.base_url
    finally:
        server.should_exit = True
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture
def system(tmp_path, monkeypatch, memory_service):
    state = {"jobs": [], "runs": {}, "turns": 0, "outage": False, "paths": [], "answer": OUTPUT,
             "answer_fn": None, "materialize": [], "prompts": {}, "obs": {}, "workspaces": tmp_path / "ws"}

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
                # The real bridge against the real Memory Plane, with the request's own query.
                state["materialize"].append(body)
                materialized = materialize_persona_memory_from_api(
                    memory_api_url=memory_service, persona_id=body["persona_id"],
                    workspace=str(state["workspaces"] / body["persona_id"]), query=body["query"], tags=body["tags"])
                result = {"status": "ok", "memory": materialized.to_dict()}
            elif self.path == "/v1/responses":
                state["turns"] += 1
                pid = body["model"].split("/", 1)[1]
                assert '"harness_version": 1' in body["input"]
                state["prompts"][pid] = body["input"]
                status = 503 if state["outage"] else 200
                answer = state["answer_fn"](pid, body["input"]) if state["answer_fn"] else state["answer"]
                result = {"output": [{"content": [{"type": "output_text", "text": json.dumps(answer)}]}]}
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
                          "persona_capital_binding_id": "pcb-cycle",
                          "strategy_artifact": {"strategy_id": "momentum", "parameters": {"symbols": ["2330.TW"]}}}}
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
    state["entries"] = entries
    state["pool_store"] = pool
    state["book"] = book
    owners = CycleOwners(owner, pool, env={"PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": base,
                                         "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": "fixture-adapter-token",
                                         "PANTHEON_MEMORY_API_URL": memory_service},
                         telemetry_reader=lambda tenant: [
                             {**row, "pool_observations": state["obs"].get(row["persona_capital_binding_id"], [])}
                             for row in telemetry.list(tenant_id=tenant)],
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


@pytest.mark.parametrize("authorization,requested_tenant,visible", [
    ("Bearer reader:viewer:mfa::tenant-a", None, True),
    ("Bearer reader:viewer:mfa::tenant-b", "tenant-a", False),
    ("Bearer reader:viewer:mfa", None, False),
    ("Bearer reader:viewer:mfa", "tenant-a", False),
    ("Bearer reader:viewer:mfa::tenant-b", None, False),
])
def test_private_packet_routes_do_not_trust_foreign_tenant_header(
    system, authorization, requested_tenant, visible,
):
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
        headers = {"Authorization": authorization}
        if requested_tenant:
            headers["X-Tenant-Id"] = requested_tenant
        listing = client.get("/bff/ooda/packets", headers=headers)
        assert listing.status_code == 200
        assert [p["packet_id"] for p in listing.json()["items"]] == ([packet["packet_id"]] if visible else [])
        detail = client.get("/bff/ooda/packets/" + packet["packet_id"], headers=headers)
        # Existing detail routes return an unavailable envelope for a missing
        # record. The private payload must be absent from that envelope.
        assert detail.status_code == 200
        if not visible:
            data = detail.json()["data"]
            assert set(data) == {"id", "status", "readSurface", "message"}
            assert data["status"] == "degraded"
        assert ("persona_cycle" in (detail.json().get("data") or {})) == visible
        # A rejected request cannot leave tenant admission in a later request.
        unscoped = client.get("/bff/ooda/packets", headers={"Authorization": "Bearer reader:viewer:mfa"})
        assert unscoped.json()["items"] == []


@pytest.mark.parametrize("enabled", [False, True])
def test_deployed_persona_lifespan_consumes_cycle_and_stops(system, monkeypatch, enabled):
    """Real app/controller/SDK startup; only HTTP and the DB write boundary are fixtures."""
    import importlib
    import importlib.util
    import sys
    from fastapi.testclient import TestClient
    import services.persona.write_owner as write_owner

    controller, state, owner, pool = system
    persona = owner._records.get("persona-cycle-test")
    assert owner._records.compare_and_set("persona-cycle-test", persona,
        {**persona, "lifecycle_state": "consultable", "strategy_family": "momentum"})[0]
    state["runs"]["job-1"] = [run(1)]
    base = controller.owners.env["PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL"]
    for key, value in {
        "PERSONA_DAILY_CYCLE_ENABLED": str(enabled).lower(),
        "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": base,
        "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": "fixture-adapter-token",
        "PANTHEON_BFF_OODA_PACKET_STORE": str(controller.path),
        "DATABASE_URL": "postgresql://unused-fixture/no-network",
        "PANTHEON_MEMORY_API_URL": controller.owners.env["PANTHEON_MEMORY_API_URL"],
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(write_owner, "build_persona_owner", lambda: owner)
    records = []
    written = threading.Event()

    async def capture_record(self, record):
        records.append(copy.deepcopy(record))
        written.set()

    store_module = importlib.import_module("services.loop-control.store")
    monkeypatch.setattr(store_module.LoopControllerStore, "upsert_record", capture_record)
    path = Path(__file__).resolve().parents[1] / "control-plane/persona/main.py"
    spec = importlib.util.spec_from_file_location("persona_daily_cycle_lifespan_test", path)
    service = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, service)
    spec.loader.exec_module(service)
    monkeypatch.setattr(service, "POOL_STORE", pool)
    with TestClient(service.app) as client:
        assert client.get("/healthz").status_code == 200
        if enabled:
            assert written.wait(timeout=10), "controller did not publish its first reconciliation"
        else:
            assert state["paths"] == []
    # Exiting the actual lifespan awaits the controller and releases its task.
    assert state["turns"] == int(enabled)
    assert len(records) == int(enabled)
    if enabled:
        packet = next(iter(read_packets(controller.path).values()))
        assert packet["status"] == "closed"
        assert records[0]["tenant_id"] == "tenant-a"
        assert records[0]["loop_id"] == "per_persona_ooda"
        assert records[0]["controller_name"] == "persona_cycle_controller"
        assert records[0]["payload"]["persona_cycle_health"]["persona-cycle-test"]["last_packet_id"] == packet["packet_id"]
        assert "last_success_at" not in records[0]


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


class _StubLessonRegistrar:
    """Test double for the memory service lesson registry (these tests do not exercise registration)."""
    configured = True

    def register(self, candidates, authorization):
        return {"registered": [c["lesson_candidate_id"] for c in candidates], "already_registered": []}


@pytest.fixture(autouse=True)
def _stub_lesson_registrar(monkeypatch):
    import services.persona.write_owner as write_owner
    monkeypatch.setattr(write_owner, "MemoryLessonRegistrar", _StubLessonRegistrar)


# --- decision-specific retrieval and case files (PERSONA-CYCLE-CASE-FILE-20261113) ---------------

def _add_second_persona(state, owner, pool):
    """A second persona with its own family, underlying, binding and a low-utilisation pool window."""
    checksum = "sha256:" + "b" * 64
    entry = {"registry_id": "artifact-two", "artifact_state": "approved", "checksum": checksum,
             "owner_tenant": TENANT_A, "approval_decision_id": "fixture-baseline-two", "approved_at": "2026-11-01T00:00:00Z",
             "metadata": {"persona_id": "persona-cycle-two", "execution_context": "paper",
                          "persona_capital_binding_id": "pcb-two",
                          "strategy_artifact": {"strategy_id": "carry", "parameters": {"symbols": ["2882.TW"]}}}}
    assert state["entries"].compare_and_set("artifact-two", None, entry)[0]
    owner.create(CreatePersonaRequest(actor_id="fixture-operator", persona_id="persona-cycle-two", name="Two",
        mandate="Consult only", tenant_id=TENANT_A, workspace_ref="/fixture/persona-cycle-two"))
    raw = owner._records.get("persona-cycle-two")
    assert owner._records.compare_and_set("persona-cycle-two", raw, {**raw, "lifecycle_state": "paper_owner"})[0]
    PersistentHarnessOwner(owner).initialize("persona-cycle-two", HarnessSurface.model_validate(SURFACE), "fixture-operator")
    # The fixture lister is unfiltered; the real owner lister returns only the persona's own artifacts.
    pool._lister = lambda pid, tenant: [e for e in state["entries"].list_all()
                                        if (e.get("metadata") or {}).get("persona_id") == pid]
    pool.seed_from_owner_records("persona-cycle-two", TENANT_A, actor_id="fixture-operator")
    dates = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    book = {**state["book"], "member_id": "artifact-two", "checksum": checksum, "dates": dates,
            "returns": [.002, -.001] * 15}
    assert state["telemetry"].project_event({"event_id": "fixture-telemetry-two", "event_type": "heartbeat",
        "created_at": datetime.now(timezone.utc).isoformat(), "tenant_id": TENANT_A, "runtime_id": "runtime-two",
        "deployment_stage": "paper", "binding_id": "rb-two", "persona_capital_binding_id": "pcb-two",
        "capital_pool_id": "pool-two", "artifact_id": "artifact-two", "metrics": {"heartbeat": 1},
        "metadata": {"member_virtual_books": [book]}})
    # Own utilisation of 10% against a 60% floor: a real, known risk-budget shortfall.
    state["obs"]["pcb-two"] = [{"date": d, "source_ref": "fixture:obs", "gross_notional": 1.0, "gross_limit": 10.0,
                                "drawdown": 0.001, "drawdown_limit": 0.1} for d in dates[-20:]]
    return next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-two")


def _cite_first_lesson(pid, prompt):
    case = json.loads(prompt.rsplit("\n", 1)[1])
    lessons = [i["item_id"] for i in case["groups"]["lessons"]["items"]]
    assert lessons, "case file carried no lessons into the turn"
    return {"orient": "Lessons reviewed.", "memory_candidates": [], "proposals": [
        {"kind": "no_action", "reason": "Stay flat.", "evidence_refs": [], "case_file_item_ids": lessons[:1]}]}


def _lesson_summaries(packet):
    case = next(iter(packet["persona_cycle"]["case_files"].values()))
    return {i["source_id"]: i["excerpt"] for i in case["groups"]["lessons"]["items"]}


def test_two_personas_with_different_shortfalls_get_different_lessons_and_never_each_others_private_memory(system):
    controller, state, owner, pool = system
    two = _add_second_persona(state, owner, pool)
    one = next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-test")
    state["answer_fn"] = _cite_first_lesson
    packets = {}
    for persona in (one, two):
        runs = {**run(1), "runId": "run-" + persona["persona_id"]}
        packets[persona["persona_id"]] = controller.consume_run(persona, "job-1", runs)
    one_packet, two_packet = packets["persona-cycle-test"], packets["persona-cycle-two"]
    for packet in packets.values():
        assert packet["persona_cycle"]["failure_reason"] is None and packet["status"] == "closed"

    # The cycle's own query: shortfall + families + underlyings, never the generic default.
    queries = {b["persona_id"]: b for b in state["materialize"]}
    assert len(state["materialize"]) == 2
    assert all(b["query"] != DEFAULT_MEMORY_QUERY for b in queries.values())
    assert "momentum" in queries["persona-cycle-test"]["query"] and "2330" in queries["persona-cycle-test"]["query"]
    assert "shortfall:health_inputs_unknown" in queries["persona-cycle-test"]["tags"]
    assert "carry" in queries["persona-cycle-two"]["query"] and "2882" in queries["persona-cycle-two"]["query"]
    assert "low risk budget utilisation" in queries["persona-cycle-two"]["query"]
    assert "shortfall:low_risk_budget_utilisation" in queries["persona-cycle-two"]["tags"]
    assert queries["persona-cycle-test"]["tags"] != queries["persona-cycle-two"]["tags"]

    # Materialised workspace caches: own lessons, never the other persona's private memory.
    one_cache = (state["workspaces"] / "persona-cycle-test" / "MEMORY.md").read_text()
    two_cache = (state["workspaces"] / "persona-cycle-two" / "MEMORY.md").read_text()
    assert "ONE-PRIVATE" in one_cache and "TWO-PRIVATE" not in one_cache and "TWO-SECRET" not in one_cache
    assert "TWO-PRIVATE" in two_cache and "ONE-PRIVATE" not in two_cache and "ONE-SECRET" not in two_cache

    # The case file each agent turn received: different lessons, no foreign private memory.
    one_lessons, two_lessons = _lesson_summaries(one_packet), _lesson_summaries(two_packet)
    assert one_lessons != two_lessons
    assert any("ONE-PRIVATE" in text for text in one_lessons.values())
    assert any("TWO-PRIVATE" in text for text in two_lessons.values())
    for text in one_lessons.values():
        assert "TWO-" not in text
    for text in two_lessons.values():
        assert "ONE-" not in text
    assert "TWO-" not in state["prompts"]["persona-cycle-test"]
    assert "ONE-" not in state["prompts"]["persona-cycle-two"]
    assert "CaseFile" in state["prompts"]["persona-cycle-test"]

    # The OODA packet records the case file id and digest, for the cycle and for each proposal.
    for packet in (one_packet, two_packet):
        cycle = packet["persona_cycle"]
        stored = cycle["case_files"][cycle["case_file"]["case_file_id"]]
        assert stored["digest"] == cycle["case_file"]["digest"] and stored["execution_may_read"] is False
        decision = cycle["decisions"][0]
        assert decision["case_file_id"] in cycle["case_files"] and decision["case_file_digest"]
        assert decision["cited_case_file_item_ids"]
        assert any(ref.endswith("case_files/" + stored["case_file_id"]) for ref in packet["orient"]["evidence_bundle_refs"])
    assert one_packet["persona_cycle"]["case_file"]["digest"] != two_packet["persona_cycle"]["case_file"]["digest"]


def test_each_proposal_gets_its_own_case_file_composed_for_its_decision(system):
    controller, state, owner, _ = system
    state["answer"] = {"orient": "Pool review.", "memory_candidates": [], "proposals": [
        {"kind": "remove", "reason": "Drawdown.", "evidence_refs": [], "parameters": {"artifact_ref": "artifact-cycle"},
         "none_applicable_reason": "Nothing retrieved applies."},
        {"kind": "demand", "reason": "Short utilisation.", "evidence_refs": [], "none_applicable_reason": "None."}]}
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    cycle = packet["persona_cycle"]
    assert cycle["failure_reason"] is None
    removal, demand = cycle["decisions"]
    assert cycle["case_files"][removal["case_file_id"]]["subject"]["decision_type"] == "member_removal"
    assert cycle["case_files"][removal["case_file_id"]]["subject"]["member_artifact_refs"] == ["artifact-cycle"]
    assert cycle["case_files"][demand["case_file_id"]]["subject"]["decision_type"] == "research_demand"
    assert removal["case_file_id"] != demand["case_file_id"]
    assert removal["case_file_scope"] == demand["case_file_scope"] == "proposal"


@pytest.mark.parametrize("proposal,reason", [
    ({"kind": "no_action", "reason": "x", "evidence_refs": []}, "neither"),
    ({"kind": "no_action", "reason": "x", "evidence_refs": [], "case_file_item_ids": ["lessons:memory_x:not-there"]}, "stranger"),
    ({"kind": "no_action", "reason": "x", "evidence_refs": [], "case_file_item_ids": ["facts:strategy_pool:p"],
      "none_applicable_reason": "both"}, "both"),
])
def test_proposal_must_cite_case_file_items_or_state_none_applicable(system, proposal, reason):
    controller, state, owner, _ = system
    state["answer"] = {"orient": "o", "memory_candidates": [], "proposals": [proposal]}
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == "case_file_citation_invalid"
    assert packet["status"] == "observing"


def test_no_family_or_underlying_fails_closed_with_reason_and_buys_no_turn_or_default_query(system):
    controller, state, owner, _ = system
    raw = state["entries"].get("artifact-cycle")
    metadata = {k: v for k, v in raw["metadata"].items() if k != "strategy_artifact"}
    assert state["entries"].compare_and_set("artifact-cycle", raw, {**raw, "metadata": metadata})[0]
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == "observe_prerequisites_unavailable:canonical_memory"
    assert "cycle_query_subject_missing" in packet["persona_cycle"]["observe_bundle"]["canonical_memory_reason"]
    assert state["materialize"] == [] and state["turns"] == 0


def test_case_file_with_every_group_unavailable_fails_closed_before_the_turn(system, monkeypatch):
    controller, state, owner, _ = system
    for key in ("PANTHEON_MEMORY_API_URL",):
        controller.owners.env = {k: v for k, v in controller.owners.env.items() if k != key}
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == "case_file_unavailable:all_case_file_groups_unavailable"
    assert state["turns"] == 0
