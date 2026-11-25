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
from services.persona import persona_cycle_controller as regime_consumer
from services.persona.cron_ooda_closure import append_ooda_packet
from services.persona.harness import HarnessSurface, PersistentHarnessOwner
from services.persona.persona_cycle_controller import (
    CaseFileProposal,
    CycleOwners, PersonaCycleController, packet_identity, read_packets, read_packet_index, run_is_pruned,
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
             "answer_fn": None, "materialize": [], "prompts": {}, "obs": {}, "workspaces": tmp_path / "ws",
             "broker_runs": {}, "users": [], "memory_ids": [], "record_override": None,
             "ensures": [], "agents": {}, "ensure_status": 200}
    broker_path = "/api/openclaw-adapter/agents/persona-tools/runs"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # Test double of the adapter's per-run record route (the real broker is exercised by the integration test).
            assert self.headers.get("X-Pantheon-Service-Token") == "fixture-adapter-token"
            assert self.path.startswith(broker_path + "/")
            run = state["broker_runs"].get(self.path.rsplit("/", 1)[1])
            override = state["record_override"]
            if override is not None:
                run = override(run)
            status, result = (200, {"status": "ok", "record": run}) if run is not None else (404, {"status": "not_found"})
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["paths"].append(self.path)
            if self.path == "/api/openclaw-adapter/gateway/cron":
                state.setdefault("methods", []).append(body["method"])
            status = 200
            if self.path == broker_path:
                assert self.headers.get("X-Pantheon-Service-Token") == "fixture-adapter-token"
                assert body["purpose"] == "daily_cycle"
                ref = f"ptr-{len(state['broker_runs']) + 1}"
                state["broker_runs"][ref] = {"run_ref": ref, "persona_id": body["persona_id"], "purpose": body["purpose"],
                                             "session_user": body["session_user"], "run_id": None, "calls": [], "proposals": []}
                status, result = 201, {"status": "ok", "run": {"run_ref": ref}}
            elif self.path == "/api/openclaw-adapter/gateway/cron":
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
            elif self.path == "/api/openclaw-adapter/agents/persona/ensure":
                assert self.headers.get("X-Pantheon-Service-Token") == "fixture-adapter-token"
                assert self.headers.get("Idempotency-Key") and self.headers.get("X-Request-Id")
                state["ensures"].append(body)
                pid = body["persona"]["persona_id"]
                if state["ensure_status"] != 200:
                    status, result = state["ensure_status"], {"status": "upstream_unavailable"}
                else:
                    created = pid not in state["agents"]
                    state["agents"][pid] = True
                    status = 201 if created else 200
                    result = {"status": "ok", "agent": {"agent_id": pid, "status": "created" if created else "unchanged"}}
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
                assert pid != 'persona-cycle-test' or '"harness_version": 1' in body["input"]
                state["prompts"][pid] = body["input"]
                status = 503 if state["outage"] else 200
                answer = state["answer_fn"](pid, body["input"]) if state["answer_fn"] else state["answer"]
                state["users"].append(body.get("user"))
                reg = next((r for r in state["broker_runs"].values() if r["session_user"] == body.get("user")), None)
                if reg is not None and not state["outage"] and answer is not None:
                    # What the gateway plugin and broker would record: memory reads, then one call per proposal.
                    reg["run_id"] = f"gw-run-{state['turns']}"
                    if state["memory_ids"]:
                        reg["calls"].append({"tool": "persona_memory_recall", "outcome": "ok",
                                             "canonical_ids": list(state["memory_ids"])})
                    for proposal in answer.get("proposals", []):
                        try:
                            CaseFileProposal.model_validate(proposal)
                            assert bool(proposal.get("case_file_item_ids")) != bool(proposal.get("none_applicable_reason"))
                        except Exception:  # the broker refuses it; nothing is recorded as a proposal
                            reg["calls"].append({"tool": "persona_proposal_submit", "outcome": "refused", "canonical_ids": []})
                            continue
                        reg["calls"].append({"tool": "persona_proposal_submit", "outcome": "recorded", "canonical_ids": []})
                        reg["proposals"].append(proposal)
                # The text reply is deliberately not the proposal channel any more.
                result = {"output": [{"content": [{"type": "output_text", "text": "Cycle done."}]}]}
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


def _stateful(gateway, at, status="ok"):
    gateway["jobs"][0]["state"] = {"lastRunAtMs": at, "lastRunStatus": status}


def _calls(gateway, since):
    return gateway["methods"][since:]


def test_poll_budget_idle_pass_is_one_call_and_runs_read_only_on_change(system):
    controller, gateway, _, _ = system
    controller.reconcile_once()
    _stateful(gateway, 1, "ok")
    gateway["runs"]["job-1"] = [run(1)]
    n = len(gateway["methods"])
    assert controller.reconcile_once()["failures"] == []  # no watermark yet: reads runs once
    assert _calls(gateway, n).count("cron.runs") == 1
    n = len(gateway["methods"])
    idle = controller.reconcile_once()
    assert _calls(gateway, n) == ["cron.list"]
    assert idle["personas"]["persona-cycle-test"]["stage"] == "closed"
    _stateful(gateway, 2, "ok")
    gateway["runs"]["job-1"].insert(0, run(2))
    n = len(gateway["methods"])
    controller.reconcile_once()
    assert _calls(gateway, n) == ["cron.list", "cron.runs"]
    assert gateway["turns"] == 2


def test_poll_budget_restart_without_watermark_reads_runs_once(system):
    controller, gateway, _, _ = system
    controller.reconcile_once()
    _stateful(gateway, 1)
    gateway["runs"]["job-1"] = [run(1)]
    controller.reconcile_once()
    restarted = PersonaCycleController(controller.registrar, controller.owners, store_path=controller.path)
    n = len(gateway["methods"])
    restarted.reconcile_once()
    assert _calls(gateway, n).count("cron.runs") == 1
    assert gateway["turns"] == 1


def test_poll_budget_payload_without_state_falls_back_to_runs_every_pass(system):
    controller, gateway, _, _ = system
    controller.reconcile_once()
    n = len(gateway["methods"])
    controller.reconcile_once()
    controller.reconcile_once()
    assert _calls(gateway, n).count("cron.runs") == 2


def test_poll_interval_default_and_env_override():
    from services.persona.persona_cycle_controller import controller_interval_seconds
    assert controller_interval_seconds({}) == 120.0
    assert controller_interval_seconds({"PANTHEON_PERSONA_CYCLE_POLL_SECONDS": "45"}) == 45.0
    assert controller_interval_seconds({"PANTHEON_PERSONA_CYCLE_POLL_SECONDS": "x"}) == 120.0


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


def test_pruned_run_is_never_consumed_again_and_store_stays_bounded(system):
    from datetime import datetime, timedelta, timezone
    controller, state, owner, _ = system
    controller.path = controller.path.with_suffix(".json")  # hosted shape: single JSON document
    persona = owner.list_json()[0]
    first = controller.consume_run(persona, "job-1", run(1))
    turns = state["turns"]
    records = json.loads(controller.path.read_text())
    for record in records:
        record["recorded_at"] = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    controller.path.write_text(json.dumps(records))
    controller.consume_run(persona, "job-1", run(2))  # write-time prune of run 1
    index = read_packet_index(controller.path)
    assert first["packet_id"] not in index["identities"]
    assert run_is_pruned(index, "job-1", run(1))
    assert not run_is_pruned(index, "job-1", run(3))
    before = state["turns"]
    stub = controller.consume_run(persona, "job-1", run(1))
    assert stub["persona_cycle"]["pruned"] is True
    assert state["turns"] == before and before >= turns
    assert first["packet_id"] not in read_packets(controller.path)
    # the persisted cycle packet carries no embedded series
    assert "quarter_equity_observations" not in controller.path.read_text()


