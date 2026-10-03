"""Regression coverage for the retained Persona interaction worker launcher."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_agora_interaction_worker.py"
PERSONA_CLIENT = ROOT / "services/control-plane/bff/agora/interaction/persona_client.py"


class InteractionWorkerLauncherTests(unittest.TestCase):
    def _run(self, *args: str, cwd: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy() if env is None else env
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *args], cwd=cwd or str(ROOT), env=run_env,
            capture_output=True, text=True, timeout=20,
        )

    def test_healthcheck_subprocess_with_clean_pythonpath_succeeds(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["AGORA_RESEARCH_BACKEND_URL"] = "http://research-orchestrator-svc:8101"
        result = self._run("--healthcheck", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck OK", result.stdout + result.stderr)
        self.assertNotIn("No module named services", result.stderr)

    def test_healthcheck_from_foreign_working_directory_succeeds(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = self._run("--healthcheck", cwd="/tmp", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck OK", result.stdout + result.stderr)

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
        original = persona_client.create_read_surface_ports
        try:
            persona_client.create_read_surface_ports = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("required client unavailable"))
            with self.assertRaisesRegex(RuntimeError, "required client unavailable"):
                persona_client.build_canonical_persona_client()
        finally:
            persona_client.create_read_surface_ports = original

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
            result = self._run("--healthcheck", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Healthcheck OK", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
