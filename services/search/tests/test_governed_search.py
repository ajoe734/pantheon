from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from integrations.openclaw.search_gateway import OpenClawSearchGateway
from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, InMemoryEvidenceRepository
from services.knowledge.evidence.models import KnowledgeObject
from services.search import JsonlSearchIndexStore, KeywordIndexAdapter, SearchAccessContext, SearchGateway, SearchRequest
from services.search.index_adapter import SearchIndexDocument
from services.search.filters import SearchPolicyError
from services.source_ingestion.connectors import SourceRecord


def _repository() -> InMemoryEvidenceRepository:
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)
    source = SourceRecord(
        source_id="src-volatility-note",
        connector_id="conn-notes",
        source_type="internal_note",
        title="Volatility note",
        content_ref="note://pantheon/volatility",
        metadata={"license_scope": "internal", "access_scope": ["research"]},
        trace_id="trace-src-volatility",
    )
    item = EvidenceItem(
        evidence_item_id="evi-volatility-note",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="note://pantheon/volatility#1",
        citation_label="volatility-note#1",
        body="Momentum decay appears when volatility clusters persist.",
        access_scope=["research"],
        trace_refs=["trace-evi-volatility"],
    )
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Momentum decay evidence.",
        created_by="Codex",
        evidence_bundle_id="evbundle-volatility",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-volatility",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Momentum decay during volatility clusters",
        text=item.body,
        keywords=["momentum", "volatility"],
        metadata={"relevance_score": 0.7, "updated_at": "2026-04-19T20:00:00Z"},
    )

    private_object = KnowledgeObject(
        knowledge_object_id="ko-private",
        source_id=source.source_id,
        evidence_item_id=item.evidence_item_id,
        evidence_bundle_id=bundle.evidence_bundle_id,
        title="Private momentum note",
        text="Momentum note that should be filtered before ranking.",
        source_type="internal_note",
        license_scope="restricted",
        access_scope=["risk-committee"],
        metadata={"relevance_score": 0.99},
    )
    repository.add_knowledge_object(private_object)

    future_item = EvidenceItem(
        evidence_item_id="evi-future-note",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref="note://pantheon/future#1",
        citation_label="future-note#1",
        body="Momentum volatility evidence that is not available yet.",
        available_time=datetime.now(timezone.utc) + timedelta(days=1),
        access_scope=["research"],
        trace_refs=["trace-evi-future"],
    )
    future_bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[future_item],
        summary="Future momentum evidence.",
        created_by="Codex",
        evidence_bundle_id="evbundle-future",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-future",
        source_record=source,
        evidence_item=future_item,
        evidence_bundle=future_bundle,
        title="Future unavailable momentum note",
        text=future_item.body,
        keywords=["momentum", "volatility"],
        metadata={"relevance_score": 0.99},
    )
    return repository


def test_search_returns_cited_evidence_bundle_refs() -> None:
    gateway = SearchGateway(_repository())
    response = gateway.search(
        SearchRequest(
            request_id="search-001",
            query="momentum volatility",
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            source_types=["internal_note"],
            trace_id="trace-search-001",
        ),
        SearchAccessContext(
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            environment="paper",
            access_scopes=["research"],
            license_scopes=["internal"],
        ),
    )

    assert [result.evidence_bundle_id for result in response.results] == ["evbundle-volatility"]
    assert response.results[0].citations == ["volatility-note#1"]
    assert response.rejected_items_count == 2
    assert response.filters_applied["pre_ranking_filter"] == "acl_license_workspace_environment"
    assert response.filters_applied["available_time"] == "not_future"


def test_openclaw_search_requires_persona_and_workspace_scope() -> None:
    gateway = OpenClawSearchGateway(_repository())

    with pytest.raises(SearchPolicyError, match="persona_id and workspace_id"):
        gateway.search({"query": "momentum volatility", "workspace_id": "workspace-research"})


def test_openclaw_search_returns_evidence_not_raw_undocumented_blob() -> None:
    gateway = OpenClawSearchGateway(_repository())
    response = gateway.search(
        {
            "request_id": "openclaw-search-001",
            "query": "momentum volatility",
            "persona_id": "persona-alpha",
            "workspace_id": "workspace-research",
            "access_scopes": ["research"],
            "license_scopes": ["internal"],
            "source_types": ["internal_note"],
        }
    )

    result = response["results"][0]
    assert result["evidence_bundle_id"] == "evbundle-volatility"
    assert result["citation_pack"] == [
        {
            "citation_label": "volatility-note#1",
            "evidence_bundle_id": "evbundle-volatility",
        }
    ]
    assert "answer_context" not in result
    assert "matched_items" not in result
    assert "citations" not in result
    assert "raw_payload" not in result


