"""Regression coverage for the retained Persona interaction worker launcher."""
from __future__ import annotations

import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_agora_interaction_worker.py"
PERSONA_CLIENT = ROOT / "services/control-plane/bff/agora/interaction/persona_client.py"


class _MockPersonaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class InteractionWorkerLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._server = http.server.HTTPServer(("127.0.0.1", 0), _MockPersonaHandler)
        cls._port = cls._server.server_port
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        cls._persona_url = f"http://127.0.0.1:{cls._port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.shutdown()
        cls._server.server_close()

    def _run(self, *args: str, cwd: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy() if env is None else env
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *args], cwd=cwd or str(ROOT), env=run_env,
            capture_output=True, text=True, timeout=20,
        )

    def test_healthcheck_fails_when_persona_unconfigured(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PERSONA_URL", None)
        env.pop("PANTHEON_PERSONA_URL", None)
        env.pop("PANTHEON_PERSONA_API_URL", None)
        result = self._run("--healthcheck", env=env)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck failed", result.stdout + result.stderr)
        self.assertNotIn("Healthcheck OK", result.stdout + result.stderr)

    def test_healthcheck_fails_when_persona_unavailable(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PERSONA_URL"] = "http://127.0.0.1:59999"
        result = self._run("--healthcheck", env=env)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck failed", result.stdout + result.stderr)
        self.assertNotIn("Healthcheck OK", result.stdout + result.stderr)

    def test_healthcheck_subprocess_with_clean_pythonpath_succeeds(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PERSONA_URL"] = self._persona_url
        env["AGORA_RESEARCH_BACKEND_URL"] = "http://research-orchestrator-svc:8101"
        result = self._run("--healthcheck", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck OK", result.stdout + result.stderr)
        self.assertNotIn("No module named services", result.stderr)

    def test_healthcheck_from_foreign_working_directory_succeeds(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PERSONA_URL"] = self._persona_url
        result = self._run("--healthcheck", cwd="/tmp", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck OK", result.stdout + result.stderr)

    def test_healthcheck_cold_start_under_deadline(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PERSONA_URL"] = self._persona_url
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        start = time.monotonic()
        result = self._run("--healthcheck", env=env)
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck OK", result.stdout + result.stderr)
        self.assertLess(elapsed, 5.0, f"Cold-start healthcheck took {elapsed:.2f}s, exceeding 5s limit")

    def test_help_argument_subprocess_succeeds(self) -> None:
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Agora Persona interaction background worker", result.stdout)
        self.assertIn("--healthcheck", result.stdout)

    def test_persona_discovery_uses_typed_canonical_client(self) -> None:
        source = LAUNCHER.read_text()
        self.assertNotIn("FastBffReadStore", source)
        self.assertNotIn("MinimalReadStore", source)
        self.assertIn("build_canonical_persona_client", source)

    def test_persona_client_module_has_no_empty_fallback(self) -> None:
        source = PERSONA_CLIENT.read_text()
        self.assertNotIn("from store import", source)
        self.assertNotIn("except Exception", source)

    def test_persona_client_construction_failure_is_not_swallowed(self) -> None:
        sys.path[:0] = [str(ROOT), str(ROOT / "services/control-plane/bff")]
        from agora.interaction import persona_client
        original = persona_client.create_persona_registry_write_owner
        try:
            persona_client.create_persona_registry_write_owner = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("required client unavailable"))
            with self.assertRaisesRegex(RuntimeError, "required client unavailable"):
                persona_client.build_canonical_persona_client()
        finally:
            persona_client.create_persona_registry_write_owner = original

    def test_healthcheck_subprocess_fails_when_persona_client_cannot_construct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "sitecustomize.py").write_text(
                "import builtins\n"
                "original = builtins.__import__\n"
                "def custom_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
                "    module = original(name, globals, locals, fromlist, level)\n"
                "    if name == 'agora.interaction.persona_client':\n"
                "        module.build_canonical_persona_client = lambda: (_ for _ in ()).throw(RuntimeError('construction failed'))\n"
                "    return module\n"
                "builtins.__import__ = custom_import\n"
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = temporary
            env["PERSONA_URL"] = self._persona_url
            result = self._run("--healthcheck", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Healthcheck OK", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
