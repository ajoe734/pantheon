from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_search_service_and_bff_normal_path() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    search = services["search-svc"]
    assert search["build"]["dockerfile"] == "services/search/Dockerfile"
    assert search["environment"]["PORT"] == "8098"
    assert search["environment"]["SEARCH_DATA_DIR"] == "/data/search"
    assert search["environment"]["SEARCH_INDEX_STORE_PATH"] == "/data/search/search-index.jsonl"
    assert search["environment"]["SEARCH_MATERIALIZE_STORE_PATH"] == "/data/search/search-materialize.jsonl"
    assert search["environment"]["SEARCH_EVIDENCE_STORE_PATH"] == "/data/source-ingest/source_evidence.jsonl"
    assert search["environment"]["PANTHEON_SOURCE_SEARCH_POSTURE"] == "${PANTHEON_SOURCE_SEARCH_POSTURE:-dev}"
    assert search["environment"]["PANTHEON_S3_ENDPOINT"] == "${PANTHEON_S3_ENDPOINT:-http://minio:9000}"
    assert search["environment"]["PANTHEON_ARTIFACT_BUCKET"] == "${PANTHEON_ARTIFACT_BUCKET:-pantheon-artifacts}"
    assert "search-data:/data/search" in search["volumes"]
    assert "source-ingest-data:/data/source-ingest:ro" in search["volumes"]
    assert search["ports"] == ["${SEARCH_PORT:-18098}:8098"]
    assert search["depends_on"]["source-ingest"]["condition"] == "service_healthy"
    assert "healthcheck" in search

    bff = services["operator-bff"]
    assert bff["environment"]["PANTHEON_SEARCH_API_URL"] == "http://search-svc:8098"
    assert bff["depends_on"]["search-svc"]["condition"] == "service_healthy"

    scheduler = services["search-index-scheduler"]
    assert scheduler["command"] == ["python", "-m", "services.search.scheduler_worker"]
    assert scheduler["environment"]["SEARCH_API_URL"] == "http://search-svc:8098"
    assert scheduler["environment"]["SEARCH_INDEX_SCHEDULER_MATERIALIZE"] == "${SEARCH_INDEX_SCHEDULER_MATERIALIZE:-true}"
    assert scheduler["environment"]["SEARCH_INDEX_SCHEDULER_ALIVE_PATH"] == "${SEARCH_INDEX_SCHEDULER_ALIVE_PATH:-/data/search/search_scheduler_alive}"
    assert "search-data:/data/search" in scheduler["volumes"]
    assert scheduler["depends_on"]["search-svc"]["condition"] == "service_healthy"
    assert "healthcheck" in scheduler