def test_restart_does_not_retry_an_uncertain_provider_or_risk_attempt(system):
    controller, state, owner, _ = system
    persona = owner.list_json()[0]

    class ProcessDeath(BaseException):
        pass

    def interrupted(*args, **kwargs):
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
    # The broker refuses the invalid proposal; with nothing recorded there is no proposal and no retry from text.
    assert packet["persona_cycle"]["failure_reason"] == "tool_record_unavailable:no_proposal_submitted"
    assert state["turns"] == 1 and "decisions" not in packet["persona_cycle"]


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
        # RC2: the consumed run and the successful cycle are now visible (they were always NULL).
        assert records[0]["last_tick_at"] and records[0]["last_success_at"]
        assert records[0]["payload"]["daily_cycle_jobs"]["persona-cycle-test"]


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
    prompt = state["prompts"]["persona-cycle-test"]
    assert "persona_memory_recall" in prompt and "persona_mental_model_read" in prompt
    assert "persona_proposal_submit" in prompt and "none_applicable_reason" in prompt
    assert "Return only JSON" not in prompt

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


@pytest.mark.parametrize("proposal,failure", [
    # neither / both: the broker refuses the submission, so the record holds no proposal
    ({"kind": "no_action", "reason": "x", "evidence_refs": []}, "tool_record_unavailable:no_proposal_submitted"),
    ({"kind": "no_action", "reason": "x", "evidence_refs": [], "case_file_item_ids": ["lessons:memory_x:not-there"]},
     "case_file_citation_invalid"),
    ({"kind": "no_action", "reason": "x", "evidence_refs": [], "case_file_item_ids": ["facts:strategy_pool:p"],
      "none_applicable_reason": "both"}, "tool_record_unavailable:no_proposal_submitted"),
])
def test_proposal_must_cite_case_file_items_or_state_none_applicable(system, proposal, failure):
    controller, state, owner, _ = system
    state["answer"] = {"proposals": [proposal]}
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == failure
    assert packet["status"] == "observing"


def test_citation_accepts_a_canonical_id_returned_to_this_run_and_rejects_a_stranger(system):
    controller, state, owner, _ = system
    returned = "pmem-00000001-1111-1111-1111-111111111111"
    state["memory_ids"] = [returned]
    proposal = {"kind": "no_action", "reason": "x", "evidence_refs": [], "case_file_item_ids": [returned]}
    state["answer"] = {"proposals": [proposal]}
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    cycle = packet["persona_cycle"]
    assert cycle["failure_reason"] is None and cycle["decisions"][0]["cited_case_file_item_ids"] == [returned]
    state["answer"] = {"proposals": [{**proposal, "case_file_item_ids": ["pmem-99999999-1111-1111-1111-111111111111"]}]}
    stranger = controller.consume_run(owner.list_json()[0], "job-1", run(2))
    assert stranger["persona_cycle"]["failure_reason"] == "case_file_citation_invalid"


def test_the_run_is_registered_as_a_daily_cycle_before_dispatch_and_text_proposals_are_ignored(system):
    controller, state, owner, _ = system
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] is None
    paths = state["paths"]
    assert paths.index("/api/openclaw-adapter/agents/persona-tools/runs") < paths.index("/v1/responses")
    registration = next(iter(state["broker_runs"].values()))
    assert registration["purpose"] == "daily_cycle" and state["users"] == [registration["session_user"]]
    # A reply whose text carries a valid proposal but whose tool record holds none yields no proposal.
    state["answer"] = None
    text_only = controller.consume_run(owner.list_json()[0], "job-1", run(2))
    assert text_only["persona_cycle"]["failure_reason"] == "tool_record_unavailable:tool_record_no_calls"
    assert "decisions" not in text_only["persona_cycle"] and state["turns"] == 2


def test_blocked_tools_and_auth_failure_are_named_with_the_backend_used(system):
    controller, state, owner, _ = system
    from types import SimpleNamespace as NS
    base = controller.turn_runner

    def blocked(persona_id, prompt, session_user=None):
        return NS(status="completed", persona_id=persona_id, model="openclaw/x", error=None,
                  blocked_reason="persona_tools_blocked:missing_host_identity",
                  model_used="anthropic/claude-sonnet-4-6", provider="claude-cli")
    state["answer"] = None
    controller.turn_runner = blocked
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == (
        "persona_tools_blocked:missing_host_identity:model=anthropic/claude-sonnet-4-6:provider=claude-cli")

    def denied(persona_id, prompt, session_user=None):
        return NS(status="error", persona_id=persona_id, model="openclaw/x", error="model_auth_failed")
    controller.turn_runner = denied
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(2))
    assert packet["persona_cycle"]["failure_reason"] == "persona_tools_blocked:model_auth_failed:model=openclaw/x"
    controller.turn_runner = base


@pytest.mark.parametrize("override,reason", [
    (lambda run: None, "tool_record_missing"),
    (lambda run: {**run, "persona_id": "someone-else"}, "tool_record_ambiguous"),
    (lambda run: {**run, "purpose": "panel_interpretation"}, "tool_record_ambiguous"),
    (lambda run: {**run, "proposals": run["proposals"] + run["proposals"]}, "tool_record_ambiguous"),
])
def test_missing_or_ambiguous_tool_record_records_the_reason_and_makes_no_proposal(system, override, reason):
    controller, state, owner, _ = system
    state["record_override"] = override
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == "tool_record_unavailable:" + reason
    assert packet["status"] == "observing" and "decisions" not in packet["persona_cycle"]
    assert state["turns"] == 1  # never retried


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


def _seed_decision_lessons(controller, owner, memory_service, base=90):
    from services.knowledge import case_file as cf
    persona = owner.list_json()[0]
    obs = controller.owners.observe(persona)
    cycle_type, _ = controller._cycle_decision(obs)
    families = [f"family:{f}" for f in obs["cycle_subject"]["families"]]
    for mid, summary, decision in ((base, "CYCLE-ONLY " + cycle_type.replace("_", " "), cycle_type),
                                   (base + 1, "REMOVAL-ONLY member removal", "member_removal")):
        entry = _private(mid, persona["persona_id"], summary, ["decision:" + decision, *families])
        cf.http_json(cf.OwnerEndpoint(memory_service), "/api/memory/persona-entries", tenant_id=TENANT_A,
                     method="POST", body=entry)
    return persona


