"""Tests for SD-SRCM-07 reviewed external evidence to memory writeback, outbox, and lineage."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.memory.institutional_memory_store import (
    InstitutionalMemoryStore,
    KnowledgeType,
    SourceEventType,
    WriteAuthority,
    reset_store as reset_inst_store,
)
from services.memory.learn_feedback_writeback import (
    LearnFeedbackUnauthorizedError,
    write_learn_feedback,
)
from services.memory.persona_memory_store import (
    PersonaMemoryStore,
    PersonaMemoryType,
    PersonaSourceEventType,
    PersonaWriteAuthority,
    reset_store as reset_persona_store,
)
import services.research.main as research_main
from services.research.store import build_research_orchestrator_store
from services.research.memory_writeback_worker import MemoryWritebackWorker
from services.research.research_memory_outbox import (
    ResearchMemoryEligibilityError,
    ResearchMemoryOutboxRecord,
    ResearchMemoryOutboxStore,
    get_outbox_store,
    reset_outbox_store,
    validate_research_memory_writeback_eligibility,
)
from services.research.retrieval_influence import (
    ResearchRetrievalInfluenceError,
    ResearchRetrievalInfluenceRecord,
    ResearchRetrievalInfluenceStore,
    get_influence_store,
    project_lineage_inspiration_edge,
    reset_influence_store,
)


@pytest.fixture(autouse=True)
def clean_environment(tmp_path, monkeypatch):
    data_dir = tmp_path / "research_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    mem_dir = tmp_path / "memory_data"
    mem_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(mem_dir))

    research_main.store = build_research_orchestrator_store(data_dir)
    reset_inst_store()
    reset_persona_store()
    reset_outbox_store()
    reset_influence_store()
    yield
    reset_inst_store()
    reset_persona_store()
    reset_outbox_store()
    reset_influence_store()


def _setup_completed_run_and_artifact(client: TestClient) -> tuple[str, str]:
    """Helper to create a task, dispatch a run, complete it, and attach a candidate artifact."""
    # 1. Create task
    t_resp = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "TW Equity Alpha Strategy Research",
            "objective": "Evaluate mean reversion signals on Taiwan market",
            "actor_id": "operator",
        },
    )
    assert t_resp.status_code == 201, t_resp.text
    task_id = t_resp.json()["task_id"]

    # 2. Dispatch run
    r_resp = client.post(
        f"/api/research-orchestrator/tasks/{task_id}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "parameters": {
                "strategy_id": "tw-equity-mean-rev",
                "version": "1.0.0",
                "dataset_version_id": "dataset://tw-equity-daily-v2",
                "code_version": "v1.0.0",
            },
        },
    )
    assert r_resp.status_code == 201, r_resp.text
    run_id = r_resp.json()["run_id"]

    # 3. Complete run
    c_resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/complete",
        json={"status": "completed", "summary": "Strategy backtest and review completed successfully."},
    )
    assert c_resp.status_code == 200, c_resp.text

    # 4. Attach artifact
    valid_sha = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    a_resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/artifacts",
        json={
            "title": "TW Equity Mean Reversion Finding",
            "artifact_type": "strategy_spec",
            "storage_ref": "file:///tmp/pantheon/strategy_spec.json",
            "checksum": valid_sha,
            "registry_hints": {
                "artifact_state": "candidate",
                "evidence_eligible": True,
                "strategy_id": "tw-equity-mean-rev",
                "version": "1.0.0",
                "source_strategy_spec_id": "spec-tw-001",
                "source_evidence_refs": ["ev-tw-001", "ev-tw-002"],
                "source_dataset_refs": ["dataset://tw-equity-daily-v2"],
            },
            "metadata": {
                "evidence_eligible": True,
                "evidence_refs": ["ev-tw-001", "ev-tw-002"],
                "source_dataset_refs": ["dataset://tw-equity-daily-v2"],
                "license_scope": "CC-BY-4.0",
                "allowed_use": ["research", "derived_memory"],
            },
        },
    )
    assert a_resp.status_code == 201, a_resp.text
    artifact_id = a_resp.json()["artifact_id"]

    return run_id, artifact_id


def test_unreviewed_or_running_run_cannot_write_memory():
    client = TestClient(research_main.app)

    # 1. Create task and run but leave it running (not completed)
    t_resp = client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "Ongoing Research", "objective": "In-flight evaluation"},
    )
    assert t_resp.status_code == 201, t_resp.text
    task_id = t_resp.json()["task_id"]
    r_resp = client.post(
        f"/api/research-orchestrator/tasks/{task_id}/runs",
        json={"adapter": "stub", "requested_mode": "stub", "dispatch_mode": "stub"},
    )
    assert r_resp.status_code == 201, r_resp.text
    run_id = r_resp.json()["run_id"]

    # 2. Attach artifact
    valid_sha = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    a_resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/artifacts",
        json={
            "title": "Unfinished Run Artifact",
            "artifact_type": "research_report",
            "storage_ref": "file:///tmp/report.pdf",
            "checksum": valid_sha,
            "metadata": {"evidence_refs": ["ev-001"]},
        },
    )
    assert a_resp.status_code == 201, a_resp.text
    artifact_id = a_resp.json()["artifact_id"]

    # 3. Attempt memory writeback on in-flight run -> 409 Conflict
    resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/memory-writeback",
        json={"artifact_id": artifact_id},
    )
    assert resp.status_code == 409, resp.text


def test_artifact_quality_and_license_gates():
    run = {
        "run_id": "rrun-test-001",
        "task_id": "rtask-test-001",
        "status": "completed",
        "adapter": "stub",
    }

    # Missing evidence refs -> raises error
    art_no_evidence = {
        "artifact_id": "art-01",
        "storage_status": "resolvable",
        "checksum_status": "valid",
        "artifact_state": "candidate",
        "evidence_eligible": True,
        "source_evidence_refs": [],
        "source_dataset_refs": ["dataset://v1"],
    }
    with pytest.raises(ResearchMemoryEligibilityError, match="source_evidence_refs"):
        validate_research_memory_writeback_eligibility(run, art_no_evidence)

    # Missing dataset refs -> raises error
    art_no_dataset = {
        "artifact_id": "art-02",
        "storage_status": "resolvable",
        "checksum_status": "valid",
        "artifact_state": "candidate",
        "evidence_eligible": True,
        "source_evidence_refs": ["ev-001"],
        "source_dataset_refs": [],
    }
    with pytest.raises(ResearchMemoryEligibilityError, match="dataset lineage"):
        validate_research_memory_writeback_eligibility(run, art_no_dataset)

    # Prohibited license scope -> raises error
    art_bad_license = {
        "artifact_id": "art-03",
        "storage_status": "resolvable",
        "checksum_status": "valid",
        "artifact_state": "candidate",
        "evidence_eligible": True,
        "source_evidence_refs": ["ev-001"],
        "source_dataset_refs": ["dataset://v1"],
        "metadata": {"license_scope": "prohibited"},
    }
    with pytest.raises(ResearchMemoryEligibilityError, match="prohibits derived memory"):
        validate_research_memory_writeback_eligibility(run, art_bad_license)

    # Prohibited allowed_use -> raises error
    art_bad_use = {
        "artifact_id": "art-04",
        "storage_status": "resolvable",
        "checksum_status": "valid",
        "artifact_state": "candidate",
        "evidence_eligible": True,
        "source_evidence_refs": ["ev-001"],
        "source_dataset_refs": ["dataset://v1"],
        "metadata": {"allowed_use": ["raw_only", "no_derivative"]},
    }
    with pytest.raises(ResearchMemoryEligibilityError, match="Allowed-use policy"):
        validate_research_memory_writeback_eligibility(run, art_bad_use)

    # Valid artifact passes eligibility
    art_valid = {
        "artifact_id": "art-05",
        "storage_status": "resolvable",
        "checksum_status": "valid",
        "artifact_state": "candidate",
        "evidence_eligible": True,
        "source_evidence_refs": ["ev-001"],
        "source_dataset_refs": ["dataset://v1"],
        "metadata": {"license_scope": "CC-BY-4.0", "allowed_use": ["research", "derived_memory"]},
    }
    res = validate_research_memory_writeback_eligibility(run, art_valid)
    assert res["eligible"] is True


def test_forbid_source_ingest_and_search_from_writing_memory():
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    with pytest.raises(LearnFeedbackUnauthorizedError):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "ingest-001",
                "write_authority": "source-ingestion-svc",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Source ingest attempt",
                "evidence_refs": ["ev-001"],
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )

    with pytest.raises(LearnFeedbackUnauthorizedError):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "search-001",
                "write_authority": "search-svc",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Search svc attempt",
                "evidence_refs": ["ev-001"],
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )


def test_successful_reviewed_memory_writeback_and_outbox_flow():
    client = TestClient(research_main.app)
    run_id, artifact_id = _setup_completed_run_and_artifact(client)

    # Use direct writeback mock in worker to verify outbox flow in-process
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    def direct_writer(payload):
        return write_learn_feedback(payload, persona_store=persona_store, institutional_store=inst_store)

    outbox_store = get_outbox_store()
    worker = MemoryWritebackWorker(outbox_store=outbox_store, direct_writeback=direct_writer)

    # 1. Perform memory writeback via research API
    resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/memory-writeback",
        json={
            "artifact_id": artifact_id,
            "sponsor_persona_id": "persona-tw-equity",
            "summary": "Mean reversion alpha candidate verified",
            "confidence": 0.92,
            "auto_deliver": False,  # Keep in outbox pending to test delivery steps
        },
    )
    assert resp.status_code == 201, resp.text
    outbox_data = resp.json()
    outbox_id = outbox_data["outbox_id"]
    assert outbox_data["status"] == "pending"

    # 2. List outbox records
    list_resp = client.get("/api/research-orchestrator/outbox/memory")
    assert list_resp.status_code == 200
    assert any(r["outbox_id"] == outbox_id for r in list_resp.json())

    # 3. Deliver via worker
    deliv_res = worker.deliver_record(outbox_id)
    assert deliv_res["status"] == "delivered"
    assert "receipt" in deliv_res

    # 4. Check outbox record status
    rec = outbox_store.get_record(outbox_id)
    assert rec.status == "delivered"
    assert rec.delivered_at is not None
    assert rec.receipt["created"] is True

    # 5. Check memory stores
    assert len(persona_store.list(active_only=True)) == 1
    assert len(inst_store.list(active_only=True)) == 1

    # 6. Idempotent replay returns same outbox record
    replay_resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/memory-writeback",
        json={"artifact_id": artifact_id},
    )
    assert replay_resp.status_code == 201, replay_resp.text
    assert replay_resp.json()["outbox_id"] == outbox_id


def test_outbox_retry_and_drain_on_transient_failure():
    outbox_store = get_outbox_store()
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    record = ResearchMemoryOutboxRecord(
        outbox_id="mout-transient-001",
        run_id="rrun-transient-001",
        task_id="rtask-001",
        artifact_id="art-001",
        source_event_type="research_finding_published",
        source_event_id="rrun-transient-001",
        sponsor_persona_id="persona-tw-equity",
        summary="Transient test finding",
        headline="Transient Alpha",
        confidence=0.8,
        evidence_refs=["ev-001"],
        dataset_refs=["dataset://v1"],
        license_scope="MIT",
        allowed_use=["research", "derived_memory"],
        supersedes=[],
        contradicts=[],
        expires_at=None,
        trace_id="trace-001",
        status="pending",
    )
    outbox_store.create_record(record)

    # 1. Delivery fails due to simulated network failure
    def failing_writer(payload):
        raise ConnectionError("Temporary connection timeout")

    worker_failing = MemoryWritebackWorker(outbox_store=outbox_store, direct_writeback=failing_writer)
    res_fail = worker_failing.deliver_record("mout-transient-001")
    assert res_fail["status"] == "failed"
    assert res_fail["retry_count"] == 1

    rec_failed = outbox_store.get_record("mout-transient-001")
    assert rec_failed.status == "failed"
    assert "Temporary connection timeout" in rec_failed.last_error

    # 2. Worker now succeeds on drain
    def successful_writer(payload):
        return write_learn_feedback(payload, persona_store=persona_store, institutional_store=inst_store)

    worker_ok = MemoryWritebackWorker(outbox_store=outbox_store, direct_writeback=successful_writer)
    drain_res = worker_ok.drain()
    assert drain_res["delivered"] == 1
    assert drain_res["failed"] == 0

    rec_delivered = outbox_store.get_record("mout-transient-001")
    assert rec_delivered.status == "delivered"


def test_retrieval_counter_evidence_and_influence_proof():
    client = TestClient(research_main.app)
    run_id, _ = _setup_completed_run_and_artifact(client)

    # 1. Record retrieval influence with counter-evidence
    influence_payload = {
        "persona_id": "persona-tw-equity",
        "query_snapshot": {
            "query": "TW equity mean reversion short term",
            "ranker": "hybrid_v2",
            "cutoff_date": "2026-08-24",
        },
        "selected_memory_refs": ["mem-tw-001", "pmem-tw-001"],
        "selected_evidence_refs": ["ev-tw-001"],
        "counter_evidence_query": "TW equity momentum trend continuation",
        "counter_evidence_results": [
            {"evidence_id": "ev-tw-trend-01", "finding": "Momentum prevails during earnings season"}
        ],
        "influence_assessment": "Mean reversion hypothesis conditioned to non-earnings periods based on counter-evidence.",
        "influence_weight": 0.85,
        "influence_state": "confirmed_influence",
        "model_ranker_version": "hybrid_ranker_v2.1",
        "resulting_seed_ref": "seed-tw-meanrev-v2",
    }

    resp = client.post(
        f"/api/research-orchestrator/runs/{run_id}/retrieval-influence",
        json=influence_payload,
    )
    assert resp.status_code == 201, resp.text
    record = resp.json()
    assert record["retrieval_id"].startswith("mret-")
    assert record["influence_weight"] == 0.85
    assert record["influence_state"] == "confirmed_influence"
    assert len(record["counter_evidence_results"]) == 1

    # 2. List records for run
    list_resp = client.get(f"/api/research-orchestrator/runs/{run_id}/retrieval-influence")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # 3. Test lineage fallback projection never synthesizes constant 1.0
    lineage_edge_without_influence = {
        "id": "lineage-001",
        "from_artifact_id": "art-upstream",
        "to_artifact_id": "art-target",
        "edge_type": "inspired_by",
    }
    insp_edge = project_lineage_inspiration_edge(lineage_edge_without_influence, "art-target")
    assert insp_edge is not None
    assert insp_edge["influence_weight"] is None
    assert insp_edge["influence_state"] == "influence_unknown"
    assert insp_edge["influence_weight"] != 1.0  # Must NEVER synthesize constant 1.0!

    # Edge with explicit weight preserves it
    lineage_edge_with_influence = {
        "id": "lineage-002",
        "from_artifact_id": "art-upstream",
        "to_artifact_id": "art-target",
        "edge_type": "derived_from",
        "influence_weight": 0.65,
        "influence_state": "confirmed_influence",
    }
    insp_edge_with_weight = project_lineage_inspiration_edge(lineage_edge_with_influence, "art-target")
    assert insp_edge_with_weight["influence_weight"] == 0.65
    assert insp_edge_with_weight["influence_state"] == "confirmed_influence"
