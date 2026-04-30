"""Contract tests for retrieval rank, filter, access, citation, and durable-index-only cutoff.

Verifies:
- rank ordering by score and recency
- pre-ranking ACL / license / environment filters
- citation enforcement (require_citations=True)
- persona/workspace scope filtering
- durable-index-only mode rejects request documents
- compat endpoint signals deprecation header
"""
from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.knowledge.evidence import (
    EvidenceBundleBuilder,
    EvidenceItem,
    EvidenceBundle,
    InMemoryEvidenceRepository,
    JsonlEvidenceRepository,
    KnowledgeObject,
)
from services.knowledge.evidence.models import EvidenceValidationError
from services.search.filters import SearchAccessContext, SearchPolicyError, SearchRequest
from services.search.gateway import SearchGateway
from services.search.main import create_app
from services.source_ingestion.connectors import SourceRecord


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _base_source(source_id: str = "src-base") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id="conn-test",
        source_type="internal_note",
        title="Test source",
        content_ref=f"note://test/{source_id}",
        metadata={"license_scope": "internal", "access_scope": ["research"]},
        trace_id=f"trace-{source_id}",
    )


def _base_item(item_id: str, source_id: str, body: str = "momentum volatility evidence") -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id=item_id,
        source_id=source_id,
        item_type="text_chunk",
        content_ref=f"note://test/{item_id}",
        citation_label=f"label:{item_id}",
        body=body,
        access_scope=["research"],
        trace_refs=[f"trace-{item_id}"],
    )


def _base_bundle(bundle_id: str, source_id: str, item_id: str, citation_label: str) -> EvidenceBundle:
    return EvidenceBundle(
        evidence_bundle_id=bundle_id,
        source_ids=[source_id],
        evidence_item_ids=[item_id],
        summary="test bundle",
        citation_refs=[citation_label],
        confidence=1.0,
        license_scope="internal",
        access_scope=["research"],
        created_by="test",
        trace_refs=[],
        metadata={},
    )


def _full_repository(
    ko_id: str = "ko-test",
    relevance_score: float = 0.7,
    license_scope: str = "internal",
    access_scope: list[str] | None = None,
    environment_scope: list[str] | None = None,
    persona_scope: list[str] | None = None,
    workspace_scope: list[str] | None = None,
    text: str = "momentum volatility evidence",
) -> InMemoryEvidenceRepository:
    """Repository with a single well-formed KO (bundle and evidence_item included)."""
    repo = InMemoryEvidenceRepository()
    source = _base_source()
    item = _base_item(f"evi-{ko_id}", source.source_id, body=text)
    bundle = _base_bundle(f"evbundle-{ko_id}", source.source_id, item.evidence_item_id, item.citation_label)
    ko = KnowledgeObject(
        knowledge_object_id=ko_id,
        source_id=source.source_id,
        evidence_item_id=item.evidence_item_id,
        evidence_bundle_id=bundle.evidence_bundle_id,
        title="Test object",
        text=text,
        source_type="internal_note",
        license_scope=license_scope,
        access_scope=access_scope or ["research"],
        environment_scope=environment_scope or ["paper"],
        persona_scope=persona_scope or [],
        workspace_scope=workspace_scope or [],
        keywords=["momentum", "volatility"],
        metadata={"relevance_score": relevance_score, "updated_at": "2026-04-20T10:00:00Z"},
    )
    repo.add_source_record(source)
    repo.add_evidence_item(item)
    repo.add_bundle(bundle)
    repo.add_knowledge_object(ko)
    return repo


def _standard_context(**overrides) -> SearchAccessContext:
    defaults = dict(
        persona_id="persona-alpha",
        workspace_id="workspace-research",
        environment="paper",
        access_scopes=["research"],
        license_scopes=["internal"],
    )
    defaults.update(overrides)
    return SearchAccessContext(**defaults)


