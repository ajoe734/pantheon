"""Regression tests for Agora interaction worker CLI launcher and container healthcheck."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = REPO_ROOT / "scripts" / "run_agora_interaction_worker.py"


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


if __name__ == "__main__":
    unittest.main()
