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


def _add_tenant_evidence(repository, tenant, *, text, citation):
    source = SourceRecord(
        source_id="same-source", connector_id="notes", source_type="internal_note",
        title="momentum", content_ref="note://same",
        metadata={"access_scope": ["research"], **({"tenant_id": tenant} if tenant else {})},
    )
    item = EvidenceItem(
        evidence_item_id="same-item", source_id=source.source_id, item_type="text_chunk",
        content_ref="note://same#1", citation_label=citation, body=text,
        access_scope=["research"], metadata={"tenant_id": tenant} if tenant else {},
    )
    builder = EvidenceBundleBuilder(repository)
    bundle = builder.build_bundle(
        source_records=[source], evidence_items=[item], summary=text,
        created_by="test", evidence_bundle_id="same-bundle",
    )
    return builder.build_knowledge_object(
        knowledge_object_id="same-object", source_record=source, evidence_item=item,
        evidence_bundle=bundle, title="momentum", text=text,
    )


@pytest.mark.parametrize("tenant", [None, "tenant-a"])
def test_same_named_foreign_evidence_cannot_affect_rank_text_citations_or_counts(tenant):
    repository = InMemoryEvidenceRepository()
    own = _add_tenant_evidence(repository, tenant, text="momentum own", citation="own#1")
    context = SearchAccessContext(tenant_id=tenant or "default", access_scopes=["research"])
    request = SearchRequest(query="momentum", persona_id="persona", workspace_id="workspace")
    before = SearchGateway(repository).search(request, context)
    _add_tenant_evidence(repository, "tenant-b", text="secret foreignword", citation="secret#1")
    after = SearchGateway(repository).search(request, context)
    assert [r.answer_context for r in after.results] == ["momentum own"]
    assert [r.citations for r in after.results] == [["own#1"]]
    assert after.rejected_items_count == before.rejected_items_count == 0
    assert [r.relevance_score for r in after.results] == [r.relevance_score for r in before.results]
    document = KeywordIndexAdapter(repository).documents_for([own])[0]
    assert "secret" not in document.search_text
    assert "foreignword" not in document.search_text


def test_backend_candidate_hydrates_only_request_tenant_owner():
    from types import SimpleNamespace

    repository = InMemoryEvidenceRepository()
    _add_tenant_evidence(repository, "tenant-a", text="momentum own", citation="own#1")
    _add_tenant_evidence(repository, "tenant-b", text="secret", citation="secret#1")
    hit = SimpleNamespace(id="same-object", ranker_version="test", score=1.0,
                          component_scores={}, matched_terms=())
    backend = SimpleNamespace(search=lambda **kwargs: [hit])
    response = SearchGateway(repository, retrieval_backend=backend).search(
        SearchRequest(query="momentum"),
        SearchAccessContext(tenant_id="tenant-a", access_scopes=["research"]),
    )
    assert [r.answer_context for r in response.results] == ["momentum own"]
    assert [r.citations for r in response.results] == [["own#1"]]


# ============================================================================
# Inbound Search API Authentication & Access Context Ceiling Tests
# ============================================================================

import time
from fastapi.testclient import TestClient
from services.runtime_auth_inbound import encode_jwt_hs256
from services.search.main import create_app

JWT_SECRET = "search-test-secret-key-12345"


def _make_jwt(
    *,
    sub: str | None = "search-operator-1",
    roles: list[str] | None = None,
    tenant_id: str | None = "tenant-alpha",
    exp_offset: int = 3600,
    secret: str = JWT_SECRET,
    custom_claims: dict | None = None,
) -> str:
    claims: dict = {}
    if sub is not None:
        claims["sub"] = sub
    if roles is not None:
        claims["roles"] = roles
    elif roles is None and "roles" not in (custom_claims or {}):
        claims["roles"] = ["operator", "researcher"]
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if exp_offset is not None:
        claims["exp"] = int(time.time()) + exp_offset
    if custom_claims:
        claims.update(custom_claims)
    return encode_jwt_hs256(claims, secret=secret)


def _valid_query_body(**overrides) -> dict:
    body = {
        "request_id": "req-search-auth-001",
        "trace_id": "trace-search-auth-001",
        "query": "alpha",
        "documents": [],
        "persona_id": "persona-researcher",
        "workspace_id": "workspace-alpha",
        "access_context": {
            "persona_id": "persona-researcher",
            "workspace_id": "workspace-alpha",
            "environment": "paper",
            "access_scopes": ["operator", "research"],
            "license_scopes": ["internal"],
        },
    }
    body.update(overrides)
    return body


@pytest.fixture
def search_client(tmp_path):
    index_path = tmp_path / "search-index.jsonl"
    return TestClient(create_app(index_path))


def test_search_auth_permissive_mode_unauthenticated_allowed(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "permissive")
    resp = search_client.post("/api/search/query", json=_valid_query_body())
    assert resp.status_code == 200, resp.text


def test_search_auth_strict_mode_missing_token_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    resp = search_client.post("/api/search/query", json=_valid_query_body())
    assert resp.status_code == 401
    assert "missing Bearer token" in resp.text


def test_search_auth_invalid_signature_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    bad_token = _make_jwt(secret="wrong-secret-key-999")
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


def test_search_auth_expired_token_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    expired_token = _make_jwt(exp_offset=-100)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


def test_search_auth_missing_sub_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub=None)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "sub" in resp.text


def test_search_auth_missing_exp_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(exp_offset=None)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "exp" in resp.text


def test_search_auth_missing_tenant_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id=None)
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "tenant" in resp.text


def test_search_auth_missing_roles_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=[])
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "role" in resp.text.lower()


def test_search_auth_whitespace_claim_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="   ")
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "whitespace" in resp.text


def test_search_auth_forged_body_tenant_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(tenant_id="tenant-alpha")
    body = _valid_query_body()
    body["access_context"]["tenant_id"] = "tenant-beta"
    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "outside the verified caller scope" in resp.text


def test_search_auth_forged_body_actor_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="operator-alpha")
    body = _valid_query_body()
    body["actor_ref"] = "operator-intruder"
    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "actor_ref does not match verified token identity" in resp.text


def test_search_auth_role_elevation_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=["researcher"])
    body = _valid_query_body()
    body["role_refs"] = ["admin"]
    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "exceed verified token roles" in resp.text


def test_search_auth_insufficient_endpoint_role_rejected(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=["guest_viewer"])
    resp = search_client.post(
        "/api/search/query",
        json=_valid_query_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_search_auth_valid_token_populates_context(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(sub="researcher-carol", tenant_id="tenant-finance", roles=["researcher"])
    body = _valid_query_body()
    body.pop("actor_ref", None)
    body["access_context"]["actor_ref"] = None
    body["access_context"]["tenant_id"] = None

    resp = search_client.post(
        "/api/search/query",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


def test_search_admin_endpoints_role_enforcement(search_client, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)

    # 1. Researcher cannot call materialize or reload
    researcher_token = _make_jwt(roles=["researcher"])
    resp = search_client.post(
        "/api/search/index/materialize",
        headers={"Authorization": f"Bearer {researcher_token}"},
    )
    assert resp.status_code == 403

    resp = search_client.post(
        "/api/search/index/reload",
        headers={"Authorization": f"Bearer {researcher_token}"},
    )
    assert resp.status_code == 403

    # 2. Admin/operator can call reload
    admin_token = _make_jwt(roles=["admin"])
    resp = search_client.post(
        "/api/search/index/reload",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

