from __future__ import annotations

import unittest
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auto_integrator


class FakeRunner(auto_integrator.CommandRunner):
    def __init__(self, pr: Mapping[str, Any] | None = None, rebase_returncode: int = 0) -> None:
        super().__init__()
        self.pr = pr
        self.rebase_returncode = rebase_returncode

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path = auto_integrator.ROOT,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ):
        command = [str(arg) for arg in args]
        self.commands.append(command)
        joined = " ".join(command)
        if command[:3] == ["gh", "pr", "list"]:
            stdout = "[]" if self.pr is None else '[{"number": %s}]' % self.pr["number"]
            return completed(command, stdout=stdout)
        if command[:3] == ["gh", "pr", "view"]:
            return completed(command, stdout=auto_integrator.json.dumps(dict(self.pr or {})))
        if command[:3] == ["git", "fetch", "origin"]:
            return completed(command)
        if command[:3] == ["git", "worktree", "add"]:
            return completed(command)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return completed(command, stdout="abc123\n")
        if command[:2] == ["git", "rebase"] and "--abort" not in command:
            return completed(command, returncode=self.rebase_returncode)
        if command[:3] == ["git", "worktree", "remove"]:
            return completed(command)
        if command[:3] == ["gh", "pr", "merge"]:
            return completed(command)
        if "scripts/ai_status.py" in joined:
            return completed(command)
        return completed(command)

    def run_shell(
        self,
        command: str,
        *,
        cwd: Path,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ):
        self.commands.append(["sh", "-lc", command])
        return completed(["sh", "-lc", command])


def completed(command: Sequence[str], stdout: str = "", returncode: int = 0):
    class Result:
        def __init__(self) -> None:
            self.args = list(command)
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    return Result()


def green_pr(number: int = 44) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Task PR",
        "url": f"https://github.example/pr/{number}",
        "headRefName": "task/ABC-001",
        "baseRefName": "dev",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [
            {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "Smoke acceptance", "state": "SUCCESS"},
        ],
    }


class CandidateSelectionTests(unittest.TestCase):
    def test_review_approved_candidates_only(self) -> None:
        state = {
            "tasks": [
                {"id": "ABC-001", "title": "Ready", "status": "review_approved", "owner": "Codex", "reviewer": "Claude"},
                {"id": "ABC-002", "title": "Todo", "status": "todo", "owner": "Codex", "reviewer": "Claude"},
                {"id": "ABC-003", "title": "Missing owner", "status": "review_approved", "reviewer": "Claude"},
            ]
        }

        candidates = auto_integrator.review_approved_candidates(state)

        self.assertEqual([candidate.task_id for candidate in candidates], ["ABC-001"])
        self.assertEqual(candidates[0].branch, "task/ABC-001")


class CheckSummaryTests(unittest.TestCase):
    def test_green_rollup(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [
                {"name": "trailers", "conclusion": "SUCCESS", "status": "COMPLETED"},
                {"context": "smoke", "state": "SUCCESS"},
            ]
        )

        self.assertEqual(summary.state, "green")
        self.assertEqual(summary.total, 2)

    def test_pending_rollup(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [{"name": "ci", "status": "IN_PROGRESS"}]
        )

        self.assertEqual(summary.state, "pending")
        self.assertEqual(summary.pending, ("ci",))

    def test_red_rollup(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [{"name": "ci", "conclusion": "FAILURE"}]
        )

        self.assertEqual(summary.state, "red")
        self.assertEqual(summary.failing, ("ci",))


class IntegrationPlanTests(unittest.TestCase):
    def test_dry_run_would_merge_green_clean_pr(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr())

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=False,
        )

        self.assertEqual(result.action, "would_merge")
        self.assertTrue(result.dry_run)
        self.assertIn(["sh", "-lc", "true"], runner.commands)
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_red_checks_open_unblock_in_execute_mode(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr["statusCheckRollup"] = [{"name": "ci", "conclusion": "FAILURE"}]
        runner = FakeRunner(pr=pr)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )

        self.assertEqual(result.action, "blocked")
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-ABC-001-CI-RED")
        self.assertTrue(any("scripts/ai_status.py" in " ".join(command) and "assign" in command for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_rebase_conflict_opens_unblock_without_merge(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(), rebase_returncode=1)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )

        self.assertEqual(result.action, "blocked")
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-ABC-001-REBASE-CONFLICT")
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))


if __name__ == "__main__":
    unittest.main()
