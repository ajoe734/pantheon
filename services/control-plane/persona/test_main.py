"""
Service-path tests for control-plane persona main.py.

Task: AUTO-TEST-PERSONA-001
Owner: Claude
Reviewer: Codex

Covers stub behaviour (GRAPH=None / langgraph not installed in dev).
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


class TestPersonaMainHealth(unittest.TestCase):

    def setUp(self) -> None:
        self.client = TestClient(persona_main.app)

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "persona-agent")

    def test_health_includes_llm_backend_field(self) -> None:
        response = self.client.get("/health")

        self.assertIn("llm_backend", response.json())

    def test_health_reflects_llm_backend_module_var(self) -> None:
        with patch.object(persona_main, "LLM_BACKEND", "openai"):
            response = self.client.get("/health")

        self.assertEqual(response.json()["llm_backend"], "openai")


class TestPersonaMainInvoke(unittest.TestCase):

    def setUp(self) -> None:
        # Ensure stub mode (GRAPH=None) regardless of environment.
        persona_main.GRAPH = None
        self.client = TestClient(persona_main.app)

    def test_invoke_returns_200(self) -> None:
        response = self.client.post("/invoke", json=_invoke_payload())

        self.assertEqual(response.status_code, 200, response.text)

    def test_invoke_stub_response_text(self) -> None:
        response = self.client.post("/invoke", json=_invoke_payload())

        body = response.json()
        self.assertEqual(body["response"], "[system not ready — upstream schemas not locked]")

    def test_invoke_stub_intent_is_unknown(self) -> None:
        response = self.client.post("/invoke", json=_invoke_payload())

        self.assertEqual(response.json()["intent"], "unknown")

    def test_invoke_stub_skill_is_null(self) -> None:
        response = self.client.post("/invoke", json=_invoke_payload())

        self.assertIsNone(response.json()["skill"])

    def test_invoke_validates_missing_required_fields(self) -> None:
        response = self.client.post("/invoke", json={"session_id": "sess-001"})

        self.assertEqual(response.status_code, 422, response.text)

    def test_invoke_accepts_different_channels(self) -> None:
        for channel in ("web", "mobile", "api"):
            with self.subTest(channel=channel):
                response = self.client.post("/invoke", json=_invoke_payload(channel=channel))
                self.assertEqual(response.status_code, 200, response.text)

    def test_invoke_empty_message_is_accepted(self) -> None:
        response = self.client.post("/invoke", json=_invoke_payload(message=""))

        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
