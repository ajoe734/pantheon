import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import yaml

from services.search.local_embeddings import LocalEmbeddingEngine


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_STORE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    monkeypatch.delenv("PANTHEON_SEARCH_BACKEND", raising=False)
    monkeypatch.delenv("PANTHEON_SEARCH_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("SEARCH_POSTGRES_DSN", raising=False)
    from services.search.main import create_app

    def factory(**kwargs):
        return create_app(
            index_store_path=tmp_path / "index.jsonl",
            evidence_store_path=tmp_path / "evidence.jsonl",
            materialize_store_path=tmp_path / "materialize.jsonl",
            pipeline_store_path=tmp_path / "pipeline.jsonl", **kwargs,
        )
    return factory


def test_configured_backend_activates_with_durable_only(app_factory, monkeypatch):
    from services.search import pg_retrieval

    backend = Mock()
    backend.check_health.return_value = {"status": "ok", "backend": "postgres_pgvector"}
    backend.search.return_value = []
    monkeypatch.setattr(pg_retrieval, "PostgresRetrievalBackend", Mock(return_value=backend))
    monkeypatch.setenv("PANTHEON_SEARCH_POSTGRES_DSN", "postgresql://test.invalid/projection")
    with TestClient(app_factory(durable_index_only=True)) as client:
        assert client.get("/readyz").status_code == 200
        result = client.post("/api/search/query", json={
            "query": "台積電", "retrieval_mode": "hybrid",
            "access_context": {"persona_id": "p1", "workspace_id": "w1"},
        })
        assert result.status_code == 200, result.text
        assert result.json()["retrieval_backend"] == "postgres_pgvector"
        assert result.json()["capabilities"]["hybrid"]["available"] is True
        assert result.headers["X-Retrieval-Backend"] == "postgres_pgvector"
        assert backend.search.call_args.kwargs["mode"] == "hybrid"
        denied = client.post("/api/search/query", json={"documents": [{"result_id": "x", "title": "x"}]})
        assert denied.status_code == 400


def test_configured_database_failure_does_not_fall_back(app_factory):
    from services.search.filters import SearchCapabilityUnavailableError

    backend = Mock()
    backend.check_health.return_value = {"status": "unavailable", "reason": "model_integrity_failed"}
    backend.search.side_effect = SearchCapabilityUnavailableError("model_integrity_failed")
    with TestClient(app_factory(retrieval_backend=backend, durable_index_only=True)) as client:
        assert client.get("/readyz").status_code == 503
        result = client.post("/api/search/query", json={
            "query": "momentum", "retrieval_mode": "hybrid",
            "access_context": {"persona_id": "p", "workspace_id": "w"},
        })
        assert result.status_code == 501
        assert result.headers["X-Retrieval-Backend"] == "postgres_pgvector"


def test_explicit_jsonl_fallback_is_visible_even_on_errors(app_factory, monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_BACKEND", "jsonl")
    with TestClient(app_factory()) as client:
        for response in (client.get("/health"), client.get("/not-found")):
            assert response.headers["X-Retrieval-Backend"] == "jsonl"
            assert response.headers["X-Retrieval-Reason"] == "explicit_jsonl_fallback"


@pytest.fixture
def model_package(tmp_path):
    # Tiny non-model bytes test the integrity boundary; never used for inference.
    files = {"model.onnx": b"test-only-model", "config.json": b'{"hidden_size": 1024}', "tokenizer.json": b"{}"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    manifest = {
        "model_name": "test/model", "dimension": 1024, "revision": "a" * 40,
        "validation_status": "accepted",
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, tmp_path, manifest


def test_packaged_model_integrity_and_corruption(model_package):
    path, directory, _ = model_package
    assert LocalEmbeddingEngine(path, directory).verify_integrity()
    (directory / "model.onnx").write_bytes(b"corrupt")
    assert not LocalEmbeddingEngine(path, directory).verify_integrity()


@pytest.mark.parametrize("failure", ["unaccepted", "empty_files", "missing_digest", "escape_path", "dimension"])
def test_model_manifest_failures_reject_readiness(model_package, failure):
    path, directory, manifest = model_package
    if failure == "unaccepted":
        manifest["validation_status"] = "pending"
    elif failure == "empty_files":
        manifest["files"] = {}
    elif failure == "missing_digest":
        manifest["files"]["model.onnx"] = {}
    elif failure == "escape_path":
        manifest["files"]["../outside"] = {"sha256": "a" * 64}
    elif failure == "dimension":
        manifest["dimension"] = 384
    path.write_text(json.dumps(manifest))
    assert not LocalEmbeddingEngine(path, directory).verify_integrity()


def test_compose_projection_is_isolated_from_main_postgres():
    root = Path(__file__).resolve().parents[3]
    config = yaml.safe_load((root / "docker-compose.yml").read_text())
    projection = config["services"]["retrieval-postgres"]
    assert projection["image"].startswith("pgvector/pgvector:0.8.0-pg16@sha256:")
    assert "ports" not in projection
    assert "retrieval-postgres-data:/var/lib/postgresql/data" in projection["volumes"]
    assert "postgres-data:/var/lib/postgresql/data" in config["services"]["postgres"]["volumes"]


def test_pg_sql_segmentation_rls_and_private_persona_isolation(monkeypatch):
    """Real PostgreSQL integration; fixture vectors are not quality evidence."""
    from services.search.filters import SearchAccessContext
    from services.search.pg_retrieval import PostgresRetrievalBackend, RetrievalIndexRecord
    from services.search.zh_segmentation import _tokenizer

    dsn = os.getenv("PANTHEON_RETRIEVAL_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("Set PANTHEON_RETRIEVAL_INTEGRATION_DSN for isolated pgvector integration")
    _tokenizer.cache_clear()
    engine = Mock(dimension=1024)
    engine.is_ready.return_value = True
    backend = PostgresRetrievalBackend(dsn, embedding_engine=engine)
    backend.setup_schema()
    backend.setup_schema()  # rebuild schema is idempotent
    assert backend.check_health()["status"] == "ok"
    vector = [1.0] + [0.0] * 1023
    engine.embed_query.return_value = vector
    records = [RetrievalIndexRecord(
        id="activation-private", record_kind="knowledge_object", tenant_id=tenant,
        persona_id="owner", title="台積電晶圓代工", search_text="2330 台股風險管理",
        embedding=vector,
    ) for tenant in ("activation-a", "activation-b")]
    try:
        assert backend.upsert_documents(records) == 2
        owner = SearchAccessContext(tenant_id="activation-a", persona_id="owner")
        for mode in ("full_text", "semantic", "hybrid"):
            assert [h.id for h in backend.search("台積電", context=owner, mode=mode)] == ["activation-private"]
        assert not backend.search("台積電", context=SearchAccessContext(tenant_id="activation-a", persona_id="other"), mode="full_text")
        assert not backend.search("台積電", context=SearchAccessContext(tenant_id="absent", persona_id="owner"), mode="full_text")
        with backend._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                assert cur.fetchone() == {"rolsuper": False, "rolbypassrls": False}
                cur.execute("SELECT count(*) AS count FROM search_retrieval_index")
                assert cur.fetchone()["count"] == 0  # no tenant -> RLS denies
                cur.execute("SELECT set_config('pantheon.current_tenant', 'activation-a', true)")
                cur.execute("SELECT lexical_text FROM search_retrieval_index WHERE id='activation-private'")
                assert "台積電" in cur.fetchone()["lexical_text"].split()
    finally:
        for tenant in ("activation-a", "activation-b"):
            backend.delete_document("activation-private", tenant_id=tenant, hard=True)


def test_shipped_manifest_is_accepted_and_complete():
    manifest = json.loads((Path(__file__).parents[1] / "model-manifest.json").read_text())
    assert manifest["validation_status"] == "accepted"
    assert manifest["model_name"] == "intfloat/multilingual-e5-large"
    assert all(len(v["sha256"]) == 64 and v["source_path"] for v in manifest["files"].values())


def test_evaluation_unavailable_below_150_and_refuses_templates():
    from services.search.evaluation.run_retrieval_eval import refuse_templated, score
    rows = [{"query_log_id": f"q{i}", "query_text": f"query {i}"} for i in range(10)]
    with pytest.raises(ValueError, match="150"):
        score(rows, {r["query_log_id"]: {"s"} for r in rows})
    with pytest.raises(ValueError, match="templated"):
        refuse_templated([f"台積電 {i} 營收" for i in range(200)])


def test_evaluation_without_dsn_reports_unavailable(monkeypatch, capsys):
    from services.search.evaluation.run_retrieval_eval import main
    monkeypatch.delenv("PANTHEON_SEARCH_POSTGRES_DSN", raising=False)
    assert main([]) == 2
    assert "unavailable" in capsys.readouterr().out


def test_absent_backend_selection_and_dsn_is_unavailable_not_jsonl(app_factory):
    """Default is the accepted Postgres backend; JSONL needs an explicit choice."""
    with TestClient(app_factory()) as client:
        for path in ("/readyz", "/api/search/query"):
            response = client.get(path) if path == "/readyz" else client.post(path, json={"query": "x"})
            assert response.headers["X-Retrieval-Backend"] != "jsonl"
            assert response.headers.get("X-Retrieval-Reason") != "explicit_jsonl_fallback"
        ready = client.get("/readyz")
        assert ready.status_code != 200
