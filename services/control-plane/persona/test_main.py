"""
Service-path tests for control-plane persona main.py.

Run:
    python3 -m pytest services/control-plane/persona/test_main.py -v
or:
    python3 -m unittest discover -s services/control-plane/persona -p 'test_main.py'
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


_MODULE_DIR = Path(__file__).resolve().parent
persona_main = _load_module("persona_main_test_module", _MODULE_DIR / "main.py")


def _invoke_payload(**overrides) -> dict:
    base = {
        "session_id": "sess-001",
        "user_id": "user-001",
        "channel": "web",
        "message": "What is my portfolio allocation?",
    }
    base.update(overrides)
    return base


def _runtime_dict(runtime) -> dict:
    if hasattr(runtime, "model_dump"):
        return runtime.model_dump()
    return runtime.dict()


class TestPersonaMainHealth(unittest.TestCase):

    def setUp(self) -> None:
        self.client = TestClient(persona_main.app)

    def test_health_returns_runtime_metadata(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "persona-agent",
                "llm_backend": persona_main.LLM_BACKEND,
                "runtime_backend": "openclaw-gateway",
                "persona_id": persona_main.DEFAULT_PERSONA_ID,
                "agent_id": persona_main.OPENCLAW_AGENT_ID,
            },
        )


class TestPersonaClassify(unittest.TestCase):

    def setUp(self) -> None:
        persona_main.PERSONA_REGISTRY = persona_main.PersonaRegistry()
        persona_main.SESSION_STORE = persona_main.PersonaSessionStore()
        persona_main.CAPABILITY_SNAPSHOTS = {}
        self.client = TestClient(persona_main.app)

    def test_classify_returns_persona_owned_surrogate_intent(self) -> None:
        runtime = persona_main.RuntimeStatus(mode="gateway_ready_surrogate", gateway_ready=True)
        with patch.object(persona_main, "_runtime_probe", return_value=runtime):
            response = self.client.post(
                "/classify",
                json={
                    "user_id": "user-001",
                    "channel": "web",
                    "message": "please run a qlib research backtest",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "intent": "research",
                "skill": "research-summary",
                "classifier": "persona.local_surrogate",
                "persona_id": persona_main.DEFAULT_PERSONA_ID,
                "runtime": _runtime_dict(runtime),
            },
        )


class TestPersonaMainInvoke(unittest.TestCase):

    def setUp(self) -> None:
        persona_main.PERSONA_REGISTRY = persona_main.PersonaRegistry()
        persona_main.SESSION_STORE = persona_main.PersonaSessionStore()
        persona_main.CAPABILITY_SNAPSHOTS = {}
        self.client = TestClient(persona_main.app)

    def test_invoke_returns_degraded_response_when_runtime_is_unavailable(self) -> None:
        runtime = persona_main.RuntimeStatus(
            mode="degraded_surrogate",
            gateway_ready=False,
            reason="OpenClaw gateway unavailable",
            error_code="UPSTREAM_UNAVAILABLE",
            owner_plane="pantheon.adapter",
        )
        with patch.object(
            persona_main,
            "_invoke_openclaw",
            return_value=(
                "[persona runtime degraded] intent=status; no governed tool execution was attempted. OpenClaw gateway unavailable",
                runtime,
            ),
        ):
            response = self.client.post("/invoke", json=_invoke_payload(message="show me status"))

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["intent"], "status")
        self.assertEqual(body["skill"], "status-summary")
        self.assertEqual(body["session_status"], "degraded")
        self.assertEqual(body["runtime"], _runtime_dict(runtime))
        self.assertIn("[persona runtime degraded]", body["response"])

    def test_invoke_returns_openclaw_response_when_runtime_succeeds(self) -> None:
        runtime = persona_main.RuntimeStatus(mode="openclaw", gateway_ready=True)
        with patch.object(
            persona_main,
            "_invoke_openclaw",
            return_value=("Research path accepted", runtime),
        ):
            response = self.client.post(
                "/invoke",
                json=_invoke_payload(
                    message="please run a qlib research backtest",
                    intent_hint="research",
                ),
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["response"], "Research path accepted")
        self.assertEqual(body["intent"], "research")
        self.assertEqual(body["skill"], "research-summary")
        self.assertEqual(body["session_status"], "active")
        self.assertEqual(body["runtime"], _runtime_dict(runtime))
        stored = persona_main.SESSION_STORE.require("sess-001")
        self.assertEqual(stored.status, "active")

    def test_invoke_validates_missing_required_fields(self) -> None:
        response = self.client.post("/invoke", json={"session_id": "sess-001"})

        self.assertEqual(response.status_code, 422, response.text)

    def test_invoke_accepts_different_channels(self) -> None:
        runtime = persona_main.RuntimeStatus(mode="openclaw", gateway_ready=True)
        with patch.object(
            persona_main,
            "_invoke_openclaw",
            return_value=("ok", runtime),
        ):
            for channel in ("web", "mobile", "api"):
                with self.subTest(channel=channel):
                    response = self.client.post("/invoke", json=_invoke_payload(channel=channel))
                    self.assertEqual(response.status_code, 200, response.text)

    def test_invoke_reactivates_existing_degraded_session_after_success(self) -> None:
        degraded_runtime = persona_main.RuntimeStatus(mode="degraded_surrogate", gateway_ready=False)
        active_runtime = persona_main.RuntimeStatus(mode="openclaw", gateway_ready=True)

        with patch.object(
            persona_main,
            "_invoke_openclaw",
            return_value=("[persona runtime degraded] intent=status; no governed tool execution was attempted.", degraded_runtime),
        ):
            first = self.client.post("/invoke", json=_invoke_payload(message="show me status"))
        self.assertEqual(first.json()["session_status"], "degraded")

        with patch.object(
            persona_main,
            "_invoke_openclaw",
            return_value=("runtime recovered", active_runtime),
        ):
            second = self.client.post("/invoke", json=_invoke_payload(message="show me status"))

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["session_status"], "active")
        stored = persona_main.SESSION_STORE.require("sess-001")
        self.assertEqual(stored.status, "active")


def test_mounted_session_reads_require_verified_tenant_and_existing_persona_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.runtime_auth_inbound import encode_jwt_hs256

    monkeypatch.setenv("PERSONA_STORE_BACKEND", "json")
    monkeypatch.setenv("PERSONA_STORE_PATH", str(tmp_path / "persona.json"))
    monkeypatch.setenv("PERSONA_CAPABILITY_STORE_BACKEND", "json")
    monkeypatch.setenv("PERSONA_CAPABILITY_STORE_PATH", str(tmp_path / "caps.json"))
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", "session-read-secret")
    service = _load_module("persona_main_session_read", _MODULE_DIR / "main.py")
    now = int(__import__("time").time())
    token = encode_jwt_hs256(
        {"sub": "reader", "roles": ["persona.admin"], "tenant_id": "tenant-a", "exp": now + 600},
        secret="session-read-secret",
    )
    headers = {"Authorization": f"Bearer {token}"}
    from services.persona.write_owner import CreatePersonaRequest

    owner = service.PERSONA_OWNER_API.state.persona_owner
    owner.create(CreatePersonaRequest(
        actor_id="reader", persona_id=service.DEFAULT_PERSONA_ID, name="Owned", mandate="research",
        tenant_id="tenant-a",
    ))
    owner.try_metadata_cas(
        service.DEFAULT_PERSONA_ID,
        guard=lambda _: True,
        metadata_updates={"trade_reflections": [{"environment": "paper", "review_state": "pending"}]},
        actor_id="reader",
    )
    with patch.object(service, "_invoke_openclaw", return_value=("ok", service.RuntimeStatus(mode="openclaw", gateway_ready=True))):
        assert TestClient(service.app).post("/invoke", json=_invoke_payload()).status_code == 200
    client = TestClient(service.app)
    denied = client.get("/api/sessions/sess-001")
    assert denied.status_code == 401
    visible = client.get("/api/sessions/sess-001", headers=headers)
    assert visible.status_code == 200, visible.text
    assert visible.json()["session_id"] == "sess-001"
    assert client.get("/api/sessions", headers={**headers, "X-Tenant-Id": "tenant-b"}).status_code == 403
    assert client.get("/api/sessions/sess-001", headers={**headers, "X-Tenant-Id": "tenant-b"}).status_code == 403
    foreign_token = encode_jwt_hs256(
        {"sub": "foreign", "roles": ["persona.admin"], "tenant_id": "tenant-foreign", "exp": now + 600},
        secret="session-read-secret",
    )
    foreign_headers = {"Authorization": f"Bearer {foreign_token}"}
    assert client.get("/api/sessions", headers=foreign_headers).json() == []
    assert client.get("/api/sessions/sess-001", headers=foreign_headers).status_code == 404
    assert client.get(f"/api/personas/{service.DEFAULT_PERSONA_ID}").status_code == 401
    assert client.get(f"/api/personas/{service.DEFAULT_PERSONA_ID}", headers=foreign_headers).status_code == 403
    assert client.get(f"/api/personas/{service.DEFAULT_PERSONA_ID}", headers=headers).status_code == 200


def test_deployed_main_exposes_authenticated_owner_api_and_restart_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "persona-main-service-token"
    actor_id = "operator-bff"
    monkeypatch.setenv("PERSONA_STORE_BACKEND", "json")
    monkeypatch.setenv("PERSONA_STORE_PATH", str(tmp_path / "personas.json"))
    monkeypatch.setenv("PERSONA_CAPABILITY_STORE_BACKEND", "json")
    monkeypatch.setenv(
        "PERSONA_CAPABILITY_STORE_PATH",
        str(tmp_path / "capability_snapshots.json"),
    )
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", token)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", actor_id)
    headers = {"Authorization": f"Bearer {token}"}

    first = _load_module("persona_main_owner_first", _MODULE_DIR / "main.py")
    first_client = TestClient(first.app)
    created = first_client.post(
        "/api/personas",
        headers=headers,
        json={
            "actor_id": actor_id,
            "persona_id": "persona-main-owner-proof",
            "name": "Mounted Owner Proof",
            "mandate": "paper opinion only",
            "metadata": {"execution_authority": "none"},
        },
    )
    capability = first_client.put(
        "/api/personas/persona-main-owner-proof/capability-snapshots/cap-main-owner-proof",
        headers=headers,
        json={
            "actor_id": actor_id,
            "snapshot_id": "cap-main-owner-proof",
            "persona_id": "persona-main-owner-proof",
            "capabilities": ["persona_opinion"],
            "generated_at": "2026-08-30T00:00:00Z",
            "metadata": {"execution_authority": "none"},
        },
    )

    assert created.status_code == 201, created.text
    assert capability.status_code == 200, capability.text
    denied = first_client.post(
        "/api/personas",
        headers={"Authorization": "Bearer wrong-service-token"},
        json={
            "actor_id": actor_id,
            "persona_id": "must-not-exist",
            "name": "Denied",
            "mandate": "denied",
        },
    )
    assert denied.status_code == 401

    # Importing a fresh deployed service app rebuilds both owner stores.
    restarted = _load_module("persona_main_owner_restarted", _MODULE_DIR / "main.py")
    restarted_client = TestClient(restarted.app)
    persona = restarted_client.get("/api/personas/persona-main-owner-proof")
    snapshot = restarted_client.get(
        "/api/capability-snapshots/cap-main-owner-proof"
    )
    assert persona.status_code == 200, persona.text
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["capabilities"] == ["persona_opinion"]


if __name__ == "__main__":
    unittest.main()
