from __future__ import annotations

import json
import sys
import unittest
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
        merge_lands_synchronously: bool = True,
        landed_merged_at: str = "2026-06-12T01:01:07Z",
        ephemeral_merge_returncode: int = 0,
        commits: Mapping[str, Mapping[str, Any]] | None = None,
        carry_forward_publish_fails: bool = False,
        requiredness_nodes: Sequence[Mapping[str, Any]] | None = None,
        requiredness_query_fails: bool = False,
    ) -> None:
        super().__init__()
        self.pr = dict(pr) if pr is not None else None
        self.merged_pr = dict(merged_pr) if merged_pr is not None else None
        self.rebase_returncode = rebase_returncode
        self.merge_base_returncode = merge_base_returncode
        self.disable_auto_clears_request = disable_auto_clears_request
        self.disable_auto_returncode = disable_auto_returncode
        self.auto_merge_read_fails = auto_merge_read_fails
        # SUP-GATED-PR-EXACT-HEAD-QUEUE-MERGE-20260805: outcome of the
        # disposable `git merge origin/dev` test-merge run against a behind
        # gated PR's exact reviewed head. 0 = clean (default); non-zero
        # models a real conflict.
        self.ephemeral_merge_returncode = ephemeral_merge_returncode
        # SUP-MERGE-QUEUE-AWARE-INTEGRATOR-20260804: models whether an actual
        # (non --auto, non --disable-auto) `gh pr merge` call lands
        # immediately -- true is the pre-merge-queue default; a caller
        # models a merge-queue-required branch by passing False, which
        # leaves `self.pr["state"]` unchanged (still OPEN) after the merge
        # call, the way a request that was only *enqueued* would.
        self.merge_lands_synchronously = merge_lands_synchronously
        self.landed_merged_at = landed_merged_at
        self.commits = {str(sha): dict(payload) for sha, payload in (commits or {}).items()}
        self.carry_forward_publish_fails = carry_forward_publish_fails
        self.requiredness_nodes = [dict(node) for node in (requiredness_nodes or [])]
        self.requiredness_query_fails = requiredness_query_fails
        self.api_payloads: list[dict[str, Any]] = []
        self.tag_refs: set[str] = set()
        self._next_tag_sha = 200

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
        api_payload: dict[str, Any] | None = None
        if "--input" in command:
            input_path = Path(command[command.index("--input") + 1])
            api_payload = json.loads(input_path.read_text(encoding="utf-8"))
            self.api_payloads.append(api_payload)
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
        if command[:3] == ["gh", "api", "graphql"]:
            if self.requiredness_query_fails:
                raise auto_integrator.CommandFailure(command, 1, "GraphQL unavailable")
            payload = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "statusCheckRollup": {
                                "contexts": {"nodes": self.requiredness_nodes}
                            }
                        }
                    }
                }
            }
            return completed(command, stdout=auto_integrator.json.dumps(payload))
        commit_prefix = "repos/ajoe734/pantheon/commits/"
        if command[:2] == ["gh", "api"] and len(command) == 3 and command[2].startswith(commit_prefix):
            head_sha = command[2][len(commit_prefix) :].partition("?")[0]
            return completed(command, stdout=auto_integrator.json.dumps(self.commits.get(head_sha, {})))
        if (
            command[:2] == ["gh", "api"]
            and "git/refs/tags/" in command[-1]
            and "--method" not in command
        ):
            tag_name = command[-1].rsplit("git/refs/tags/", 1)[-1].replace("%2F", "/")
            ref = f"refs/tags/{tag_name}"
            if ref in self.tag_refs:
                return completed(command, stdout=auto_integrator.json.dumps({"ref": ref}))
            return completed(command, stdout="{}")
        if command[:2] == ["gh", "api"] and "/git/tags" in joined and "POST" in command:
            if self.carry_forward_publish_fails:
                raise auto_integrator.CommandFailure(command, 1, "review-proof tag write failed")
            self._next_tag_sha += 1
            return completed(command, stdout=auto_integrator.json.dumps({"sha": f"tagobj{self._next_tag_sha}"}))
        if command[:2] == ["gh", "api"] and "/git/refs" in joined and "POST" in command:
            assert api_payload is not None
            self.tag_refs.add(api_payload["ref"])
            return completed(command, stdout=auto_integrator.json.dumps({"ref": api_payload["ref"]}))
        if command[:2] == ["gh", "api"] and "/actions/workflows/" in joined and "POST" in command:
            return completed(command)
        if command[:3] == ["git", "fetch", "origin"]:
            return completed(command)
        if command[:3] == ["git", "merge-base", "--is-ancestor"]:
            return completed(command, returncode=self.merge_base_returncode)
        if command[:2] == ["git", "merge"] and "--abort" not in command:
            return completed(command, returncode=self.ephemeral_merge_returncode)
        if command[:3] == ["git", "merge", "--abort"]:
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
            if "--disable-auto" in command:
                if self.disable_auto_clears_request and self.pr is not None:
                    self.pr = {**self.pr, "autoMergeRequest": None}
                return completed(command, returncode=self.disable_auto_returncode)
            if "--auto" not in command and self.merge_lands_synchronously and self.pr is not None:
                # A direct (non-queued) merge request that GitHub completes
                # immediately -- the next `gh pr view` should see it MERGED.
                self.pr = {
                    **self.pr,
                    "state": "MERGED",
                    "mergedAt": self.landed_merged_at,
                    "mergeCommit": {"oid": "merge123"},
                }
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


