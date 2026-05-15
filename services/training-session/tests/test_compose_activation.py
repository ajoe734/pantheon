from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_training_session_service_and_bff_normal_path() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    training = services["training-session-svc"]
    assert training["build"]["dockerfile"] == "services/training-session/Dockerfile"
    assert training["environment"]["PORT"] == "8099"
    assert training["environment"]["TRAINING_SESSION_DATA_DIR"] == "/data/training-session"
    assert "training-session-data:/data/training-session" in training["volumes"]
    assert training["ports"] == ["${TRAINING_SESSION_PORT:-18099}:8099"]
    assert "healthcheck" in training

    bff = services["operator-bff"]
    assert bff["environment"]["PANTHEON_TRAINING_SESSION_API_URL"] == "http://training-session-svc:8099"
    assert bff["depends_on"]["training-session-svc"]["condition"] == "service_healthy"

    smoke = services["smoke-stack"]
    assert smoke["environment"]["TRAINING_SESSION_URL"] == "http://training-session-svc:8099"
    assert smoke["depends_on"]["training-session-svc"]["condition"] == "service_healthy"
    assert "training-session-data" in compose["volumes"]
