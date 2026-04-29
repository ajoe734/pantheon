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


def test_root_compose_wires_consultation_service_boundary() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    consultation = services["consultation-svc"]
    consultation_env = _env_map(consultation)
    assert consultation["build"]["dockerfile"] == "services/consultation/Dockerfile"
    assert consultation_env["PORT"] == "8096"
    assert consultation_env["CONSULTATION_DATA_DIR"] == "/data/consultation"
    assert "consultation-data:/data/consultation" in consultation["volumes"]
    assert "${CONSULTATION_PORT:-18096}:8096" in consultation["ports"]
    healthcheck = " ".join(consultation["healthcheck"]["test"])
    assert "os.environ.get('PORT','8096')" in healthcheck
    assert "/readyz" in healthcheck

    bff_env = _env_map(services["operator-bff"])
    runtime_env = _env_map(services["runtime-manager"])
    expected_url = "http://consultation-svc:8096"
    assert bff_env["PANTHEON_CONSULTATION_API_URL"] == expected_url
    assert runtime_env["PANTHEON_CONSULTATION_API_URL"] == expected_url
    assert "PANTHEON_RUNTIME_CONSULTATION_DATA_DIR" not in runtime_env
    assert "consultation-svc" in services["operator-bff"]["depends_on"]
    assert "consultation-svc" in services["runtime-manager"]["depends_on"]

    smoke_env = _env_map(services["smoke-stack"])
    assert smoke_env["CONSULTATION_URL"] == expected_url
    assert "consultation-svc" in services["smoke-stack"]["depends_on"]
    assert "consultation-data" in compose["volumes"]
