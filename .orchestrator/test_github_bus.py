#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

import github_bus


class GitHubReviewEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {"github_bus": {"reviewers": {"Claude": ["reviewer"]}}}

    def test_only_bound_pr_reviews_are_observed_without_mutating_task_state(self) -> None:
        status = {"tasks": [{"id": "TASK", "status": "review", "owner": "Codex", "reviewer": "Claude"}]}
        state = {"tasks": {"TASK": {"review_pr": {"number": 12}}}}
        with (
            mock.patch.object(github_bus, "gh_json", return_value=[{"id": 9, "state": "APPROVED", "user": {"login": "reviewer"}}]),
            mock.patch.object(github_bus, "write_activity_log") as activity,
        ):
            self.assertTrue(github_bus.poll_pr_reviews(self.config, state, status, "ajoe734/pantheon"))
        self.assertEqual(activity.call_args.args[1]["type"], "github_review_observed")
        self.assertEqual(status["tasks"][0]["status"], "review")

    def test_unbound_tasks_never_trigger_github_requests(self) -> None:
        with mock.patch.object(github_bus, "gh_json") as request:
            self.assertFalse(github_bus.poll_pr_reviews(self.config, {"tasks": {}}, {"tasks": [{"id": "TASK"}]}, "ajoe734/pantheon"))
        request.assert_not_called()

    def test_legacy_bus_state_is_reduced_to_review_refs(self) -> None:
        config = {"paths": {"github_bus_state": "/tmp/github-bus.json"}}
        legacy = {"processed_comment_ids": ["issue:1"], "poll_cursors": {"issue_comments": 2}, "tasks": {"TASK": {"review_pr": {"number": 2}, "ops_issue": {"number": 3}}}}
        with mock.patch.object(github_bus, "load_json", return_value=legacy):
            state = github_bus.load_bus_state(config)
        self.assertEqual(state["tasks"], {"TASK": {"review_pr": {"number": 2}}})
        self.assertNotIn("processed_comment_ids", state)


class GitHubProcessTests(unittest.TestCase):
    def test_run_gh_process_kills_process_group_on_timeout(self) -> None:
        process = mock.Mock(pid=4321)
        process.wait.side_effect = [subprocess.TimeoutExpired(cmd=["gh"], timeout=1), None]
        with (
            mock.patch.object(github_bus.subprocess, "Popen", return_value=process),
            mock.patch.object(github_bus.os, "killpg") as killpg,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            github_bus.run_gh_process(["api", "x"], timeout_seconds=1, gh_binary="gh")
        killpg.assert_called_once_with(4321, github_bus.signal.SIGKILL)