def _remove_citing(marker, seen_prompts):
    def answer(pid, prompt):
        seen_prompts.append(prompt)
        revision = "ProposalCaseFiles: " in prompt
        text = prompt.split("ProposalCaseFiles: ", 1)[1] if revision else prompt.rsplit("\n", 1)[1]
        items = []
        stack = [json.loads(text)]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "item_id" in node and "excerpt" in node and marker in node["excerpt"]:
                    items.append(node["item_id"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        assert items, "marker item not shown to the turn"
        return {"orient": "Remove.", "memory_candidates": [], "proposals": [
            {"kind": "remove", "reason": "Review.", "evidence_refs": [],
             "parameters": {"artifact_ref": "artifact-cycle"}, "case_file_item_ids": items[:1]}]}
    return answer


def test_proposal_case_content_is_shown_before_finalising_and_citations_bind_to_it(system, memory_service):
    controller, state, owner, _ = system
    persona = _seed_decision_lessons(controller, owner, memory_service)
    prompts = []

    def answer(pid, prompt):
        # first turn only sees the cycle case (CYCLE-ONLY); revision shows the removal case.
        return _remove_citing("REMOVAL-ONLY" if "ProposalCaseFiles: " in prompt else "CYCLE-ONLY", prompts)(pid, prompt)
    state["answer_fn"] = answer
    packet = controller.consume_run(persona, "job-1", run(90))
    cycle = packet["persona_cycle"]
    assert cycle["failure_reason"] is None and packet["status"] == "closed"
    assert len(prompts) == 2 and "REMOVAL-ONLY" in prompts[1]
    decision = cycle["decisions"][0]
    attached = cycle["case_files"][decision["case_file_id"]]
    assert attached["subject"]["decision_type"] == "member_removal"
    attached_ids = {i["item_id"] for g in attached["groups"].values() for i in g["items"]}
    assert decision["cited_case_file_item_ids"] and set(decision["cited_case_file_item_ids"]) <= attached_ids


def test_citation_outside_the_proposal_case_file_is_rejected_after_revision(system, memory_service):
    controller, state, owner, _ = system
    persona = _seed_decision_lessons(controller, owner, memory_service, base=95)
    prompts = []
    state["answer_fn"] = lambda pid, prompt: _remove_citing("CYCLE-ONLY", prompts)(
        pid, prompt.split("\nRevision:")[0] if "ProposalCaseFiles: " in prompt else prompt)
    packet = controller.consume_run(persona, "job-1", run(90))
    assert packet["persona_cycle"]["failure_reason"] == "case_file_citation_invalid"
    assert packet["status"] == "observing"


@pytest.mark.parametrize("second_kind", ["demand", "no_action", "harness_patch"])
def test_revision_revalidates_every_final_proposal(system, memory_service, second_kind):
    controller, state, owner, _ = system
    persona = _seed_decision_lessons(controller, owner, memory_service,
                                     base=100 + 10 * ["demand", "no_action", "harness_patch"].index(second_kind))

    def answer(pid, prompt):
        revision = "ProposalCaseFiles: " in prompt
        result = _remove_citing("REMOVAL-ONLY" if revision else "CYCLE-ONLY", [])(pid, prompt)
        second = {"kind": second_kind, "reason": "Review another proposal.", "evidence_refs": []}
        if revision:
            second["case_file_item_ids"] = ["lessons:memory_x:not-in-any-case-file"]
        else:
            second["none_applicable_reason"] = "No lesson applies to this second proposal."
        result["proposals"].append(second)
        return result

    state["answer_fn"] = answer
    packet = controller.consume_run(persona, "job-1", run(100))
    assert state["turns"] == 2
    assert packet["persona_cycle"]["failure_reason"] == "case_file_citation_invalid"
    assert packet["status"] == "observing"
    assert "decisions" not in packet["persona_cycle"]


def test_daily_cycle_harness_patch_reaches_the_owner_and_loop_state_is_on_the_cycle_record(system):
    """Real entry: consume the cron run; the harness loop is called by production code, not a test."""
    controller, gateway, owner, _ = system
    unsupported = {"orient": "Raise the evidence bar.", "memory_candidates": [], "proposals": [
        {"kind": "harness_patch", "reason": "weak removals", "evidence_refs": [],
         "none_applicable_reason": "No case-file item bears on this harness change.",
         "parameters": {"patch_type": "threshold", "expected_effect": "fewer weak removals",
                        "proposed_change": {"cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.9}},
                        "evidence_refs": [{"kind": "packet", "ref": "unknown-packet", "moment": "unknown-packet"}]}}]}
    gateway["answer"] = unsupported
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    result = controller.reconcile_once()
    packet = next(iter(read_packets(controller.path).values()))
    loop = packet["persona_cycle"]["harness_loop"]
    decision = packet["persona_cycle"]["decisions"][0]
    assert decision["status"] == "patch_recorded"
    patch = PersistentHarnessOwner(owner).read("persona-cycle-test")["patches"][decision["patch_id"]]
    assert patch["proposal"]["session_id"] == packet["packet_id"] and patch["status"] == "draft"
    # The unresolved evidence is a readable degraded state, visible in the persona cycle health.
    assert loop["status"] == "degraded" and any("trajectory_evidence_unresolved" in r for r in loop["reasons"])
    assert result["personas"]["persona-cycle-test"]["harness_loop"]["status"] == "degraded"


# --- trade-lesson loop callers (TRADE-LESSON-LOOP-CALLERS-20261115) -----------------------------

class _LessonRegistrar:
    configured = True

    def __init__(self):
        self.sent = []

    def register(self, candidates, authorization):
        self.sent.append(candidates)
        return {"registered": [c["lesson_candidate_id"] for c in candidates], "already_registered": []}


def test_daily_cycle_runs_lesson_governance_and_stages_nothing_from_reply_text(system, monkeypatch):
    """Real entry: consume the cron run. The default controller builds the lesson cycle itself."""
    from services.persona.lesson_cycle import LessonCycle
    controller, gateway, owner, _ = system
    registrar = _LessonRegistrar()
    listed = []

    def memory(method, url, headers, body):
        listed.append(url)
        return 200, {"candidates": []}

    monkeypatch.setenv("PERSONA_JWT_SECRET", "controller-test-secret")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")
    assert controller.lesson_cycle is None
    controller.lesson_cycle = LessonCycle(turn_runner=controller.turn_runner, registrar=registrar, http=memory)
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    result = controller.reconcile_once()
    packet = next(iter(read_packets(controller.path).values()))
    cycle = packet["persona_cycle"]
    assert cycle["failure_reason"] is None
    # Memory candidates were a text-reply field; with no text parsing nothing is staged from the cycle turn.
    assert cycle["lesson_staging"] == [] and registrar.sent == []
    assert cycle["lesson_governance"] == {"status": "completed", "results": []}
    assert any("review_state=proposed" in url for url in listed)
    health = result["personas"]["persona-cycle-test"]["lessons"]
    assert health["staged"] == 0 and health["governance"] == "completed"


def test_lesson_steps_that_cannot_run_are_recorded_and_never_fail_the_cycle(system, monkeypatch):
    controller, gateway, owner, _ = system
    monkeypatch.delenv("PANTHEON_MEMORY_API_URL", raising=False)
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    result = controller.reconcile_once()
    cycle = next(iter(read_packets(controller.path).values()))["persona_cycle"]
    assert cycle["failure_reason"] is None and cycle["decisions"][0]["status"] == "no_action"
    assert cycle["lesson_governance"]["status"] == "unavailable" and cycle["lesson_governance"]["reason"]
    assert result["personas"]["persona-cycle-test"]["lessons"]["governance"] == "unavailable"


def _cycle_with_memory_double(system, monkeypatch, memory):
    from services.persona.lesson_cycle import LessonCycle
    controller, gateway, _owner, _ = system
    monkeypatch.setenv("PERSONA_JWT_SECRET", "controller-test-secret")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")
    controller.lesson_cycle = LessonCycle(turn_runner=controller.turn_runner, registrar=_LessonRegistrar(), http=memory)
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    controller.reconcile_once()
    return next(iter(read_packets(controller.path).values()))["persona_cycle"]


def test_daily_cycle_declares_one_question_per_pool_family_and_risk_lessons_without_regime(system, monkeypatch):
    sent = []

    def memory(method, url, headers, body):
        if method == "PUT":
            sent.append((url, headers, body))
            return 200, {"created": ["risk_lessons"], "updated": [], "unchanged": [], "retired": ["family:old"]}
        return 200, {"candidates": []}

    cycle = _cycle_with_memory_double(system, monkeypatch, memory)
    assert cycle["failure_reason"] is None
    assert len(sent) == 1
    url, headers, body = sent[0]
    assert url == "http://memory.test/api/memory/persona-standing-questions"
    assert headers["Authorization"].startswith("Bearer ") and headers["X-Tenant-Id"]
    families = cycle["observe_bundle"]["cycle_subject"]["families"]
    keys = [q["key"] for q in body["questions"]]
    assert keys == [f"family:{f}" for f in sorted(families)] + ["risk_lessons"]
    assert not any("regime" in k for k in keys) and body["persona_id"]
    assert cycle["standing_questions"]["status"] == "declared" and cycle["standing_questions"]["retired"] == ["family:old"]
    # The declaration is deterministic: the same pool yields byte-identical questions.
    controller = system[0]
    assert controller.standing_questions(families) == body["questions"]


@pytest.mark.parametrize("response,status,reason", [
    ((503, {"detail": {"reason": "llm_layers_disabled"}}), "not_run", "llm_layers_disabled"),
    ((503, {"detail": {"reason": "hindsight_unreachable"}}), "unavailable", "hindsight_unreachable"),
    ((403, None), "unavailable", "http_403"),
])
def test_standing_question_declaration_failure_never_fails_the_cycle(system, monkeypatch, response, status, reason):
    def memory(method, url, headers, body):
        return response if method == "PUT" else (200, {"candidates": []})

    cycle = _cycle_with_memory_double(system, monkeypatch, memory)
    assert cycle["failure_reason"] is None
    assert cycle["standing_questions"] == {"status": status, "reason": reason}


def test_standing_questions_not_declared_without_pool_families(system):
    controller = system[0]
    assert controller._declare_standing_questions({"tenant_id": "t", "persona_id": "p"}, {}) == {
        "status": "not_run", "reason": "pool_families_unavailable"}
    assert controller.standing_questions([]) == [q for q in controller.standing_questions([]) if q["key"] == "risk_lessons"]
    assert [q["key"] for q in controller.standing_questions(["Momentum", "momentum", " "])] == ["family:momentum", "risk_lessons"]


# --- Integration: a cron-dispatched cycle turn through the gateway plugin and the real broker ---------------
# Real here: the compiled pantheon-persona-tools plugin (run by node: its before_tool_call hook, then execute),
# the real adapter app with the real PersonaToolBroker (sqlite per-run record), the real Memory Plane over HTTP,
# the controller's production consume_run path and AdapterToolBrokerClient. The gateway process that would load
# the plugin and call the model is the fixture server's /v1/responses, which executes the agent's scripted tool
# calls through the plugin exactly as the gateway would (registry fixture = governed persona tool policy).

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "integrations" / "openclaw" / "plugins" / "pantheon-persona-tools"
_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "services" / "openclaw-gateway-adapter"
_DRIVER = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const { config, pluginPath, steps } = JSON.parse(readFileSync(0, "utf8"));
const plugin = (await import(pathToFileURL(pluginPath).href)).default;
let hook, factory;
plugin.register({ pluginConfig: config, registerTool(f) { factory = f; }, on(_n, h) { hook = h; } });
const out = [];
let i = 0;
for (const step of steps) {
  i += 1;
  const ctx = { agentId: step.agentId, sessionKey: step.sessionKey, runId: step.runId };
  const event = { toolName: step.tool, params: step.params ?? {}, runId: step.runId, toolCallId: `tc-${i}` };
  const verdict = await hook(event, ctx);
  if (verdict && verdict.block) { out.push({ blocked: true, reason: verdict.blockReason }); continue; }
  const tool = (factory({ agentId: step.agentId, sessionKey: step.sessionKey }) ?? []).find((t) => t.name === step.tool);
  if (!tool) { out.push({ passed: true, exposed: false }); continue; }
  try {
    const result = await tool.execute(event.toolCallId, event.params);
    out.push({ ok: true, details: result.details });
  } catch (error) { out.push({ error: String(error.message) }); }
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture
def brokered(system, memory_service, monkeypatch, tmp_path):
    import shutil
    import socket
    import subprocess
    import sys
    import time
    import uvicorn
    if not shutil.which("node") or not shutil.which("npm"):
        pytest.fail("node and npm are required for the plugin leg of this integration test")
    if not (_PLUGIN_DIR / "node_modules" / ".bin" / "tsc").exists():
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=_PLUGIN_DIR, check=True, capture_output=True, text=True)
    subprocess.run(["npm", "run", "build"], cwd=_PLUGIN_DIR, check=True, capture_output=True, text=True)
    monkeypatch.syspath_prepend(str(_ADAPTER_DIR))
    monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "registry.lock"))
    import main as adapter
    from persona_tool_broker import BrokerConfig, PersonaToolBroker, http_memory_get
    from integrations.openclaw.persona_agent_sync import persona_agent_runtime_policy
    from integrations.openclaw.persona_ooda_runtime import AdapterToolBrokerClient
    controller, state, owner, _ = system
    config = BrokerConfig(enabled=True, token="it-broker-secret", memory_api_url=memory_service,
                          registry_ttl_seconds=0.0, state_path=str(tmp_path / "broker.sqlite3"))
    tools = persona_agent_runtime_policy()["tools"]
    registry = {"persona-cycle-test": {"id": "persona-cycle-test", "tools": tools}, "main": {"id": "main"}}
    broker = PersonaToolBroker(config, agent_lookup=registry.get, memory_get=http_memory_get(config))
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER_CONFIG", config)
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER", broker)
    monkeypatch.setattr(adapter, "_ASSISTANT_SERVICE_TOKEN", "fixture-adapter-token")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(adapter.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    controller.tool_broker = AdapterToolBrokerClient(f"http://127.0.0.1:{port}", "fixture-adapter-token")
    driver = tmp_path / "driver.mjs"
    driver.write_text(_DRIVER)
    plugin_path = _PLUGIN_DIR / "dist" / "src" / "index.js"
    steps_log = []

    def agent_turn(user, script):
        """The gateway session for this OpenResponses `user`: the agent's tool calls go through the plugin."""
        session = f"agent:persona-cycle-test:openresponses-user:{user}"
        steps = [{"agentId": "persona-cycle-test", "sessionKey": session, "runId": "gw-run-1", "tool": tool,
                  "params": params} for tool, params in script]
        payload = {"config": {"brokerUrl": f"http://127.0.0.1:{port}/internal/persona-tools/v1", "brokerToken": "it-broker-secret"},
                   "pluginPath": str(plugin_path), "steps": steps}
        done = subprocess.run(["node", str(driver)], input=json.dumps(payload), text=True, capture_output=True, timeout=120)
        assert done.returncode == 0, done.stderr
        results = json.loads(done.stdout)
        steps_log.append(results)
        return results

    state["agent_turn"] = agent_turn
    state["steps_log"] = steps_log
    state["broker"] = broker
    try:
        yield controller, state, owner
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _proposal(item_ids=None, none_reason=None):
    p = {"kind": "no_action", "reason": "Nothing to change today.", "evidence_refs": []}
    if item_ids:
        p["case_file_item_ids"] = item_ids
    if none_reason:
        p["none_applicable_reason"] = none_reason
    return p


def test_cron_dispatched_cycle_turn_through_gateway_plugin_and_broker(brokered):
    controller, state, owner = brokered
    seen = {}

    def answer(pid, prompt):
        return None  # the fixture's own recorder is bypassed; tool calls come from agent_turn below

    state["answer_fn"] = answer
    original_turn = controller.turn_runner

    def dispatch(persona_id, prompt, *, session_user):
        # The OpenResponses user is the registered session identity; the gateway then runs the agent's tool calls.
        results = state["agent_turn"](session_user, state["script"](seen))
        seen["results"] = results
        return original_turn(persona_id, prompt, session_user=session_user)

    controller.turn_runner = dispatch
    persona = owner.list_json()[0]

    # Cycle 1: own-bank recall + standing-question read + a proposal citing a canonical id the recall returned.
    def script_valid(seen_):
        return [("persona_memory_recall", {"query": "momentum 2330 sizing"}),
                ("persona_memory_recall", {"query": "momentum 2330 sizing", "bank": "facts"}),
                ("persona_mental_model_read", {}),
                ("persona_proposal_submit", _proposal(none_reason="No case-file item bears on staying flat."))]
    state["script"] = script_valid
    first = controller.consume_run(persona, "job-1", run(1))
    results = seen["results"]
    assert results[0].get("ok"), results[0]
    own_ids = [h["canonical_ref"] for h in results[0]["details"]["hits"]]
    assert own_ids and all(i.startswith("persona_memory:pmem-") for i in own_ids)
    assert not any("TWO-" in json.dumps(r) for r in results)  # nothing of another persona's bank
    assert results[3]["details"]["recorded"] is True
    assert first["persona_cycle"]["failure_reason"] is None and first["status"] == "closed"
    assert first["persona_cycle"]["decisions"][0]["status"] == "no_action"

    # Cycle 2: cites a canonical id returned to THIS run -> accepted.
    def script_returned(seen_):
        return [("persona_memory_recall", {"query": "momentum 2330 sizing"}),
                ("persona_proposal_submit", _proposal(item_ids=own_ids[:1]))]
    state["script"] = script_returned
    second = controller.consume_run(persona, "job-1", run(2))
    assert second["persona_cycle"]["failure_reason"] is None
    assert second["persona_cycle"]["decisions"][0]["cited_case_file_item_ids"] == own_ids[:1]

    # Cycle 3: cites an id nobody returned (a stranger, even a well-formed one) -> rejected, no decisions.
    def script_stranger(seen_):
        return [("persona_proposal_submit", _proposal(item_ids=["persona_memory:pmem-99999999-1111-1111-1111-111111111111"]))]
    state["script"] = script_stranger
    third = controller.consume_run(persona, "job-1", run(3))
    assert third["persona_cycle"]["failure_reason"] == "case_file_citation_invalid"
    assert third["status"] == "observing" and "decisions" not in third["persona_cycle"]

    # The broker's per-run records are the only proposal source: three registered daily-cycle runs.
    records = [state["broker"].run_record(ref) for ref in sorted(
        r["run_ref"] for r in (state["broker"]._store._db.execute("SELECT run_ref FROM runs").fetchall()))]
    assert [r["purpose"] for r in records] == ["daily_cycle"] * 3
    assert [len(r["proposals"]) for r in records] == [1, 1, 1]
    assert state["turns"] == 3

# --- SD-REGIME-STATE-ENGINE R13: first-wave regime consumers (real registry routes over HTTP) ---

def _registry_transport(monkeypatch, *, observations="seeded", register_spec=True):
    """Transport over the real registry app: real routes, real stores, the registry's own authentication."""
    for module in ("numpy", "pandas", "scipy", "statsmodels"):
        pytest.importorskip(module)
    from fastapi.testclient import TestClient
    from services.registry import main as registry_service
    from services.registry.regime import routes as regime_routes
    from services.registry.regime.tests import test_evaluator as evaluator_fixtures
    from services.registry.regime.tests import test_routes as route_fixtures
    from services.registry.storage import reset_store

    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", route_fixtures.SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    reset_store()
    rows = evaluator_fixtures.observations() if observations == "seeded" else [
        o for o in evaluator_fixtures.observations() if o["dataset"] != "tw_index_daily"]
    monkeypatch.setattr(regime_routes, "_observations_loader", lambda: (lambda: rows))
    client = TestClient(registry_service.app)
    headers = route_fixtures._jwt("tenant-a")
    route_fixtures._register_models(client, headers)
    if register_spec:
        route_fixtures._register_spec(client, headers, "momentum", regime={"primary_axis": "volatility", "horizon": "short"})
    session = evaluator_fixtures.sessions()[-1]
    monkeypatch.setattr(regime_consumer, "session_for_run", lambda run: session)
    calls = []

    def transport(method, path, body=None):
        calls.append((method, path))
        response = client.request(method, path, headers=headers, json=body)
        return response.status_code, response.json()

    return transport, calls, session, regime_routes


def test_observe_evaluates_then_reads_active_record_and_case_file_carries_regime_tag(system, monkeypatch):
    from services.registry.regime.store import get_regime_store
    transport, calls, session, _ = _registry_transport(monkeypatch)
    controller, state, owner, _pool = system
    controller.owners.regime_transport = transport
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    cycle = packet["persona_cycle"]
    assert cycle["failure_reason"] is None
    stored = get_regime_store().list_regimes(strategy_id="momentum", states=("active",))
    assert len(stored) == 1
    record = stored[0]["record"]
    # Evaluate first, then the active read, then the shared market state; one mutating call only.
    assert calls[0] == ("POST", "/api/registry/regime/evaluate")
    assert ("GET", "/api/registry/regime/strategies/momentum/active") in calls
    assert [c for c in calls if c[0] != "GET"] == [("POST", "/api/registry/regime/evaluate")]
    assert packet["orient"]["regime_state_ref"] == "regime-state://" + record["regime_id"]
    assert cycle["regime"]["status"] == "observed" and cycle["regime"]["records"]["momentum"]["regime_id"] == record["regime_id"]
    # The case file subject and the lesson query carry the stored record's class.
    case_file = next(iter(cycle["case_files"].values()))
    assert record["regime_class"] and case_file["subject"]["regime_tag"] == record["regime_class"]
    assert f"regime:{record['regime_class']}" in case_file["lesson_query"]["tags"]
    # The persona's interpretation stays in the packet; the registry record is untouched.
    assert cycle["regime_interpretation"]["written_to_regime_state"] is False
    # Free-text replies are no longer parsed; the brokered tool record carries no orient narrative.
    assert cycle["regime_interpretation"]["text"] is None
    assert get_regime_store().list_regimes(strategy_id="momentum", states=("active",))[0]["record"] == record


@pytest.mark.parametrize("scenario,reason_prefix", [
    ("refused", "evaluate_refused:409:session_data_incomplete"),
    ("no_record", "no_active_record_for_pool_strategies"),
])
def test_registry_refusal_or_missing_record_runs_the_cycle_with_an_explicit_reason(system, monkeypatch, scenario, reason_prefix):
    transport, _calls, _session, _ = _registry_transport(
        monkeypatch, observations="empty" if scenario == "refused" else "seeded", register_spec=scenario != "no_record")
    controller, state, owner, _pool = system
    controller.owners.regime_transport = transport
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    cycle = packet["persona_cycle"]
    assert cycle["failure_reason"] is None and packet["status"] == "closed"
    assert packet["orient"]["regime_state_ref"] is None
    assert cycle["regime"]["status"] == "unavailable" and cycle["regime"]["reason"].startswith(reason_prefix)
    case_file = next(iter(cycle["case_files"].values()))
    assert case_file["subject"]["regime_tag"] == ""
    assert not any(t.startswith("regime:") for t in case_file["lesson_query"]["tags"])
    assert cycle["regime_interpretation"]["regime_unavailable_reason"].startswith(reason_prefix)


def test_unconfigured_or_failing_registry_never_blocks_or_invents_a_regime(system):
    controller, state, owner, _pool = system
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))  # env has no registry URL/token
    assert packet["persona_cycle"]["regime"]["reason"] == "registry_unconfigured"
    assert packet["orient"]["regime_state_ref"] is None and packet["persona_cycle"]["failure_reason"] is None

    def boom(method, path, body=None):
        raise ConnectionError("registry down")
    controller.owners.regime_transport = boom
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(2))
    assert packet["persona_cycle"]["regime"]["reason"] == "registry_unavailable:ConnectionError"
    assert packet["orient"]["regime_state_ref"] is None and packet["persona_cycle"]["failure_reason"] is None


