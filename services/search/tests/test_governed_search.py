from __future__ import annotations

import pytest

from integrations.openclaw.search_gateway import OpenClawSearchGateway
from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, InMemoryEvidenceRepository
from services.knowledge.evidence.models import KnowledgeObject
from services.search import SearchAccessContext, SearchGateway, SearchRequest
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
    assert response.rejected_items_count == 1
    assert response.filters_applied["pre_ranking_filter"] == "acl_license_workspace_environment"


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
    assert result["citations"] == ["volatility-note#1"]
    assert "raw_payload" not in result
