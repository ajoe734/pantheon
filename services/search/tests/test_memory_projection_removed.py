"""Search accepts knowledge requests only; memory belongs to memory-svc/Hindsight."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from services.search.main import create_app


@pytest.fixture
def search_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_STORE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    backend = Mock()
    backend.check_health.return_value = {"status": "ok", "backend": "postgres_pgvector"}
    backend.search.return_value = []
    with TestClient(create_app(
        index_store_path=tmp_path / "index.jsonl",
        evidence_store_path=tmp_path / "evidence.jsonl",
        materialize_store_path=tmp_path / "materialize.jsonl",
        pipeline_store_path=tmp_path / "pipeline.jsonl",
        retrieval_backend=backend,
        durable_index_only=True,
    )) as client:
        yield client, backend, tmp_path


@pytest.mark.parametrize("route", [
    "/api/search/query", "/api/search/query/request-documents-compat",
    "/api/search/v2/query", "/api/search/v2/alpha-query",
    "/api/search/index/refresh", "/api/search/index/source-completions",
])
@pytest.mark.parametrize("legacy", [
    {"memory_entries": []}, {"memory_entries": None},
    {"memory_entries": [{"record_kind": "persona_memory", "entry": {"id": "private"}}]},
    {"memory_entries": [{"record_kind": "institutional_memory", "entry": {"id": "shared"}}]},
    {"memory_record_kind": "persona_memory"},
    {"memory_record_kind": "institutional_memory"}, {"memory_record_kind": None},
    {"memory_record_kind": "unknown"},
])
def test_legacy_memory_fields_rejected_before_backend_or_checkpoint(search_client, route, legacy):
    client, backend, tmp_path = search_client
    response = client.post(route, json={"ingest_run_id": "old-client", **legacy})
    assert response.status_code == 422, response.text
    assert "memory_search_projection_removed" in response.text
    assert "canonical memory service" in response.text
    assert next(iter(legacy)) in response.text
    backend.index_documents.assert_not_called()
    backend.search.assert_not_called()
    backend.record_query.assert_not_called()
    assert not (tmp_path / "pipeline.jsonl").exists()
    assert not (tmp_path / "index.jsonl").exists()


def test_memory_fields_rejected_even_without_retrieval_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_STORE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    monkeypatch.setenv("PANTHEON_SEARCH_BACKEND", "postgres_pgvector")
    monkeypatch.delenv("PANTHEON_SEARCH_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("SEARCH_POSTGRES_DSN", raising=False)
    with TestClient(create_app(
        index_store_path=tmp_path / "i.jsonl", evidence_store_path=tmp_path / "e.jsonl",
        materialize_store_path=tmp_path / "m.jsonl", pipeline_store_path=tmp_path / "p.jsonl",
    )) as client:
        for route, legacy in [
            ("/api/search/query", {"memory_record_kind": "persona_memory"}),
            ("/api/search/index/refresh", {"memory_entries": []}),
        ]:
            response = client.post(route, json=legacy)
            assert response.status_code == 422
            assert "memory_search_projection_removed" in response.text


def test_openapi_has_no_memory_projection_contract(search_client):
    client, _, _ = search_client
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "MemoryProjectionBody" not in schemas
    for name in ("SearchQueryBody", "StructuredAlphaQueryBody", "IndexRefreshBody", "SourceCompletionBody"):
        assert not {"memory_record_kind", "memory_entries"}.intersection(schemas[name]["properties"])


def test_knowledge_query_and_full_refresh_keep_existing_backend_paths(search_client):
    client, backend, _ = search_client
    response = client.post("/api/search/query", json={
        "query": "台積電", "retrieval_mode": "hybrid",
        "access_context": {"persona_id": "p1", "workspace_id": "w1"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["results"] == []
    assert backend.search.call_args.kwargs["record_kind"] == "knowledge_object"
    assert backend.search.call_args.kwargs["mode"] == "hybrid"
    backend.record_query.assert_called_once()
    refresh = client.post("/api/search/index/refresh", json={"force_full": True})
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["pipeline_snapshot"]["is_full_rebuild"] is True


def test_backend_contract_delegates_memory_to_hindsight():
    contract = json.loads((Path(__file__).parents[1] / "backend-contract.json").read_text())
    memory = contract["memory_index"]
    assert memory["backend"] == "hindsight"
    assert memory["owner_service"] == "memory"
    assert memory["search_projection_enabled"] is False
    assert (Path(__file__).parents[3] / memory["design_ref"]).is_file()
    assert all(not path.startswith("services/memory/") for path in contract["candidate_modules"])
    assert contract["accepted"] and contract["activation_allowed"]
