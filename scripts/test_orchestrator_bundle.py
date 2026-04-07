#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SCRIPT = ROOT / "scripts" / "orchestrator_bundle.py"


class OrchestratorBundleTests(unittest.TestCase):
    def test_bootstrap_creates_portable_repo_scaffold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bundle-bootstrap-") as temp_dir:
            target = Path(temp_dir) / "demo-repo"
            subprocess.run(
                [
                    "python3",
                    str(BUNDLE_SCRIPT),
                    "bootstrap",
                    "--target-repo",
                    str(target),
                    "--project-name",
                    "Demo Project",
                    "--objective",
                    "Stand up Demo Project with supervisor, auto workers, and dashboard.",
                ],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue((target / ".orchestrator" / "config.json").exists())
            self.assertTrue((target / "scripts" / "run-supervisor.sh").exists())
            self.assertTrue((target / "scripts" / "setup-llm-cli.sh").exists())
            self.assertTrue((target / "docs-site" / "index.html").exists())
            self.assertTrue((target / "AI_COLLABORATION_GUIDE.md").exists())
            self.assertTrue((target / "ORCHESTRATOR_QUICKSTART.md").exists())

            state = json.loads((target / "ai-status.json").read_text(encoding="utf-8"))
            self.assertEqual(state["project"], "demo-project")
            self.assertEqual(state["tasks"], [])
            self.assertIn("Demo Project", state["objective"])

            current_work = (target / "current-work.md").read_text(encoding="utf-8")
            self.assertIn("Current Work", current_work)
            self.assertIn("Demo Project", current_work)

    def test_export_creates_tarball(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bundle-export-") as temp_dir:
            archive_path = Path(temp_dir) / "orchestrator-bundle.tar.gz"
            subprocess.run(
                [
                    "python3",
                    str(BUNDLE_SCRIPT),
                    "export",
                    "--output",
                    str(archive_path),
                    "--project-name",
                    "Portable Demo",
                    "--objective",
                    "Portable demo objective.",
                ],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(archive_path.exists())
            self.assertGreater(archive_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
