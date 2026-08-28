"""Regression tests for Agora interaction worker CLI launcher and container healthcheck."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = REPO_ROOT / "scripts" / "run_agora_interaction_worker.py"
PERSONA_CLIENT_PATH = (
    REPO_ROOT
    / "services"
    / "control-plane"
    / "bff"
    / "agora"
    / "interaction"
    / "persona_client.py"
)


class AgoraInteractionWorkerLauncherTests(unittest.TestCase):
    def test_healthcheck_subprocess_with_clean_pythonpath_succeeds(self) -> None:
        """Verify the container healthcheck command succeeds without ModuleNotFoundError."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
            cwd=str(REPO_ROOT),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"Healthcheck exited with code {proc.returncode}.\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertIn("Healthcheck OK", proc.stdout + proc.stderr)
        self.assertNotIn("ModuleNotFoundError", proc.stderr)
        self.assertNotIn("No module named services", proc.stderr)

    def test_healthcheck_from_foreign_working_directory_succeeds(self) -> None:
        """Verify the launcher resolves repo imports even when executed from a foreign directory."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
            cwd="/tmp",
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"Foreign cwd healthcheck exited with code {proc.returncode}.\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertIn("Healthcheck OK", proc.stdout + proc.stderr)
        self.assertNotIn("ModuleNotFoundError", proc.stderr)

    def test_help_argument_subprocess_succeeds(self) -> None:
        """Verify the launcher argument parser outputs help without errors."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--help"],
            cwd=str(REPO_ROOT),
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertIn("Agora Persona interaction background worker", proc.stdout)
        self.assertIn("--healthcheck", proc.stdout)

    def test_persona_discovery_uses_typed_canonical_client(self) -> None:
        """The nonexistent `store.FastBffReadStore` import and its catch-all
        empty fallback must be gone; the launcher must depend on the typed
        canonical Persona client instead."""
        source = LAUNCHER_PATH.read_text()
        self.assertNotIn("FastBffReadStore", source)
        self.assertNotIn("MinimalReadStore", source)
        self.assertIn("build_canonical_persona_client", source)

    def test_persona_client_module_has_no_empty_fallback(self) -> None:
        """`persona_client.py` must construct the canonical Persona client
        directly and must not catch construction errors to substitute an
        empty implementation."""
        self.assertTrue(PERSONA_CLIENT_PATH.exists())
        source = PERSONA_CLIENT_PATH.read_text()
        self.assertNotIn("from store import", source)
        self.assertNotIn("except Exception", source)

    def test_persona_client_construction_failure_is_not_swallowed(self) -> None:
        """A required-dependency construction failure must propagate to the
        caller, proving there is no empty-fallback branch left to catch it."""
        for path in (
            str(REPO_ROOT),
            str(REPO_ROOT / "services" / "control-plane" / "bff"),
        ):
            if path not in sys.path:
                sys.path.insert(0, path)

        from agora.interaction import persona_client

        original_read_surface_store = persona_client.ReadSurfaceStore

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated required Persona dependency construction failure")

        persona_client.ReadSurfaceStore = _boom
        try:
            with self.assertRaises(RuntimeError):
                persona_client.build_canonical_persona_client()
        finally:
            persona_client.ReadSurfaceStore = original_read_surface_store

    def test_healthcheck_subprocess_fails_when_persona_client_cannot_construct(self) -> None:
        """A container healthcheck must fail, not report false health, when
        the required Persona discovery client cannot be constructed."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        with tempfile.NamedTemporaryFile() as blocker_file:
            # BFF_DATA_DIR points at an existing regular file, so the
            # required os.makedirs(data_dir) construction step raises
            # instead of silently succeeding.
            clean_env["BFF_DATA_DIR"] = blocker_file.name

            proc = subprocess.run(
                [sys.executable, str(LAUNCHER_PATH), "--healthcheck"],
                cwd=str(REPO_ROOT),
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=15,
            )

        self.assertNotEqual(
            proc.returncode,
            0,
            f"Healthcheck reported success despite an unconstructable Persona client.\n"
            f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        self.assertNotIn("Healthcheck OK", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
