from __future__ import annotations

from pathlib import Path

import yaml


def _env_map(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return env
    result = {}
    for item in env:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def test_root_compose_wires_source_ingest_service_boundary() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    source_ingest = services["source-ingest"]
    source_ingest_env = _env_map(source_ingest)
    assert source_ingest["build"]["dockerfile"] == "services/source_ingestion/Dockerfile"
    assert source_ingest_env["PORT"] == "8097"
    assert source_ingest_env["SOURCE_INGEST_DATA_DIR"] == "/data/source-ingest"
    assert source_ingest_env["SOURCE_INGEST_CONNECTOR_STORE_PATH"] == "/data/source-ingest/connector_config.jsonl"
    assert source_ingest_env["SOURCE_INGEST_EVIDENCE_STORE_PATH"] == "/data/source-ingest/source_evidence.jsonl"
    assert source_ingest_env["SOURCE_INGEST_MAX_RECORDS"] == "${SOURCE_INGEST_MAX_RECORDS:-100}"
    assert source_ingest_env["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] == "${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-2}"
    assert source_ingest_env["SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"] == "${SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS:-2}"
    assert source_ingest_env["SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"] == "${SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS:-60}"
    assert source_ingest_env["SEARCH_INGEST_NOTIFY_URL"] == "${SEARCH_INGEST_NOTIFY_URL:-http://search-svc:8098}"
    assert source_ingest_env["PANTHEON_SOURCE_SEARCH_POSTURE"] == "${PANTHEON_SOURCE_SEARCH_POSTURE:-dev}"
    assert source_ingest_env["PANTHEON_S3_ENDPOINT"] == "${PANTHEON_S3_ENDPOINT:-http://minio:9000}"
    assert source_ingest_env["PANTHEON_ARTIFACT_BUCKET"] == "${PANTHEON_ARTIFACT_BUCKET:-pantheon-artifacts}"
    assert "source-ingest-data:/data/source-ingest" in source_ingest["volumes"]
    assert "${SOURCE_INGEST_PORT:-18097}:8097" in source_ingest["ports"]
    healthcheck = " ".join(source_ingest["healthcheck"]["test"])
    assert "os.environ.get('PORT','8097')" in healthcheck
    assert "/readyz" in healthcheck

    source_ingest_scheduler = services["source-ingest-scheduler"]
    scheduler_env = _env_map(source_ingest_scheduler)
    assert source_ingest_scheduler["profiles"] == ["source-ingest-scheduler"]
    assert source_ingest_scheduler["command"] == ["python", "-m", "services.source_ingestion.scheduler_worker"]
    assert scheduler_env["SOURCE_INGEST_API_URL"] == "http://source-ingest:8097"
    assert scheduler_env["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] == "${SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY:-2}"
    assert "source-ingest" in source_ingest_scheduler["depends_on"]

    smoke_env = _env_map(services["smoke-stack"])
    assert smoke_env["SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert "source-ingest" in services["smoke-stack"]["depends_on"]
    assert "source-ingest-data" in compose["volumes"]

    source_search_smoke = services["source-search-bounded-smoke"]
    source_search_smoke_env = _env_map(source_search_smoke)
    assert source_search_smoke["profiles"] == ["source-search-bounded"]
    assert "run --rm --use-aliases source-search-bounded-smoke" in compose_path.read_text(encoding="utf-8")
    assert source_search_smoke["command"] == ["python", "scripts/smoke_source_search_bounded.py"]
    assert source_search_smoke_env["SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert source_search_smoke_env["SEARCH_URL"] == "http://search-svc:8098"
    assert source_search_smoke_env["SOURCE_INGEST_EXTERNAL_FEED_HOST"] == "source-search-bounded-smoke"
    assert source_search_smoke["depends_on"]["source-ingest"]["condition"] == "service_healthy"
    assert source_search_smoke["depends_on"]["search-svc"]["condition"] == "service_healthy"

    bff_env = _env_map(services["operator-bff"])
    assert bff_env["PANTHEON_SOURCE_INGEST_API_URL"] == "http://source-ingest:8097"
    assert "source-ingest" in services["operator-bff"]["depends_on"]

    smoke = (compose_path.parent / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")
    assert 'SOURCE_INGEST_EXTERNAL_FEED_HOST = os.getenv("SOURCE_INGEST_EXTERNAL_FEED_HOST", "smoke-stack")' in smoke
    assert 'source_search_token = f"source-smoke-{suffix}"' in smoke
    assert '"mode": "external_feed"' in smoke
    assert '"allowed_url_prefixes": [source_feed_url.rsplit("/", 1)[0] + "/"]' in smoke
    assert '"max_records": 10' in smoke
    assert '"keywords": ["compose", "smoke", "momentum", "volatility", source_search_token]' in smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/connectors"' in smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/jobs"' in smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled"' in smoke
    assert 'f"{SEARCH_URL}/api/search/query"' in smoke

    bounded_smoke = (compose_path.parent / "scripts/smoke_source_search_bounded.py").read_text(encoding="utf-8")
    assert '"mode": "static_records"' in bounded_smoke
    assert '"mode": "external_feed"' in bounded_smoke
    assert '"allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"]' in bounded_smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/dlq/replay"' in bounded_smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/run-scheduled"' in bounded_smoke
    assert 'f"{SOURCE_INGEST_URL}/api/source-ingest/audit"' in bounded_smoke
    assert 'f"{SEARCH_URL}/api/search/index/refresh"' in bounded_smoke
    assert 'f"{SEARCH_URL}/api/search/index/source-completions/{feed_run_id}"' in bounded_smoke

    prod_env = (compose_path.parent / "env/prod-control.env.example").read_text(encoding="utf-8")
    assert "PANTHEON_SOURCE_SEARCH_POSTURE=production" in prod_env
    assert "SOURCE_INGEST_EVIDENCE_BACKEND=postgres" in prod_env
    assert "PANTHEON_S3_ENDPOINT=http://minio:9000" in prod_env

    prod_smoke = (compose_path.parent / "scripts/smoke_source_search_prod_posture.py").read_text(encoding="utf-8")
    assert "posture_alert_count" in prod_smoke
    assert '"SOURCE_INGEST_EVIDENCE_BACKEND": "postgres"' in prod_smoke
