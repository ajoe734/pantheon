#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diff_budget

CONFIG = {
    "branch_workflow": {
        "diff_budget": {
            "evidence_max_added_lines": 400,
        }
    }
}


def f(path: str, added: int, deleted: int) -> dict:
    return {"filename": path, "additions": added, "deletions": deleted}


class ClassifyTest(unittest.TestCase):
    def test_classes(self) -> None:
        cases = {
            "services/control-plane/bff/main.py": diff_budget.PRODUCTION,
            ".orchestrator/supervisor.py": diff_budget.PRODUCTION,
            "services/control-plane/bff/tests/test_x.py": diff_budget.TEST,
            "scripts/git/test_diff_budget.py": diff_budget.TEST,
            "services/x/conftest.py": diff_budget.TEST,
            "web/src/a.spec.ts": diff_budget.TEST,
            "docs/deployment/evidence/T-1/evidence.json": diff_budget.DOCS,
            "services/x/README.md": diff_budget.DOCS,
            "ai-task-archive/tasks/T-1.json": diff_budget.DOCS,
        }
        for path, expected in cases.items():
            self.assertEqual(diff_budget.classify(path), expected, path)


class EvidenceCapTest(unittest.TestCase):
    def test_unclassified_task_only_gets_evidence_cap(self) -> None:
        summary_ok = diff_budget.summarize([f("svc/a.py", 5000, 0)])
        self.assertEqual(diff_budget.violations(summary_ok, CONFIG, label="T-1"), [])

        summary_bad = diff_budget.summarize([f("docs/deployment/evidence/T-1/evidence.json", 401, 0)])
        problems = diff_budget.violations(summary_bad, CONFIG, label="T-1")
        self.assertTrue(any("cap 400" in p for p in problems))

    def test_missing_line_counts_fail_closed(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            diff_budget.summarize([{"filename": "svc/a.py"}])
        self.assertIn("line counts unavailable", str(ctx.exception))


class CliTest(unittest.TestCase):
    def test_cli_reports_and_enforces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            (root / "svc").mkdir()
            (root / "svc" / "a.py").write_text("x\n" * 10)
            git("add", ".")
            git("commit", "-qm", "base")
            (root / "svc" / "a.py").write_text("x\n" * 2)
            (root / "svc" / "tests").mkdir()
            (root / "svc" / "tests" / "test_a.py").write_text("y\n" * 4)
            git("add", ".")
            git("commit", "-qm", "head")
            config = root / "config.json"
            config.write_text(json.dumps(CONFIG))
            summary = root / "summary.md"
            argv = ["--base", "HEAD~1", "--head", "HEAD", "--config", str(config), "--summary", str(summary)]
            cwd = Path.cwd()
            try:
                os.chdir(root)
                self.assertEqual(diff_budget.main(argv), 0)
                self.assertIn("| production | +0 | -8 | -8 |", summary.read_text())
                evidence_dir = root / "docs" / "deployment" / "evidence" / "T-1"
                evidence_dir.mkdir(parents=True)
                (evidence_dir / "evidence.json").write_text("z\n" * 401)
                git("add", ".")
                git("commit", "-qm", "add evidence over cap")
                self.assertEqual(
                    diff_budget.main(["--base", "HEAD~1", "--head", "HEAD", "--config", str(config)]),
                    1,
                )
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
