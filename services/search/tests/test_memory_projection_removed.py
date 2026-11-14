"""Search accepts knowledge requests only; memory belongs to memory-svc/Hindsight."""

import json
import os
import uuid
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


@pytest.fixture
def legacy_memory_backend(tmp_path, monkeypatch):
    """Opt-in real pgvector test in a disposable schema, never a served index."""
    import psycopg
    from psycopg import sql
    from services.search.pg_retrieval import PostgresRetrievalBackend
    from services.search.zh_segmentation import _tokenizer

    dsn = os.getenv("PANTHEON_MEMORY_PURGE_TEST_DSN")
    if not dsn:
        pytest.skip("Set PANTHEON_MEMORY_PURGE_TEST_DSN for isolated pgvector cleanup integration")
    dictionary = tmp_path / "dict.txt.big"
    dictionary.write_text("測試 20 n\n", encoding="utf-8")
    monkeypatch.setenv("SEARCH_JIEBA_DICTIONARY", str(dictionary))
    _tokenizer.cache_clear()
    schema = "memory_purge_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        conn.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(schema)))
    backend = PostgresRetrievalBackend(
        dsn,
        embedding_engine=Mock(dimension=1024),
    )
    connection = backend._get_connection

    def scoped_connection(**kwargs):
        conn = connection(**kwargs)
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        return conn

    backend._get_connection = scoped_connection
    try:
        backend.setup_schema()
        with backend._get_connection(schema_admin=True) as conn:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO pantheon_retrieval").format(sql.Identifier(schema)))
            for tenant in ("tenant-a", "tenant-b"):
                for kind in ("persona_memory", "institutional_memory", "knowledge_object"):
                    # Include inactive, uncheckpointed memory rows and overlapping ids.
                    conn.execute("""INSERT INTO search_retrieval_index
                        (id, tenant_id, record_kind, title, search_text, is_active)
                        VALUES (%s, %s, %s, 'fixture', 'fixture', %s)""",
                        (kind, tenant, kind, kind != "institutional_memory"))
            conn.execute("""INSERT INTO retrieval_query_log
                (request_id, query_text, retrieval_mode, model_name, latency_ms)
                VALUES ('keep-log', 'keep', 'keyword', 'fixture', 1)""")
            conn.execute("""INSERT INTO retrieval_query_labels
                (query_log_id, source_event_id, labelled_by)
                SELECT query_log_id, 'keep-label', 'fixture' FROM retrieval_query_log""")
        yield backend
    finally:
        _tokenizer.cache_clear()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_rebuild_purges_all_tenants_and_preserves_knowledge_and_logs(legacy_memory_backend, tmp_path, monkeypatch):
    from services.search import pg_retrieval

    backend = legacy_memory_backend
    monkeypatch.setattr(pg_retrieval, "PostgresRetrievalBackend", Mock(return_value=backend))
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    # Real DB health and cleanup; no model inference is needed for an empty owner store.
    result = pg_retrieval.rebuild_index(evidence_path=tmp_path / "empty.jsonl", pipeline_path=tmp_path / "p.jsonl")
    assert result["is_full_rebuild"] is True
    assert result["memory_cleanup"] == {
        "before_counts": {"institutional_memory": 2, "persona_memory": 2},
        "deleted_count": 4,
        "after_counts": {"institutional_memory": 0, "persona_memory": 0},
        "retained_counts": {"knowledge_object": 2},
    }
    repeated = pg_retrieval.rebuild_index(evidence_path=tmp_path / "empty.jsonl", pipeline_path=tmp_path / "p.jsonl")
    assert repeated["memory_cleanup"]["deleted_count"] == 0
    with backend._get_connection(schema_admin=True) as conn:
        for table in ("retrieval_query_log", "retrieval_query_labels"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 1
    # RLS remains enabled: request role cannot see rows without tenant context.
    with backend._get_connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM search_retrieval_index").fetchone()["n"] == 0
        conn.execute("SELECT set_config('pantheon.current_tenant', 'tenant-a', true)")
        rows = conn.execute("SELECT tenant_id, record_kind FROM search_retrieval_index").fetchall()
        assert rows == [{"tenant_id": "tenant-a", "record_kind": "knowledge_object"}]


def test_purge_fails_closed_with_tenant_only_authority(legacy_memory_backend, monkeypatch):
    import psycopg

    backend = legacy_memory_backend
    connection = backend._get_connection
    monkeypatch.setattr(backend, "_get_connection", lambda **kwargs: connection())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        backend.purge_legacy_memory()
    with connection(schema_admin=True) as conn:
        assert conn.execute("SELECT count(*) AS n FROM search_retrieval_index").fetchone()["n"] == 6


def test_purge_rolls_back_incomplete_deletion(legacy_memory_backend):
    backend = legacy_memory_backend
    with backend._get_connection(schema_admin=True) as conn:
        conn.execute("""CREATE FUNCTION retain_one_memory() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.record_kind = 'persona_memory' THEN RETURN NULL; END IF;
                RETURN OLD;
            END $$""")
        conn.execute("""CREATE TRIGGER retain_one_memory BEFORE DELETE ON search_retrieval_index
            FOR EACH ROW EXECUTE FUNCTION retain_one_memory()""")
    with pytest.raises(RuntimeError, match="cleanup count mismatch"):
        backend.purge_legacy_memory()
    with backend._get_connection(schema_admin=True) as conn:
        assert conn.execute("SELECT count(*) AS n FROM search_retrieval_index").fetchone()["n"] == 6