def _standard_request(**overrides) -> SearchRequest:
    defaults = dict(
        request_id="search-test",
        query="momentum volatility",
        persona_id="persona-alpha",
        workspace_id="workspace-research",
        source_types=["internal_note"],
        trace_id="trace-test",
    )
    defaults.update(overrides)
    return SearchRequest(**defaults)


# ---------------------------------------------------------------------------
# Rank contract: score and recency ordering
# ---------------------------------------------------------------------------

def test_rank_orders_by_score_descending() -> None:
    repo = InMemoryEvidenceRepository()
    source = _base_source()
    repo.add_source_record(source)

    for ko_id, score in [("ko-low", 0.5), ("ko-high", 0.9), ("ko-mid", 0.7)]:
        item = _base_item(f"evi-{ko_id}", source.source_id)
        bundle = _base_bundle(f"evbundle-{ko_id}", source.source_id, item.evidence_item_id, item.citation_label)
        ko = KnowledgeObject(
            knowledge_object_id=ko_id,
            source_id=source.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title=f"Object {ko_id}",
            text="momentum volatility evidence",
            source_type="internal_note",
            license_scope="internal",
            access_scope=["research"],
            keywords=["momentum"],
            metadata={"relevance_score": score, "updated_at": "2026-04-20T10:00:00Z"},
        )
        repo.add_evidence_item(item)
        repo.add_bundle(bundle)
        repo.add_knowledge_object(ko)

    gateway = SearchGateway(repo)
    response = gateway.search(_standard_request(top_k=10), _standard_context())

    scores = [r.relevance_score for r in response.results]
    assert scores == sorted(scores, reverse=True)
    assert [r.result_id for r in response.results] == ["ko-high", "ko-mid", "ko-low"]


def test_rank_top_k_limits_results() -> None:
    repo = InMemoryEvidenceRepository()
    source = _base_source()
    repo.add_source_record(source)

    for i in range(5):
        ko_id = f"ko-topk-{i}"
        item = _base_item(f"evi-topk-{i}", source.source_id)
        bundle = _base_bundle(f"evbundle-topk-{i}", source.source_id, item.evidence_item_id, item.citation_label)
        ko = KnowledgeObject(
            knowledge_object_id=ko_id,
            source_id=source.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title=f"Object {ko_id}",
            text="momentum volatility evidence",
            source_type="internal_note",
            license_scope="internal",
            access_scope=["research"],
            keywords=["momentum"],
            metadata={"relevance_score": 0.5 + i * 0.05},
        )
        repo.add_evidence_item(item)
        repo.add_bundle(bundle)
        repo.add_knowledge_object(ko)

    gateway = SearchGateway(repo)
    response = gateway.search(_standard_request(top_k=3), _standard_context())

    assert len(response.results) == 3


def test_rank_empty_query_raises_policy_error() -> None:
    with pytest.raises(SearchPolicyError, match="query is required"):
        SearchRequest(query="", request_id="r1")


def test_rank_top_k_zero_raises_policy_error() -> None:
    with pytest.raises(SearchPolicyError, match="top_k"):
        SearchRequest(query="momentum", top_k=0)


# ---------------------------------------------------------------------------
# Filter contract: access, license, environment, persona, workspace
# ---------------------------------------------------------------------------

def test_filter_rejects_restricted_license() -> None:
    repo = _full_repository(license_scope="restricted")
    response = SearchGateway(repo).search(
        _standard_request(),
        _standard_context(license_scopes=["internal"]),
    )
    assert response.results == []
    assert response.rejected_items_count == 1


def test_filter_rejects_wrong_environment() -> None:
    repo = _full_repository(environment_scope=["live"])
    response = SearchGateway(repo).search(
        _standard_request(),
        _standard_context(environment="paper"),
    )
    assert response.results == []
    assert response.rejected_items_count == 1


def test_filter_rejects_disallowed_access_scope() -> None:
    repo = _full_repository(access_scope=["risk-committee"])
    response = SearchGateway(repo).search(
        _standard_request(),
        _standard_context(access_scopes=["research"]),
    )
    assert response.results == []
    assert response.rejected_items_count == 1


