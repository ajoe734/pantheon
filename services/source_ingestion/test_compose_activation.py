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
    assert source_ingest_env["SOURCE_INGEST_MAX_RECORDS"] == "${SOURCE_INGEST_MAX_RECORDS:-100}"
    assert "source-ingest-data:/data/source-ingest" in source_ingest["volumes"]
    assert "${SOURCE_INGEST_PORT:-18097}:8097" in source_ingest["ports"]
    healthcheck = " ".join(source_ingest["healthcheck"]["test"])
    assert "os.environ.get('PORT','8097')" in healthcheck
    assert "/health" in healthcheck

    smoke_env = _env_map(services["smoke-stack"])
    assert smoke_env["SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert "source-ingest" in services["smoke-stack"]["depends_on"]
    assert "source-ingest-data" in compose["volumes"]
