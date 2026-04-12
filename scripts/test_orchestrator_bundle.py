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
            self.assertTrue((target / "scripts" / "planning_state.py").exists())
            self.assertTrue((target / "scripts" / "planning-state.sh").exists())
            self.assertTrue((target / "docs-site" / "index.html").exists())
            self.assertTrue((target / "docs" / "02-architecture" / "consensus" / "phase1" / "README.md").exists())
            self.assertTrue((target / "AI_COLLABORATION_GUIDE.md").exists())
            self.assertTrue((target / "LLM_ONBOARDING.md").exists())
            self.assertTrue((target / "ORCHESTRATOR_QUICKSTART.md").exists())
            self.assertFalse((target / "docs-site" / "ai-status.json").exists())
            self.assertFalse((target / "docs-site" / "current-work.md").exists())
            self.assertFalse((target / "docs-site" / "planning-state.json").exists())
            self.assertFalse((target / ".orchestrator" / "state.json").exists())
            self.assertFalse((target / ".orchestrator" / "planning-state.json").exists())

            state = json.loads((target / "ai-status.json").read_text(encoding="utf-8"))
            self.assertEqual(state["project"], "demo-project")
            self.assertEqual(state["tasks"], [])
            self.assertIn("Demo Project", state["objective"])
            self.assertNotIn("TARGET_ARCHITECTURE.md", state.get("canonical_files", []))
            self.assertIn("docs/02-architecture/consensus/phase1/README.md", state.get("canonical_files", []))

            config = json.loads((target / ".orchestrator" / "config.json").read_text(encoding="utf-8"))
            self.assertFalse(config["github_bus"]["enabled"])

            current_work = (target / "current-work.md").read_text(encoding="utf-8")
            self.assertIn("Current Work", current_work)
            self.assertIn("Demo Project", current_work)
            self.assertNotIn("Pantheon Product Work", current_work)
            self.assertNotIn("Canonical map", current_work)

            prompt_result = subprocess.run(
                ["python3", str(target / "scripts" / "ai_status.py"), "prompt"],
                cwd=str(target),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Read AI_COLLABORATION_GUIDE.md, current-work.md, and ai-status.json first.", prompt_result.stdout)
            self.assertNotIn("TARGET_ARCHITECTURE.md", prompt_result.stdout)

            setup_script = (target / "scripts" / "setup-llm-cli.sh").read_text(encoding="utf-8")
            self.assertIn('scripts/ai_status.py" prompt', setup_script)

            onboarding = (target / "LLM_ONBOARDING.md").read_text(encoding="utf-8")
            self.assertIn("python3 scripts/ai_status.py prompt", onboarding)
            self.assertIn("ai-status.json", onboarding)
            self.assertIn("planning-state.sh", onboarding)

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

            listing = subprocess.run(
                ["tar", "-tzf", str(archive_path)],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn("./docs-site/ai-status.json", listing)
            self.assertNotIn("./docs-site/current-work.md", listing)
            self.assertNotIn("./.orchestrator/state.json", listing)
            self.assertNotIn("./docs-site/planning-state.json", listing)
            self.assertNotIn("./.orchestrator/planning-state.json", listing)


if __name__ == "__main__":
    unittest.main()