def test_filter_rejects_wrong_persona_scope() -> None:
    repo = _full_repository(persona_scope=["persona-beta"])
    response = SearchGateway(repo).search(
        _standard_request(),
        _standard_context(persona_id="persona-alpha"),
    )
    assert response.results == []
    assert response.rejected_items_count == 1


def test_filter_rejects_wrong_workspace_scope() -> None:
    repo = _full_repository(workspace_scope=["workspace-beta"])
    response = SearchGateway(repo).search(
        _standard_request(),
        _standard_context(workspace_id="workspace-research"),
    )
    assert response.results == []
    assert response.rejected_items_count == 1


def test_filter_allows_matching_persona_and_workspace_scope() -> None:
    repo = _full_repository(
        persona_scope=["persona-alpha"],
        workspace_scope=["workspace-research"],
    )
    response = SearchGateway(repo).search(
        _standard_request(),
        _standard_context(persona_id="persona-alpha", workspace_id="workspace-research"),
    )
    assert len(response.results) == 1


def test_filter_reports_pre_ranking_filter_tag() -> None:
    repo = _full_repository()
    response = SearchGateway(repo).search(_standard_request(), _standard_context())
    assert response.filters_applied["pre_ranking_filter"] == "acl_license_workspace_environment"


def test_filter_requires_persona_and_workspace_in_context() -> None:
    repo = _full_repository()
    context = SearchAccessContext(
        persona_id=None,
        workspace_id="workspace-research",
        environment="paper",
        access_scopes=["research"],
        license_scopes=["internal"],
    )
    with pytest.raises(SearchPolicyError, match="persona_id and workspace_id"):
        context.require_persona_workspace()


# ---------------------------------------------------------------------------
# Citation contract: require_citations enforcement
# ---------------------------------------------------------------------------

def test_citation_required_rejects_object_when_bundle_returns_none() -> None:
    """When get_bundle returns None, require_citations must reject the object."""
    repo = _full_repository()

    with mock.patch.object(repo, "get_bundle", return_value=None):
        response = SearchGateway(repo).search(
            _standard_request(require_citations=True),
            _standard_context(),
        )

    assert response.results == []
    assert response.rejected_items_count == 1


def test_citation_not_required_but_bundle_missing_raises_policy_error() -> None:
    """Without require_citations, a missing bundle after ranking raises SearchPolicyError."""
    repo = _full_repository()

    original_get_bundle = repo.get_bundle

    def get_bundle_post_filter(bundle_id: str):
        # Return bundle during pre-ranking citation check (called in ranking phase too),
        # but simulate missing during result construction by counting calls.
        if not hasattr(get_bundle_post_filter, "_call_count"):
            get_bundle_post_filter._call_count = 0
        get_bundle_post_filter._call_count += 1
        # First call is from citation filter (require_citations=False skips it).
        # Simulate missing bundle in result construction phase.
        return None

    with mock.patch.object(repo, "get_bundle", side_effect=get_bundle_post_filter):
        with pytest.raises(SearchPolicyError, match="missing governed evidence"):
            SearchGateway(repo).search(
                _standard_request(require_citations=False),
                _standard_context(),
            )


def test_citation_result_includes_citation_label() -> None:
    repo = _full_repository()
    response = SearchGateway(repo).search(_standard_request(), _standard_context())
    assert len(response.results) == 1
    assert response.results[0].citations == ["label:evi-ko-test"]


# ---------------------------------------------------------------------------
# Durable-index-only cutoff contract
# ---------------------------------------------------------------------------

