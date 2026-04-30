from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from flask import Flask
import yaml

from services.foundation.health import (
    health_payload,
    readiness_status_code,
    register_flask_health_routes,
    register_fastapi_health_routes,
)

ROOT = Path(__file__).resolve().parents[3]


def test_health_payload_degrades_on_dependency_failure() -> None:
    payload = health_payload(
        "example",
        dependencies={"postgres": {"status": "error", "detail": "connection refused"}},
        metrics={"requests_total": 3},
    )

    assert payload["status"] == "degraded"
    assert payload["ready"] is False
    assert readiness_status_code(payload) == 503
    assert payload["dependencies"]["postgres"]["detail"] == "connection refused"
    assert payload["metrics"]["service_up"] == 1
    assert payload["metrics"]["requests_total"] == 3


def test_fastapi_health_routes_expose_standard_contract() -> None:
    app = FastAPI()
    register_fastapi_health_routes(
        app,
        "example",
        dependencies={"store": {"status": "ok"}},
        metrics={"queue_depth": 0},
    )
    client = TestClient(app)

    for path in ("/healthz", "/readyz"):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        assert payload["service"] == "example"
        assert payload["status"] == "ok"
        assert payload["live"] is True
        assert payload["ready"] is True
        assert "timestamp" in payload
        assert payload["dependencies"]["store"]["status"] == "ok"

    livez = client.get("/livez").json()
    assert livez["service"] == "example"
    assert livez["status"] == "ok"
    assert livez["live"] is True
    assert livez["ready"] is True

    metrics = client.get("/metrics").json()
    assert metrics["metrics"]["service_up"] == 1
    assert metrics["metrics"]["queue_depth"] == 0


def test_fastapi_readyz_returns_503_when_dependency_is_unavailable() -> None:
    app = FastAPI()
    register_fastapi_health_routes(
        app,
        "example",
        dependencies={"postgres": {"status": "unavailable"}},
    )
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_flask_health_routes_expose_standard_contract() -> None:
    app = Flask(__name__)
    register_flask_health_routes(
        app,
        "example-flask",
        dependencies={"store": {"status": "ok"}},
        metrics={"queue_depth": 0},
    )
    client = app.test_client()

    for path in ("/healthz", "/readyz"):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["service"] == "example-flask"
        assert payload["status"] == "ok"
        assert payload["metrics"]["service_up"] == 1
        assert payload["dependencies"]["store"]["status"] == "ok"

    livez = client.get("/livez").get_json()
    assert livez["service"] == "example-flask"
    assert livez["status"] == "ok"
    assert livez["metrics"]["service_up"] == 1

    metrics = client.get("/metrics").get_json()
    assert metrics["metrics"]["service_up"] == 1
    assert metrics["metrics"]["queue_depth"] == 0


def test_compose_healthchecks_do_not_use_misspelled_readiness_paths() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    serialized = yaml.safe_dump(compose)
    services = compose["services"]

    assert "readyzz" not in serialized
    assert "http://127.0.0.1:8222/healthz" in serialized

    openclaw_gateway_healthcheck = " ".join(services["openclaw-gateway"]["healthcheck"]["test"])
    assert "http://127.0.0.1:18789/readyz" in openclaw_gateway_healthcheck
    assert "http://127.0.0.1:18789/healthz" not in openclaw_gateway_healthcheck

    openclaw_adapter_healthcheck = " ".join(services["openclaw-gateway-adapter"]["healthcheck"]["test"])
    assert "http://127.0.0.1:{port}/livez" in openclaw_adapter_healthcheck
    assert "/readyz" not in openclaw_adapter_healthcheck
    assert "/healthz" not in openclaw_adapter_healthcheck