def test_cycle_requests_macro_regime_shift_consultation_only_on_a_change(system, monkeypatch):
    controller, state, owner, _pool = system
    sequence = iter([("2026-11-12", "expansion"), ("2026-11-13", "slowdown"), ("2026-11-16", "slowdown")])

    def regime(persona, observe, run_):
        session, macro = next(sequence)
        return {"status": "unavailable", "reason": "test", "session": session, "regime_state_ref": None,
                "regime_tag": "", "records": {}, "macro": {"state": macro, "session": session}}
    monkeypatch.setattr(controller.owners, "regime", regime)
    persona = owner.list_json()[0]
    first, second, third = (controller.consume_run(persona, "job-1", run(n))["persona_cycle"] for n in (1, 2, 3))
    assert "consult_requests" not in first                      # no earlier session: nothing to compare
    request = second["consult_requests"][0]                     # expansion -> slowdown
    assert request["trigger_rule"] == "macro_regime_shift" and request["status"] == "requested"
    assert request["panel"]["gated_decision"]["type"] == "macro_regime_shift"
    assert "persona-risk-officer" in [p["persona_id"] for p in request["panel"]["participants"]]
    assert "consult_requests" not in third                      # slowdown -> slowdown


# --- deliberative meeting triggers (MEETING-TRIGGERS-R2-20261116) ---------------------------------------------
def _meeting_controller(controller, opener, *, records=(), weekday=None):
    from services.persona import meeting_triggers as mt

    class Attribution(mt.AttributionPort):
        def __init__(self):
            self.runs = []

        def request_weekly_run(self, window_end):
            self.runs.append(window_end)
            return {"run_id": "run-w", "status": "queued"}

        def new_records(self, since, limit=50):
            return list(records)

    attribution = Attribution()
    controller.meeting_triggers = mt.MeetingTriggers(
        opener=opener, attribution=attribution,
        now=lambda: datetime(2026, 11, 16 + (weekday or 0), 3, tzinfo=timezone.utc))
    return attribution


