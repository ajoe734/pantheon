"""Regression coverage for the retained Persona interaction worker launcher."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_agora_interaction_worker.py"
COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS = 5
PERSONA_CLIENT = ROOT / "services/control-plane/bff/agora/interaction/persona_client.py"


class InteractionWorkerLauncherTests(unittest.TestCase):
    def _run(self, *args: str, cwd: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy() if env is None else env
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *args], cwd=cwd or str(ROOT), env=run_env,
            capture_output=True, text=True, timeout=20,
        )

    def _heartbeat_env(self, heartbeat: Path, max_age: str = "300") -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["AGORA_WORKER_HEARTBEAT_PATH"] = str(heartbeat)
        env["AGORA_WORKER_HEARTBEAT_MAX_AGE_SECONDS"] = max_age
        return env

    def _timed_healthcheck(self, env: dict[str, str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        result = self._run("--healthcheck", cwd=cwd, env=env)
        self.assertLess(time.monotonic() - started, COMPOSE_HEALTHCHECK_TIMEOUT_SECONDS)
        return result

    def test_healthcheck_with_live_loop_heartbeat_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            heartbeat = Path(temporary, "heartbeat")
            heartbeat.touch()
            result = self._timed_healthcheck(self._heartbeat_env(heartbeat), cwd="/tmp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Healthcheck OK", result.stdout + result.stderr)

    def test_healthcheck_with_stalled_loop_heartbeat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            heartbeat = Path(temporary, "heartbeat")
            heartbeat.touch()
            stale = time.time() - 600
            os.utime(heartbeat, (stale, stale))
            result = self._timed_healthcheck(self._heartbeat_env(heartbeat))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Healthcheck OK", result.stdout + result.stderr)

    def test_healthcheck_without_heartbeat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._timed_healthcheck(self._heartbeat_env(Path(temporary, "missing")))
        self.assertNotEqual(result.returncode, 0)

    def test_run_loop_refreshes_heartbeat_while_idle(self) -> None:
        sys.path[:0] = [str(ROOT), str(ROOT / "services/control-plane/bff")]
        from agora.interaction.worker import AgoraInteractionWorker
        with tempfile.TemporaryDirectory() as temporary:
            heartbeat = Path(temporary, "heartbeat")
            worker = AgoraInteractionWorker(store=object(), worker_id="hb-test")
            worker.run_once = lambda **kwargs: 0
            worker.run_loop(poll_interval=0.01, max_ticks=2, heartbeat_path=heartbeat)
            self.assertTrue(heartbeat.exists())

    def test_startup_fails_closed_when_persona_client_cannot_construct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "sitecustomize.py").write_text(
                "import sys\n"
                f"sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'services/control-plane/bff')!r}]\n"
                "from agora.interaction import persona_client\n"
                "def fail():\n"
                "    raise RuntimeError('construction failed')\n"
                "persona_client.build_canonical_persona_client = fail\n"
            )
            heartbeat = Path(temporary, "heartbeat")
            env = self._heartbeat_env(heartbeat)
            env["PYTHONPATH"] = temporary
            env.update(
                AGORA_WORKSHOP_STORE_BACKEND="memory",
                AGORA_GOVERNANCE_STORE_BACKEND="memory",
            )
            result = self._run("--once", env=env)
            self.assertFalse(heartbeat.exists())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("construction failed", result.stderr)

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


if __name__ == "__main__":
    unittest.main()
