#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ensure_worker_test_python as wtp


class FakeRun:
    """Stands in for subprocess.run: `-m venv` creates bin/python3, the probe can fail."""

    def __init__(self, probe_fails: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.probe_fails = probe_fails

    def __call__(self, argv, check=True, cwd=None):
        self.calls.append(list(argv))
        if argv[1:3] == ["-m", "venv"]:
            interpreter = Path(argv[3]) / "bin" / "python3"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("")
        if argv[1] == "-c" and self.probe_fails:
            raise subprocess.CalledProcessError(1, argv)


class EnsureWorkerTestPythonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.parent = base / "repo", base / "worker-test-python"
        self.root.mkdir()
        (self.root / "requirements.txt").write_text("flask\n")
        (self.root / "scripts" / "dev").mkdir(parents=True)
        (self.root / wtp.REQUIREMENTS[0]).write_text("-r ../../requirements.txt\npytest\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_builds_once_then_reuses(self) -> None:
        run = FakeRun()
        first = wtp.ensure(self.root, self.parent, python="py", run=run)
        second = wtp.ensure(self.root, self.parent, python="py", run=run)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(sum(1 for c in run.calls if c[1:3] == ["-m", "venv"]), 1)
        self.assertEqual(os.readlink(self.parent / "current"), wtp.requirements_hash(self.root))

    def test_interrupted_build_is_rebuilt(self) -> None:
        stale = self.parent / wtp.requirements_hash(self.root)
        (stale / "bin").mkdir(parents=True)  # no .ready marker
        result = wtp.ensure(self.root, self.parent, python="py", run=FakeRun())
        self.assertFalse(result["reused"])
        self.assertTrue((stale / wtp.READY).is_file())

    def test_failed_probe_publishes_nothing(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            wtp.ensure(self.root, self.parent, python="py", run=FakeRun(probe_fails=True))
        self.assertFalse((self.parent / "current").exists())
        self.assertFalse((self.parent / wtp.requirements_hash(self.root) / wtp.READY).exists())

    def test_new_requirements_switch_current_and_prune_old_builds(self) -> None:
        run = FakeRun()
        digests = []
        for n in range(wtp.KEEP + 2):
            (self.root / wtp.REQUIREMENTS[0]).write_text(f"-r ../../requirements.txt\npytest\n# {n}\n")
            wtp.ensure(self.root, self.parent, python="py", run=run)
            digests.append(wtp.requirements_hash(self.root))
            os.utime(self.parent / digests[-1] / wtp.READY, (n, n))
        self.assertEqual(os.readlink(self.parent / "current"), digests[-1])
        remaining = {p.name for p in self.parent.iterdir() if p.is_dir() and not p.is_symlink()}
        self.assertEqual(remaining, set(digests[-wtp.KEEP:]))


class SupervisorExportTests(unittest.TestCase):
    def test_export_only_existing_and_unset(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".orchestrator"))
        import supervisor

        with tempfile.TemporaryDirectory() as tmp:
            interpreter = Path(tmp) / "python3"
            config = {"worker_runtime": {"dependency_python": str(interpreter)}}
            saved = os.environ.pop("PANTHEON_DEPENDENCY_PYTHON", None)
            try:
                supervisor.export_worker_dependency_python(config)
                self.assertNotIn("PANTHEON_DEPENDENCY_PYTHON", os.environ)  # missing file
                interpreter.write_text("")
                supervisor.export_worker_dependency_python(config)
                self.assertEqual(os.environ["PANTHEON_DEPENDENCY_PYTHON"], str(interpreter))
                os.environ["PANTHEON_DEPENDENCY_PYTHON"] = "/operator/choice"
                supervisor.export_worker_dependency_python(config)
                self.assertEqual(os.environ["PANTHEON_DEPENDENCY_PYTHON"], "/operator/choice")
            finally:
                os.environ.pop("PANTHEON_DEPENDENCY_PYTHON", None)
                if saved is not None:
                    os.environ["PANTHEON_DEPENDENCY_PYTHON"] = saved


if __name__ == "__main__":
    unittest.main()
