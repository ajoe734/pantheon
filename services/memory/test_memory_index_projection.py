"""Production memory routes -> search HTTP routes -> real PostgreSQL projection.

Fixture embeddings are disabled: the Chinese regression proves jieba/FTS wiring,
not semantic quality, hosted delivery, or acceptance of an embedding model.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
import os
from types import SimpleNamespace
import urllib.error
import uuid
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from services.memory import search_retrieval as retrieval
from services.memory.institutional_memory_store import InstitutionalMemoryEntry, InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryEntry, PersonaMemoryStore

main = importlib.import_module("services.memory.main")


def institution(**changes):
    payload = dict(entry_id=f"mem-{uuid.uuid4()}", knowledge_type="incident_lesson",
        content={"headline": "台股期貨風控檢討", "body": "流動性不足時應避免大部位下單", "tags": ["risk"]},
        source_event_type="postmortem_published", source_event_id=f"pm-{uuid.uuid4()}",
        written_at=datetime.now(timezone.utc).isoformat(), write_authority="incident-svc",
        scope="system_wide", tenant_id="projection-test")
    payload.update(changes)
    return InstitutionalMemoryEntry(**payload)


def persona(**changes):
    payload = dict(memory_id=str(uuid.uuid4()), persona_id="persona-alpha", memory_type="strategy_lesson",
        content={"summary": "流動性不足時應避免大部位下單", "tags": ["risk"]},
        source_event_type="session_end", source_event_id=f"session-{uuid.uuid4()}",
        written_at=datetime.now(timezone.utc).isoformat(), write_authority="persona-memory-svc",
        tenant_id="projection-test", relevance_scope="persona_and_committee")
    payload.update(changes)
    return PersonaMemoryEntry(**payload)


@pytest.fixture
def canonical(monkeypatch):
    inst, per = InstitutionalMemoryStore(), PersonaMemoryStore()
    monkeypatch.setattr(main, "_store", lambda: inst)
    monkeypatch.setattr(main, "_persona_store", lambda: per)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setattr(retrieval, "_GLOBAL_SEARCH_BACKEND", None)
    monkeypatch.delenv("PANTHEON_SEARCH_API_URL", raising=False)
    return inst, per


def params(scope="institutional", query="流動性風控", **changes):
    value = dict(actor_id="tester", actor_roles="operator", session_id="projection-http-test",
                 scope=scope, query=query)
    value.update(changes)
    return value


@pytest.mark.parametrize("entry_factory,store_type", [(institution, InstitutionalMemoryStore), (persona, PersonaMemoryStore)])
def test_every_lifecycle_mutation_projects_tombstones(monkeypatch, entry_factory, store_type):
    projected = []
    monkeypatch.setattr(retrieval, "get_search_retrieval_backend", lambda: SimpleNamespace(project=projected.append))
    store = store_type()
    entry = store.create(entry_factory())
    key = getattr(entry, "entry_id", None) or entry.memory_id
    replacement = store.create(entry_factory())
    store.supersede(key, getattr(replacement, "entry_id", None) or replacement.memory_id)
    store.archive(getattr(replacement, "entry_id", None) or replacement.memory_id, reason="operator_retirement")
    expired = store.create(entry_factory(expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()))
    store.archive_expired()
    assert len(projected) == 6
    assert not projected[2].is_active and not projected[3].is_active
    assert projected[-1].archived_at and projected[-1].is_expired()
    project = retrieval.project_institutional_entry if store_type is InstitutionalMemoryStore else retrieval.project_persona_entry
    assert not project(projected[-1]).is_active
    assert project(entry).metadata["source_event_id"] == entry.source_event_id
    assert project(entry).tenant_id == "projection-test"
    assert project(entry).environment_scope == ["paper"]
    if store_type is PersonaMemoryStore:
        assert project(entry).metadata["relevance_scope"] == entry.relevance_scope


def test_backfill_replays_existing_entries_and_reports_failures(canonical, monkeypatch):
    inst, per = canonical
    inst.create(institution()); per.create(persona())
    records = {}
    def project(entry):
        records[getattr(entry, "entry_id", None) or entry.memory_id] = entry
    monkeypatch.setattr(retrieval, "get_search_retrieval_backend", lambda: SimpleNamespace(project=project))
    for _ in range(2):
        report = retrieval.backfill_memory_index(inst, per)
        assert report == {"processed": 2, "projected": 2, "tombstones": 0, "failures": []}
        assert len(records) == 2
    monkeypatch.setattr(retrieval, "get_search_retrieval_backend", lambda: None)
    report = retrieval.backfill_memory_index(inst, per)
    assert report["projected"] == 0 and len(report["failures"]) == 2
    assert {f["reason"] for f in report["failures"]} == {"search_service_unconfigured"}


def test_hydration_rejects_bad_candidates_and_preserves_committee_boundary(canonical, monkeypatch):
    inst, per = canonical
    entries = [persona(), persona(tenant_id="other"), persona(persona_id="persona-beta"),
               persona(relevance_scope="persona_private"), persona(superseded_by="replacement"),
               persona(expires_at="2020-01-01T00:00:00Z"), persona(archived_at="2020-01-01T00:00:00Z", archived_reason="retired")]
    for entry in entries:
        per.create(entry)
    backend = SimpleNamespace(search=lambda **kw: [SimpleNamespace(id=e.memory_id, score=1) for e in entries] + [SimpleNamespace(id="missing", score=1)])
    hits = retrieval.retrieve_persona_with_backend(per, "persona-alpha", backend, query="風控",
        tenant_id="projection-test", relevance_scope="persona_and_committee")
    assert [h.entry.memory_id for h in hits] == [entries[0].memory_id]
    for tenant in ("projection-test", "other"):
        inst.create(institution(tenant_id=tenant))
    # Fallback also applies tenant filters before limit.
    hits = retrieval.retrieve_institutional_with_backend(inst, query="流動性", tenant_id="projection-test", limit=1)
    assert len(hits) == 1 and hits[0].entry.tenant_id == "projection-test"


def test_http_fallback_modes_and_reasons(canonical, monkeypatch, caplog):
    canonical[0].create(institution())
    with TestClient(main.app) as client:
        result = client.get("/api/memory/retrieve", params=params(query="流動性")).json()
        assert result["count"] == 1 and result["retrieval_mode"] == "keyword_fallback"
        assert result["fallback_reason"] == "search_service_unconfigured"
        def broken(**kwargs):
            raise OSError("unreachable")
        monkeypatch.setattr(retrieval, "get_search_retrieval_backend", lambda: SimpleNamespace(search=broken))
        result = client.get("/api/memory/retrieve", params=params(query="流動性")).json()
        assert result["retrieval_mode"] == "keyword_fallback"
        assert result["fallback_reason"] == "search_service_unavailable"
        assert "memory_retrieval_fallback" in caplog.text


@pytest.fixture
def search_http(tmp_path, monkeypatch):
    from services.search.main import create_app
    from services.memory.test_main import _make_jwt, JWT_SECRET
    backend = Mock()
    backend.embedding_engine.is_ready.return_value = False
    monkeypatch.setenv("SEARCH_INDEX_STORE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    monkeypatch.setenv("PANTHEON_SEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_SEARCH_JWT_SECRET", JWT_SECRET)
    app = create_app(index_store_path=tmp_path / "index", evidence_store_path=tmp_path / "evidence",
        pipeline_store_path=tmp_path / "pipeline", materialize_store_path=tmp_path / "materialize",
        retrieval_backend=backend, durable_index_only=True)
    token = _make_jwt(sub="memory-svc", tenant_id="projection-test", roles=["system"])
    with TestClient(app) as client:
        yield client, backend, {"Authorization": f"Bearer {token}"}


def test_existing_search_routes_require_owner_identity_and_tenant(search_http):
    from services.memory.test_main import _make_jwt
    client, backend, headers = search_http
    payload = {"memory_entries": [{"record_kind": "institutional_memory", "entry": institution().to_dict()}]}
    assert client.post("/api/search/index/refresh", json=payload).status_code == 401
    other = {"Authorization": f"Bearer {_make_jwt(sub='research-svc', tenant_id='projection-test', roles=['system'])}"}
    assert client.post("/api/search/index/refresh", json=payload, headers=other).status_code == 403
    payload["memory_entries"][0]["entry"]["tenant_id"] = "other"
    assert client.post("/api/search/index/refresh", json=payload, headers=headers).status_code == 403
    assert not backend.index_documents.called
    body = dict(query="風控", memory_record_kind="persona_memory", role_refs=["system"],
                access_context={"tenant_id": "projection-test", "persona_id": "persona-alpha", "workspace_id": "memory", "role_refs": ["system"]})
    assert client.post("/api/search/query", json=body, headers=other).status_code == 403
    assert client.post("/api/search/query", json={**body, "access_context": {**body["access_context"], "tenant_id": "other"}}, headers=headers).status_code == 403


def test_memory_http_to_search_http_real_postgres_zh(canonical, search_http, monkeypatch):
    from services.search.pg_retrieval import PostgresRetrievalBackend
    dsn = os.getenv("PANTHEON_RETRIEVAL_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("Set PANTHEON_RETRIEVAL_INTEGRATION_DSN for real pgvector integration")
    client, server_backend, headers = search_http
    engine = Mock(dimension=1024)
    engine.is_ready.return_value = False  # explicitly lexical-only; no fixture model quality claims
    engine.model_name = "test-lexical-only"
    backend = PostgresRetrievalBackend(dsn, embedding_engine=engine)
    backend.setup_schema()
    for method in ("search", "index_documents", "record_query"):
        getattr(server_backend, method).side_effect = getattr(backend, method)
    # Exercise the real SearchServiceClient JSON and authentication transport through ASGI.
    transport_errors = []
    def urlopen(request, timeout):
        response = client.post(request.full_url.removeprefix("http://search-test"),
                               content=request.data, headers=dict(request.header_items()))
        if response.status_code >= 400:
            transport_errors.append(f"Search HTTP {response.status_code}: {response.text}")
            raise urllib.error.HTTPError(request.full_url, response.status_code, response.text, {}, None)
        return io.BytesIO(response.content)
    monkeypatch.setattr(retrieval.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("PANTHEON_SEARCH_API_URL", "http://search-test")
    monkeypatch.setenv("PANTHEON_SEARCH_SERVICE_TOKEN", headers["Authorization"].removeprefix("Bearer "))
    from services.memory.test_main import _make_jwt, JWT_SECRET
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    memory_headers = {"Authorization": f"Bearer {_make_jwt(sub='tester', tenant_id='projection-test', roles=['operator'])}"}
    entries = [institution(), persona()]
    try:
        with TestClient(main.app, headers=memory_headers) as memory:
            for route, entry in zip(("entries", "persona-entries"), entries):
                response = memory.post(f"/api/memory/{route}", json=entry.to_dict())
                assert response.status_code == 201, response.text
            assert server_backend.index_documents.call_count == 2
            assert canonical[0].retrieve(query="流動性風控") == []  # space-splitting path misses
            result = memory.get("/api/memory/retrieve", params=params()).json()
            assert result["retrieval_mode"] == "lexical_only", (result, transport_errors)
            assert result["fallback_reason"] == "embedding_model_unavailable"
            assert result["hits"][0]["entry"]["entry_id"] == entries[0].entry_id
            result = memory.get("/api/memory/retrieve", params=params(scope="persona", persona_id="persona-alpha", query="流動性下單")).json()
            assert result["hits"][0]["entry"]["memory_id"] == entries[1].memory_id
            # Tombstones on supersede/archive and repeated backfill preserve identity.
            canonical[0].supersede(entries[0].entry_id, "replacement")
            canonical[1].archive(entries[1].memory_id, reason="test_retired")
            for _ in range(2):
                report = retrieval.backfill_memory_index(*canonical)
                assert report["projected"] == 2 and report["tombstones"] == 2 and not report["failures"]
            assert memory.get("/api/memory/retrieve", params=params()).json()["hits"] == []
            assert memory.get("/api/memory/retrieve", params=params(scope="persona", persona_id="persona-alpha", query="流動性下單")).json()["hits"] == []
    finally:
        for entry in entries:
            backend.delete_document(getattr(entry, "entry_id", None) or entry.memory_id,
                                    tenant_id="projection-test", hard_delete=True)


@pytest.mark.parametrize("ready,mode", [(True, "hybrid"), (False, "lexical_only")])
def test_search_candidate_modes_and_logging_fail_closed(search_http, ready, mode):
    client, backend, headers = search_http
    backend.embedding_engine.is_ready.return_value = ready
    backend.search.return_value = [SimpleNamespace(id="canonical-id", score=0.9, search_text="never returned")]
    body = dict(query="風控", memory_record_kind="persona_memory", role_refs=["system"],
                source_event_id="session-accepted", access_context={"tenant_id": "projection-test", "persona_id": "persona-alpha", "workspace_id": "memory", "role_refs": ["system"]})
    response = client.post("/api/search/query", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"candidates": [{"id": "canonical-id", "score": 0.9}],
                              "retrieval_mode": mode, "fallback_reason": None if ready else "embedding_model_unavailable"}
    assert backend.record_query.call_args.kwargs["source_event_id"] == "session-accepted"
    backend.record_query.side_effect = RuntimeError("database unavailable")
    response = client.post("/api/search/query", json=body, headers=headers)
    assert response.status_code == 501 and "retrieval_query_log_unavailable" in response.text
