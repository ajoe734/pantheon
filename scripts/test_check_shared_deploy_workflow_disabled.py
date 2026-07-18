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

    def test_unreachable_gh_cli_is_an_observation_failure(self) -> None:
        with mock.patch.object(guard.subprocess, "run", side_effect=FileNotFoundError):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "gh-cli-not-found")
        self.assertIsNone(findings[0]["state"])

    def test_timeout_is_an_observation_failure(self) -> None:
        with mock.patch.object(guard.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "api-timeout")

    def test_api_failure_is_an_observation_failure(self) -> None:
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 4, stdout="", stderr="auth failed")

        with mock.patch.object(guard.subprocess, "run", side_effect=_run):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "api-exit-4")

    def test_malformed_json_is_an_observation_failure(self) -> None:
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

        with mock.patch.object(guard.subprocess, "run", side_effect=_run):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "invalid-json")

    def test_missing_state_is_an_observation_failure(self) -> None:
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        with mock.patch.object(guard.subprocess, "run", side_effect=_run):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "missing-state")

    def test_non_object_json_is_an_observation_failure(self) -> None:
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

        with mock.patch.object(guard.subprocess, "run", side_effect=_run):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "invalid-payload")
        self.assertIsNone(findings[0]["state"])

    def test_unexpected_inactive_state_is_a_finding(self) -> None:
        with mock.patch.object(
            guard.subprocess,
            "run",
            side_effect=_fake_run({"269991390": "disabled_inactivity"}),
        ):
            findings = guard.check((("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),))
        self.assertEqual(findings[0]["error"], "unexpected-state")
        self.assertEqual(findings[0]["state"], "disabled_inactivity")

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

    def test_main_reports_observation_failure_without_trying_enable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                guard,
                "check",
                return_value=[
                    {
                        "repo": "ajoe734/pantheon",
                        "workflow_id": "269991390",
                        "label": "Pantheon Nonprod Deploy",
                        "state": None,
                        "error": "api-timeout",
                    }
                ],
            ), mock.patch.object(guard, "enable_workflow") as enable_mock:
                exit_code = guard.main(["--root", str(root), "--enable"])
            enable_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            log_path = root / ".orchestrator" / "logs" / "disabled-shared-workflow-guard.log"
            self.assertIn("OBSERVATION_FAILED", log_path.read_text())
            self.assertIn("error=api-timeout", log_path.read_text())

    def test_main_reports_unexpected_state_without_trying_enable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                guard,
                "check",
                return_value=[
                    {
                        "repo": "ajoe734/pantheon",
                        "workflow_id": "269991390",
                        "label": "Pantheon Nonprod Deploy",
                        "state": "disabled_inactivity",
                        "error": "unexpected-state",
                    }
                ],
            ), mock.patch.object(guard, "enable_workflow") as enable_mock:
                exit_code = guard.main(["--root", str(root), "--enable"])
            enable_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            log_path = root / ".orchestrator" / "logs" / "disabled-shared-workflow-guard.log"
            self.assertIn("UNEXPECTED_STATE", log_path.read_text())
            self.assertIn("state=disabled_inactivity", log_path.read_text())


if __name__ == "__main__":
    unittest.main()
