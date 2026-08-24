"""Unit tests for research finding memory writeback to persona and institutional stores."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.memory.institutional_memory_store import (
    InstitutionalMemoryStore,
    KnowledgeType,
    SourceEventType,
    WriteAuthority,
    get_store as get_inst_store,
    reset_store as reset_inst_store,
)
from services.memory.learn_feedback_writeback import (
    LearnFeedbackUnauthorizedError,
    LearnFeedbackWritebackError,
    write_learn_feedback,
)
from services.memory.main import app
from services.memory.persona_memory_store import (
    PersonaMemoryStore,
    PersonaMemoryType,
    PersonaSourceEventType,
    PersonaWriteAuthority,
    get_store as get_persona_store,
    reset_store as reset_persona_store,
)


@pytest.fixture(autouse=True)
def clean_memory_stores(tmp_path, monkeypatch):
    data_dir = tmp_path / "memory_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(data_dir / "inst_mem.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(data_dir / "persona_mem.json"))
    reset_inst_store()
    reset_persona_store()
    yield
    reset_inst_store()
    reset_persona_store()


def test_research_finding_published_success():
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    payload = {
        "source_event_type": "research_finding_published",
        "source_event_id": "rrun-20260824-001",
        "write_authority": "research-svc",
        "sponsor_persona_id": "persona-tw-equity",
        "summary": "Mean reversion pattern confirmed over 3-year TW equity dataset with positive sharpe.",
        "headline": "TW Equity Mean Reversion Alpha",
        "confidence": 0.88,
        "evidence_refs": [
            {"type": "evidence", "id": "ev-tw-001", "source_url": "https://example.com/tw-data"},
            "ev-tw-002",
        ],
        "dataset_refs": ["dataset://tw-equity-daily-v2"],
        "license_scope": "CC-BY-4.0",
        "allowed_use": ["research", "derived_memory", "internal_strategy"],
        "supersedes": [],
        "contradicts": [],
        "expires_at": "2028-08-24T00:00:00Z",
        "trace_id": "trace-tw-001",
        "tags": ["mean_reversion", "tw_equity"],
    }

    result = write_learn_feedback(
        payload,
        persona_store=persona_store,
        institutional_store=inst_store,
    )

    assert result["created"] is True
    assert result["source_event_type"] == "research_finding_published"
    assert result["source_event_id"] == "rrun-20260824-001"
    assert result["sponsor_persona_id"] == "persona-tw-equity"
    assert len(result["persona_memory_ids"]) == 1
    assert result["institutional_entry_id"] is not None

    # Verify Persona Memory Entry
    p_entry = persona_store.get(result["persona_memory_ids"][0])
    assert p_entry is not None
    assert p_entry.persona_id == "persona-tw-equity"
    assert p_entry.memory_type == PersonaMemoryType.STRATEGY_LESSON.value
    assert p_entry.source_event_type == PersonaSourceEventType.RESEARCH_FINDING_PUBLISHED.value
    assert p_entry.write_authority == PersonaWriteAuthority.RESEARCH_SVC.value
    assert p_entry.content["summary"] == payload["summary"]
    p_payload = p_entry.content["structured_payload"]
    assert p_payload["dataset_refs"] == ["dataset://tw-equity-daily-v2"]
    assert p_payload["license_scope"] == "CC-BY-4.0"
    assert p_payload["allowed_use"] == ["research", "derived_memory", "internal_strategy"]
    assert p_payload["confidence"] == 0.88
    assert p_payload["trace_id"] == "trace-tw-001"

    # Verify Institutional Memory Entry
    inst_entry = inst_store.get(result["institutional_entry_id"])
    assert inst_entry is not None
    assert inst_entry.knowledge_type == KnowledgeType.RESEARCH_FINDING.value
    assert inst_entry.source_event_type == SourceEventType.RESEARCH_FINDING_PUBLISHED.value
    assert inst_entry.write_authority == WriteAuthority.RESEARCH_SVC.value
    assert inst_entry.content["headline"] == "TW Equity Mean Reversion Alpha"
    assert inst_entry.expires_at == "2028-08-24T00:00:00Z"
    inst_payload = inst_entry.content["structured_payload"]
    assert inst_payload["license_scope"] == "CC-BY-4.0"
    assert inst_payload["confidence"] == 0.88
    assert inst_payload["dataset_refs"] == ["dataset://tw-equity-daily-v2"]


def test_research_finding_writeback_idempotent():
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    payload = {
        "source_event_type": "research_finding_published",
        "source_event_id": "rrun-20260824-002",
        "write_authority": "research-svc",
        "sponsor_persona_id": "persona-tw-equity",
        "summary": "First run finding",
        "evidence_refs": ["ev-001"],
        "dataset_refs": ["dataset://v1"],
    }

    res1 = write_learn_feedback(payload, persona_store=persona_store, institutional_store=inst_store)
    assert res1["created"] is True

    res2 = write_learn_feedback(payload, persona_store=persona_store, institutional_store=inst_store)
    assert res2["created"] is False
    assert res2["persona_memory_ids"] == res1["persona_memory_ids"]
    assert res2["institutional_entry_ids"] == res1["institutional_entry_ids"]

    # Verify count didn't increase
    assert len(persona_store.list(active_only=False)) == 1
    assert len(inst_store.list(active_only=False)) == 1


def test_unauthorized_source_authorities_forbidden():
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    # Raw Source ingestion cannot write memory
    with pytest.raises(LearnFeedbackUnauthorizedError):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "src-001",
                "write_authority": "source-ingestion-svc",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Attempt by source ingest",
                "evidence_refs": ["ev-001"],
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )

    # Search cannot write memory
    with pytest.raises(LearnFeedbackUnauthorizedError):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "search-001",
                "write_authority": "search-svc",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Attempt by search svc",
                "evidence_refs": ["ev-001"],
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )

    # Operator cannot write memory directly via learn feedback
    with pytest.raises(LearnFeedbackUnauthorizedError):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "op-001",
                "write_authority": "operator",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Attempt by operator",
                "evidence_refs": ["ev-001"],
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )


def test_license_restrictions_prohibit_derived_memory():
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    # Prohibited in allowed_use
    with pytest.raises(LearnFeedbackWritebackError, match="prohibits derived memory"):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "rrun-restricted-01",
                "write_authority": "research-svc",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Restricted finding",
                "evidence_refs": ["ev-001"],
                "allowed_use": ["raw_only", "no_derivative"],
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )

    # Prohibited license_scope
    with pytest.raises(LearnFeedbackWritebackError, match="license_scope"):
        write_learn_feedback(
            {
                "source_event_type": "research_finding_published",
                "source_event_id": "rrun-restricted-02",
                "write_authority": "research-svc",
                "sponsor_persona_id": "persona-tw-equity",
                "summary": "Restricted finding",
                "evidence_refs": ["ev-001"],
                "license_scope": "prohibited",
            },
            persona_store=persona_store,
            institutional_store=inst_store,
        )


def test_supersession_propagation():
    persona_store = PersonaMemoryStore()
    inst_store = InstitutionalMemoryStore()

    payload_old = {
        "source_event_type": "research_finding_published",
        "source_event_id": "rrun-finding-v1",
        "write_authority": "research-svc",
        "sponsor_persona_id": "persona-tw-equity",
        "summary": "Old finding v1",
        "evidence_refs": ["ev-001"],
        "dataset_refs": ["dataset://v1"],
    }
    res_old = write_learn_feedback(payload_old, persona_store=persona_store, institutional_store=inst_store)
    old_inst_id = res_old["institutional_entry_id"]
    old_p_id = res_old["persona_memory_ids"][0]

    assert inst_store.get(old_inst_id).is_active is True
    assert persona_store.get(old_p_id).is_active is True

    # Publish new finding that supersedes old finding
    payload_new = {
        "source_event_type": "research_finding_published",
        "source_event_id": "rrun-finding-v2",
        "write_authority": "research-svc",
        "sponsor_persona_id": "persona-tw-equity",
        "summary": "New finding v2 superseding v1",
        "evidence_refs": ["ev-002"],
        "dataset_refs": ["dataset://v2"],
        "supersedes": [old_inst_id, "rrun-finding-v1"],
    }
    res_new = write_learn_feedback(payload_new, persona_store=persona_store, institutional_store=inst_store)
    new_inst_id = res_new["institutional_entry_id"]
    new_p_id = res_new["persona_memory_ids"][0]

    old_inst_entry = inst_store.get(old_inst_id)
    assert old_inst_entry.superseded_by == new_inst_id
    assert old_inst_entry.is_active is False

    old_p_entry = persona_store.get(old_p_id)
    assert old_p_entry.superseded_by == new_p_id
    assert old_p_entry.is_active is False

    # Active lists only return new entry
    assert len(inst_store.list(active_only=True)) == 1
    assert inst_store.list(active_only=True)[0].entry_id == new_inst_id
    assert len(persona_store.list(active_only=True)) == 1
    assert persona_store.list(active_only=True)[0].memory_id == new_p_id


def test_fastapi_http_endpoint_research_writeback():
    client = TestClient(app)

    payload = {
        "source_event_type": "research_finding_published",
        "source_event_id": "rrun-http-001",
        "write_authority": "research-svc",
        "sponsor_persona_id": "persona-tw-equity",
        "summary": "HTTP writeback verified finding",
        "evidence_refs": ["ev-http-001"],
        "dataset_refs": ["dataset://http-v1"],
        "license_scope": "MIT",
        "allowed_use": ["research", "derived_memory"],
    }

    # Initial writeback -> 201 Created
    resp = client.post("/api/memory/writebacks/learn-feedback", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["created"] is True
    assert len(data["persona_memory_ids"]) == 1

    # Idempotent replay -> 200 OK
    resp_replay = client.post("/api/memory/writebacks/learn-feedback", json=payload)
    assert resp_replay.status_code == 200, resp_replay.text
    assert resp_replay.json()["created"] is False

    # Unauthorized caller -> 403 Forbidden
    bad_payload = dict(payload, source_event_id="rrun-http-002", write_authority="source-ingestion-svc")
    resp_bad = client.post("/api/memory/writebacks/learn-feedback", json=bad_payload)
    assert resp_bad.status_code == 403, resp_bad.text