def test_daily_cycle_convenes_weekly_meetings_and_requests_the_outlier_run_once_per_week(system):
    controller, gateway, owner, _ = system
    calls = []
    attribution = _meeting_controller(
        controller, lambda **kw: calls.append(kw) or {"meeting_id": f"m{len(calls)}", "status": "convened"},
        records=[{"record_id": "r1", "subject_kind": "persona", "subject_id": "persona-cycle-test",
                  "direction": "underperform"}], weekday=2)
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    controller.reconcile_once()
    packet = next(iter(read_packets(controller.path).values()))
    meetings = packet["persona_cycle"]["meetings"]
    assert {"MT-3", "MT-4"} <= {c["meeting_type"] for c in meetings["convened"]}
    assert meetings["weekly_outlier_run"]["status"] == "requested" and len(attribution.runs) == 1
    assert next(c for c in calls if c["meeting_type"] == "MT-4")["attribution_record_id"] == "r1"
    assert all(c["convened_by"] == {"kind": "persona", "id": "persona-cycle-test"} for c in calls)
    # the next daily cycle of the same ISO week neither re-convenes nor re-requests
    gateway["runs"]["job-1"] = [run(2), *gateway["runs"]["job-1"]]
    controller.reconcile_once()
    assert len(attribution.runs) == 1 and len([c for c in calls if c["meeting_type"] == "MT-3"]) == 1


