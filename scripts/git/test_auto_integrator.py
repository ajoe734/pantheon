from __future__ import annotations

import unittest
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.git import auto_integrator


class FakeRunner(auto_integrator.CommandRunner):
    def __init__(
        self,
        pr: Mapping[str, Any] | None = None,
        rebase_returncode: int = 0,
        merged_pr: Mapping[str, Any] | None = None,
        merge_base_returncode: int = 0,
        disable_auto_clears_request: bool = True,
        disable_auto_returncode: int = 0,
        auto_merge_read_fails: bool = False,
    ) -> None:
        super().__init__()
        self.pr = dict(pr) if pr is not None else None
        self.merged_pr = dict(merged_pr) if merged_pr is not None else None
        self.rebase_returncode = rebase_returncode
        self.merge_base_returncode = merge_base_returncode
        self.disable_auto_clears_request = disable_auto_clears_request
        self.disable_auto_returncode = disable_auto_returncode
        self.auto_merge_read_fails = auto_merge_read_fails

    def _pr_for_command_state(self, command: Sequence[str]) -> Mapping[str, Any] | None:
        if "--state" not in command:
            return self.pr
        state = command[command.index("--state") + 1]
        return self.merged_pr if state == "merged" else self.pr

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
            pr = self._pr_for_command_state(command)
            stdout = "[]" if pr is None else '[{"number": %s}]' % pr["number"]
            return completed(command, stdout=stdout)
        if command[:3] == ["gh", "pr", "view"]:
            if command[-2:] == ["--json", "autoMergeRequest"] and self.auto_merge_read_fails:
                raise auto_integrator.CommandFailure(command, 1, "readback unavailable")
            number = command[3]
            for pr in (self.pr, self.merged_pr):
                if pr is not None and str(pr.get("number")) == number:
                    return completed(command, stdout=auto_integrator.json.dumps(dict(pr)))
            return completed(command, stdout="{}")
        if command[:3] == ["git", "fetch", "origin"]:
            return completed(command)
        if command[:3] == ["git", "merge-base", "--is-ancestor"]:
            return completed(command, returncode=self.merge_base_returncode)
        if command[:3] == ["git", "worktree", "add"]:
            return completed(command)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return completed(command, stdout="abc123\n")
        if command[:2] == ["git", "rebase"] and "--abort" not in command:
            return completed(command, returncode=self.rebase_returncode)
        if command[:3] == ["git", "worktree", "remove"]:
            return completed(command)
        if command[:3] == ["gh", "pr", "merge"]:
            if "--disable-auto" in command and self.disable_auto_clears_request and self.pr is not None:
                self.pr = {**self.pr, "autoMergeRequest": None}
            return completed(
                command,
                returncode=self.disable_auto_returncode if "--disable-auto" in command else 0,
            )
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


APPROVED_HEAD = "a" * 40


def green_pr(number: int = 44) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Task PR",
        "url": f"https://github.example/pr/{number}",
        "headRefName": "task/ABC-001",
        "headRefOid": APPROVED_HEAD,
        "baseRefName": "dev",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "commits": [{"oid": APPROVED_HEAD, "committedDate": "2026-06-12T00:30:00Z"}],
        "statusCheckRollup": [
            {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "Smoke acceptance", "state": "SUCCESS"},
        ],
    }


def approved_gate(task_id: str = "ABC-001", pr_number: int = 44) -> auto_integrator.ReviewGate:
    """Canonical state where the assigned reviewer approved the exact head.

    The approval carries the PR identity binding `command_approve` records;
    without it the gate refuses the merge, which is the point of the binding.
    """

    return auto_integrator.ReviewGate(
        state={
            "tasks": [
                {
                    "id": task_id,
                    "title": "Ready",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        },
        events=[
            {
                "ts": "2026-06-12T00:45:00Z",
                "agent": "Claude",
                "type": "review_approved",
                "task_id": task_id,
                "message": "Independent review approved.",
                "review_binding": {
                    "pr": pr_number,
                    "head_sha": APPROVED_HEAD,
                    "head_branch": f"task/{task_id}",
                    "base": "dev",
                },
            }
        ],
    )


def merged_pr(number: int = 55) -> dict[str, Any]:
    pr = green_pr(number)
    pr["state"] = "MERGED"
    pr["mergeCommit"] = {"oid": "merge123"}
    pr["mergedAt"] = "2026-06-12T01:01:07Z"
    return pr


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
            gate=approved_gate(),
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
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "blocked")
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-ABC-001-CI-RED")
        self.assertTrue(any("scripts/ai_status.py" in " ".join(command) and "assign" in command for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_merge_then_review_rebase_conflict_opens_unblock_without_merge(self) -> None:
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
            gate=auto_integrator.ReviewGate(
                state={
                    "tasks": [
                        {
                            "id": "ABC-001",
                            "title": "Ready",
                            "status": "review_approved",
                            "owner": "Codex",
                            "reviewer": "Codex",
                            "merge_policy": "merge_then_review",
                        }
                    ]
                },
                events=[],
            ),
        )

        self.assertEqual(result.action, "blocked")
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-ABC-001-REBASE-CONFLICT")
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_execute_reconciles_already_merged_pr_without_unblock(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=None, merged_pr=merged_pr())

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(pr_number=55),
        )

        self.assertEqual(result.action, "reconciled_done")
        self.assertEqual(result.pr_number, 55)
        self.assertTrue(any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands))
        self.assertFalse(any("scripts/ai_status.py" in " ".join(command) and "assign" in command for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))
        self.assertIn(["git", "merge-base", "--is-ancestor", "merge123", "origin/dev"], runner.commands)

    def test_missing_pr_still_opens_unblock_when_no_open_or_merged_pr(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=None, merged_pr=None)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "blocked")
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-ABC-001-MISSING-PR")
        self.assertIn("No open or merged PR found", result.detail)


if __name__ == "__main__":
    unittest.main()
