from __future__ import annotations

import importlib.util
import re
import sys
import sysconfig
from pathlib import Path


def _preload_stdlib_secrets() -> None:
    # Pytest may add services/foundation to sys.path; Starlette needs stdlib secrets.
    secrets_path = Path(sysconfig.get_paths()["stdlib"]) / "secrets.py"
    spec = importlib.util.spec_from_file_location("secrets", secrets_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["secrets"] = module


_preload_stdlib_secrets()

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
INFRASTRUCTURE_SERVICES = {"postgres", "minio", "nats", "signal-store"}


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_override)


def _load_compose(filename: str) -> dict:
    return yaml.load((ROOT / filename).read_text(encoding="utf-8"), Loader=_ComposeLoader)


def _healthcheck_command(service: dict) -> str:
    test = service["healthcheck"]["test"]
    return " ".join(test) if isinstance(test, list) else str(test)


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
    compose = _load_compose("docker-compose.yml")
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


def test_control_and_exec_app_healthchecks_use_readiness_contract() -> None:
    legacy_probe = re.compile(r"/(?:__health__|health)(?:['\"\s)]|$)")

    for filename in ("docker-compose.control.yml", "docker-compose.exec.yml"):
        compose = _load_compose(filename)
        for service_name, service in compose["services"].items():
            if service_name in INFRASTRUCTURE_SERVICES or "healthcheck" not in service:
                continue
            command = _healthcheck_command(service)

            assert "/readyz" in command, f"{filename}:{service_name} must use /readyz"
            assert not legacy_probe.search(command), (
                f"{filename}:{service_name} healthcheck still uses a legacy probe: {command}"
            )


def _exploding(*_a, **_k):
    raise AssertionError("liveness must not call service callables")


def test_fastapi_livez_does_not_call_service_callables() -> None:
    app = FastAPI()
    register_fastapi_health_routes(app, "ex", dependencies=_exploding, metrics=_exploding, details=_exploding)
    body = TestClient(app).get("/livez")
    assert body.status_code == 200
    payload = body.json()
    assert payload["status"] == "ok" and payload["live"] is True and payload["ready"] is True
    assert payload["metrics"] == {"service_up": 1}
    assert payload["dependencies"] == {} and payload["details"] == {}


def test_flask_livez_does_not_call_service_callables() -> None:
    app = Flask(__name__)
    register_flask_health_routes(app, "ex", dependencies=_exploding, metrics=_exploding, details=_exploding)
    response = app.test_client().get("/livez")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok" and payload["metrics"] == {"service_up": 1}


def test_fastapi_blocking_callable_does_not_stall_event_loop() -> None:
    import asyncio
    import threading
    import time

    import httpx

    lock = threading.Lock()
    lock.acquire()
    entered = threading.Event()

    def slow_metrics():
        entered.set()
        with lock:
            return {"q": 1}

    app = FastAPI()
    register_fastapi_health_routes(app, "ex", metrics=slow_metrics)

    @app.get("/other")
    async def other():
        return {"ok": True}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            for path in ("/metrics", "/healthz", "/readyz"):
                entered.clear()
                blocked = asyncio.create_task(c.get(path))
                while not entered.is_set():
                    await asyncio.sleep(0.01)
                t0 = time.monotonic()
                other_r = await asyncio.wait_for(c.get("/other"), timeout=2)
                live_r = await asyncio.wait_for(c.get("/livez"), timeout=2)
                assert other_r.status_code == 200 and live_r.status_code == 200
                assert time.monotonic() - t0 < 1.0
                assert not blocked.done()
                lock.release()
                assert (await asyncio.wait_for(blocked, timeout=5)).status_code == 200
                lock.acquire()

    try:
        asyncio.run(run())
    finally:
        if lock.locked():
            lock.release()