def test_consult_request_for_a_meeting_opens_it_and_the_harness_loop_does_not_override_the_decision(system):
    controller, gateway, owner, _ = system
    ask = {"orient": "Need a shared view.", "memory_candidates": [], "proposals": [
        {"kind": "consult_request", "reason": "why do exits lag", "evidence_refs": [],
         "none_applicable_reason": "No case-file item bears on this request.",
         "parameters": {"meeting_type": "MT-2", "topic": "exit lag"}}]}
    gateway["answer"] = ask
    _meeting_controller(controller, lambda **kw: {"meeting_id": "m-req", "status": "convened"})
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    controller.reconcile_once()
    packet = next(iter(read_packets(controller.path).values()))
    decision = packet["persona_cycle"]["decisions"][0]
    assert decision["status"] == "meeting_opened" and decision["meeting_id"] == "m-req"
    assert packet["persona_cycle"]["meetings"]["requests"][0]["status"] == "meeting_opened"


def test_meeting_refusal_and_unconfigured_owner_are_recorded_and_never_fail_the_cycle(system):
    controller, gateway, owner, _ = system

    def refuse(**kw):
        err = RuntimeError("refused")
        err.payload = {"reason": "meeting_budget_exhausted"}
        raise err
    _meeting_controller(controller, refuse, weekday=2)
    controller.reconcile_once()
    gateway["runs"]["job-1"] = [run(1)]
    result = controller.reconcile_once()
    packet = next(iter(read_packets(controller.path).values()))
    assert result["failures"] == [] and packet["persona_cycle"]["failure_reason"] is None
    assert packet["persona_cycle"]["meetings"]["refused"][0]["reason"] == "meeting_budget_exhausted"
    controller.meeting_triggers = None  # production default: no consultation owner configured in this env
    assert controller._meeting_step({**owner.list_json()[0]}, {}, {"packet_id": "x"}, {}) == {
        "status": "not_run", "reason": "consultation_owner_unconfigured"}


