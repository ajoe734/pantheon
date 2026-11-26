from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from services.search.filters import SearchCapabilityUnavailableError


@pytest.fixture
def client_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_STORE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    from services.search.main import create_app

    def factory(backend):
        return TestClient(create_app(
            index_store_path=tmp_path / "i.jsonl", evidence_store_path=tmp_path / "e.jsonl",
            materialize_store_path=tmp_path / "m.jsonl", pipeline_store_path=tmp_path / "p.jsonl",
            durable_index_only=True, retrieval_backend=backend))
    return factory


def _backend():
    backend = Mock()
    backend.check_health.return_value = {"status": "ok", "backend": "postgres_pgvector"}
    backend.search.return_value = []
    return backend


BODY = {"query": "台積電 法說", "retrieval_mode": "hybrid", "request_id": "r1",
        "caller_decision_type": "cycle_review", "source_event_id": "evt-1",
        "access_context": {"persona_id": "p1", "workspace_id": "w1"}}


def test_every_served_query_is_recorded(client_factory):
    backend = _backend()
    with client_factory(backend) as client:
        assert client.post("/api/search/query", json=BODY).status_code == 200
    kwargs = backend.record_query.call_args.kwargs
    assert kwargs["query"] == "台積電 法說" and kwargs["retrieval_mode"] == "hybrid"
    assert kwargs["caller_decision_type"] == "cycle_review" and kwargs["source_event_id"] == "evt-1"
    assert kwargs["request_id"] == "r1" and kwargs["latency_ms"] >= 0


def test_log_records_only_gateway_validated_hits(client_factory):
    """A backend candidate with no canonical owner is dropped by the gateway and must not be logged."""
    from services.search.pg_retrieval import RetrievalHitItem
    backend = _backend()
    backend.search.return_value = [RetrievalHitItem(
        id="stale-no-canonical-owner", record_kind="knowledge_object", score=1.0, title="t", search_text="s",
        content_ref=None, citation_label=None, evidence_bundle_id=None, evidence_item_id=None,
        component_scores={}, metadata={}, updated_at=None, ranker_version="postgres-hybrid-v1")]
    with client_factory(backend) as client:
        response = client.post("/api/search/query", json=BODY)
    assert response.status_code == 200 and response.json()["results"] == []
    assert backend.record_query.call_args.kwargs["hits"] == []


def test_query_fails_closed_when_log_unavailable(client_factory):
    backend = _backend()
    backend.record_query.side_effect = RuntimeError("db down")
    with client_factory(backend) as client:
        response = client.post("/api/search/query", json=BODY)
    assert response.status_code == 501
    assert "retrieval_query_log_unavailable" in response.text