class GitHubJsonCommandRunnerTests(unittest.TestCase):
    def test_post_payload_is_available_to_gh_and_removed_afterward(self) -> None:
        runner = FakeRunner()
        client = auto_integrator.GitHubJsonCommandRunner(runner, root=REPO_ROOT)

        result = client.run_json(
            ["gh", "api", "--method", "POST", "repos/example/repo/example", "--input", "-"],
            payload={"hello": "world"},
        )

        self.assertIsNone(result)
        self.assertEqual(runner.api_payloads, [{"hello": "world"}])
        command = runner.commands[-1]
        self.assertNotEqual(command[-1], "-")
        self.assertFalse(Path(command[-1]).exists())


class IntegrationPlanTests(unittest.TestCase):
    def test_task_brief_only_successor_is_carried_forward_automatically(self) -> None:
        successor = "b" * 40
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr.update(
            {
                "url": "https://github.com/ajoe734/pantheon/pull/44",
                "headRefOid": successor,
                "commits": [{"oid": successor, "committedDate": "2026-06-12T00:50:00Z"}],
            }
        )
        runner = FakeRunner(
            pr=pr,
            commits={
                successor: {
                    "sha": successor,
                    "parents": [{"sha": APPROVED_HEAD}],
                    "files": [
                        {
                            "filename": ".orchestrator/task-briefs/abc_001.md",
                            "status": "modified",
                        }
                    ],
                }
            },
        )

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=False,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "would_merge")
        self.assertIn("would merge", result.detail)
        self.assertTrue(
            any(
                command[:2] == ["gh", "api"]
                and command[-1].startswith(
                    f"repos/ajoe734/pantheon/commits/{successor}?"
                )
                for command in runner.commands
            )
        )

    def test_task_brief_successor_publishes_before_red_rollup_then_merges_green_pass(self) -> None:
        successor = "b" * 40
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr.update(
            {
                "url": "https://github.com/ajoe734/pantheon/pull/44",
                "headRefOid": successor,
                "commits": [{"oid": successor, "committedDate": "2026-06-12T00:50:00Z"}],
                "statusCheckRollup": [
                    {
                        "name": "Pantheon canonical review gate",
                        "conclusion": "FAILURE",
                        "status": "COMPLETED",
                    },
                    {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED"},
                ],
            }
        )
        runner = FakeRunner(
            pr=pr,
            commits={
                successor: {
                    "sha": successor,
                    "parents": [{"sha": APPROVED_HEAD}],
                    "files": [{"filename": ".orchestrator/task-briefs/abc_001.md"}],
                }
            },
        )

        first_result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(first_result.action, "waiting")
        self.assertIn("waiting for that successor check", first_result.detail)
        self.assertTrue(any("/git/tags" in " ".join(command) for command in runner.commands))
        self.assertTrue(any("/git/refs" in " ".join(command) for command in runner.commands))
        self.assertTrue(any("/actions/workflows/" in " ".join(command) for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))
        proof_index = next(index for index, command in enumerate(runner.commands) if "/git/tags" in " ".join(command))
        dispatch_index = next(index for index, command in enumerate(runner.commands) if "/actions/workflows/" in " ".join(command))
        self.assertLess(proof_index, dispatch_index)

        runner.pr = {
            **runner.pr,
            "statusCheckRollup": [
                {
                    "name": "Pantheon canonical review gate",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                },
                {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED"},
            ],
        }
        workflow_calls_after_first_pass = sum(
            1 for command in runner.commands if "/actions/workflows/" in " ".join(command)
        )

        second_result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(second_result.action, "merged")
        self.assertEqual(
            workflow_calls_after_first_pass,
            sum(1 for command in runner.commands if "/actions/workflows/" in " ".join(command)),
        )
        self.assertTrue(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_rejected_gate_never_publishes_task_brief_carry_forward_proof(self) -> None:
        successor = "b" * 40
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr.update(
            {
                "url": "https://github.com/ajoe734/pantheon/pull/44",
                "headRefOid": successor,
                "commits": [{"oid": successor, "committedDate": "2026-06-12T00:50:00Z"}],
            }
        )
        runner = FakeRunner(
            pr=pr,
            commits={
                successor: {
                    "sha": successor,
                    "parents": [{"sha": APPROVED_HEAD}],
                    "files": [{"filename": ".orchestrator/task-briefs/abc_001.md"}],
                }
            },
        )
        rejected_gate = auto_integrator.ReviewGate(
            state={
                "tasks": [
                    {
                        "id": "ABC-001",
                        "title": "Ready",
                        "status": "review",
                        "owner": "Codex",
                        "reviewer": "Claude",
                    }
                ]
            },
            events=approved_gate().events,
        )

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            open_unblock=False,
            gate=rejected_gate,
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("review_not_approved", result.detail)
        self.assertTrue(any(f"/commits/{successor}" in " ".join(command) for command in runner.commands))
        self.assertFalse(any("/git/tags" in " ".join(command) for command in runner.commands))
        self.assertFalse(any("/git/refs" in " ".join(command) for command in runner.commands))
        self.assertFalse(any("/actions/workflows/" in " ".join(command) for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_carry_forward_publication_failure_blocks_before_merging(self) -> None:
        successor = "b" * 40
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr.update(
            {
                "url": "https://github.com/ajoe734/pantheon/pull/44",
                "headRefOid": successor,
                "commits": [{"oid": successor, "committedDate": "2026-06-12T00:50:00Z"}],
            }
        )
        runner = FakeRunner(
            pr=pr,
            carry_forward_publish_fails=True,
            commits={
                successor: {
                    "sha": successor,
                    "parents": [{"sha": APPROVED_HEAD}],
                    "files": [{"filename": ".orchestrator/task-briefs/abc_001.md"}],
                }
            },
        )

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            open_unblock=False,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("proof publication failed", result.detail)
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

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

    def test_execute_merges_when_gh_pr_merge_lands_synchronously(self) -> None:
        """SUP-MERGE-QUEUE-AWARE-INTEGRATOR-20260804: the common case today
        (no merge queue) -- `gh pr merge` completes immediately, the
        post-merge re-check already sees MERGED, and reconcile_done still
        fires in the same pass exactly as before this change."""

        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44))

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "merged")
        self.assertTrue(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))
        self.assertTrue(
            any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands)
        )

    def test_execute_defers_reconcile_when_merge_has_not_landed_yet(self) -> None:
        """A branch that requires a merge queue does not merge synchronously
        -- `gh pr merge` enqueues the request instead (see `gh pr merge
        --help`). The integrator must not call reconcile_done (mark the task
        `done`) for a merge that has not actually happened."""

        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        # merge_lands_synchronously=False models a merge-queue-required
        # branch: `gh pr merge` is accepted (enqueued) but the PR has not
        # actually landed within this process's lifetime.
        runner = FakeRunner(pr=green_pr(number=44), merge_lands_synchronously=False)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "queued_for_merge")
        self.assertFalse(result.dry_run)
        self.assertTrue(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))
        self.assertFalse(
            any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands)
        )

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


