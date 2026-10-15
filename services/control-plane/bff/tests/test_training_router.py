"""Ownership checks for the dedicated Training BFF router."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BFF_ROOT = Path(__file__).resolve().parents[1]

from services.control_plane.bff.training.router import create_training_router


def _page_slice(
    items: List[Dict[str, Any]],
    _page_token: Optional[str],
    _page_size: int,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return items, None


def test_training_router_owns_all_trainer_session_routes() -> None:
    router = create_training_router(
        get_read_store=lambda: object(),
        extract_identity=lambda _authorization: object(),
        require_read_role=lambda _identity: None,
        bff_error=lambda *_args, **_kwargs: RuntimeError("unexpected test error"),
        utc_now=lambda: "2026-08-30T00:00:00Z",
        page_slice=_page_slice,
        dataset_surface_status=lambda *_args, **_kwargs: {"status": "available"},
    )

    expected = {
        ("POST", "/api/v1/trainer/sessions"),
        ("GET", "/api/v1/trainer/sessions"),
        ("GET", "/api/v1/trainer/sessions/{session_id}"),
        ("GET", "/api/v1/trainer/sessions/{session_id}/controls"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/patch"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/message"),
        ("GET", "/api/v1/trainer/sessions/{session_id}/preview"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/preview"),
        ("GET", "/api/v1/trainer/replay"),
        ("GET", "/api/v1/trainer/replay/{session_id}"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/commit"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/discard"),
        ("POST", "/api/v1/trainer/sessions/{session_id}/rapid-eval"),
        ("GET", "/api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}"),
    }
    actual = {
        (method, route.path)
        for route in router.routes
        for method in route.methods
        if method in {"GET", "POST"} and route.path.startswith("/api/v1/trainer/")
    }
    assert actual == expected


def test_main_composes_training_router_without_trainer_decorators() -> None:
    factory_source = (BFF_ROOT / "core" / "app_factory.py").read_text(encoding="utf-8")
    assert "create_training_router" in factory_source
    assert "create_training_router(" in factory_source

    main_source = (BFF_ROOT / "main.py").read_text(encoding="utf-8")
    assert not re.search(
        r'@app\.(?:get|post|put|patch|delete)\([^\n]*"/api/v1/trainer/',
        main_source,
    )


# --- Persona model authority forwarding -------------------------------------

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.control_plane.bff.models import ErrorCode  # noqa: E402
from services.control_plane.bff.ports import persona_training as persona_training_ports  # noqa: E402
from services.execution.model_authority_fixture import momentum_artifact, persona_with_model  # noqa: E402


class _PersonaStore:
    def __init__(self, persona: Dict[str, Any]) -> None:
        self.persona = persona
        self.calls: List[Any] = []

    def create_trainer_session(self, **kwargs):
        self.calls.append(("create_trainer_session", kwargs))
        return {
            "session_id": "trn-1", "persona_id": kwargs["persona_id"], "session_type": "trainer",
            "objective": kwargs["objective"], "status": "active", "started_at": kwargs["created_at"],
            "allowedActions": {}, "links": {},
        }

    def get_persona(self, persona_id):
        return self.persona if persona_id == self.persona["persona_id"] else None


def _client(store: Any) -> TestClient:
    def bff_error(status, code, title, detail, **extra):
        return HTTPException(status_code=status, detail={"code": str(code), "detail": detail, **extra})

    app = FastAPI()
    app.include_router(
        create_training_router(
            get_read_store=lambda: store,
            extract_identity=lambda _auth: SimpleNamespace(operator_id="op-1"),
            require_read_role=lambda _identity: None,
            bff_error=bff_error,
            utc_now=lambda: "2026-10-15T00:00:00Z",
            page_slice=_page_slice,
            dataset_surface_status=lambda *_a, **_k: {"status": "available"},
        )
    )
    return TestClient(app)


def _create(client: TestClient, persona_id: str, **extra):
    return client.post(
        "/api/v1/trainer/sessions",
        json={"persona_id": persona_id, "session_type": "trainer", "objective": "teach", **extra},
    )


def test_create_forwards_owner_persona_model_not_caller_values() -> None:
    persona = persona_with_model()
    store = _PersonaStore(persona)
    forged = {"schema": "canonical_model_authority.v1", "artifact_id": "attacker"}
    response = _create(_client(store), persona["persona_id"], model_authority=forged)
    assert response.status_code == 200, response.text
    sent = store.calls[0][1]["model_authority"]
    assert sent["artifact_id"] == momentum_artifact()["artifact_id"]
    assert sent["artifact_checksum"] and sent["strategy_artifact"]["parameters"]["lookback_bars"] == 2


def test_create_without_runtime_binding_stays_unbound() -> None:
    store = _PersonaStore({"persona_id": "persona-legacy", "metadata": {}})
    assert _create(_client(store), "persona-legacy").status_code == 200
    assert "model_authority" not in store.calls[0][1]


def test_create_fails_closed_on_unverifiable_persona_artifact() -> None:
    persona = persona_with_model()
    store_map = persona["metadata"]["provisioning_authoritative_readback"]["runtime_binding"]["metadata"]["object_store"]
    key = next(k for k in store_map if k.endswith("artifact.bin"))
    store_map[key] = store_map[key].replace('"order_quantity":1', '"order_quantity":5')
    store = _PersonaStore(persona)
    response = _create(_client(store), persona["persona_id"])
    assert response.status_code == 409
    assert store.calls == []


def test_http_store_forwards_model_authority_in_create_payload() -> None:
    captured: Dict[str, Any] = {}

    def fake_request(url, *, method, payload, **_kwargs):
        captured.update(url=url, method=method, payload=payload)
        return {"session_id": "trn-1", "persona_id": "p", "objective": "o", "status": "active"}

    store = persona_training_ports.TrainingSessionHttpStore(base_url="http://training.test")
    with mock.patch.object(persona_training_ports, "http_request_json", fake_request):
        store.create_trainer_session(persona_id="p", objective="o", model_authority={"schema": "x"})
        assert captured["payload"]["model_authority"] == {"schema": "x"}
        store.create_trainer_session(persona_id="p", objective="o")
        assert "model_authority" not in captured["payload"]
