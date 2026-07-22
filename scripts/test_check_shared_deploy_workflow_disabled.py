#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_shared_deploy_workflow_disabled as guard


def _fake_run(state_by_workflow_id):
    def _run(cmd, **kwargs):
        path = cmd[-1]
        parts = path.rstrip("/").split("/")
        if parts[-1] == "enable":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        workflow_id = parts[-1]
        state = state_by_workflow_id.get(workflow_id, "active")
        return subprocess.CompletedProcess(cmd, 0, stdout=f'{{"state": "{state}"}}', stderr="")

    return _run


class WorkflowStateTests(unittest.TestCase):
    def test_active_workflow_is_not_a_finding(self) -> None:
        with mock.patch.object(guard.subprocess, "run", side_effect=_fake_run({"269991390": "active"})):
            self.assertEqual(guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),)), [])

    def test_disabled_manually_workflow_is_a_finding(self) -> None:
        with mock.patch.object(
            guard.subprocess, "run", side_effect=_fake_run({"269991390": "disabled_manually"})
        ):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["workflow_id"], "269991390")
        self.assertEqual(findings[0]["state"], "disabled_manually")

    def test_unreachable_gh_cli_is_not_a_finding(self) -> None:
        with mock.patch.object(guard.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),)), [])

    def test_malformed_json_is_not_a_finding(self) -> None:
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

        with mock.patch.object(guard.subprocess, "run", side_effect=_run):
            self.assertEqual(guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),)), [])

    def test_multiple_watched_workflows_each_evaluated(self) -> None:
        workflows = (
            ("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),
            ("ajoe734/execute-plans", "292028803", "Pantheon Dev FE Deploy"),
        )
        with mock.patch.object(
            guard.subprocess,
            "run",
            side_effect=_fake_run({"269991390": "disabled_manually", "292028803": "active"}),
        ):
            findings = guard.check(workflows)
        self.assertEqual([f["workflow_id"] for f in findings], ["269991390"])


class MainTests(unittest.TestCase):
    def test_main_reports_without_enabling_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                guard, "check", return_value=[
                    {"repo": "ajoe734/pantheon", "workflow_id": "269991390", "label": "Pantheon Nonprod Deploy", "state": "disabled_manually"}
                ]
            ), mock.patch.object(guard, "enable_workflow") as enable_mock:
                exit_code = guard.main(["--root", str(root)])
            enable_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            log_path = root / ".orchestrator" / "logs" / "disabled-shared-workflow-guard.log"
            self.assertIn("DISABLED_MANUALLY", log_path.read_text())
            self.assertIn("report-only", log_path.read_text())

    def test_main_enables_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                guard, "check", return_value=[
                    {"repo": "ajoe734/pantheon", "workflow_id": "269991390", "label": "Pantheon Nonprod Deploy", "state": "disabled_manually"}
                ]
            ), mock.patch.object(guard, "enable_workflow", return_value=True) as enable_mock:
                exit_code = guard.main(["--root", str(root), "--enable"])
            enable_mock.assert_called_once_with("ajoe734/pantheon", "269991390")
            self.assertEqual(exit_code, 1)
            log_path = root / ".orchestrator" / "logs" / "disabled-shared-workflow-guard.log"
            self.assertIn("re-enabled", log_path.read_text())

    def test_main_returns_zero_when_nothing_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(guard, "check", return_value=[]):
                exit_code = guard.main(["--root", str(root)])
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