def test_pg_query_log_private_scope_and_eval_unavailable():
    """Real PostgreSQL: persona-private rows never reach another persona; eval is unavailable <150."""
    import os
    from services.search.filters import SearchAccessContext
    from services.search.pg_retrieval import PostgresRetrievalBackend, RetrievalIndexRecord

    dsn = os.getenv("PANTHEON_RETRIEVAL_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("Set PANTHEON_RETRIEVAL_INTEGRATION_DSN for isolated pgvector integration")
    engine = Mock(dimension=1024, model_name="test-model")
    engine.is_ready.return_value = True
    backend = PostgresRetrievalBackend(dsn, embedding_engine=engine)
    backend.setup_schema()
    vector = [1.0] + [0.0] * 1023
    engine.embed_query.return_value = vector
    import uuid
    rid = f"qlog-{uuid.uuid4().hex}"
    tenant = "qlog-tenant"
    backend.upsert_documents([RetrievalIndexRecord(
        id="qlog-private", record_kind="knowledge_object", tenant_id=tenant, persona_id="owner",
        title="私有備忘", search_text="私有 備忘 台積電", embedding=vector)])
    try:
        owner = SearchAccessContext(tenant_id=tenant, persona_id="owner")
        hits = backend.search("台積電", context=owner, mode="hybrid")
        assert [h.id for h in hits] == ["qlog-private"]
        backend.record_query(context=owner, request_id=rid, query="台積電", retrieval_mode="hybrid",
                             hits=hits, latency_ms=3.0, caller_decision_type="cycle_review", source_event_id="e1")
        assert rid in [r["request_id"] for r in backend.list_query_log(owner)]
        assert rid not in [r["request_id"] for r in backend.list_query_log(SearchAccessContext(tenant_id=tenant, persona_id="other"))]
        row = next(r for r in backend.list_query_log(owner) if r["request_id"] == rid)
        assert row["persona_private"] and row["model_name"] == "test-model" and row["returned_ids"] == ["qlog-private"]
        from services.search.evaluation.run_retrieval_eval import main
        assert main(["--dsn", dsn, "--tenant", tenant]) == 2
    finally:
        backend.delete_document("qlog-private", tenant_id=tenant, hard=True)


def test_pg_query_log_private_regardless_of_hits_and_labels_inherit_scope():
    """Real PostgreSQL: a persona's query is private even with zero or public-only hits; labels follow."""
    import os, uuid
    from services.search.filters import SearchAccessContext
    from services.search.pg_retrieval import PostgresRetrievalBackend

    dsn = os.getenv("PANTHEON_RETRIEVAL_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("Set PANTHEON_RETRIEVAL_INTEGRATION_DSN for isolated pgvector integration")
    engine = Mock(dimension=1024, model_name="test-model")
    engine.is_ready.return_value = True
    backend = PostgresRetrievalBackend(dsn, embedding_engine=engine)
    backend.setup_schema()
    tenant = "qlog-private-tenant"
    owner = SearchAccessContext(tenant_id=tenant, persona_id="owner")
    other = SearchAccessContext(tenant_id=tenant, persona_id="other")
    rid = f"zero-{uuid.uuid4().hex}"
    backend.record_query(context=owner, request_id=rid, query="私人決策文字", retrieval_mode="hybrid",
                         hits=[], latency_ms=1.0, caller_decision_type="cycle_review", source_event_id="evt-private")
    row = next(r for r in backend.list_query_log(owner) if r["request_id"] == rid)
    assert row["persona_private"] is True
    assert rid not in [r["request_id"] for r in backend.list_query_log(other)]
    backend.record_label(tenant_id=tenant, query_log_id=str(row["query_log_id"]),
                         source_event_id="evt-x", labelled_by="reviewer", persona_id="owner")
    with backend._get_connection() as conn:
        with conn.cursor() as cur:
            for persona, expected in (("owner", 1), ("other", 0)):
                cur.execute("SELECT set_config('pantheon.current_tenant', %s, true)", (tenant,))
                cur.execute("SELECT set_config('pantheon.current_persona', %s, true)", (persona,))
                cur.execute("SELECT count(*) AS n FROM retrieval_query_labels WHERE query_log_id = %s", (row["query_log_id"],))
                assert cur.fetchone()["n"] == expected


def test_rebuild_entry_point_fails_closed_without_model(monkeypatch):
    from services.search import pg_retrieval
    fake = Mock()
    fake.check_health.return_value = {"status": "unavailable", "reason": "embedding_model_unavailable"}
    monkeypatch.setattr(pg_retrieval, "PostgresRetrievalBackend", Mock(return_value=fake))
    with pytest.raises(RuntimeError, match="embedding_model_unavailable"):
        pg_retrieval.rebuild_index()
    fake.setup_schema.assert_not_called()


def test_eval_scores_above_threshold_fixture_not_quality_evidence(monkeypatch, tmp_path):
    """Wiring regression only: fabricated rows prove ranx runs; the numbers are not retrieval-quality evidence."""
    monkeypatch.setenv("IR_DATASETS_HOME", str(tmp_path))
    pytest.importorskip("ranx")
    from services.search.evaluation.run_retrieval_eval import score

    rows, labels = [], {}
    for i in range(150):
        qid = f"00000000-0000-0000-0000-{i:012d}"
        rows.append({"query_log_id": qid, "query_text": "查詢" * (i + 1) + f" {i}",
                     "model_name": "fixture", "retrieval_mode": "hybrid",
                     "returned_source_event_ids": [f"e{i}", "other"], "latency_ms": 5.0})
        labels[qid] = {f"e{i}"}
    out = score(rows, labels)
    assert out["labelled_queries"] == 150
    assert out["metrics"][0]["recall@10"] == 1.0


def test_pg_eval_reads_private_rows_only_in_authorized_persona_scope():
    """Real PostgreSQL: --persona scope exposes the owner's private labelled rows; nonowner scope sees none."""
    import os, uuid
    from services.search.evaluation.run_retrieval_eval import load_recorded
    from services.search.filters import SearchAccessContext
    from services.search.pg_retrieval import PostgresRetrievalBackend

    dsn = os.getenv("PANTHEON_RETRIEVAL_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("Set PANTHEON_RETRIEVAL_INTEGRATION_DSN for isolated pgvector integration")
    engine = Mock(dimension=1024, model_name="test-model")
    engine.is_ready.return_value = True
    backend = PostgresRetrievalBackend(dsn, embedding_engine=engine)
    backend.setup_schema()
    tenant = f"eval-tenant-{uuid.uuid4().hex[:8]}"
    owner = SearchAccessContext(tenant_id=tenant, persona_id="owner")
    rid = f"eval-{uuid.uuid4().hex}"
    backend.record_query(context=owner, request_id=rid, query="私人查詢", retrieval_mode="hybrid", hits=[],
                         latency_ms=1.0, caller_decision_type="cycle_review", source_event_id="evt-1")
    row = next(r for r in backend.list_query_log(owner) if r["request_id"] == rid)
    backend.record_label(tenant_id=tenant, query_log_id=str(row["query_log_id"]),
                         source_event_id="evt-x", labelled_by="reviewer", persona_id="owner")
    for personas, expected in (([], 0), (["other"], 0), (["owner"], 1), (["other", "owner"], 1)):
        rows, labels = load_recorded(dsn, tenant, personas)
        assert len(rows) == expected, personas
        assert len(labels) == expected, personas