def test_search_index_store_replays_evidence_bundle_refs_without_raw_payloads(tmp_path) -> None:
    index_store = JsonlSearchIndexStore(tmp_path / "search-index.jsonl")
    gateway = SearchGateway(_repository(), index_store=index_store)
    gateway.search(
        SearchRequest(
            request_id="search-refs-001",
            query="momentum volatility",
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            source_types=["internal_note"],
            trace_id="trace-search-refs-001",
        ),
        SearchAccessContext(
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            environment="paper",
            access_scopes=["research"],
            license_scopes=["internal"],
        ),
    )

    replayed = JsonlSearchIndexStore(tmp_path / "search-index.jsonl")
    snapshot = replayed.get_snapshot("search-refs-001")

    assert snapshot is not None
    assert snapshot.result_refs == [
        {
            "result_id": "ko-volatility",
            "evidence_bundle_id": "evbundle-volatility",
            "citations": ["volatility-note#1"],
            "matched_items": [
                {
                    "knowledge_object_id": "ko-volatility",
                    "source_id": "src-volatility-note",
                    "evidence_item_id": "evi-volatility-note",
                    "content_ref": "note://pantheon/volatility#1",
                    "citation_label": "volatility-note#1",
                    "matched_terms": ["momentum", "volatility"],
                }
            ],
            "relevance_score": 0.75,
        }
    ]
    assert "answer_context" not in snapshot.to_dict()["result_refs"][0]
    assert "raw_payload" not in snapshot.to_dict()["result_refs"][0]


def test_search_uses_index_adapter_documents_for_keyword_inputs() -> None:
    repository = _repository()

    class AdapterOnlyKeywordIndex(KeywordIndexAdapter):
        def documents_for(self, knowledge_objects):
            return [
                SearchIndexDocument(
                    knowledge_object=knowledge_object,
                    search_text="adapter-only-token",
                    relevance_score=0.42,
                    indexed_at=None,
                    source_watermark="2026-04-28T09:00:00Z",
                    metadata={},
                )
                for knowledge_object in knowledge_objects
                if knowledge_object.knowledge_object_id == "ko-volatility"
            ]

    gateway = SearchGateway(repository, index_adapter=AdapterOnlyKeywordIndex(repository))
    response = gateway.search(
        SearchRequest(
            request_id="search-adapter-boundary",
            query="adapter-only-token",
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            source_types=["internal_note"],
            trace_id="trace-search-adapter-boundary",
        ),
        SearchAccessContext(
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            environment="paper",
            access_scopes=["research"],
            license_scopes=["internal"],
        ),
    )

    assert [result.result_id for result in response.results] == ["ko-volatility"]
    assert response.results[0].relevance_score == 0.43


def test_keyword_index_adapter_preserves_metadata_search_text_input() -> None:
    repository = _repository()
    source = repository.get_source_record("src-volatility-note")
    item = repository.get_evidence_item("evi-volatility-note")
    bundle = repository.get_bundle("evbundle-volatility")
    assert source is not None
    assert item is not None
    assert bundle is not None
    repository.add_knowledge_object(
        KnowledgeObject(
            knowledge_object_id="ko-adapter-metadata",
            source_id=source.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title="Durable adapter metadata",
            text="This object only exposes the query token through metadata.",
            source_type="internal_note",
            license_scope="internal",
            access_scope=["research"],
            metadata={"search_text": "adapter-only-token", "relevance_score": 0.5},
        )
    )

    gateway = SearchGateway(repository)
    response = gateway.search(
        SearchRequest(
            request_id="search-metadata-search-text",
            query="adapter-only-token",
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            source_types=["internal_note"],
            trace_id="trace-search-metadata-search-text",
        ),
        SearchAccessContext(
            persona_id="persona-alpha",
            workspace_id="workspace-research",
            environment="paper",
            access_scopes=["research"],
            license_scopes=["internal"],
        ),
    )

    assert [result.result_id for result in response.results] == ["ko-adapter-metadata"]
