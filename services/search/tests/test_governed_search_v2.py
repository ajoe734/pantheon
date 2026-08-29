"""Tests for governed search v2: pre-retrieval filter plan, honest capabilities, hybrid retrieval, and snapshot replay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.knowledge.evidence import (
    EvidenceBundleBuilder,
    EvidenceItem,
    InMemoryEvidenceRepository,
    KnowledgeObject,
)
from services.search import (
    FullTextRetriever,
    GovernedSearchResponse,
    HybridRetriever,
    JsonlSearchIndexStore,
    KeywordRetriever,
    MockVectorEmbeddingBackend,
    SearchAccessContext,
    SearchCapabilityUnavailableError,
    SearchFilters,
    SearchGateway,
    SearchPolicyError,
    SearchRequest,
    SemanticRetriever,
)
from services.search.main import create_app
from services.source_ingestion.connectors import SourceRecord


def _build_test_repository() -> InMemoryEvidenceRepository:
    repository = InMemoryEvidenceRepository()
    builder = EvidenceBundleBuilder(repository)
    now = datetime.now(timezone.utc)

    # 1. Standard authorized research note
    source1 = SourceRecord(
        source_id="src-factor-momentum",
        connector_id="conn-research-notes",
        source_type="internal_note",
        title="Momentum Factor Research",
        content_ref="note://pantheon/factors/momentum",
        metadata={"license_scope": "internal", "access_scope": ["research", "operator"]},
        trace_id="trace-src-momentum",
    )
    item1 = EvidenceItem(
        evidence_item_id="evi-factor-momentum-1",
        source_id=source1.source_id,
        item_type="text_chunk",
        content_ref="note://pantheon/factors/momentum#1",
        citation_label="factor-note#momentum",
        body="Momentum quality score exhibits persistent alpha during trend regimes.",
        event_time=now - timedelta(days=10),
        available_time=now - timedelta(days=9),
        access_scope=["research", "operator"],
    )
    bundle1 = builder.build_bundle(
        source_records=[source1],
        evidence_items=[item1],
        summary="Momentum alpha evidence bundle.",
        created_by="Researcher",
        evidence_bundle_id="bundle-momentum-001",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-momentum-001",
        source_record=source1,
        evidence_item=item1,
        evidence_bundle=bundle1,
        title="Momentum Quality Alpha Signal",
        text=item1.body,
        keywords=["momentum", "quality", "alpha"],
        metadata={
            "relevance_score": 0.85,
            "sensitivity": "internal",
            "capital_pool_scope": ["pool-alpha", "pool-beta"],
            "role_scope": ["researcher", "pm"],
            "asset_class": ["equity"],
            "strategy_id": "strat-momentum-v1",
        },
    )

    # 2. Object with different workspace scope
    source2 = SourceRecord(
        source_id="src-other-workspace",
        connector_id="conn-research-notes",
        source_type="internal_note",
        title="Restricted Workspace Document",
        content_ref="note://pantheon/workspace-secret",
        metadata={"license_scope": "internal", "access_scope": ["research"]},
        trace_id="trace-src-other-ws",
    )
    item2 = EvidenceItem(
        evidence_item_id="evi-other-workspace",
        source_id=source2.source_id,
        item_type="text_chunk",
        content_ref="note://pantheon/workspace-secret#1",
        citation_label="ws-secret#1",
        body="Momentum alpha document belonging to another workspace.",
        event_time=now - timedelta(days=5),
        available_time=now - timedelta(days=4),
        access_scope=["research"],
    )
    bundle2 = builder.build_bundle(
        source_records=[source2],
        evidence_items=[item2],
        summary="Other workspace bundle.",
        created_by="Admin",
        evidence_bundle_id="bundle-other-ws",
    )
    ko_ws = KnowledgeObject(
        knowledge_object_id="ko-other-ws",
        source_id=source2.source_id,
        evidence_item_id=item2.evidence_item_id,
        evidence_bundle_id=bundle2.evidence_bundle_id,
        title="Secret Workspace Alpha",
        text=item2.body,
        source_type="internal_note",
        license_scope="internal",
        access_scope=["research"],
        workspace_scope=["workspace-secret-only"],
        metadata={"relevance_score": 0.99, "sensitivity": "internal"},
    )
    repository.add_source_record(source2)
    repository.add_evidence_item(item2)
    repository.add_bundle(bundle2)
    repository.add_knowledge_object(ko_ws)

    # 3. Object with high sensitivity / restricted license
    source3 = SourceRecord(
        source_id="src-confidential-note",
        connector_id="conn-research-notes",
        source_type="internal_note",
        title="Confidential Risk Report",
        content_ref="note://pantheon/confidential",
        metadata={"license_scope": "restricted", "access_scope": ["risk-committee"]},
        trace_id="trace-src-confidential",
    )
    item3 = EvidenceItem(
        evidence_item_id="evi-confidential-1",
        source_id=source3.source_id,
        item_type="text_chunk",
        content_ref="note://pantheon/confidential#1",
        citation_label="confidential#1",
        body="Confidential momentum risk model details.",
        event_time=now - timedelta(days=2),
        available_time=now - timedelta(days=1),
        access_scope=["risk-committee"],
    )
    bundle3 = builder.build_bundle(
        source_records=[source3],
        evidence_items=[item3],
        summary="Confidential bundle.",
        created_by="RiskOfficer",
        evidence_bundle_id="bundle-confidential",
    )
    ko_conf = KnowledgeObject(
        knowledge_object_id="ko-confidential-001",
        source_id=source3.source_id,
        evidence_item_id=item3.evidence_item_id,
        evidence_bundle_id=bundle3.evidence_bundle_id,
        title="Confidential Momentum Analysis",
        text=item3.body,
        source_type="internal_note",
        license_scope="restricted",
        access_scope=["risk-committee"],
        metadata={"relevance_score": 0.95, "sensitivity": "restricted"},
    )
    repository.add_source_record(source3)
    repository.add_evidence_item(item3)
    repository.add_bundle(bundle3)
    repository.add_knowledge_object(ko_conf)

    # 4. Object with future available_time (future lookahead leak test)
    source4 = SourceRecord(
        source_id="src-future-note",
        connector_id="conn-research-notes",
        source_type="internal_note",
        title="Future Market Analysis",
        content_ref="note://pantheon/future",
        metadata={"license_scope": "internal", "access_scope": ["research"]},
        trace_id="trace-src-future",
    )
    item4 = EvidenceItem(
        evidence_item_id="evi-future-1",
        source_id=source4.source_id,
        item_type="text_chunk",
        content_ref="note://pantheon/future#1",
        citation_label="future#1",
        body="Future momentum alpha released tomorrow.",
        event_time=now - timedelta(days=1),
        available_time=now + timedelta(days=2),
        access_scope=["research"],
    )
    bundle4 = builder.build_bundle(
        source_records=[source4],
        evidence_items=[item4],
        summary="Future bundle.",
        created_by="Analyst",
        evidence_bundle_id="bundle-future",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-future-001",
        source_record=source4,
        evidence_item=item4,
        evidence_bundle=bundle4,
        title="Future Momentum Signal",
        text=item4.body,
        keywords=["momentum", "future"],
        metadata={"relevance_score": 0.99, "sensitivity": "internal"},
    )

    return repository


def _default_context() -> SearchAccessContext:
    return SearchAccessContext(
        persona_id="persona-quant-01",
        workspace_id="workspace-research",
        role_refs=["researcher"],
        environment="paper",
        access_scopes=["research", "operator", "public"],
        license_scopes=["internal", "open"],
        sensitivity_scopes=["public", "internal"],
        capital_pool_scopes=["pool-alpha"],
    )


# ---------------------------------------------------------------------------
# 1. Acceptance 2: Translate v1 time_window to explicit event bounds / reject ambiguity
# ---------------------------------------------------------------------------

def test_time_window_translation_to_explicit_bounds() -> None:
    req = SearchRequest(
        query="momentum quality",
        persona_id="persona-01",
        workspace_id="workspace-01",
        time_window={"start": "2026-01-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    )
    assert req.filters is not None
    assert req.filters.event_time_gte == "2026-01-01T00:00:00Z"
    assert req.filters.event_time_lte == "2026-06-01T00:00:00Z"


def test_time_window_supports_gte_lte_and_since_until() -> None:
    req1 = SearchRequest(
        query="momentum",
        persona_id="persona-01",
        workspace_id="workspace-01",
        time_window={"gte": "2026-03-01T00:00:00Z", "lte": "2026-04-01T00:00:00Z"},
    )
    assert req1.filters.event_time_gte == "2026-03-01T00:00:00Z"
    assert req1.filters.event_time_lte == "2026-04-01T00:00:00Z"

    req2 = SearchRequest(
        query="momentum",
        persona_id="persona-01",
        workspace_id="workspace-01",
        time_window={"since": "2026-03-01T00:00:00Z"},
    )
    assert req2.filters.event_time_gte == "2026-03-01T00:00:00Z"


def test_ambiguous_or_invalid_time_window_rejected() -> None:
    # Non-dict time_window
    with pytest.raises(SearchPolicyError, match="Ambiguous or invalid time_window"):
        SearchRequest(
            query="momentum",
            persona_id="persona-01",
            workspace_id="workspace-01",
            time_window="last_30_days",  # type: ignore[arg-type]
        )

    # Unknown / ambiguous keys
    with pytest.raises(SearchPolicyError, match="Ambiguous or invalid time_window"):
        SearchRequest(
            query="momentum",
            persona_id="persona-01",
            workspace_id="workspace-01",
            time_window={"relative_days": 30},
        )

    # Invalid timestamp format
    with pytest.raises(SearchPolicyError, match="Ambiguous or invalid time_window"):
        SearchRequest(
            query="momentum",
            persona_id="persona-01",
            workspace_id="workspace-01",
            time_window={"start": "invalid-time-string"},
        )


# ---------------------------------------------------------------------------
# 2. Acceptance 3 & 4: Pre-retrieval filters and authorization isolation
# ---------------------------------------------------------------------------

def test_pre_retrieval_filtering_and_audit_breakdown() -> None:
    repo = _build_test_repository()
    gateway = SearchGateway(repo)
    ctx = _default_context()

    req = SearchRequest(
        query="momentum alpha",
        persona_id=ctx.persona_id,
        workspace_id=ctx.workspace_id,
        filters={"capital_pool_scope": ["pool-alpha"]},
    )
    response = gateway.search(req, ctx)

    # ko-momentum-001 is permitted
    assert len(response.results) == 1
    assert response.results[0].result_id == "ko-momentum-001"
    assert response.rejected_items_count == 3

    # Audit breakdown by rejection reason
    breakdown = response.rejected_by_reason
    assert breakdown.get("workspace_scope", 0) == 1  # ko-other-ws rejected
    assert breakdown.get("license_scope", 0) == 1 or breakdown.get("sensitivity", 0) == 1 or breakdown.get("access_scope", 0) == 1  # ko-confidential
    assert breakdown.get("available_time", 0) == 1  # ko-future


def test_semantic_retrieval_restricted_to_authorized_ids_no_leakage() -> None:
    repo = _build_test_repository()
    backend = MockVectorEmbeddingBackend()
    sem_retriever = SemanticRetriever(embedding_backend=backend)
    gateway = SearchGateway(repo, semantic_retriever=sem_retriever)
    ctx = _default_context()

    req = SearchRequest(
        query="momentum alpha",
        retrieval_mode="semantic",
        persona_id=ctx.persona_id,
        workspace_id=ctx.workspace_id,
    )
    response = gateway.search(req, ctx)

    # Even though ko-confidential and ko-other-ws have high relevance / similarity,
    # they must be rejected before vector ranking and NEVER returned
    returned_ids = [r.result_id for r in response.results]
    assert "ko-momentum-001" in returned_ids
    assert "ko-other-ws" not in returned_ids
    assert "ko-confidential-001" not in returned_ids
    assert "ko-future-001" not in returned_ids


# ---------------------------------------------------------------------------
# 3. Acceptance 5 & 8: Honest capability reporting and mode-unavailable tests
# ---------------------------------------------------------------------------

def test_capabilities_reported_honestly_when_semantic_disabled() -> None:
    repo = _build_test_repository()
    # Semantic retriever with embedding_backend=None
    sem_retriever = SemanticRetriever(embedding_backend=None)
    gateway = SearchGateway(repo, semantic_retriever=sem_retriever)

    caps = gateway.get_capabilities()
    assert caps["keyword"]["available"] is True
    assert caps["full_text"]["available"] is True
    assert caps["semantic"]["available"] is False
    assert caps["hybrid"]["available"] is False
    assert caps["structured_alpha"]["available"] is True


def test_semantic_mode_raises_unavailable_when_backend_not_configured() -> None:
    repo = _build_test_repository()
    sem_retriever = SemanticRetriever(embedding_backend=None)
    gateway = SearchGateway(repo, semantic_retriever=sem_retriever)
    ctx = _default_context()

    req = SearchRequest(
        query="momentum alpha",
        retrieval_mode="semantic",
        persona_id=ctx.persona_id,
        workspace_id=ctx.workspace_id,
    )
    with pytest.raises(SearchCapabilityUnavailableError, match="Semantic retrieval mode is unavailable"):
        gateway.search(req, ctx)


def test_hybrid_mode_raises_unavailable_when_semantic_unavailable() -> None:
    repo = _build_test_repository()
    sem_retriever = SemanticRetriever(embedding_backend=None)
    gateway = SearchGateway(repo, semantic_retriever=sem_retriever)
    ctx = _default_context()

    req = SearchRequest(
        query="momentum alpha",
        retrieval_mode="hybrid",
        persona_id=ctx.persona_id,
        workspace_id=ctx.workspace_id,
    )
    with pytest.raises(SearchCapabilityUnavailableError, match="Hybrid retrieval mode is unavailable"):
        gateway.search(req, ctx)


# ---------------------------------------------------------------------------
# 4. Hybrid retrieval with Reciprocal Rank Fusion and component scores
# ---------------------------------------------------------------------------

def test_hybrid_retrieval_executes_rrf_and_returns_component_scores() -> None:
    repo = _build_test_repository()
    backend = MockVectorEmbeddingBackend()
    sem_retriever = SemanticRetriever(embedding_backend=backend)
    ft_retriever = FullTextRetriever()
    hyb_retriever = HybridRetriever(lexical_retriever=ft_retriever, semantic_retriever=sem_retriever)
    gateway = SearchGateway(repo, full_text_retriever=ft_retriever, semantic_retriever=sem_retriever, hybrid_retriever=hyb_retriever)
    ctx = _default_context()

    req = SearchRequest(
        query="momentum quality alpha",
        retrieval_mode="hybrid",
        persona_id=ctx.persona_id,
        workspace_id=ctx.workspace_id,
    )
    response = gateway.search(req, ctx)

    assert len(response.results) == 1
    result = response.results[0]
    assert result.result_id == "ko-momentum-001"
    assert result.ranker_version == "rrf-v1"
    assert "rrf_score" in result.component_scores
    assert "lexical_score" in result.component_scores
    assert "semantic_score" in result.component_scores


# ---------------------------------------------------------------------------
# 5. Snapshot persistence & replay with fingerprints
# ---------------------------------------------------------------------------

def test_search_snapshot_persistence_and_replay(tmp_path: Path) -> None:
    index_store = JsonlSearchIndexStore(tmp_path / "search-index.jsonl")
    repo = _build_test_repository()
    gateway = SearchGateway(repo, index_store=index_store)
    ctx = _default_context()

    req = SearchRequest(
        request_id="search-v2-req-001",
        query="momentum quality",
        persona_id=ctx.persona_id,
        workspace_id=ctx.workspace_id,
        filters={"capital_pool_scope": ["pool-alpha"]},
    )
    response = gateway.search(req, ctx)
    assert response.request_id == "search-v2-req-001"

    # Replay from store
    reloaded_store = JsonlSearchIndexStore(tmp_path / "search-index.jsonl")
    snapshot = reloaded_store.get_snapshot("search-v2-req-001")
    assert snapshot is not None
    assert snapshot.schema_version == "governed_search_refs.v2"
    assert snapshot.retrieval_mode == "keyword"
    assert snapshot.rejected_items_count == 3
    assert "query_fingerprint" in snapshot.fingerprints
    assert len(snapshot.result_refs) == 1
    assert snapshot.result_refs[0]["result_id"] == "ko-momentum-001"


# ---------------------------------------------------------------------------
# 6. HTTP API v2 endpoints test
# ---------------------------------------------------------------------------

def test_http_api_v2_search_and_capabilities(tmp_path: Path) -> None:
    app = create_app(
        index_store_path=tmp_path / "index.jsonl",
        evidence_store_path=tmp_path / "evidence.jsonl",
        materialize_store_path=tmp_path / "materialize.jsonl",
        pipeline_store_path=tmp_path / "pipeline.jsonl",
    )
    client = TestClient(app)

    # Test capabilities endpoint
    cap_resp = client.get("/api/search/capabilities")
    assert cap_resp.status_code == 200
    caps = cap_resp.json()
    assert "keyword" in caps
    assert "full_text" in caps
    assert "semantic" in caps
    assert "hybrid" in caps
    assert "structured_alpha" in caps

    # Test v2 query endpoint
    query_resp = client.post(
        "/api/search/v2/query",
        json={
            "query": "momentum",
            "retrieval_mode": "keyword",
            "persona_id": "persona-01",
            "workspace_id": "workspace-01",
            "access_context": {
                "persona_id": "persona-01",
                "workspace_id": "workspace-01",
                "access_scopes": ["public", "research"],
            },
        },
    )
    assert query_resp.status_code == 200
    data = query_resp.json()
    assert "results" in data
    assert "capabilities" in data
    assert data["retrieval_mode"] == "keyword"
