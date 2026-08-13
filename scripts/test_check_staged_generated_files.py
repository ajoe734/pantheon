#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_staged_generated_files.py"


class CheckStagedGeneratedFilesTests(unittest.TestCase):
    def run_script(self, *paths: str, input_text: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *paths],
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_allows_source_and_canonical_files(self) -> None:
        result = self.run_script("scripts/ai_status.py", "ai-status.json", "docs-site/index.html")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_blocks_runtime_and_generated_files(self) -> None:
        result = self.run_script(
            "ai-activity-log.jsonl",
            "dashboard-bundle.json",
            ".orchestrator/state.json",
            "docs-site/orchestrator-state.json",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ai-activity-log.jsonl", result.stderr)
        self.assertIn("dashboard-bundle.json", result.stderr)
        self.assertIn(".orchestrator/state.json", result.stderr)

    def test_diff_mode_allows_pure_generated_file_deletions(self) -> None:
        result = self.run_script(
            "--diff-name-status",
            input_text=(
                "D\tdashboard-bundle.json\n"
                "D\tdocs-site/dashboard-bundle.json\n"
                "D\tai-activity-log.jsonl\n"
                "D\t.orchestrator/state.json\n"
            ),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_diff_mode_still_blocks_generated_additions_and_modifications(self) -> None:
        result = self.run_script(
            "--diff-name-status",
            input_text=(
                "A\tdashboard-bundle.json\n"
                "M\tdocs-site/dashboard-bundle.json\n"
            ),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("dashboard-bundle.json", result.stderr)
        self.assertIn("docs-site/dashboard-bundle.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