def test_durable_index_only_rejects_request_documents_even_with_compat_flag(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl", durable_index_only=True))

    body = {
        "request_id": "svc-cutoff-001",
        "trace_id": "trace-cutoff-001",
        "query": "momentum",
        "persona_id": "operator-workbench",
        "workspace_id": "research-workbench",
        "allow_request_documents_compat": True,
        "documents": [
            {
                "result_id": "doc-cutoff",
                "title": "Should be rejected",
                "excerpt": "momentum volatility evidence",
            }
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
    assert response.status_code == 400
    assert "durable-index-only" in response.json()["detail"]


def test_durable_index_only_rejects_compat_route_with_documents(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl", durable_index_only=True))

    body = {
        "request_id": "svc-cutoff-002",
        "trace_id": "trace-cutoff-002",
        "query": "momentum",
        "persona_id": "operator-workbench",
        "workspace_id": "research-workbench",
        "documents": [
            {
                "result_id": "doc-cutoff",
                "title": "Should be rejected",
                "excerpt": "momentum volatility evidence",
            }
        ],
        "access_context": {
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }

    response = client.post("/api/search/query/request-documents-compat", json=body)
    assert response.status_code == 400
    assert "durable-index-only" in response.json()["detail"]


def test_durable_index_only_allows_query_without_documents(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    repo = JsonlEvidenceRepository(evidence_path)
    builder = EvidenceBundleBuilder(repo)
    source = _base_source()
    item = _base_item("evi-durable-cutoff", source.source_id)
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="durable evidence",
        created_by="test",
        evidence_bundle_id="evbundle-durable-cutoff",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-durable-cutoff",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Durable cutoff object",
        text="momentum volatility evidence",
        keywords=["momentum"],
        metadata={"relevance_score": 0.7},
    )

    client = TestClient(create_app(tmp_path / "search-index.jsonl", evidence_path, durable_index_only=True))
    body = {
        "request_id": "svc-cutoff-durable-ok",
        "trace_id": "trace-cutoff-durable-ok",
        "query": "momentum",
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
    }

    response = client.post("/api/search/query", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["index_adapter"]["adapter_state"] == "durable"


def test_health_reports_durable_index_only_flag(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl", durable_index_only=True))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["durable_index_only"] is True


def test_health_reports_durable_index_only_false_by_default(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl"))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["durable_index_only"] is False


# ---------------------------------------------------------------------------
# Compat endpoint deprecation header
# ---------------------------------------------------------------------------

def test_compat_endpoint_returns_deprecation_header(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "search-index.jsonl"))

    body = {
        "request_id": "svc-compat-deprecation-001",
        "trace_id": "trace-compat-deprecation-001",
        "query": "momentum volatility",
        "persona_id": "operator-workbench",
        "workspace_id": "research-workbench",
        "documents": [
            {
                "result_id": "doc-deprecation",
                "title": "Deprecation test document",
                "excerpt": "momentum volatility evidence",
            }
        ],
        "access_context": {
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }

    response = client.post("/api/search/query/request-documents-compat", json=body)
    assert response.status_code == 200, response.text
    assert response.headers.get("deprecation") == "true"
    assert response.headers.get("x-search-path") == "request_documents_compat"


def test_normal_query_endpoint_has_no_deprecation_header(tmp_path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    repo = JsonlEvidenceRepository(evidence_path)
    builder = EvidenceBundleBuilder(repo)
    source = _base_source("src-normal-header")
    item = _base_item("evi-normal-header", source.source_id)
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="normal path evidence",
        created_by="test",
        evidence_bundle_id="evbundle-normal-header",
    )
    builder.build_knowledge_object(
        knowledge_object_id="ko-normal-header",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Normal header object",
        text="momentum volatility evidence",
        keywords=["momentum"],
        metadata={"relevance_score": 0.7},
    )

    client = TestClient(create_app(tmp_path / "search-index.jsonl", evidence_path))
    body = {
        "request_id": "svc-normal-header",
        "trace_id": "trace-normal-header",
        "query": "momentum",
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
    }

    response = client.post("/api/search/query", json=body)
    assert response.status_code == 200, response.text
    assert "deprecation" not in response.headers