def test_honest_stack_smoke_waits_for_and_queries_search_service() -> None:
    smoke = (ROOT / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")

    assert 'SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098")' in smoke
    assert '_wait_for_health("search-svc", f"{SEARCH_URL}/readyz")' in smoke
    assert 'f"{SEARCH_URL}/api/search/index/reload"' in smoke
    assert 'f"{SEARCH_URL}/api/search/query"' in smoke
    assert 'f"{SEARCH_URL}/api/search/snapshots/{search_body[\'request_id\']}"' in smoke
    assert '"query": f"momentum volatility {source_search_token}"' in smoke
    assert '"documents": [' not in smoke

    scheduler_worker = (ROOT / "services/search/scheduler_worker.py").read_text(encoding="utf-8")
    assert '"/api/search/index/refresh"' in scheduler_worker
    assert '"/api/search/index/materialize"' in scheduler_worker
    assert '"triggered_by": "scheduled_refresh"' in scheduler_worker

    prod_env = (ROOT / "env/prod-control.env.example").read_text(encoding="utf-8")
    assert "PANTHEON_SOURCE_SEARCH_POSTURE=production" in prod_env
    assert "SEARCH_INDEX_STORE_BACKEND=postgres" in prod_env
    assert "SEARCH_EVIDENCE_BACKEND=postgres" in prod_env
    assert "SEARCH_DURABLE_INDEX_ONLY=true" in prod_env

    prod_smoke = (ROOT / "scripts/smoke_source_search_prod_posture.py").read_text(encoding="utf-8")
    assert "posture_alert_count" in prod_smoke
    assert '"SEARCH_INDEX_STORE_BACKEND": "postgres"' in prod_smoke


def test_search_dockerfile_exposes_service_port_and_uses_service_requirements() -> None:
    dockerfile = (ROOT / "services/search/Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "services/search/requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "COPY services/search/requirements.txt /tmp/requirements.txt" in dockerfile
    assert "ENV PORT=8098" in dockerfile
    assert "EXPOSE 8098" in dockerfile
    assert "uvicorn services.search.main:app" in dockerfile
    assert {"fastapi", "uvicorn", "pydantic"}.issubset(set(requirements))


@pytest.mark.parametrize("dsn_variable", ["PANTHEON_SEARCH_POSTGRES_DSN", "SEARCH_POSTGRES_DSN"])
@pytest.mark.parametrize("candidate_ready", [True, False], ids=["available", "unavailable"])
def test_unaccepted_candidate_cannot_activate_product_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dsn_variable: str, candidate_ready: bool,
) -> None:
    """Candidate configuration must not import a backend or run schema DDL in product startup."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PANTHEON_SOURCE_SEARCH_POSTURE", "dev")
    monkeypatch.setenv("SEARCH_INDEX_STORE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_EVIDENCE_BACKEND", "jsonl")
    monkeypatch.setenv("SEARCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEARCH_VECTOR_EMBEDDING_ENABLED", "false")
    for name in ("PANTHEON_SEARCH_POSTGRES_DSN", "SEARCH_POSTGRES_DSN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(dsn_variable, "postgresql://candidate.invalid/retrieval")
    monkeypatch.setenv("PANTHEON_SEARCH_BACKEND", "postgres_pgvector")
    for name, filename in (
        ("SEARCH_INDEX_STORE_PATH", "index.jsonl"),
        ("SEARCH_EVIDENCE_STORE_PATH", "evidence.jsonl"),
        ("SEARCH_MATERIALIZE_STORE_PATH", "materialize.jsonl"),
        ("SEARCH_PIPELINE_STORE_PATH", "pipeline.jsonl"),
    ):
        monkeypatch.setenv(name, str(tmp_path / filename))

    candidate = Mock(name="unaccepted_postgres_backend")
    candidate.return_value.check_health.return_value = {"status": "ok"}
    if not candidate_ready:
        candidate.return_value.setup_schema.side_effect = RuntimeError("candidate unavailable")
    candidate_module = ModuleType("services.search.pg_retrieval")
    candidate_module.PostgresRetrievalBackend = candidate
    monkeypatch.setitem(sys.modules, candidate_module.__name__, candidate_module)

    attempted_candidate_imports: list[str] = []
    original_import = builtins.__import__

    def observe_import(name, *args, **kwargs):
        if name == "pg_retrieval" or name.endswith(".pg_retrieval"):
            attempted_candidate_imports.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", observe_import)
    # Load startup afresh without changing the globals of other tests' main module.
    module_name = "services.search._activation_probe"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "services/search/main.py")
    assert spec is not None and spec.loader is not None
    product_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, product_module)
    spec.loader.exec_module(product_module)

    with TestClient(product_module.create_app(durable_index_only=True)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["capabilities"]["keyword"]["ranker_version"] == "keyword-v1"
        refresh = client.post("/api/search/index/refresh", json={"force_full": True})
        assert refresh.status_code == 200
        query = client.post("/api/search/query", json={
            "query": "momentum",
            "access_context": {"persona_id": "persona-test", "workspace_id": "workspace-test"},
        })
        assert query.status_code == 200
        assert query.json()["results"] == []

    assert attempted_candidate_imports == []
    candidate.assert_not_called()
    candidate.return_value.setup_schema.assert_not_called()