def test_daily_cycle_declares_market_regime_standing_question_with_active_record_and_retires_when_none_exists(
    system, monkeypatch
):
    from services.memory import hindsight_index as hs
    from services.memory.test_main import FakeMentalModelHindsight, JWT_SECRET
    from services.registry.regime.store import get_regime_store

    class _CycleMentalModelHindsight(FakeMentalModelHindsight):
        def recall(self, bank, body):
            raise hs.HindsightError("hindsight_unconfigured")

    controller, _state, owner, _pool = system
    persona = owner.list_json()[0]
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", controller.owners.env["PANTHEON_MEMORY_API_URL"])
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PERSONA_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv(hs.LLM_LAYERS_ENV, "true")
    monkeypatch.setattr(hs, "_LAYERED_BANKS", set())
    fake = _CycleMentalModelHindsight()
    hs.set_hindsight_client(fake)
    try:
        transport, _calls, _session, _ = _registry_transport(monkeypatch, register_spec=True)
        controller.owners.regime_transport = transport
        first = controller.consume_run(persona, "job-1", run(1))["persona_cycle"]
        assert first["failure_reason"] is None
        stored = get_regime_store().list_regimes(strategy_id="momentum", states=("active",))
        assert len(stored) == 1
        record = stored[0]["record"]
        assert first["standing_questions"]["status"] == "declared"
        assert first["standing_questions"]["keys"] == ["family:momentum", "risk_lessons", "market_regime"]
        assert sorted(first["standing_questions"]["created"]) == ["family:momentum", "market_regime", "risk_lessons"]
        bank = hs.persona_bank(persona["tenant_id"], persona["persona_id"])
        models = {m["name"]: m for m in fake.banks[bank].values()}
        assert sorted(models) == ["family:momentum", "market_regime", "risk_lessons"]
        assert models["market_regime"]["tags"] == [f"anchor:regime:{record['regime_class']}"]
        assert controller.standing_questions(["momentum"], first["regime"]) == [
            {"key": m["name"], "text": m["source_query"],
             **({"anchors": [t.removeprefix("anchor:") for t in m["tags"]]} if m["name"] != "risk_lessons"
                else {"memory_types": [t.removeprefix("type:") for t in m["tags"]]})}
            for m in (models["family:momentum"], models["risk_lessons"], models["market_regime"])
        ]

        # Next daily cycle: when no active regime record exists for the pool strategy, market_regime is retired.
        no_record_transport, _, _, _ = _registry_transport(monkeypatch, register_spec=False)
        controller.owners.regime_transport = no_record_transport
        second = controller.consume_run(persona, "job-1", run(2))["persona_cycle"]
        assert second["failure_reason"] is None
        assert second["regime"]["status"] == "unavailable"
        assert second["regime"]["reason"] == "no_active_record_for_pool_strategies"
        assert second["standing_questions"]["status"] == "declared"
        assert second["standing_questions"]["keys"] == ["family:momentum", "risk_lessons"]
        assert second["standing_questions"]["retired"] == ["market_regime"]
        assert sorted(second["standing_questions"]["unchanged"]) == ["family:momentum", "risk_lessons"]
        assert sorted(m["name"] for m in fake.banks[bank].values()) == ["family:momentum", "risk_lessons"]
    finally:
        hs.set_hindsight_client(None)


def test_daily_cycle_convenes_mt5_once_on_governed_regime_transition_and_skips_repeated_missing_or_proposed(
    system, monkeypatch
):
    controller, state, owner, pool = system
    two = _add_second_persona(state, owner, pool)
    one = next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-test")
    calls = []
    _meeting_controller(
        controller,
        lambda **kw: calls.append(kw) or {"meeting_id": f"m-{len(calls)}", "status": "convened"},
        weekday=5,
    )

    sessions_cfg = {
        "2026-11-12": {"macro": "expansion", "vol": "low", "trend": "up", "state": "active", "model_status": "approved"},
        "2026-11-13": {"macro": "slowdown", "vol": "high", "trend": "up", "state": "active", "model_status": "approved"},
        "2026-11-16": {"macro": "slowdown", "vol": "high", "trend": "down", "state": "active", "model_status": "approved"},
        "2026-11-17": {"macro": "contraction", "vol": "low", "trend": "up", "state": "proposed", "model_status": "candidate"},
    }
    current_session = {"value": "2026-11-12"}
    monkeypatch.setattr(regime_consumer, "session_for_run", lambda _run: current_session["value"])

    def transport(method, path, body=None):
        sess = current_session["value"]
        cfg = sessions_cfg[sess]
        if method == "POST" and path == "/api/registry/regime/evaluate":
            return 200, {"market": "TW", "session": sess, "sets": [{"model_status": cfg["model_status"]}]}
        if method == "GET" and path.startswith("/api/registry/regime/strategies/"):
            sid = path.split("/")[5]
            axis = "volatility" if sid == "momentum" else "trend"
            horizon = "short" if sid == "momentum" else "medium"
            ax_state = cfg["vol"] if sid == "momentum" else cfg["trend"]
            cls = (
                ("low_volatility" if ax_state == "low" else "high_volatility")
                if sid == "momentum"
                else ("trending_up" if ax_state == "up" else "trending_down")
            )
            return 200, {
                "regime_id": f"regime:TW:{sid}:{sess}:set1:r1",
                "strategy_id": sid,
                "state": cfg["state"],
                "model_status": cfg["model_status"],
                "regime_class": cls,
                "confidence": 0.85,
                "feature_snapshot": {
                    "profile": {"primary_axis": axis, "horizon": horizon},
                    "axes": {
                        axis: {"available": True, "state": ax_state, "horizon": horizon},
                        "macro": {"available": True, "state": cfg["macro"], "as_of_session": sess[:7]},
                    },
                    "macro_environment": cfg["macro"],
                },
            }
        if method == "GET" and path.startswith("/api/registry/regime/market-state/TW/"):
            return 200, {
                "market": "TW",
                "session": sess,
                "market_states": [
                    {
                        "market_state_id": f"TW:{sess}:set1:r1",
                        "market": "TW",
                        "session": sess,
                        "model_set_version": "set1",
                        "model_status": cfg["model_status"],
                        "revision": 1,
                        "state": "proposed" if cfg["state"] == "proposed" else "current",
                        "axes": {
                            "volatility": {"short": {"available": True, "state": cfg["vol"], "horizon": "short"}},
                            "trend": {"medium": {"available": True, "state": cfg["trend"], "horizon": "medium"}},
                            "macro": {"available": True, "state": cfg["macro"], "as_of_session": sess[:7]},
                        },
                    }
                ],
            }
        raise AssertionError((method, path))

    controller.owners.regime_transport = transport

    # Session 1 (2026-11-12): baseline governed observation, no transition yet
    c1 = controller.consume_run(one, "job-1", {**run(1), "runId": "r-s1-one"})["persona_cycle"]
    assert not [c for c in calls if c["meeting_type"] == "MT-5"]
    assert {"meeting_type": "MT-5", "reason": "no_regime_transition"} in c1["meetings"]["not_fired"]

    # Session 2 (2026-11-13): macro expansion->slowdown and momentum volatility low->high -> MT-5 convened once
    current_session["value"] = "2026-11-13"
    c2_one = controller.consume_run(one, "job-1", {**run(2), "runId": "r-s2-one"})["persona_cycle"]
    mt5_calls = [c for c in calls if c["meeting_type"] == "MT-5"]
    assert len(mt5_calls) == 1
    assert any(c["meeting_type"] == "MT-5" for c in c2_one["meetings"]["convened"])
    cf = mt5_calls[0]["case_file"]
    assert set(cf["regime_records"]) == {"momentum", "carry"}
    assert cf["market_state"]["session"] == "2026-11-13"
    assert set(cf["pool_exposures"]) == {"persona-cycle-test", "persona-cycle-two"}
    assert cf["pool_exposures"]["persona-cycle-two"]["gross_exposure"] == 1.0
    assert [p["persona_id"] for p in mt5_calls[0]["participants"]] == [
        "persona-cycle-test",
        "persona-cycle-two",
        "persona-risk-officer",
    ]

    # Repeated read of the same transition in the same session by the second enrolled persona -> already_convened
    c2_two = controller.consume_run(two, "job-2", {**run(2), "jobId": "job-2", "runId": "r-s2-two"})["persona_cycle"]
    assert len([c for c in calls if c["meeting_type"] == "MT-5"]) == 1
    assert any(
        n.get("meeting_type") == "MT-5" and n.get("reason") == "already_convened"
        for n in c2_two["meetings"]["not_fired"]
    )

    # Session 3 (2026-11-16): carry primary axis trend transitions up->down -> MT-5 convened once with rotated chair
    current_session["value"] = "2026-11-16"
    c3_one = controller.consume_run(one, "job-1", {**run(3), "runId": "r-s3-one"})["persona_cycle"]
    mt5_calls = [c for c in calls if c["meeting_type"] == "MT-5"]
    assert len(mt5_calls) == 2
    assert any(c["meeting_type"] == "MT-5" for c in c3_one["meetings"]["convened"])
    assert mt5_calls[1]["metadata"]["chair_persona_id"] == "persona-cycle-two"
    assert mt5_calls[1]["metadata"]["regime_transitions"][0]["strategy_id"] == "carry"

    # Session 4 (2026-11-17): proposed/candidate records only -> never convenes MT-5
    current_session["value"] = "2026-11-17"
    c4_one = controller.consume_run(one, "job-1", {**run(4), "runId": "r-s4-one"})["persona_cycle"]
    assert len([c for c in calls if c["meeting_type"] == "MT-5"]) == 2
    assert {"meeting_type": "MT-5", "reason": "no_governed_regime_record"} in c4_one["meetings"]["not_fired"]




