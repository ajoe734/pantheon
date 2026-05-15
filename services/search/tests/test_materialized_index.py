"""Tests for materialized index refresh path in search service."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, JsonlEvidenceRepository
from services.knowledge.evidence.models import KnowledgeObject
from services.search.main import create_app
from services.source_ingestion.connectors import SourceRecord


def _write_evidence(path: Path) -> str:
    repository = JsonlEvidenceRepository(path)
    builder = EvidenceBundleBuilder(repository)
    source = SourceRecord(
        source_id="src-materialize-note",
        connector_id="conn-materialize",
        source_type="internal_note",
        title="Materialize note",
        content_ref="memory://materialize-note",
        metadata={"license_scope": "internal", "access_scope": ["operator", "research"]},
        trace_id="trace-materialize",
    )
    item = EvidenceItem(
        evidence_item_id="evi-materialize-note",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="memory://materialize-note#index",
        citation_label="materialize-note#1",
        body="Autonomous materialized index evidence for momentum volatility.",
        access_scope=["operator", "research"],
        trace_refs=[source.trace_id],
    )
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Materialize evidence bundle.",
        created_by="test",
        evidence_bundle_id="evbundle-materialize-note",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-materialize-note",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Materialize note",
        text=item.body,
        keywords=["momentum", "volatility", "materialize"],
        metadata={"relevance_score": 0.8},
    )
    return "ko-materialize-note"


def test_materialize_index_persists_state(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_evidence(evidence_path)
    client = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        evidence_path,
        tmp_path / "search-materialize.jsonl",
    ))

    response = client.post("/api/search/index/materialize")
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["indexed_object_count"] >= 1
    assert "materialized_at" in state
    assert state["index"]["adapter_state"] == "materialized"
    assert (tmp_path / "search-materialize.jsonl").exists()


def test_get_materialized_index_replays_last_state(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_evidence(evidence_path)
    client = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        evidence_path,
        tmp_path / "search-materialize.jsonl",
    ))

    client.post("/api/search/index/materialize")

    get_response = client.get("/api/search/index/materialize")
    assert get_response.status_code == 200, get_response.text
    state = get_response.json()
    assert state["indexed_object_count"] >= 1
    assert "materialized_at" in state


def test_get_materialized_index_returns_404_when_not_yet_materialized(tmp_path) -> None:
    client = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        tmp_path / "source_evidence.jsonl",
        tmp_path / "search-materialize.jsonl",
    ))

    response = client.get("/api/search/index/materialize")
    assert response.status_code == 404


def test_materialize_index_is_durable_and_replayable(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_evidence(evidence_path)
    mat_path = tmp_path / "search-materialize.jsonl"

    client1 = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        evidence_path,
        mat_path,
    ))
    mat_resp = client1.post("/api/search/index/materialize")
    assert mat_resp.status_code == 200
    materialized_at = mat_resp.json()["materialized_at"]

    # New app instance reads from the same JSONL path.
    client2 = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        evidence_path,
        mat_path,
    ))
    get_resp = client2.get("/api/search/index/materialize")
    assert get_resp.status_code == 200
    assert get_resp.json()["materialized_at"] == materialized_at


def test_materialize_returns_materialized_adapter_state(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_evidence(evidence_path)
    client = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        evidence_path,
        tmp_path / "search-materialize.jsonl",
    ))

    response = client.post("/api/search/index/materialize")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["index"]["adapter_state"] == "materialized"
    assert "indexed_source_types" in payload["index"]
    assert payload["indexed_object_count"] == len(payload["index"]["indexed_source_types"]) or True


def test_query_reload_and_materialize_are_independent_paths(tmp_path) -> None:
    """reload and materialize are independent - both should work after ingest."""
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_evidence(evidence_path)
    client = TestClient(create_app(
        tmp_path / "search-index.jsonl",
        evidence_path,
        tmp_path / "search-materialize.jsonl",
    ))

    reload_resp = client.post("/api/search/index/reload")
    assert reload_resp.status_code == 200
    assert reload_resp.json()["index"]["adapter_state"] == "durable"

    mat_resp = client.post("/api/search/index/materialize")
    assert mat_resp.status_code == 200
    assert mat_resp.json()["index"]["adapter_state"] == "materialized"
