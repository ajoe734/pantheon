from __future__ import annotations

from fastapi.testclient import TestClient

from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, JsonlEvidenceRepository
from services.knowledge.evidence.models import KnowledgeObject
from services.search.main import create_app
from services.source_ingestion.connectors import SourceRecord


def _write_durable_evidence(path) -> None:
    repository = JsonlEvidenceRepository(path)
    builder = EvidenceBundleBuilder(repository)
    source = SourceRecord(
        source_id="src-durable-note",
        connector_id="conn-durable-notes",
        source_type="internal_note",
        title="Durable momentum note",
        content_ref="memory://durable-note",
        metadata={"license_scope": "internal", "access_scope": ["operator", "research"]},
        trace_id="trace-src-durable-note",
    )
    item = EvidenceItem(
        evidence_item_id="evi-durable-note",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="memory://durable-note#search-index",
        citation_label="durable-note#1",
        body="Momentum volatility evidence is indexed from the durable source evidence store.",
        access_scope=["operator", "research"],
        trace_refs=[source.trace_id],
    )
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Durable momentum evidence.",
        created_by="test",
        evidence_bundle_id="evbundle-durable-note",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-durable-note",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Durable momentum note",
        text=item.body,
        keywords=["momentum", "volatility"],
        metadata={"relevance_score": 0.7, "updated_at": "2026-04-29T06:00:00Z"},
    )
    repository.add_knowledge_object(
        KnowledgeObject(
            knowledge_object_id="ko-private-durable-note",
            source_id=source.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title="Private durable momentum note",
            text="Momentum volatility evidence filtered before ranking.",
            source_type="internal_note",
            license_scope="internal",
            access_scope=["risk-committee"],
            keywords=["momentum", "volatility"],
            metadata={"relevance_score": 0.99},
        )
    )


def test_search_service_filters_before_ranking_and_replays_refs(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl"))
    body = {
        "request_id": "svc-search-001",
        "trace_id": "trace-svc-search-001",
        "query": "momentum volatility",
        "persona_id": "operator-workbench",
        "workspace_id": "research-workbench",
        "source_types": ["internal_note"],
        "documents": [
            {
                "result_id": "search-public",
                "match_type": "ticket",
                "title": "Public momentum note",
                "excerpt": "Momentum volatility evidence is available to research operators.",
                "content_ref": "/research/tickets/search-public",
                "relevance_score": 0.6,
            },
            {
                "result_id": "search-private",
                "match_type": "ticket",
                "title": "Private momentum note",
                "excerpt": "Momentum volatility evidence that must be filtered before ranking.",
                "content_ref": "/research/tickets/search-private",
                "access_scope": ["risk-committee"],
                "relevance_score": 0.99,
            },
        ],
        "access_context": {
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }

    response = client.post("/api/search/query", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert [item["result_id"] for item in payload["results"]] == ["search-public"]
    assert payload["rejected_items_count"] == 1
    assert payload["index_adapter"]["adapter_state"] == "request_documents_compat"
    assert payload["index_adapter"]["indexed_source_types"] == ["internal_note"]
    assert payload["index_snapshot"]["request_id"] == "svc-search-001"
    assert "answer_context" not in payload["index_snapshot"]["result_refs"][0]
    assert "raw_payload" not in payload["index_snapshot"]["result_refs"][0]

    replay = client.get("/api/search/snapshots/svc-search-001")
    assert replay.status_code == 200, replay.text
    assert replay.json()["snapshot"] == payload["index_snapshot"]


def test_search_service_requires_scoped_access_context(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl"))

    response = client.post(
        "/api/search/query",
        json={
            "request_id": "svc-search-missing-scope",
            "trace_id": "trace-svc-search-missing-scope",
            "query": "momentum",
            "documents": [],
            "access_context": {
                "environment": "paper",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        },
    )

    assert response.status_code == 400
    assert "persona_id and workspace_id" in response.json()["detail"]


def test_search_service_queries_durable_evidence_index_without_documents(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_durable_evidence(evidence_path)
    client = TestClient(create_app(tmp_path / "search-index.jsonl", evidence_path))

    status = client.get("/api/search/index/status")
    assert status.status_code == 200, status.text
    assert status.json()["indexed_object_count"] == 2
    assert status.json()["index"]["adapter_state"] == "durable"

    response = client.post(
        "/api/search/query",
        json={
            "request_id": "svc-search-durable-001",
            "trace_id": "trace-svc-search-durable-001",
            "query": "momentum volatility",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["internal_note"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["result_id"] for item in payload["results"]] == ["ko-durable-note"]
    assert payload["rejected_items_count"] == 1
    assert payload["index_adapter"]["adapter_state"] == "durable"
    assert payload["index_snapshot"]["request_id"] == "svc-search-durable-001"
