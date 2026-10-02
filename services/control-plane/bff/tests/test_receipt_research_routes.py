"""Mounted Research/Job actions use admission, then the existing owners."""
import json
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error, require_operator_role, require_read_role
from services.control_plane.bff.command_adapters import registry
from services.control_plane.bff.command_adapters.experiment_adapter import ExperimentCommandAdapter
from services.control_plane.bff.command_adapters.job_adapter import JobCommandAdapter
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.models import CommandType, ObjectType, utc_now
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.research_commands import ResearchCommandsPort
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.research.routes.experiments import create_research_experiments_router
from services.control_plane.bff.tests.test_receipt_owner_routes import identity
from services.control_plane.bff.tests.test_research_jobs_action_receipts import research_client
from services.research.write_owner import ResearchWriteOwner


class DiskStore:
    def __init__(self, path):
        self.path = path
        path.write_text("{}")

    def put(self, key, payload):
        records = json.loads(self.path.read_text())
        records[key] = payload
        self.path.write_text(json.dumps(records))

    def get(self, key):
        return json.loads(self.path.read_text()).get(key)

    def list_all(self, **kwargs):
        return list(json.loads(self.path.read_text()).values())


def test_experiment_admission_changes_owner_once(tmp_path, monkeypatch):
    stores = [DiskStore(tmp_path / f"{name}.json") for name in ("tickets", "experiments", "notes")]
    owner = ResearchWriteOwner(tickets_store=stores[0], experiments_store=stores[1], notes_store=stores[2])
    exp = owner.create_research_experiment(ticket_id="ticket", experiment_name="test",
        strategy_selector={}, parameter_set={}, run_config={}, launch_context={})
    exp_id = exp["experiment_id"]
    monkeypatch.setattr(registry, "_DEFAULT_ADAPTERS", [ExperimentCommandAdapter(research_write_owner_factory=lambda: owner)])
    ports = create_read_surface_ports(research_knowledge_source=DefaultResearchKnowledgeSourcePort(research_write_owner=owner))
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    service = CommandAdapterService(command_store=store, read_surface=ports, extract_identity=identity)
    app = FastAPI()
    app.include_router(create_research_experiments_router(
        read_surface=ports, extract_identity=identity, require_read_role=require_read_role,
        require_operator_role=require_operator_role, bff_error=bff_error, utc_now=utc_now,
        submit_experiment_action=lambda typ, eid, action, key, ident, body, **ctx:
            service.submit_resource_action(ObjectType.EXPERIMENT, eid, action, key, ident, body,
                                           CommandType.EXPERIMENT_ACTION, **ctx),
    ))
    client = TestClient(app)
    path = f"/bff/experiments/{exp_id}/actions/cancel"
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "cancel"}
    assert client.post(path, json={}).status_code == 401
    result = client.post(path, json={}, headers=headers)
    assert result.status_code == 202, result.text
    record = store.get_command_by_idempotency_key("cancel", operator_id="tenant-a")
    assert record["status"] == "executed", record
    persisted = stores[1].path.read_text()
    assert json.loads(persisted)[exp_id]["status"] == "canceled"
    assert client.post(path, json={}, headers=headers).status_code == 202
    assert stores[1].path.read_text() == persisted
    assert ports.get_experiment_bff(exp_id)["status"] == "canceled"
    assert client.post(path.replace("cancel", "promote"), json={}, headers=headers).status_code == 410


def test_job_admission_executes_real_orchestrator(research_client, tmp_path, monkeypatch):
    task = research_client.post("/api/research-orchestrator/tasks", json={"title": "bounded run", "objective": "test", "actor_id": "tenant-a"}).json()
    run = research_client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={"adapter": "stub", "requested_mode": "stub", "dispatch_mode": "stub"}).json()
    job_id = "job-orchestrator-" + run["run_id"]

    def post(url, payload, headers):
        result = research_client.post(urlsplit(url).path, json=payload, headers=headers)
        return result.status_code, result.json()

    adapter = JobCommandAdapter(research_commands_port=ResearchCommandsPort(base_url="http://isolated", http_post=post))
    monkeypatch.setattr(registry, "_DEFAULT_ADAPTERS", [adapter])

    class Reads:
        def get_job_bff(self, jid):
            result = research_client.get("/api/research-orchestrator/runs/" + jid.removeprefix("job-orchestrator-"))
            return {"job_id": jid, **result.json()} if result.status_code == 200 else None

    reads = Reads()
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    service = CommandAdapterService(command_store=store, read_surface=reads, extract_identity=identity)
    app = FastAPI()
    app.include_router(create_jobs_router(
        read_surface=reads, extract_identity=identity, require_read_role=require_read_role,
        require_operator_role=require_operator_role, bff_error=bff_error, utc_now=utc_now,
        page_slice=lambda rows, token, size: (rows, None), read_surface_meta=lambda *a, **k: {},
        dataset_surface_status=lambda *a, **k: {"status": "ok"},
        raise_if_read_surface_unavailable=lambda *a, **k: None,
        reject_body_idempotency_key=lambda body: None,
        resolve_final_idempotency_key=lambda key, other: key or other,
        submit_job_action=lambda jid, action, key, ident, body, **ctx:
            service.submit_resource_action(ObjectType.JOB, jid, action, key, ident, body, CommandType.JOB_ACTION, **ctx),
    ))
    client = TestClient(app)
    path = f"/bff/jobs/{job_id}/actions/cancel"
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "cancel"}
    assert client.post(path, json={}).status_code == 401
    result = client.post(path, json={}, headers=headers)
    assert result.status_code == 202, result.text
    record = store.get_command_by_idempotency_key("cancel", operator_id="tenant-a")
    assert record["status"] == "executed", record
    readback = reads.get_job_bff(job_id)
    assert readback["status"] == "canceled"
    assert client.post(path, json={}, headers=headers).status_code == 202
    assert reads.get_job_bff(job_id) == readback
    assert client.post(path.replace("cancel", "archive"), json={}, headers=headers).status_code == 410
