"""Exercise deployed writer entry points; substitute only outbound HTTP transport."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import threading
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from services.memory import main as memory
from services.governance import main as governance
from services.memory.test_main import JWT_SECRET, _make_jwt, _persona_auth_payload
from services.persona.learning_feedback_bridge import run_learning_feedback_bridge
from services.research import main as research
from services.research.memory_writeback_worker import MemoryWritebackWorker
from services.research.research_memory_outbox import (
    ResearchMemoryOutboxStore, get_outbox_store, reset_outbox_store,
)
from services.research.store import build_research_orchestrator_store
from services.research.tests.test_reviewed_memory_writeback import _setup_completed_run_and_artifact


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTHZ_MODE", raising=False)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTHZ_URL", "http://governance:8082")
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "institutional.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "persona.json"))
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", str(tmp_path / "research"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", _make_jwt(roles=["memory-writer"]))
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "test-evolution-service-credential")
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", "tenant-alpha")
    monkeypatch.setattr(research, "store", build_research_orchestrator_store(tmp_path / "research"))
    reset_outbox_store()
    mc, gc, rc = TestClient(memory.app), TestClient(governance.app), TestClient(research.app)
    requests = []
    source = {"decisions": [], "postmortems": [], "lose_response": False}

    def http(request, timeout=10):
        requests.append(request)
        url = request.full_url
        if url.startswith("http://evolution:8093"):
            assert request.get_header("Authorization") == "Bearer test-evolution-service-credential"
            assert request.get_header("X-tenant-id") == "tenant-alpha"
            return io.BytesIO(json.dumps(source["decisions"]).encode())
        if url.startswith("http://postmortems:8091"):
            return io.BytesIO(json.dumps(source["postmortems"]).encode())
        data = json.loads(request.data)
        if url.startswith("http://governance:8082"):
            response = gc.post("/api/governance/authz/check", json=data)
        else:
            assert url == "http://memory:8086/api/memory/writebacks/learn-feedback"
            assert request.get_header("Authorization").startswith("Bearer ey")
            response = mc.post("/api/memory/writebacks/learn-feedback", json=data, headers=dict(request.header_items()))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, response.text, {}, io.BytesIO(response.content))
        if source["lose_response"]:
            source["lose_response"] = False
            raise TimeoutError("Memory committed; response lost")
        return io.BytesIO(response.content)

    monkeypatch.setattr(urllib.request, "urlopen", http)
    yield mc, rc, requests, source
    reset_outbox_store()


def test_learning_worker_real_http_entry_replay_and_failure(runtime):
    _, _, requests, source = runtime
    source["decisions"] = [{
        "decision_id": "evo-1", "decision_state": "executed", "persona_id": "persona-alpha",
        "tenant_id": "tenant-alpha", "execution_result": {"status": "succeeded", "outcome_summary": "Paper mutation verified"},
    }]
    source["postmortems"] = [{
        "postmortem_id": "pm-1", "status": "published", "tenant_id": "tenant-alpha",
        "persona_capital_binding_id": "pcb-persona-alpha-paper", "root_cause": "Observed paper slippage",
    }]
    source["lose_response"] = True
    first = run_learning_feedback_bridge(evolution_api_url="http://evolution:8093", postmortems_api_url="http://postmortems:8091", memory_api_url="http://memory:8086", skip_openclaw_sync=True)
    assert first["errors"]
    second = run_learning_feedback_bridge(evolution_api_url="http://evolution:8093", postmortems_api_url="http://postmortems:8091", memory_api_url="http://memory:8086", skip_openclaw_sync=True)
    assert second["errors"] == []
    assert len(memory._store().list()) == len(memory._persona_store().list()) == 2
    assert {e.source_event_id for e in memory._persona_store().list()} == {"evo-1", "pm-1"}
    assert len([r for r in requests if r.get_method() == "POST"]) == 4


@pytest.mark.parametrize("event,authority", [
    ("pool_outcome", "persona-svc"), ("harness_experience", "persona-svc"),
    ("consultation_closed", "consultation-svc"),
])
def test_new_writer_http_whitelist_and_replay(runtime, event, authority):
    mc, _, _, _ = runtime
    payload = {"source_event_type": event, "source_event_id": "event-1", "write_authority": authority,
               "sponsor_persona_id": "persona-alpha", "summary": "Observed paper outcome", "evidence_refs": ["evidence:1"],
               "cue": "paper observation", "lesson": "retain controls", "outcome": "no_effect"}
    headers = {"Authorization": f"Bearer {_make_jwt(roles=[authority])}"}
    first = mc.post("/api/memory/writebacks/learn-feedback", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    assert mc.post("/api/memory/writebacks/learn-feedback", json=payload, headers=headers).status_code == 200
    assert len(memory._store().list()) == len(memory._persona_store().list()) == 1
    assert memory._persona_store().list()[0].tenant_id == "tenant-alpha"
    assert memory._persona_store().list()[0].content["structured_payload"]["outcome"] == "no_effect"
    payload["write_authority"] = "search-svc"
    assert mc.post("/api/memory/writebacks/learn-feedback", json=payload, headers=headers).status_code == 403
    payload["write_authority"] = authority
    wrong_role = {"Authorization": f"Bearer {_make_jwt(roles=['research-svc'])}"}
    assert mc.post("/api/memory/writebacks/learn-feedback", json=payload, headers=wrong_role).status_code == 403
    payload["tenant_id"] = "tenant-beta"
    assert mc.post("/api/memory/writebacks/learn-feedback", json=payload, headers=headers).status_code == 403


def test_research_http_entry_attribution_retry_restart_and_no_duplicates(runtime):
    _, rc, _, source = runtime
    run_id, artifact_id = _setup_completed_run_and_artifact(rc)
    task = research.store.get_task(research.get_run(run_id)["task_id"])
    task["constraints"] = {"research_demand": {"persona_id": "persona-alpha"}}
    research.store.put_task(task)
    path = f"/api/research-orchestrator/runs/{run_id}/memory-writeback"
    wrong = rc.post(path, json={"artifact_id": artifact_id, "sponsor_persona_id": "persona-other"})
    assert wrong.status_code == 400
    source["lose_response"] = True
    result = rc.post(path, json={"artifact_id": artifact_id}).json()
    assert result["status"] == "failed", result
    assert "response lost" in result["last_error"]
    assert result["sponsor_persona_id"] == "persona-alpha"
    # Reopen the durable queue after restart, then use the production HTTP worker.
    restarted = ResearchMemoryOutboxStore(get_outbox_store().data_dir)
    worker = MemoryWritebackWorker(outbox_store=restarted)
    assert worker.drain()["delivered"] == 1
    assert worker.retry(result["outbox_id"])["status"] == "delivered"
    assert len(memory._store().list()) == len(memory._persona_store().list()) == 1
    assert memory._persona_store().list()[0].persona_id == "persona-alpha"
    assert restarted.get_record(result["outbox_id"]).receipt["created"] is False


def test_retrieval_uses_existing_strict_jwt_and_real_governance(runtime):
    mc, _, _, _ = runtime
    operator = {"Authorization": f"Bearer {_make_jwt()}"}
    for visibility in ["persona_private", "persona_and_committee"]:
        entry = _persona_auth_payload(relevance_scope=visibility, tenant_id="tenant-alpha")
        response = mc.post("/api/memory/persona-entries", json=entry, headers=operator)
        assert response.status_code == 201, response.text
    response = mc.get("/api/memory/retrieve", params={"scope": "persona", "persona_id": "persona-alpha", "query": "", "actor_id": "memory-operator-1", "actor_roles": "operator", "session_id": "op-1"}, headers=operator)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    committee = {"Authorization": f"Bearer {_make_jwt(sub='committee-1', roles=['consultation_session'])}"}
    params = {"scope": "persona", "persona_id": "persona-alpha", "session_persona_id": "persona-alpha", "session_id": "consult-1", "query": "", "actor_id": "committee-1", "actor_roles": "consultation_session"}
    response = mc.get("/api/memory/retrieve", params=params, headers=committee)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["hits"][0]["entry"]["relevance_scope"] == "persona_and_committee"
    params["session_persona_id"] = "persona-other"
    assert mc.get("/api/memory/retrieve", params=params, headers=committee).status_code == 403


def test_compose_activates_both_workers():
    compose = yaml.safe_load((Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text())
    bridge = compose["services"]["learning-feedback-bridge"]
    assert "--poll" in bridge["command"]
    assert bridge["environment"]["MEMORY_API_URL"] == "http://memory:8086"
    research_env = compose["services"]["research-orchestrator-svc"]["environment"]
    assert research_env["RESEARCH_MEMORY_WORKER_ENABLED"] == "true"
    assert research_env["MEMORY_SVC_URL"] == "http://memory:8086"
    assert "PANTHEON_MEMORY_AUTH_TOKEN" in research_env


def test_research_startup_drains_queue_and_shutdown_collects_task(runtime, monkeypatch):
    _, rc, _, _ = runtime
    run_id, artifact_id = _setup_completed_run_and_artifact(rc)
    path = f"/api/research-orchestrator/runs/{run_id}/memory-writeback"
    response = rc.post(path, json={"artifact_id": artifact_id, "auto_deliver": False})
    assert response.status_code == 201, response.text
    delivered = threading.Event()
    http = urllib.request.urlopen

    def observe(request, timeout=10):
        result = http(request, timeout=timeout)
        if request.full_url.endswith("/writebacks/learn-feedback"):
            delivered.set()
        return result

    monkeypatch.setattr(urllib.request, "urlopen", observe)
    monkeypatch.setenv("RESEARCH_MEMORY_WORKER_ENABLED", "true")
    with TestClient(research.app):
        assert delivered.wait(timeout=5), "startup worker did not deliver the queued finding"
    assert research.app.state.memory_writeback_task.done()
    assert len(memory._persona_store().list()) == 1


def test_missing_credential_stays_queued_then_rotated_file_retries(runtime, monkeypatch, tmp_path):
    _, rc, requests, _ = runtime
    run_id, artifact_id = _setup_completed_run_and_artifact(rc)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTH_TOKEN")
    response = rc.post(f"/api/research-orchestrator/runs/{run_id}/memory-writeback", json={"artifact_id": artifact_id})
    record = response.json()
    assert record["status"] == "failed"
    assert "PANTHEON_MEMORY_AUTH_TOKEN" in record["last_error"]
    assert requests == []
    token_file = tmp_path / "existing-principal"
    token_file.write_text(_make_jwt(roles=["memory-writer"]))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN_FILE", str(token_file))
    assert MemoryWritebackWorker().drain()["delivered"] == 1
    assert len(memory._persona_store().list()) == 1


def test_missing_attribution_is_refused_without_outbox_record(runtime):
    _, rc, _, _ = runtime
    run_id, artifact_id = _setup_completed_run_and_artifact(rc)
    task = research.store.get_task(research.get_run(run_id)["task_id"])
    task["constraints"] = {}
    research.store.put_task(task)
    response = rc.post(f"/api/research-orchestrator/runs/{run_id}/memory-writeback", json={"artifact_id": artifact_id})
    assert response.status_code == 400, response.text
    assert "attribution" in response.text
    assert get_outbox_store().list_records() == []


def test_replay_repairs_partial_pair_through_http(runtime):
    mc, _, _, _ = runtime
    headers = {"Authorization": f"Bearer {_make_jwt(roles=['persona-svc'])}"}
    payload = {"source_event_type": "pool_outcome", "source_event_id": "partial-1", "write_authority": "persona-svc",
               "sponsor_persona_id": "persona-alpha", "summary": "Observed paper removal", "evidence_refs": ["event:remove"]}
    path = "/api/memory/writebacks/learn-feedback"
    first = mc.post(path, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    # Model a crash between the two durable stores, retaining the institutional half.
    memory._persona_store_path().unlink()
    replay = mc.post(path, json=payload, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.json()["persona_memory_ids"] == first.json()["persona_memory_ids"]
    assert len(memory._store().list()) == len(memory._persona_store().list()) == 1


def test_learning_bridge_records_missing_results_and_attribution(runtime):
    _, _, _, source = runtime
    source["decisions"] = [
        {"decision_id": "submitted", "decision_state": "executed", "persona_id": "persona-alpha",
         "execution_result": {"status": "submitted", "outcome_summary": "Dispatch accepted"}},
        {"decision_id": "no-owner", "decision_state": "executed",
         "execution_result": {"status": "succeeded", "outcome_summary": "Terminal owner receipt"}},
    ]
    report = run_learning_feedback_bridge(evolution_api_url="http://evolution:8093", postmortems_api_url="http://postmortems:8091", memory_api_url="http://memory:8086", skip_openclaw_sync=True)
    assert len(report["errors"]) == 2
    assert report["processed_decisions"] == 0
    assert memory._persona_store().list() == []