def _hosted_shaped(persona_id):
    return {"persona_id": persona_id, "tenant_id": "tenant-a", "lifecycle_state": "paper_owner",
            "workspace_ref": None, "metadata": {"persona_harness": {"active_version": 1}}}


def test_hosted_null_workspace_ref_derives_workspace_for_materialize_and_ensure(system):
    controller, state, owner, _ = system
    persona = {**next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-test"), "workspace_ref": None}
    packet = controller.consume_run(persona, "job-1", run(1))
    expected = "/home/node/.openclaw/workspaces/persona-cycle-test"
    assert state["materialize"][-1]["workspace_ref"] == expected
    assert state["ensures"][-1]["workspace_ref"] == expected
    assert packet["persona_cycle"]["agent_ensure"]["status"] == "ready"


def test_ensure_precedes_turn_once_per_persona_and_second_cycle_creates_no_duplicate(system):
    controller, state, owner, pool = system
    base = next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-test")
    personas = [{**base, "persona_id": pid, "workspace_ref": None}
                for pid in ("persona-cycle-test", "persona-hosted-b", "persona-hosted-c")]
    for cycle_no in (1, 2):
        before = len(state["ensures"])
        for persona in personas:
            packet = controller.consume_run(persona, "job-1", {**run(cycle_no), "runId": f"c{cycle_no}-{persona['persona_id']}"})
            assert not (packet["persona_cycle"]["failure_reason"] or "").startswith("agent_ensure")
        assert [e["persona"]["persona_id"] for e in state["ensures"][before:]] == [p["persona_id"] for p in personas]
    assert sorted(state["agents"]) == sorted(p["persona_id"] for p in personas)  # no duplicate agent
    assert state["paths"].index("/api/openclaw-adapter/agents/persona/ensure") < state["paths"].index("/v1/responses")


def test_failed_ensure_records_named_reason_and_never_attempts_turn(system):
    controller, state, owner, _ = system
    state["ensure_status"] = 503
    persona = next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-test")
    packet = controller.consume_run(persona, "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] == "agent_ensure_unavailable:http_503"
    assert state["turns"] == 0
    assert "persona-cycle-failure:agent_ensure_unavailable:http_503" in packet["audit_refs"]
    from services.control_plane.bff.loop_inventory import project_persona_cycle_health
    assert project_persona_cycle_health(persona, [packet])["status"] != "healthy"


def test_pending_lesson_count_from_governance_result():
    from services.persona.persona_cycle_controller import pending_lesson_count as n
    skipped = {"status": "skipped", "reason": "human_endorsement_required"}
    hosted = {"status": "completed", "results": [{"lesson_candidate_id": str(i), **skipped} for i in range(4)]}
    assert n(hosted) == 4
    mixed = {"status": "completed", "results": [
        {"status": "rejected"}, {"status": "merged"}, {"status": "skipped", "reason": "foreign_candidate"},
        {"status": "deferred", "reason": "review_budget_exhausted"}]}
    assert n(mixed) == 1
    assert n({"status": "completed", "results": []}) == 0
    assert n({"status": "unavailable", "reason": "x", "results": []}) is None
    assert n(None) is None


@pytest.mark.parametrize("body,expected", [
    ({"detail": "Insufficient role"}, "evaluate_refused:403:Insufficient role"),
    ({"detail": {"code": "session_data_incomplete"}}, "evaluate_refused:403:session_data_incomplete"),
    ({"detail": None}, "evaluate_refused:403:unknown"),
    (None, "evaluate_refused:403:unknown"),
    ("plain text", "evaluate_refused:403:unknown"),
])
def test_observe_regime_reports_a_refused_evaluate_for_every_body_shape(body, expected):
    out = regime_consumer.observe_regime(lambda m, p, b: (403, body), session="2026-11-18", strategy_ids=["s"])
    assert out["status"] == "unavailable" and out["reason"] == expected
    assert "AttributeError" not in out["reason"]


def test_turn_input_size_is_recorded_and_gateway_overflow_is_a_named_failure(system):
    controller, state, owner, _ = system
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    cycle = packet["persona_cycle"]
    assert cycle["turn_input_chars"] == len(state["prompts"][owner.list_json()[0]["persona_id"]])
    base = controller.turn_runner

    def overflow(persona_id, prompt, session_user=None):
        return SimpleNamespace(status="error", persona_id=persona_id, model="openclaw/x",
                               error="turn_context_overflow")
    controller.turn_runner = overflow
    refused = controller.consume_run(owner.list_json()[0], "job-1", run(2))
    assert refused["persona_cycle"]["failure_reason"] == "turn_context_overflow"
    assert refused["persona_cycle"]["turn_input_chars"] > 0

    def overflow_text(persona_id, prompt, session_user=None):
        return SimpleNamespace(status="completed", persona_id=persona_id, model="openclaw/x", error=None,
                               blocked_reason="turn_context_overflow", model_used="m", provider="p")
    controller.turn_runner = overflow_text
    refused = controller.consume_run(owner.list_json()[0], "job-1", run(3))
    assert refused["persona_cycle"]["failure_reason"] == "turn_context_overflow"
    controller.turn_runner = base


def test_observe_gives_the_cycle_at_most_the_last_twenty_pool_observation_rows(system):
    controller, state, owner, _ = system
    persona = next(p for p in owner.list_json() if p["persona_id"] == "persona-cycle-test")
    days = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    state["obs"]["pcb-cycle"] = [{"date": d, "source_ref": "pool_risk_snapshot:pool-cycle:" + d, "gross_notional": 0.5,
                                  "gross_limit": 2.0, "drawdown": 0.0, "drawdown_limit": 0.1} for d in days]
    bundle = controller.owners.observe(persona)
    assert [r["date"] for r in bundle["observations"]] == days[-20:]
