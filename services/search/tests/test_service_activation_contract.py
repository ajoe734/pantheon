from __future__ import annotations

from pathlib import Path

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
