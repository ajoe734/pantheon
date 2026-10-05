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
            "enabled": True,
            "change_classes": ["refactor", "simplify", "corrective"],
            "default_net_prod_line_budget": 0,
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


class HandoffGateTest(unittest.TestCase):
    def test_refactor_growth_is_rejected_with_numbers(self) -> None:
        task = {"id": "T-1", "change_class": "refactor"}
        files = [f("svc/a.py", 120, 20), f("svc/b.py", 5, 50), f("svc/tests/test_a.py", 300, 0)]
        with self.assertRaises(SystemExit) as ctx:
            diff_budget.enforce_handoff(task, CONFIG, files)
        message = str(ctx.exception)
        self.assertIn("net +55", message)
        self.assertIn("tests +300", message)
        self.assertIn("svc/a.py (+120/-20)", message)

    def test_refactor_that_deletes_code_passes(self) -> None:
        task = {"id": "T-1", "change_class": "simplify"}
        diff_budget.enforce_handoff(task, CONFIG, [f("svc/a.py", 10, 200), f("svc/tests/test_a.py", 90, 0)])

    def test_explicit_budget_is_respected(self) -> None:
        task = {"id": "T-1", "change_class": "corrective", "net_prod_line_budget": 60}
        diff_budget.enforce_handoff(task, CONFIG, [f("svc/a.py", 80, 30)])
        task["net_prod_line_budget"] = 40
        with self.assertRaises(SystemExit):
            diff_budget.enforce_handoff(task, CONFIG, [f("svc/a.py", 80, 30)])

    def test_unclassified_task_only_gets_evidence_cap(self) -> None:
        task = {"id": "T-1"}
        diff_budget.enforce_handoff(task, CONFIG, [f("svc/a.py", 5000, 0)])
        with self.assertRaises(SystemExit) as ctx:
            diff_budget.enforce_handoff(task, CONFIG, [f("docs/deployment/evidence/T-1/evidence.json", 401, 0)])
        self.assertIn("cap 400", str(ctx.exception))

    def test_disabled_config_is_a_no_op(self) -> None:
        diff_budget.enforce_handoff({"id": "T-1", "change_class": "refactor"}, {}, [f("svc/a.py", 999, 0)])

    def test_missing_line_counts_fail_closed(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            diff_budget.enforce_handoff({"id": "T-1"}, CONFIG, [{"filename": "svc/a.py"}])
        self.assertIn("line counts unavailable", str(ctx.exception))


class MetadataTest(unittest.TestCase):
    def test_validation(self) -> None:
        diff_budget.validate_task_metadata({})
        diff_budget.validate_task_metadata({"change_class": "refactor", "net_prod_line_budget": -500})
        for bad in (
            {"change_class": "feature"},
            {"net_prod_line_budget": 0},
            {"change_class": "refactor", "net_prod_line_budget": "0"},
            {"change_class": "refactor", "net_prod_line_budget": True},
        ):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                diff_budget.validate_task_metadata(bad)


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
                self.assertEqual(diff_budget.main([*argv, "--change-class", "refactor"]), 0)
                self.assertIn("| production | +0 | -8 | -8 |", summary.read_text())
                self.assertEqual(diff_budget.main([*argv, "--change-class", "refactor", "--budget", "-9"]), 1)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