class CheckClassifierTests(unittest.TestCase):
    def test_non_required_diagnostic_failure_ignored_in_summary(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [
                {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED", "isRequired": True},
                {
                    "name": "Audit signed canonical review (not required issuer) (4741)",
                    "conclusion": "FAILURE",
                    "status": "COMPLETED",
                    "workflowName": "Canonical Review Attestation Audit",
                    "isRequired": False,
                },
            ]
        )
        self.assertEqual(summary.state, "green")
        self.assertEqual(summary.failing, ())
        self.assertEqual(summary.ignored_diagnostic, ("Audit signed canonical review (not required issuer) (4741)",))

    def test_non_required_actual_ci_failure_still_blocks(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [
                {
                    "name": "Python packaging provision",
                    "conclusion": "FAILURE",
                    "status": "COMPLETED",
                    "workflowName": "Branch CI Gate",
                    "isRequired": False,
                }
            ]
        )

        self.assertEqual(summary.state, "red")
        self.assertEqual(summary.failing, ("Python packaging provision",))

    def test_non_required_failure_without_diagnostic_provenance_blocks(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [
                {
                    "name": "Unattributed optional check",
                    "conclusion": "FAILURE",
                    "status": "COMPLETED",
                    "isRequired": False,
                }
            ]
        )

        self.assertEqual(summary.state, "red")
        self.assertEqual(summary.failing, ("Unattributed optional check",))

    def test_ambiguous_requiredness_treated_as_required_in_summary(self) -> None:
        summary = auto_integrator.summarize_status_rollup(
            [
                {"name": "Ambiguous check", "conclusion": "FAILURE", "status": "COMPLETED"},
            ]
        )
        self.assertEqual(summary.state, "red")
        self.assertEqual(summary.failing, ("Ambiguous check",))

    def test_graphql_enrichment_classifies_live_rollup_shape(self) -> None:
        pr = green_pr(number=4741)
        pr["url"] = "https://github.com/ajoe734/pantheon/pull/4741"
        pr["statusCheckRollup"] = [
            {
                "__typename": "CheckRun",
                "name": "Smoke acceptance",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
            },
            {
                "__typename": "CheckRun",
                "name": "Audit signed canonical review (not required issuer) (4741)",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
                "workflowName": "Canonical Review Attestation Audit",
            },
        ]
        runner = FakeRunner(
            requiredness_nodes=[
                {"__typename": "CheckRun", "name": "Smoke acceptance", "isRequired": True},
                {
                    "__typename": "CheckRun",
                    "name": "Audit signed canonical review (not required issuer) (4741)",
                    "isRequired": False,
                },
            ]
        )

        enriched = auto_integrator.enrich_pr_status_rollup(pr, runner)
        self.assertIsNotNone(enriched)
        rollup = enriched["statusCheckRollup"]
        self.assertEqual([item["isRequired"] for item in rollup], [True, False])
        summary = auto_integrator.summarize_status_rollup(rollup)
        self.assertEqual(summary.state, "green")
        self.assertEqual(
            summary.ignored_diagnostic,
            ("Audit signed canonical review (not required issuer) (4741)",),
        )
        self.assertTrue(any(command[:3] == ["gh", "api", "graphql"] for command in runner.commands))

    def test_graphql_failure_leaves_failing_check_ambiguous_and_blocking(self) -> None:
        pr = green_pr(number=4741)
        pr["url"] = "https://github.com/ajoe734/pantheon/pull/4741"
        pr["statusCheckRollup"] = [
            {
                "__typename": "CheckRun",
                "name": "Audit signed canonical review (not required issuer) (4741)",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
            }
        ]
        runner = FakeRunner(requiredness_query_fails=True)

        enriched = auto_integrator.enrich_pr_status_rollup(pr, runner)
        self.assertIsNotNone(enriched)
        summary = auto_integrator.summarize_status_rollup(enriched["statusCheckRollup"])
        self.assertEqual(summary.state, "red")
        self.assertEqual(
            summary.failing,
            ("Audit signed canonical review (not required issuer) (4741)",),
        )

    def test_unmatched_graphql_check_remains_ambiguous_and_blocking(self) -> None:
        pr = green_pr(number=4741)
        pr["url"] = "https://github.com/ajoe734/pantheon/pull/4741"
        pr["statusCheckRollup"] = [
            {
                "__typename": "CheckRun",
                "name": "Unmatched failure",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
            }
        ]
        runner = FakeRunner(
            requiredness_nodes=[
                {"__typename": "CheckRun", "name": "Different check", "isRequired": False}
            ]
        )

        enriched = auto_integrator.enrich_pr_status_rollup(pr, runner)
        self.assertIsNotNone(enriched)
        summary = auto_integrator.summarize_status_rollup(enriched["statusCheckRollup"])
        self.assertEqual(summary.state, "red")
        self.assertEqual(summary.failing, ("Unmatched failure",))

    def test_duplicate_graphql_identity_is_required_if_any_match_is_required(self) -> None:
        runner = FakeRunner(
            requiredness_nodes=[
                {"__typename": "CheckRun", "name": "Smoke acceptance", "isRequired": False},
                {"__typename": "CheckRun", "name": "Smoke acceptance", "isRequired": True},
            ]
        )

        requiredness = auto_integrator.fetch_is_required_map(
            runner,
            "https://github.com/ajoe734/pantheon/pull/4741",
            4741,
        )

        self.assertIs(requiredness[("CheckRun", "Smoke acceptance")], True)

    def test_pr_4741_shaped_diagnostic_failure_allows_merge(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="SUP-PROVIDER-REPORT-CALL-SIGNATURE-20260811",
            title="add evidence",
            owner="Antigravity2",
            reviewer="Codex2",
            branch="task/SUP-PROVIDER-REPORT-CALL-SIGNATURE-20260811",
        )
        pr = green_pr()
        pr.update(
            {
                "number": 4741,
                "headRefName": "task/SUP-PROVIDER-REPORT-CALL-SIGNATURE-20260811",
                "mergeStateStatus": "UNSTABLE",
                "statusCheckRollup": [
                    {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED", "isRequired": True},
                    {"name": "Smoke acceptance", "state": "SUCCESS", "isRequired": True},
                    {
                        "name": "Audit signed canonical review (not required issuer) (4741)",
                        "conclusion": "FAILURE",
                        "status": "COMPLETED",
                        "workflowName": "Canonical Review Attestation Audit",
                        "isRequired": False,
                    },
                ],
            }
        )
        runner = FakeRunner(pr=pr)
        gate = approved_gate(task_id="SUP-PROVIDER-REPORT-CALL-SIGNATURE-20260811", pr_number=4741)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=False,
            gate=gate,
        )
        self.assertEqual(result.action, "would_merge")
        self.assertIn("Ignored explicitly non-required diagnostics", result.detail)

    def test_required_check_failure_blocks_integration(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr["statusCheckRollup"] = [
            {"name": "Smoke acceptance", "conclusion": "FAILURE", "status": "COMPLETED", "isRequired": True}
        ]
        runner = FakeRunner(pr=pr)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(),
        )
        self.assertEqual(result.action, "blocked")

    def test_ambiguous_requiredness_failure_blocks_integration(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr["statusCheckRollup"] = [
            {"name": "Ambiguous check", "conclusion": "FAILURE", "status": "COMPLETED"}
        ]
        runner = FakeRunner(pr=pr)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(),
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("Ambiguous check", result.detail)

    def test_failing_trailer_check_blocks_integration(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr["statusCheckRollup"] = [
            {"name": "Commit trailers", "conclusion": "FAILURE", "status": "COMPLETED", "isRequired": True}
        ]
        runner = FakeRunner(pr=pr)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(),
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("Commit trailers", result.detail)

    def test_canonical_review_gate_failure_blocks_integration(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr["statusCheckRollup"] = [
            {"name": "Pantheon canonical review gate", "state": "FAILURE", "isRequired": True}
        ]
        runner = FakeRunner(pr=pr)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(),
        )
        self.assertEqual(result.action, "blocked")

    def test_exact_head_drift_blocks_integration(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        pr = green_pr()
        pr["headRefOid"] = "f" * 40
        runner = FakeRunner(pr=pr)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=approved_gate(),
        )
        self.assertEqual(result.action, "blocked")
        self.assertTrue("approval_head_mismatch" in result.detail or "head_changed_after_approval" in result.detail)


if __name__ == "__main__":
    unittest.main()
