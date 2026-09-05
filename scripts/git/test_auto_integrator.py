from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.git import auto_integrator
from rewrite import task_state_store


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
        check_filesystem_paths: bool = False,
        git_toplevel_returncode: int = 0,
        git_toplevel_path: str | None = None,
        git_common_dir_returncode: int = 0,
        git_common_dir_path: str | None = None,
        git_status_returncode: int = 0,
        git_status_output: str = "",
        origin_remote_returncode: int = 0,
        origin_remote_slug: str | None = None,
        git_head: str = "abc123",
        symbolic_ref_returncode: int = 1,
        merge_sha: str = "merge123",
    ) -> None:
        super().__init__()
        self.pr = dict(pr) if pr is not None else None
        self.merged_pr = dict(merged_pr) if merged_pr is not None else None
        self.rebase_returncode = rebase_returncode
        self.merge_base_returncode = merge_base_returncode
        self.disable_auto_clears_request = disable_auto_clears_request
        self.disable_auto_returncode = disable_auto_returncode
        self.auto_merge_read_fails = auto_merge_read_fails
        self.check_filesystem_paths = check_filesystem_paths
        self.git_toplevel_returncode = git_toplevel_returncode
        self.git_toplevel_path = git_toplevel_path
        self.git_common_dir_returncode = git_common_dir_returncode
        self.git_common_dir_path = git_common_dir_path
        self.git_status_returncode = git_status_returncode
        self.git_status_output = git_status_output
        self.origin_remote_returncode = origin_remote_returncode
        self.origin_remote_slug = origin_remote_slug
        self.git_head = git_head
        self.symbolic_ref_returncode = symbolic_ref_returncode
        self.merge_sha = merge_sha
        # SUP-GATED-PR-EXACT-HEAD-QUEUE-MERGE-20260805: outcome of the
        # disposable `git merge origin/dev` test-merge run against a behind
        # gated PR's exact reviewed head. 0 = clean (default); non-zero
        # models a real conflict.
        self.ephemeral_merge_returncode = ephemeral_merge_returncode
        # Models whether the synchronous REST merge endpoint returns
        # merged=true. A false result never creates a queue/auto-merge request.
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
        timeout: float | None = None,
    ):
        del timeout
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
        if (
            command[:2] == ["gh", "api"]
            and len(command) == 3
            and command[2]
            == "repos/ajoe734/pantheon/actions/workflows?per_page=100"
        ):
            return completed(
                command,
                stdout=auto_integrator.json.dumps(
                    {
                        "workflows": [
                            {
                                "id": 123,
                                "name": "Canonical Review Gate",
                                "state": "active",
                            }
                        ]
                    }
                ),
            )
        if (
            command[:4] == ["gh", "api", "--method", "PUT"]
            and "/pulls/" in joined
            and "/merge" in joined
        ):
            if self.merge_lands_synchronously and self.pr is not None:
                self.pr = {
                    **self.pr,
                    "state": "MERGED",
                    "mergedAt": self.landed_merged_at,
                    "mergeCommit": {"oid": self.merge_sha},
                }
                payload = {"merged": True, "sha": self.merge_sha, "message": "merged"}
            else:
                payload = {"merged": False, "message": "not directly mergeable"}
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
        if command[:1] == ["git"] and "merge" in command and "--abort" not in command:
            return completed(command, returncode=self.ephemeral_merge_returncode)
        if command[:1] == ["git"] and "merge" in command and "--abort" in command:
            return completed(command)
        if command[:3] == ["git", "worktree", "add"]:
            return completed(command)
        if command[:3] == ["git", "rev-parse", "--show-toplevel"]:
            if self.git_toplevel_returncode != 0:
                return completed(command, returncode=self.git_toplevel_returncode)
            top = self.git_toplevel_path if self.git_toplevel_path is not None else str(cwd)
            return completed(command, stdout=f"{top}\n")
        if command[:3] == ["git", "rev-parse", "--git-common-dir"]:
            if self.git_common_dir_returncode != 0:
                return completed(command, returncode=self.git_common_dir_returncode)
            common_dir = (
                self.git_common_dir_path
                if self.git_common_dir_path is not None
                else str(Path(cwd) / ".git")
            )
            return completed(command, stdout=f"{common_dir}\n")
        if command[:2] == ["git", "status"]:
            return completed(
                command,
                stdout=self.git_status_output,
                returncode=self.git_status_returncode,
            )
        if command[:3] == ["git", "remote", "get-url"]:
            if self.origin_remote_returncode != 0:
                return completed(command, returncode=self.origin_remote_returncode)
            if self.origin_remote_slug:
                slug = self.origin_remote_slug
            elif "execute-plans" in str(cwd) or "execute_plans" in str(cwd):
                slug = "ajoe734/execute-plans"
            else:
                slug = "ajoe734/pantheon"
            return completed(command, stdout=f"https://github.com/{slug}.git\n")
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return completed(command, stdout=f"{self.git_head}\n")
        if command[:3] == ["git", "symbolic-ref", "-q"]:
            return completed(command, returncode=self.symbolic_ref_returncode)
        if command[:2] == ["git", "rebase"] and "--abort" not in command:
            return completed(command, returncode=self.rebase_returncode)
        if command[:3] == ["git", "worktree", "remove"]:
            return completed(command)
        if command[:3] == ["gh", "pr", "merge"]:
            if "--disable-auto" in command:
                if self.disable_auto_clears_request and self.pr is not None:
                    self.pr = {**self.pr, "autoMergeRequest": None}
                return completed(command, returncode=self.disable_auto_returncode)
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


def green_pr(number: int = 44, *, task_id: str = "ABC-001") -> dict[str, Any]:
    return {
        "number": number,
        "title": "Task PR",
        "url": f"https://github.example/pr/{number}",
        "headRefName": f"task/{task_id}",
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


def operator_accepted_gate(
    task_id: str = "ABC-001", pr_number: int = 44
) -> auto_integrator.ReviewGate:
    binding = {
        "pr": pr_number,
        "head_sha": APPROVED_HEAD,
        "head_branch": f"task/{task_id}",
        "base": "dev",
    }
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
                "agent": "Human/Ops",
                "type": "operator_accepted",
                "task_id": task_id,
                "message": "Human/Ops accepted this exact head.",
                "review_binding": binding,
                "operator_acceptance": {
                    "repository": "ajoe734/pantheon",
                    **binding,
                    "decision": "operator-accept",
                    "actor": "Human/Ops",
                    "mode": "operator_exact_head",
                    "operator_acceptance_proof_ref": (
                        "refs/tags/pantheon-review/operator-accept/" + APPROVED_HEAD
                    ),
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
    def test_candidates_include_approved_and_permitted_merge_then_review_only(self) -> None:
        state = {
            "tasks": [
                {"id": "ABC-001", "title": "Ready", "status": "review_approved", "owner": "Codex", "reviewer": "Claude"},
                {"id": "ABC-002", "title": "Todo", "status": "todo", "owner": "Codex", "reviewer": "Claude"},
                {"id": "ABC-003", "title": "Missing owner", "status": "review_approved", "reviewer": "Claude"},
                {"id": "ABC-004", "title": "Merge then review", "status": "in_progress", "owner": "Codex", "reviewer": "Codex", "merge_policy": "merge_then_review"},
                {"id": "ABC-005", "title": "Independent review", "status": "in_progress", "owner": "Codex", "reviewer": "Claude", "merge_policy": "merge_then_review"},
                {"id": "ABC-006", "title": "Unknown policy", "status": "in_progress", "owner": "Codex", "reviewer": "Codex", "merge_policy": "merge_when_green"},
                {"id": "ABC-007", "title": "Not active", "status": "todo", "owner": "Codex", "reviewer": "Codex", "merge_policy": "merge_then_review"},
            ]
        }

        candidates = auto_integrator.integration_candidates(state)

        self.assertEqual(
            [candidate.task_id for candidate in candidates],
            ["ABC-001", "ABC-004"],
        )
        self.assertEqual(candidates[0].branch, "task/ABC-001")

    def test_candidate_prefers_dedicated_integration_path_over_local_path(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "ABC-001",
                    "title": "Ready",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }
        config = {
            "coordination": {
                "repositories": {
                    "pantheon": {
                        "repo": "ajoe734/pantheon",
                        "default_branch": "dev",
                        "local_path": "/worker/source/pantheon",
                        "integration_path": "/integration/pantheon/" + "a" * 40,
                    }
                }
            }
        }

        candidate = auto_integrator.integration_candidates(state, config=config)[0]

        self.assertEqual(
            candidate.repository_root,
            Path("/integration/pantheon/" + "a" * 40),
        )
        self.assertTrue(candidate.dedicated_integration_path)

    def test_stale_first_candidate_does_not_starve_open_second_candidate(self) -> None:
        self._assert_observation_does_not_consume_limit("not_ready")

    def test_pending_first_candidate_does_not_starve_ready_second_candidate(self) -> None:
        self._assert_observation_does_not_consume_limit("waiting")

    def test_blocked_first_candidate_does_not_starve_ready_second_candidate(self) -> None:
        self._assert_observation_does_not_consume_limit("blocked")

    def _assert_observation_does_not_consume_limit(self, first_action: str) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            status_file = root / "ai-status.json"
            config_file = root / "config.json"
            status_file.write_text('{"tasks": []}\n', encoding="utf-8")
            config_file.write_text("{}\n", encoding="utf-8")
            first = auto_integrator.TaskCandidate(
                task_id="STALE-001",
                title="Stale or pending",
                owner="Codex",
                reviewer="Codex",
                branch="task/STALE-001",
                raw_task={
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Codex",
                    "merge_policy": "merge_then_review",
                },
            )
            second = auto_integrator.TaskCandidate(
                task_id="READY-002",
                title="Ready",
                owner="Codex",
                reviewer="Claude",
                branch="task/READY-002",
                raw_task={"status": "review_approved"},
            )
            outcomes = [
                auto_integrator.IntegrationResult(first.task_id, first_action, "observe"),
                auto_integrator.IntegrationResult(second.task_id, "would_merge", "ready"),
            ]
            output = io.StringIO()
            with mock.patch.object(
                auto_integrator,
                "integration_candidates",
                return_value=[first, second],
            ), mock.patch.object(
                auto_integrator,
                "integrate_candidate",
                side_effect=outcomes,
            ) as integrate, mock.patch("sys.stdout", output):
                auto_integrator.main(
                    [
                        "--no-lock",
                        "--max-tasks",
                        "1",
                        "--status-file",
                        str(status_file),
                        "--config-file",
                        str(config_file),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(integrate.call_count, 2)
            self.assertEqual(payload["candidate_count"], 2)
            self.assertEqual(
                [result["task_id"] for result in payload["results"]],
                ["STALE-001", "READY-002"],
            )

    def test_default_discovery_binds_to_canonical_status_root_over_local_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            canonical_root = tmp_path / "coordination-root"
            canonical_root.mkdir()
            canonical_status = canonical_root / "ai-status.json"
            canonical_status.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "CANONICAL-001",
                                "title": "Canonical task",
                                "status": "review_approved",
                                "owner": "Codex",
                                "reviewer": "Claude",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            canonical_audit = canonical_root / "ai-activity-log.jsonl"
            canonical_audit.write_text(
                json.dumps(
                    {
                        "ts": "2026-06-12T00:45:00Z",
                        "agent": "Claude",
                        "type": "review_approved",
                        "task_id": "CANONICAL-001",
                        "message": "Independent review approved.",
                        "review_binding": {
                            "pr": 101,
                            "head_sha": APPROVED_HEAD,
                            "head_branch": "task/CANONICAL-001",
                            "base": "dev",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            worktree_root = tmp_path / "worktree"
            worktree_root.mkdir()
            worktree_status = worktree_root / "ai-status.json"
            worktree_status.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "STALE-001",
                                "title": "Stale local task",
                                "status": "todo",
                                "owner": "Codex",
                                "reviewer": "Claude",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(canonical_root)}):
                with mock.patch.object(auto_integrator, "ROOT", worktree_root):
                    runner = FakeRunner(pr=green_pr(number=101, task_id="CANONICAL-001"))
                    with mock.patch.object(auto_integrator, "CommandRunner", return_value=runner):
                        buf = io.StringIO()
                        with mock.patch("sys.stdout", buf):
                            exit_code = auto_integrator.main(["--json", "--no-lock"])
                        self.assertEqual(exit_code, 0)
                        output = json.loads(buf.getvalue())
                        self.assertEqual(output["candidate_count"], 1)
                        self.assertEqual(output["results"][0]["task_id"], "CANONICAL-001")
                        self.assertEqual(output["results"][0]["action"], "would_merge")

    def test_explicit_status_file_overrides_canonical_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            canonical_root = tmp_path / "coordination-root"
            canonical_root.mkdir()
            canonical_status = canonical_root / "ai-status.json"
            canonical_status.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "CANONICAL-001",
                                "title": "Canonical task",
                                "status": "review_approved",
                                "owner": "Codex",
                                "reviewer": "Claude",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            explicit_dir = tmp_path / "explicit"
            explicit_dir.mkdir()
            explicit_status = explicit_dir / "explicit-status.json"
            explicit_status.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "EXPLICIT-001",
                                "title": "Explicit test task",
                                "status": "review_approved",
                                "owner": "Codex",
                                "reviewer": "Claude",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            explicit_audit = explicit_dir / "ai-activity-log.jsonl"
            explicit_audit.write_text(
                json.dumps(
                    {
                        "ts": "2026-06-12T00:45:00Z",
                        "agent": "Claude",
                        "type": "review_approved",
                        "task_id": "EXPLICIT-001",
                        "message": "Independent review approved.",
                        "review_binding": {
                            "pr": 102,
                            "head_sha": APPROVED_HEAD,
                            "head_branch": "task/EXPLICIT-001",
                            "base": "dev",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"PANTHEON_STATUS_ROOT": str(canonical_root)}):
                runner = FakeRunner(pr=green_pr(number=102, task_id="EXPLICIT-001"))
                with mock.patch.object(auto_integrator, "CommandRunner", return_value=runner):
                    buf = io.StringIO()
                    with mock.patch("sys.stdout", buf):
                        exit_code = auto_integrator.main(
                            ["--status-file", str(explicit_status), "--json", "--no-lock"]
                        )
                    self.assertEqual(exit_code, 0)
                    output = json.loads(buf.getvalue())
                    self.assertEqual(output["candidate_count"], 1)
                    self.assertEqual(output["results"][0]["task_id"], "EXPLICIT-001")
                    self.assertEqual(output["results"][0]["action"], "would_merge")

    def test_stale_local_worktree_status_without_canonical_root_has_no_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            worktree_root = tmp_path / "worktree"
            worktree_root.mkdir()
            worktree_status = worktree_root / "ai-status.json"
            worktree_status.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "LOCAL-001",
                                "title": "Local task",
                                "status": "todo",
                                "owner": "Codex",
                                "reviewer": "Claude",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            env = dict(os.environ)
            env.pop("PANTHEON_STATUS_ROOT", None)
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(auto_integrator, "ROOT", worktree_root):
                    runner = FakeRunner(pr=green_pr(number=103))
                    with mock.patch.object(auto_integrator, "CommandRunner", return_value=runner):
                        buf = io.StringIO()
                        with mock.patch("sys.stdout", buf):
                            exit_code = auto_integrator.main(["--json", "--no-lock"])
                        self.assertEqual(exit_code, 0)
                        output = json.loads(buf.getvalue())
                        self.assertEqual(output["candidate_count"], 0)


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

    def test_merge_owner_contract_is_task_dev_not_release_promotion(self) -> None:
        contract = (REPO_ROOT / "scripts/git/auto_integrator_contract.md").read_text(
            encoding="utf-8"
        )
        release_runner = (REPO_ROOT / "scripts/git/publish_promote.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("canonical `task/* -> dev` integration", contract)
        self.assertIn("separate release authority", contract)
        self.assertIn("def request_verified_auto_merge", release_runner)
        self.assertIn('["gh", "pr", "merge", promote_branch, "--auto", "--merge"]', release_runner)

    def test_execute_cannot_bypass_integration_lock(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            auto_integrator.main(["--execute", "--no-lock"])
        self.assertEqual(raised.exception.code, 2)

    def test_execute_cannot_override_promoted_smoke_policy(self) -> None:
        for override in (["--skip-smoke"], ["--smoke-command", "true"]):
            with self.subTest(override=override), self.assertRaises(SystemExit) as raised:
                auto_integrator.main(["--execute", *override])
            self.assertEqual(raised.exception.code, 2)

    def test_execute_authority_binds_watchdog_runtime_status_and_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            head = "b" * 40
            command_root = root / "command-runtimes" / head
            command_root.mkdir(parents=True)
            status_root = root / "status"
            (status_root / ".orchestrator").mkdir(parents=True)
            status_file = status_root / "ai-status.json"
            status_file.write_text('{"tasks": []}\n', encoding="utf-8")
            live_config = root / "runtime" / "live.json"
            live_config.parent.mkdir()
            payload = {
                "paths": {"status_file": str(status_file)},
                "watchdog": {
                    "supervisor_command": [
                        sys.executable,
                        str(command_root / ".orchestrator" / "supervisor.py"),
                        "--config",
                        str(live_config),
                    ]
                },
                "branch_workflow": {
                    "auto_integrator": {
                        "lock_file": ".orchestrator/auto-integrator.lock"
                    }
                },
            }
            live_config.write_text(json.dumps(payload), encoding="utf-8")
            runner = FakeRunner(git_head=head)

            resolved = auto_integrator.resolve_execute_authority(
                live_config, runner, command_root=command_root
            )
            self.assertEqual(resolved[0], status_file)
            self.assertEqual(
                resolved[2].lock_path.resolve(),
                (status_root / auto_integrator.DEFAULT_LOCK).resolve(),
            )
            self.assertEqual(resolved[2].command_runtime_sha, head)

            forged = json.loads(json.dumps(payload))
            forged["branch_workflow"]["auto_integrator"]["lock_file"] = str(
                root / "private.lock"
            )
            live_config.write_text(json.dumps(forged), encoding="utf-8")
            with self.assertRaisesRegex(
                auto_integrator.ExecuteAuthorityError, "lock must be canonical"
            ):
                auto_integrator.resolve_execute_authority(
                    live_config, runner, command_root=command_root
                )

    def test_live_execute_requires_explicit_dedicated_integration_path(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
            repository_root=Path("/worker/source/pantheon"),
            dedicated_integration_path=False,
        )
        runner = FakeRunner(pr=green_pr())

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            require_dedicated_integration_path=True,
            open_unblock=False,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("no explicit integration_path", result.detail)
        self.assertEqual(runner.commands, [])

    def test_execute_preflight_rejects_linked_worktree_common_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-b", "dev"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            (source / "README").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "test"], cwd=source, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
            ).stdout.strip()
            linked = root / head
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(linked), head],
                cwd=source,
                check=True,
                capture_output=True,
            )
            candidate = auto_integrator.TaskCandidate(
                "ABC-001", "Ready", "Codex", "Claude", "task/ABC-001",
                repository_root=linked,
                dedicated_integration_path=True,
            )

            problem = auto_integrator.preflight_repository(
                candidate,
                auto_integrator.CommandRunner(),
                linked,
                require_standalone_integration=True,
            )
            self.assertEqual(problem[0], "integration-checkout-not-standalone")

    def test_execute_preflight_accepts_two_standalone_versioned_clones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for repository_id, slug in (
                ("pantheon", "ajoe734/pantheon"),
                ("execute_plans", "ajoe734/execute-plans"),
            ):
                source = root / f"{repository_id}-source"
                source.mkdir()
                subprocess.run(["git", "init", "-b", "dev"], cwd=source, check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
                subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
                (source / "README").write_text(repository_id + "\n", encoding="utf-8")
                subprocess.run(["git", "add", "README"], cwd=source, check=True)
                subprocess.run(["git", "commit", "-m", "test"], cwd=source, check=True, capture_output=True)
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
                ).stdout.strip()
                destination_parent = root / f"{repository_id}-integration"
                destination_parent.mkdir()
                destination = destination_parent / head
                subprocess.run(
                    ["git", "clone", "--no-local", str(source), str(destination)],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(["git", "checkout", "--detach", head], cwd=destination, check=True, capture_output=True)
                candidate = auto_integrator.TaskCandidate(
                    repository_id + "-TASK", "Ready", "Codex", "Claude", "task/TASK",
                    repository_id=repository_id,
                    repository_slug=slug,
                    repository_root=destination,
                    dedicated_integration_path=True,
                )
                self.assertIsNone(
                    auto_integrator.preflight_repository(
                        candidate,
                        auto_integrator.CommandRunner(),
                        destination,
                        require_standalone_integration=True,
                    )
                )

    def test_main_execute_rejects_cli_status_and_config_authority_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            status_file = root / "ai-status.json"
            config_file = root / "config.json"
            with self.assertRaises(SystemExit) as raised:
                auto_integrator.main(
                    [
                        "--execute",
                        "--status-file",
                        str(status_file),
                        "--config-file",
                        str(config_file),
                    ]
                )
            self.assertEqual(raised.exception.code, 2)

    def test_merge_then_review_open_pr_flows_through_locked_runner_to_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            coordination_root = root / "coordination"
            repository_root = root / APPROVED_HEAD
            coordination_root.mkdir()
            subprocess.run(
                ["git", "init", "-b", "dev", str(repository_root)],
                check=True,
                capture_output=True,
            )
            status_file = coordination_root / "ai-status.json"
            config_file = coordination_root / "config.json"
            lock_path = coordination_root / "auto-integrator.lock"
            status_file.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "ABC-001",
                                "title": "Ready to integrate",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Codex",
                                "merge_policy": "merge_then_review",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config_file.write_text(
                json.dumps(
                    {
                        "paths": {"status_file": str(status_file)},
                        "coordination": {
                            "repositories": {
                                "pantheon": {
                                    "local_path": str(repository_root),
                                    "integration_path": str(repository_root),
                                }
                            }
                        },
                        "branch_workflow": {
                            "auto_integrator": {
                                "lock_file": str(lock_path),
                                "max_tasks_per_run": 1,
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            runner = FakeRunner(
                pr=green_pr(),
                check_filesystem_paths=True,
                git_head=APPROVED_HEAD,
            )
            output = io.StringIO()

            with mock.patch.object(
                auto_integrator, "CommandRunner", return_value=runner
            ), mock.patch.object(
                auto_integrator,
                "resolve_execute_authority",
                return_value=(
                    status_file,
                    coordination_root,
                    auto_integrator.Settings(
                        lock_path=lock_path,
                        max_tasks_per_run=1,
                        smoke_commands=("true",),
                    ),
                    json.loads(config_file.read_text(encoding="utf-8")),
                ),
            ), mock.patch("sys.stdout", output):
                returncode = auto_integrator.main(
                    [
                        "--execute",
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(returncode, 0)
            self.assertEqual(payload["candidate_count"], 1)
            self.assertEqual(payload["results"][0]["action"], "merged")
            self.assertIn(
                "left ABC-001 at canonical status in_progress for post-merge review/finalization",
                payload["results"][0]["detail"],
            )
            self.assertIn(
                [
                    "gh",
                    "api",
                    "--method",
                    "PUT",
                    "repos/ajoe734/pantheon/pulls/44/merge",
                    "-f",
                    f"sha={APPROVED_HEAD}",
                    "-f",
                    "merge_method=merge",
                ],
                runner.commands,
            )
            self.assertIn(["sh", "-lc", "true"], runner.commands)
            self.assertFalse(
                any(
                    command[:3] == ["gh", "pr", "merge"] and "--auto" in command
                    for command in runner.commands
                )
            )
            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8"))["state"],
                "released",
            )

    def test_merge_command_uses_synchronous_exact_head_rest_endpoint(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        command = auto_integrator.merge_command(
            candidate, 44, exact_head=APPROVED_HEAD
        )
        self.assertEqual(
            command,
            [
                "gh",
                "api",
                "--method",
                "PUT",
                "repos/ajoe734/pantheon/pulls/44/merge",
                "-f",
                f"sha={APPROVED_HEAD}",
                "-f",
                "merge_method=merge",
            ],
        )

    def test_non_merge_method_is_rejected_at_config_load(self) -> None:
        with mock.patch.object(
            auto_integrator,
            "load_json",
            return_value={
                "branch_workflow": {"auto_integrator": {"merge_method": "squash"}}
            },
        ):
            with self.assertRaisesRegex(ValueError, "requires merge commits"):
                auto_integrator.load_settings()


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
        self.assertTrue(
            any("actions/workflows?per_page=100" in " ".join(command) for command in runner.commands)
        )
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
        self.assertTrue(
            any(
                command[:4] == ["gh", "api", "--method", "PUT"]
                and "/pulls/44/merge" in " ".join(command)
                for command in runner.commands
            )
        )

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

    def test_dry_run_reports_operator_acceptance_without_calling_it_reviewer_approval(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            FakeRunner(pr=green_pr()),
            execute=False,
            gate=operator_accepted_gate(),
        )

        self.assertEqual(result.action, "would_merge")
        self.assertIn("accepted by Human/Ops", result.detail)
        self.assertNotIn("approved by Claude", result.detail)

    def test_red_checks_open_unblock_in_execute_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch.dict(
            os.environ, {}, clear=True
        ):
            status_root = Path(tmp_dir)
            candidate = auto_integrator.TaskCandidate(
                task_id="ABC-001",
                title="Ready",
                owner="Codex",
                reviewer="Claude",
                branch="task/ABC-001",
                raw_task={
                    "generation": 7,
                    "delivery_binding": {"pr": 44, "head_sha": APPROVED_HEAD},
                },
            )
            pr = green_pr()
            pr["statusCheckRollup"] = [{"name": "ci", "conclusion": "FAILURE"}]
            runner = FakeRunner(pr=pr)

            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(
                    status_identity_sha256="d" * 64,
                    command_runtime_sha="b" * 40,
                ),
                runner,
                status_root=status_root,
                execute=True,
                gate=approved_gate(),
            )

            self.assertEqual(result.action, "blocked")
            self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-ABC-001-CI-RED")
            requests = list((status_root / auto_integrator.UNBLOCK_REQUEST_INBOX).glob("*.json"))
            self.assertEqual(len(requests), 1)
            request = json.loads(requests[0].read_text(encoding="utf-8"))
            self.assertEqual(request["command_runtime_sha"], "b" * 40)
            self.assertEqual(request["pr"], 44)
            self.assertEqual(request["head_sha"], APPROVED_HEAD)
            self.assertEqual(request["source_task_generation"], 7)
            self.assertFalse(any("scripts/ai_status.py" in " ".join(command) for command in runner.commands))
            self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

            auto_integrator.open_unblock_task(
                candidate,
                "ci-red",
                result.detail,
                auto_integrator.Settings(
                    status_identity_sha256="d" * 64,
                    command_runtime_sha="b" * 40,
                ),
                runner,
                root=status_root,
                execute=True,
            )
            self.assertEqual(len(list((status_root / auto_integrator.UNBLOCK_REQUEST_INBOX).glob("*.json"))), 1)

    def test_merge_then_review_exact_head_conflict_opens_unblock_without_merge(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(
            pr=green_pr(),
            merge_base_returncode=1,
            ephemeral_merge_returncode=1,
        )

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
                            "status": "in_progress",
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
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-ABC-001-EXACT-HEAD-MERGE-CONFLICT",
        )
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_unblock_request_write_failure_does_not_abort_candidate_result(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
            raw_task={
                "generation": 1,
                "delivery_binding": {"pr": 44, "head_sha": APPROVED_HEAD},
            },
        )
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(auto_integrator, "_write_unblock_request", side_effect=OSError("disk full")),
        ):
            result = auto_integrator.open_unblock_task(
                candidate,
                "ci-red",
                "CI failed",
                auto_integrator.Settings(
                    status_identity_sha256="d" * 64,
                    command_runtime_sha="b" * 40,
                ),
                FakeRunner(),
                root=REPO_ROOT,
                execute=True,
            )

        self.assertEqual(result, "INTEGRATION-UNBLOCK-ABC-001-CI-RED")

    def test_shared_unblock_ids_are_bounded_and_long_reasons_do_not_collide(self) -> None:
        source = "OPS-AUTO-INTEGRATOR-STATUS-AUTHORITY-PREREQUISITE-001"
        first = auto_integrator.unblock_task_id(
            source, "review-gate-approval-head-" + "a" * 80
        )
        second = auto_integrator.unblock_task_id(
            source, "review-gate-approval-head-" + "b" * 80
        )

        self.assertLessEqual(len(first), 96)
        self.assertLessEqual(len(second), 96)
        self.assertNotEqual(first, second)
        self.assertEqual(
            first,
            auto_integrator.unblock_contract.task_id(
                source, "review-gate-approval-head-" + "a" * 80
            ),
        )

    def test_clean_disposable_exact_head_merge_uses_scoped_identity_and_lands_head(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(), merge_base_returncode=1)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "merged")
        merge_commands = [
            command
            for command in runner.commands
            if command[:1] == ["git"] and "merge" in command and "--abort" not in command
        ]
        self.assertEqual(len(merge_commands), 1)
        self.assertEqual(
            merge_commands[0][:5],
            [
                "git",
                "-c",
                "user.name=Pantheon Auto Integrator",
                "-c",
                "user.email=pantheon-auto-integrator@noreply.local",
            ],
        )
        self.assertIn("--no-edit", merge_commands[0])
        self.assertTrue(
            any(command[:4] == ["gh", "api", "--method", "PUT"] for command in runner.commands)
        )

    def test_execute_merges_when_rest_endpoint_returns_merged_true(self) -> None:
        """After merging an exact approved head, the integrator leaves the task
        review_approved for supervisor owned_finalize dispatch and never calls
        owner-only done without a lease."""

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
        self.assertIn("left ABC-001 in review_approved for owner finalization", result.detail)
        self.assertTrue(
            any(command[:4] == ["gh", "api", "--method", "PUT"] for command in runner.commands)
        )
        self.assertFalse(
            any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands)
        )
        self.assertFalse(
            any("scripts/ai_status.py" in " ".join(command) and "assign" in command for command in runner.commands)
        )

    def test_operator_exact_head_merge_never_claims_reviewer_or_owner_finalization(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
            raw_task={
                "status": "review_approved",
                "operator_acceptance": {"mode": "operator_exact_head"},
            },
        )

        detail = auto_integrator.post_merge_task_handoff(candidate)

        self.assertIn("Human/Ops exact-head closeout", detail)
        self.assertIn("no owner finalization", detail)

    def test_final_revalidation_blocks_when_canonical_reviewer_changes(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44))
        with tempfile.TemporaryDirectory() as tmp_dir:
            canonical_state = Path(tmp_dir) / "ai-status.json"
            canonical_state.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "ABC-001",
                                "title": "Ready",
                                "status": "review_approved",
                                "owner": "Codex",
                                "reviewer": "Gemini",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(smoke_commands=("true",)),
                runner,
                canonical_state_file=canonical_state,
                execute=True,
                gate=approved_gate(),
            )

        self.assertEqual(result.action, "blocked")
        self.assertIn("Canonical merge authority changed", result.detail)
        self.assertFalse(
            any(command[:4] == ["gh", "api", "--method", "PUT"] for command in runner.commands)
        )

    def test_final_revalidation_blocks_when_pr_head_changes_after_smoke(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        initial_pr = green_pr(number=44)
        moved_pr = {**initial_pr, "headRefOid": "f" * 40}
        runner = FakeRunner(pr=initial_pr)

        with mock.patch.object(
            auto_integrator,
            "fetch_pr_for_task",
            side_effect=[initial_pr, moved_pr],
        ) as fetch:
            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(smoke_commands=("true",)),
                runner,
                execute=True,
                gate=approved_gate(),
            )

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result.action, "blocked")
        self.assertIn("Canonical merge authority changed", result.detail)
        self.assertFalse(
            any(command[:4] == ["gh", "api", "--method", "PUT"] for command in runner.commands)
        )

    def test_final_github_timeouts_release_authority_locks_before_unblock(self) -> None:
        matchers = {
            "list": lambda command: command[:3] == ["gh", "pr", "list"],
            "view": lambda command: command[:3] == ["gh", "pr", "view"],
            "graphql": lambda command: command[:3] == ["gh", "api", "graphql"],
        }

        for label, matcher in matchers.items():
            with self.subTest(operation=label), tempfile.TemporaryDirectory() as tmp_dir:
                class TimeoutRunner(FakeRunner):
                    def run(self, args: Sequence[str], **kwargs: Any):  # type: ignore[override]
                        command = [str(item) for item in args]
                        if self.default_timeout is not None and matcher(command):
                            self.commands.append(command)
                            raise subprocess.TimeoutExpired(command, self.default_timeout)
                        return super().run(args, **kwargs)

                status_root = Path(tmp_dir)
                state_file = status_root / "ai-status.json"
                state_file.write_text(
                    json.dumps(approved_gate().state) + "\n", encoding="utf-8"
                )
                timed_pr = green_pr()
                if label == "graphql":
                    timed_pr["url"] = "https://github.com/ajoe734/pantheon/pull/44"
                runner = TimeoutRunner(pr=timed_pr)
                events: list[str] = []
                held = {"task": False, "activity": False}

                @contextmanager
                def task_lock(*_args: Any, **_kwargs: Any):
                    held["task"] = True
                    events.append("task_enter")
                    try:
                        yield
                    finally:
                        events.append("task_exit")
                        held["task"] = False

                @contextmanager
                def activity_lock(*_args: Any, **_kwargs: Any):
                    self.assertTrue(held["task"])
                    held["activity"] = True
                    events.append("activity_enter")
                    try:
                        yield
                    finally:
                        events.append("activity_exit")
                        held["activity"] = False

                def unblock(*_args: Any, **_kwargs: Any) -> str:
                    self.assertFalse(held["task"] or held["activity"])
                    events.append("unblock")
                    return "UNBLOCK"

                with mock.patch.object(
                    auto_integrator.orchestrator_common,
                    "canonical_task_state_lock_file",
                    task_lock,
                ), mock.patch.object(
                    auto_integrator.orchestrator_common,
                    "activity_audit_lock_file",
                    activity_lock,
                ), mock.patch.object(
                    auto_integrator, "open_unblock_task", side_effect=unblock
                ):
                    result = auto_integrator.integrate_candidate(
                        auto_integrator.TaskCandidate(
                            task_id="ABC-001",
                            title="Ready",
                            owner="Codex",
                            reviewer="Claude",
                            branch="task/ABC-001",
                        ),
                        auto_integrator.Settings(smoke_commands=("true",)),
                        runner,
                        status_root=status_root,
                        canonical_state_file=state_file,
                        execute=True,
                        gate=approved_gate(),
                    )

                self.assertEqual(result.action, "blocked")
                self.assertEqual(result.unblock_task_id, "UNBLOCK")
                self.assertEqual(
                    events,
                    ["task_enter", "activity_enter", "activity_exit", "task_exit", "unblock"],
                )
                self.assertIsNone(runner.default_timeout)

    def test_execute_waits_when_rest_endpoint_refuses_direct_merge(self) -> None:
        """A refusal never turns into a merge queue or auto-merge request."""

        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44), merge_lands_synchronously=False)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("true",)),
            runner,
            execute=True,
            gate=approved_gate(),
        )

        self.assertEqual(result.action, "waiting")
        self.assertFalse(result.dry_run)
        self.assertTrue(
            any(command[:4] == ["gh", "api", "--method", "PUT"] for command in runner.commands)
        )
        self.assertFalse(
            any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands)
        )
        self.assertFalse(
            any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands)
        )

    def test_execute_already_merged_pr_leaves_task_for_owner_finalization_without_unblock_or_done(self) -> None:
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

        self.assertEqual(result.action, "already_merged")
        self.assertEqual(result.pr_number, 55)
        self.assertIn("left ABC-001 in review_approved for owner finalization", result.detail)
        self.assertFalse(any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands))
        self.assertFalse(any("scripts/ai_status.py" in " ".join(command) and "assign" in command for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))
        self.assertIn(["git", "merge-base", "--is-ancestor", "merge123", "origin/dev"], runner.commands)

    def test_dry_run_already_merged_pr(self) -> None:
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
            execute=False,
            gate=approved_gate(pr_number=55),
        )

        self.assertEqual(result.action, "already_merged")
        self.assertTrue(result.dry_run)
        self.assertIn("left ABC-001 in review_approved for owner finalization", result.detail)
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_already_merged_reruns_are_idempotent_and_do_not_create_unblock_tasks(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=None, merged_pr=merged_pr())
        gate = approved_gate(pr_number=55)

        first_result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=gate,
        )
        self.assertEqual(first_result.action, "already_merged")
        self.assertIsNone(first_result.unblock_task_id)

        second_result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=gate,
        )
        self.assertEqual(second_result.action, "already_merged")
        self.assertIsNone(second_result.unblock_task_id)
        self.assertFalse(any("scripts/ai_status.py" in " ".join(cmd) and "assign" in cmd for cmd in runner.commands))

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


def green_ep_pr(number: int = 99, *, task_id: str = "FE-001") -> dict[str, Any]:
    return {
        "number": number,
        "title": "FE Task PR",
        "url": f"https://github.com/ajoe734/execute-plans/pull/{number}",
        "headRefName": f"task/{task_id}",
        "headRefOid": APPROVED_HEAD,
        "baseRefName": "dev",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "commits": [{"oid": APPROVED_HEAD, "committedDate": "2026-06-12T00:30:00Z"}],
        "statusCheckRollup": [
            {"name": "Commit trailers", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "Frontend tests", "state": "SUCCESS"},
        ],
    }


class CrossRepoIntegrationTests(unittest.TestCase):
    def test_candidate_derives_execute_plans_scope_from_target_repo_and_artifacts(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FE-001",
                    "title": "FE task",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "target_repo": "execute_plans",
                    "artifacts": ["execute-plans/src/App.tsx"],
                },
                {
                    "id": "PANTHEON-001",
                    "title": "Pantheon task",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "artifacts": ["services/telemetry/main.py"],
                },
            ]
        }
        candidates = auto_integrator.integration_candidates(state)
        self.assertEqual(len(candidates), 2)
        fe_cand = candidates[0]
        self.assertEqual(fe_cand.task_id, "FE-001")
        self.assertEqual(fe_cand.repository_id, "execute_plans")
        self.assertEqual(fe_cand.repository_slug, "ajoe734/execute-plans")
        self.assertEqual(fe_cand.target_branch, "dev")
        self.assertIsNone(fe_cand.scope_error)

        pan_cand = candidates[1]
        self.assertEqual(pan_cand.task_id, "PANTHEON-001")
        self.assertEqual(pan_cand.repository_id, "pantheon")
        self.assertEqual(pan_cand.repository_slug, "ajoe734/pantheon")
        self.assertEqual(pan_cand.target_branch, "dev")
        self.assertIsNone(pan_cand.scope_error)

    def test_candidate_captures_scope_error_for_invalid_target_repo(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "BAD-001",
                    "title": "Bad target repo",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "target_repo": "nonexistent_repo",
                }
            ]
        }
        candidates = auto_integrator.integration_candidates(state)
        self.assertEqual(len(candidates), 1)
        self.assertIsNotNone(candidates[0].scope_error)
        self.assertIn("unrecognized target_repo", candidates[0].scope_error or "")

    def test_candidate_captures_scope_error_for_conflicting_multi_repo_artifacts(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "MULTI-001",
                    "title": "Multi repo artifacts",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "artifacts": ["execute-plans/src/a.ts", "lean-platform/b.py"],
                }
            ]
        }
        candidates = auto_integrator.integration_candidates(state)
        self.assertEqual(len(candidates), 1)
        self.assertIsNotNone(candidates[0].scope_error)
        self.assertIn("multiple non-Pantheon repositories", candidates[0].scope_error or "")

    def test_execute_plans_dry_run_would_merge(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        pr = green_ep_pr(number=99, task_id="FE-001")
        runner = FakeRunner(pr=pr)
        gate = approved_gate(task_id="FE-001", pr_number=99)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("npm test",)),
            runner,
            execute=False,
            gate=gate,
        )

        self.assertEqual(result.action, "would_merge")
        self.assertTrue(result.dry_run)
        self.assertEqual(result.pr_number, 99)
        self.assertIn("would merge", result.detail)
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in runner.commands))
        self.assertTrue(any(cmd[:3] == ["gh", "pr", "list"] for cmd in runner.commands))

    def test_execute_plans_execute_merges_in_target_repository(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        pr = green_ep_pr(number=99, task_id="FE-001")
        runner = FakeRunner(pr=pr)
        gate = approved_gate(task_id="FE-001", pr_number=99)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(smoke_commands=("npm test",)),
            runner,
            execute=True,
            gate=gate,
        )

        self.assertEqual(result.action, "merged")
        self.assertFalse(result.dry_run)
        self.assertEqual(result.pr_number, 99)
        self.assertIn("Merged the reviewer-approved head", result.detail)
        self.assertIn("into dev", result.detail)
        self.assertTrue(
            any(
                cmd[:4] == ["gh", "api", "--method", "PUT"]
                and "repos/ajoe734/execute-plans/pulls/99/merge" in cmd
                and f"sha={APPROVED_HEAD}" in cmd
                for cmd in runner.commands
            )
        )

    def test_execute_plans_already_merged_pr_reconciles(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        pr = green_ep_pr(number=99, task_id="FE-001")
        pr["state"] = "MERGED"
        pr["mergeCommit"] = {"oid": "merge999"}
        pr["mergedAt"] = "2026-06-12T01:01:07Z"
        runner = FakeRunner(pr=None, merged_pr=pr)
        gate = approved_gate(task_id="FE-001", pr_number=99)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=gate,
        )

        self.assertEqual(result.action, "already_merged")
        self.assertEqual(result.pr_number, 99)
        self.assertIn("already merged into dev", result.detail)
        self.assertIn(["git", "merge-base", "--is-ancestor", "merge999", "origin/dev"], runner.commands)
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in runner.commands))

    def test_wrong_repository_slug_in_pr_fails_closed(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        # PR is from pantheon instead of execute-plans
        pr = green_pr(number=44, task_id="FE-001")
        pr["url"] = "https://github.com/ajoe734/pantheon/pull/44"
        runner = FakeRunner(pr=pr)
        gate = approved_gate(task_id="FE-001", pr_number=44)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=gate,
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("repository_mismatch", result.detail)
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-FE-001-REPOSITORY-MISMATCH")
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in runner.commands))

    def test_pantheon_task_rejects_execute_plans_pr(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Backend Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
            repository_id="pantheon",
            repository_slug="ajoe734/pantheon",
            repository_root=REPO_ROOT,
            target_branch="dev",
        )
        # PR is from execute-plans instead of pantheon
        pr = green_ep_pr(number=99, task_id="ABC-001")
        runner = FakeRunner(pr=pr)
        gate = approved_gate(task_id="ABC-001", pr_number=99)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=gate,
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("repository_mismatch", result.detail)
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in runner.commands))

    def test_invalid_scope_error_blocks_and_opens_unblock_task(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="BAD-001",
            title="Bad scope",
            owner="Codex",
            reviewer="Claude",
            branch="task/BAD-001",
            scope_error="Task BAD-001 specifies unrecognized target_repo: 'unknown'",
        )
        runner = FakeRunner(pr=green_pr())
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("unrecognized target_repo", result.detail)
        self.assertEqual(result.unblock_task_id, "INTEGRATION-UNBLOCK-BAD-001-INVALID-REPOSITORY-SCOPE")

    def test_execute_plans_exact_head_mismatch_fails_closed(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        pr = green_ep_pr(number=99, task_id="FE-001")
        pr["headRefOid"] = "e" * 40
        runner = FakeRunner(pr=pr)
        gate = approved_gate(task_id="FE-001", pr_number=99)

        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
            gate=gate,
        )

    def test_pantheon_status_root_resolves_execute_plans_path_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            coordination_root = tmp_path / "coordination-root"
            coordination_root.mkdir()
            config = {
                "paths": {"status_file": str(coordination_root / "ai-status.json")},
                "coordination": {
                    "repositories": {
                        "execute_plans": {"local_path": "../code/execute-plans"}
                    }
                },
            }
            state = {
                "tasks": [
                    {
                        "id": "FE-001",
                        "title": "FE Task",
                        "status": "review_approved",
                        "owner": "Codex",
                        "reviewer": "Claude",
                        "target_repo": "execute_plans",
                        "artifacts": ["execute-plans/src/App.tsx"],
                    }
                ]
            }
            candidates = auto_integrator.integration_candidates(
                state, config=config, status_root=coordination_root
            )
            self.assertEqual(len(candidates), 1)
            expected_root = (coordination_root / "../code/execute-plans").resolve()
            self.assertEqual(candidates[0].repository_root, expected_root)
            self.assertIsNone(candidates[0].scope_error)

    def test_missing_repository_checkout_fails_closed(self) -> None:
        missing_root = Path("/nonexistent/custom/path/to/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=missing_root,
            target_branch="dev",
        )
        runner = FakeRunner(check_filesystem_paths=True)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("does not exist", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-MISSING-REPOSITORY-CHECKOUT",
        )

    def test_invalid_git_repository_fails_closed(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        runner = FakeRunner(git_toplevel_returncode=1)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("is not a git repository", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-INVALID-GIT-REPOSITORY",
        )

    def test_missing_git_common_dir_fails_closed(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=Path("/fake/execute-plans"),
            target_branch="dev",
        )
        runner = FakeRunner(git_common_dir_returncode=1)

        error = auto_integrator.preflight_repository(
            candidate, runner, candidate.repository_root
        )

        self.assertIsNotNone(error)
        self.assertEqual(error[0], "invalid-git-common-dir")
        self.assertFalse(any(command[:3] == ["gh", "pr", "list"] for command in runner.commands))

    def test_dirty_repository_checkout_fails_closed(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=Path("/fake/execute-plans"),
            target_branch="dev",
        )
        runner = FakeRunner(git_status_output=" M src/App.tsx\n")

        error = auto_integrator.preflight_repository(
            candidate, runner, candidate.repository_root
        )

        self.assertIsNotNone(error)
        self.assertEqual(error[0], "dirty-repository-checkout")
        self.assertIn("src/App.tsx", error[1])

    def test_unwritable_checkout_and_git_common_dir_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "execute-plans"
            subprocess.run(
                ["git", "init", "-b", "dev", str(repo)],
                check=True,
                capture_output=True,
            )
            candidate = auto_integrator.TaskCandidate(
                task_id="FE-001",
                title="FE Task",
                owner="Codex",
                reviewer="Claude",
                branch="task/FE-001",
                repository_id="execute_plans",
                repository_slug="ajoe734/execute-plans",
                repository_root=repo,
                target_branch="dev",
            )
            runner = FakeRunner(check_filesystem_paths=True)

            with mock.patch.object(
                auto_integrator,
                "_directory_is_writable",
                side_effect=lambda path: path != repo,
            ):
                checkout_error = auto_integrator.preflight_repository(
                    candidate, runner, repo
                )
            self.assertIsNotNone(checkout_error)
            self.assertEqual(checkout_error[0], "repository-checkout-not-writable")

            git_common_dir = (repo / ".git").resolve()
            with mock.patch.object(
                auto_integrator,
                "_directory_is_writable",
                side_effect=lambda path: path.resolve() != git_common_dir,
            ):
                common_error = auto_integrator.preflight_repository(
                    candidate, runner, repo
                )
            self.assertIsNotNone(common_error)
            self.assertEqual(common_error[0], "git-common-dir-not-writable")

    def test_mismatched_origin_remote_fails_closed(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        runner = FakeRunner(origin_remote_slug="ajoe734/other-repo")
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("repository origin remote mismatch", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-REPOSITORY-ORIGIN-MISMATCH",
        )

    def test_command_runner_handles_oserror_cleanly(self) -> None:
        runner = auto_integrator.CommandRunner()
        nonexistent = Path("/nonexistent/directory/path/for/test")

        res = runner.run(["git", "status"], cwd=nonexistent, check=False)
        self.assertEqual(res.returncode, 127)
        self.assertIn("No such file or directory", res.stderr)

        with self.assertRaises(auto_integrator.CommandFailure) as ctx:
            runner.run(["git", "status"], cwd=nonexistent, check=True)
        self.assertEqual(ctx.exception.returncode, 127)

    def test_real_filesystem_execute_plans_candidate_preflights_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ep_dir = Path(tmp_dir) / "execute-plans"
            ep_dir.mkdir()
            subprocess.run(["git", "init", "-b", "dev", str(ep_dir)], check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/ajoe734/execute-plans.git"],
                cwd=ep_dir,
                check=True,
                capture_output=True,
            )
            candidate = auto_integrator.TaskCandidate(
                task_id="FE-001",
                title="FE Task",
                owner="Codex",
                reviewer="Claude",
                branch="task/FE-001",
                repository_id="execute_plans",
                repository_slug="ajoe734/execute-plans",
                repository_root=ep_dir,
                target_branch="dev",
            )
            pr = green_ep_pr(number=99, task_id="FE-001")
            runner = FakeRunner(pr=pr, check_filesystem_paths=True)
            gate = approved_gate(task_id="FE-001", pr_number=99)

            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(smoke_commands=("true",)),
                runner,
                execute=True,
                gate=gate,
            )
            self.assertEqual(result.action, "merged")
            self.assertEqual(result.pr_number, 99)

    def test_non_absolute_repository_root_fails_closed(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=Path("relative/path/to/execute-plans"),
            target_branch="dev",
        )
        runner = FakeRunner()
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("must be an absolute path", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-INVALID-REPOSITORY-ROOT",
        )

    def test_missing_repository_slug_fails_closed(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="",
            repository_root=ep_root,
            target_branch="dev",
        )
        runner = FakeRunner()
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("has no configured GitHub slug", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-MISSING-REPOSITORY-SLUG",
        )

    def test_origin_remote_command_failure_fails_closed(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        runner = FakeRunner(origin_remote_returncode=1)
        result = auto_integrator.integrate_candidate(
            candidate,
            auto_integrator.Settings(),
            runner,
            execute=True,
        )
        self.assertEqual(result.action, "blocked")
        self.assertIn("origin remote is unavailable", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-MISSING-ORIGIN-REMOTE",
        )

    def test_pr_lookup_failure_fails_closed_without_crashing(self) -> None:
        ep_root = Path("/fake/execute-plans")
        candidate = auto_integrator.TaskCandidate(
            task_id="FE-001",
            title="FE Task",
            owner="Codex",
            reviewer="Claude",
            branch="task/FE-001",
            repository_id="execute_plans",
            repository_slug="ajoe734/execute-plans",
            repository_root=ep_root,
            target_branch="dev",
        )
        runner = FakeRunner()
        with mock.patch.object(
            auto_integrator,
            "fetch_pr_for_task",
            side_effect=auto_integrator.CommandFailure(["gh", "pr", "list"], 1, "network timeout"),
        ):
            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(),
                runner,
                execute=True,
            )
        self.assertEqual(result.action, "blocked")
        self.assertIn("Failed to inspect PR", result.detail)
        self.assertEqual(
            result.unblock_task_id,
            "INTEGRATION-UNBLOCK-FE-001-PR-LOOKUP-FAILED",
        )


class AutoIntegratorProcessE2ETests(unittest.TestCase):
    def _run_child(self, body: str, *arguments: str) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), environment.get("PYTHONPATH", "")]
        )
        result = subprocess.run(
            [sys.executable, "-c", body, *arguments],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        return json.loads(result.stdout)

    def test_fresh_process_exact_merge_and_unknown_outcome_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_state = root / "ai-status.json"
            canonical_state.write_text(
                json.dumps(approved_gate().state) + "\n", encoding="utf-8"
            )
            outcome = root / "github-outcome.json"

            merged = self._run_child(
                "import json,sys; from pathlib import Path; "
                "from scripts.git import auto_integrator as a; "
                "from scripts.git.test_auto_integrator import FakeRunner,approved_gate,green_pr; "
                "c=a.TaskCandidate(task_id='ABC-001',title='Ready',owner='Codex',reviewer='Claude',branch='task/ABC-001'); "
                "r=FakeRunner(pr=green_pr(number=44)); "
                "x=a.integrate_candidate(c,a.Settings(smoke_commands=('true',)),r,canonical_state_file=Path(sys.argv[1]),execute=True,open_unblock=False,gate=approved_gate()); "
                "print(json.dumps({'action':x.action,'commands':r.commands}))",
                str(canonical_state),
            )
            self.assertEqual(merged["action"], "merged")
            merge_commands = [
                command
                for command in merged["commands"]
                if command[:4] == ["gh", "api", "--method", "PUT"]
                and command[4].endswith("/pulls/44/merge")
            ]
            self.assertEqual(len(merge_commands), 1)
            self.assertIn("sha=" + "a" * 40, merge_commands[0])
            self.assertIn("merge_method=merge", merge_commands[0])

            timed_out = self._run_child(
                "import json,subprocess,sys; from pathlib import Path; "
                "from scripts.git import auto_integrator as a; "
                "from scripts.git.test_auto_integrator import FakeRunner,approved_gate,green_pr; "
                "out=Path(sys.argv[2]); "
                "exec('class TimeoutAfterServerMerge(FakeRunner):\\n"
                " def run(self,args,**kwargs):\\n"
                "  command=[str(v) for v in args]\\n"
                "  if command[:4]==[\\\"gh\\\",\\\"api\\\",\\\"--method\\\",\\\"PUT\\\"] and command[4].endswith(\\\"/pulls/44/merge\\\"):\\n"
                "   self.commands.append(command); out.write_text(json.dumps({\\\"merged\\\":True})); raise subprocess.TimeoutExpired(command,kwargs.get(\\\"timeout\\\") or 30)\\n"
                "  return super().run(args,**kwargs)'); "
                "c=a.TaskCandidate(task_id='ABC-001',title='Ready',owner='Codex',reviewer='Claude',branch='task/ABC-001'); "
                "r=TimeoutAfterServerMerge(pr=green_pr(number=44)); "
                "x=a.integrate_candidate(c,a.Settings(smoke_commands=('true',)),r,canonical_state_file=Path(sys.argv[1]),execute=True,open_unblock=False,gate=approved_gate()); "
                "print(json.dumps({'action':x.action,'detail':x.detail,'commands':r.commands}))",
                str(canonical_state),
                str(outcome),
            )
            self.assertEqual(timed_out["action"], "blocked")
            self.assertIn("outcome is unknown", timed_out["detail"])
            self.assertEqual(json.loads(outcome.read_text(encoding="utf-8")), {"merged": True})

            reconciled = self._run_child(
                "import json,sys; from pathlib import Path; "
                "from scripts.git import auto_integrator as a; "
                "from scripts.git.test_auto_integrator import FakeRunner,approved_gate,green_pr; "
                "p=green_pr(number=44); p.update({'state':'MERGED','mergedAt':'2026-08-29T00:00:00Z','mergeCommit':{'oid':'merge123'}}); "
                "c=a.TaskCandidate(task_id='ABC-001',title='Ready',owner='Codex',reviewer='Claude',branch='task/ABC-001'); "
                "r=FakeRunner(pr=None,merged_pr=p); "
                "x=a.integrate_candidate(c,a.Settings(smoke_commands=('true',)),r,canonical_state_file=Path(sys.argv[1]),execute=True,open_unblock=False,gate=approved_gate()); "
                "print(json.dumps({'action':x.action,'commands':r.commands}))",
                str(canonical_state),
            )
            self.assertEqual(reconciled["action"], "already_merged")
            self.assertFalse(
                any(
                    command[:4] == ["gh", "api", "--method", "PUT"]
                    and command[4].endswith("/pulls/44/merge")
                    for command in reconciled["commands"]
                )
            )


class IntegrationLockTests(unittest.TestCase):
    def test_existing_empty_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            lock_path.touch()
            with self.assertRaisesRegex(
                auto_integrator.IntegrationLockError, "metadata is empty"
            ):
                with auto_integrator.lock_file(lock_path):
                    self.fail("empty pre-existing lock was accepted")

    def test_first_publish_exposes_only_complete_locked_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            real_link = os.link
            observed: list[dict[str, Any]] = []

            def observe_publish(source: object, destination: object) -> None:
                real_link(source, destination)
                observed.append(json.loads(lock_path.read_text(encoding="utf-8")))

            with mock.patch.object(auto_integrator.os, "link", side_effect=observe_publish):
                with auto_integrator.lock_file(lock_path):
                    pass

            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]["schema"], auto_integrator.LOCK_SCHEMA)
            self.assertEqual(observed[0]["state"], "held")
            self.assertEqual(observed[0]["pid"], os.getpid())

    def test_separate_process_cannot_share_merge_owner_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            script = (
                "import sys; from pathlib import Path; "
                "from scripts.git.auto_integrator import lock_file; "
                f"p=Path({str(lock_path)!r}); "
                "ctx=lock_file(p); ctx.__enter__(); print('ready', flush=True); "
                "sys.stdin.readline(); ctx.__exit__(None,None,None)"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=REPO_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaises(auto_integrator.IntegrationLockHeld):
                    with auto_integrator.lock_file(lock_path):
                        self.fail("separate process shared merge authority")
            finally:
                child.communicate("release\n", timeout=5)
            self.assertEqual(child.returncode, 0)

    def test_main_reports_active_lock_as_machine_readable_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            lock_path = root / "auto-integrator.lock"
            status_file = root / "ai-status.json"
            config_file = root / "config.json"
            status_file.write_text('{"tasks": []}\n', encoding="utf-8")
            config_file.write_text("{}\n", encoding="utf-8")

            output = io.StringIO()
            with auto_integrator.lock_file(lock_path):
                with mock.patch.object(
                    auto_integrator,
                    "load_settings",
                    return_value=auto_integrator.Settings(lock_path=lock_path),
                ):
                    with mock.patch("sys.stdout", output):
                        returncode = auto_integrator.main(
                            [
                                "--status-file",
                                str(status_file),
                                "--config-file",
                                str(config_file),
                                "--json",
                            ]
                        )
                    plain_output = io.StringIO()
                    plain_error = io.StringIO()
                    with mock.patch("sys.stdout", plain_output), mock.patch(
                        "sys.stderr", plain_error
                    ):
                        plain_returncode = auto_integrator.main(
                            [
                                "--status-file",
                                str(status_file),
                                "--config-file",
                                str(config_file),
                            ]
                        )

            payload = json.loads(output.getvalue())
            self.assertEqual(returncode, 0)
            self.assertTrue(payload["skipped"])
            self.assertEqual(payload["reason"], "integration_lock_held")
            self.assertEqual(payload["candidate_count"], 0)
            self.assertEqual(payload["results"], [])
            self.assertEqual(plain_returncode, 0)
            self.assertIn("skipped reason=integration_lock_held", plain_output.getvalue())
            self.assertEqual(plain_error.getvalue(), "")

    def test_main_reports_corrupt_lock_as_machine_readable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            lock_path = root / "auto-integrator.lock"
            status_file = root / "ai-status.json"
            config_file = root / "config.json"
            lock_path.write_text("not-json\n", encoding="utf-8")
            status_file.write_text('{"tasks": []}\n', encoding="utf-8")
            config_file.write_text("{}\n", encoding="utf-8")

            output = io.StringIO()
            with mock.patch.object(
                auto_integrator,
                "load_settings",
                return_value=auto_integrator.Settings(lock_path=lock_path),
            ), mock.patch("sys.stdout", output):
                returncode = auto_integrator.main(
                    [
                        "--status-file",
                        str(status_file),
                        "--config-file",
                        str(config_file),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(returncode, 2)
            self.assertFalse(payload["skipped"])
            self.assertEqual(payload["reason"], "integration_lock_error")
            self.assertIn("metadata is corrupt", payload["detail"])

    def test_active_kernel_lock_is_not_stolen_and_owner_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            with auto_integrator.lock_file(lock_path):
                held = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(held["schema"], auto_integrator.LOCK_SCHEMA)
                self.assertEqual(held["state"], "held")
                self.assertEqual(held["owner"], "supervisor_integration_runner")
                self.assertTrue(held["owner_id"])
                self.assertEqual(held["pid"], os.getpid())
                with self.assertRaisesRegex(
                    auto_integrator.IntegrationLockHeld, "lock is already held"
                ):
                    with auto_integrator.lock_file(lock_path):
                        self.fail("active lock was stolen")

            released = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(released["state"], "released")
            self.assertIn("released_at", released)

    def test_dead_legacy_owner_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            lock_path.write_text(
                json.dumps({"pid": 2_147_483_647, "created_at": 1}) + "\n",
                encoding="utf-8",
            )

            with auto_integrator.lock_file(lock_path):
                held = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(held["state"], "held")
                self.assertEqual(held["recovered_from"]["pid"], 2_147_483_647)

            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8"))["state"],
                "released",
            )

    def test_legacy_unlink_recreate_race_retries_on_the_path_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "schema": auto_integrator.LOCK_SCHEMA,
                        "state": "released",
                        "pid": 2_147_483_647,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            real_read = auto_integrator._read_lock_metadata
            reads = 0
            first_inode = 0

            def unlink_during_first_metadata_read(handle: Any) -> dict[str, Any]:
                nonlocal reads, first_inode
                reads += 1
                if reads == 1:
                    first_inode = os.fstat(handle.fileno()).st_ino
                    lock_path.unlink()
                    lock_path.write_text(
                        json.dumps(
                            {
                                "schema": auto_integrator.LOCK_SCHEMA,
                                "state": "released",
                                "pid": 2_147_483_647,
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    return {
                        "schema": auto_integrator.LOCK_SCHEMA,
                        "state": "released",
                        "pid": 2_147_483_647,
                    }
                return real_read(handle)

            with mock.patch.object(
                auto_integrator,
                "_read_lock_metadata",
                side_effect=unlink_during_first_metadata_read,
            ):
                with auto_integrator.lock_file(lock_path):
                    held = json.loads(lock_path.read_text(encoding="utf-8"))
                    self.assertEqual(held["state"], "held")
                    self.assertNotEqual(lock_path.stat().st_ino, first_inode)

            self.assertEqual(reads, 2)
            self.assertEqual(
                json.loads(lock_path.read_text(encoding="utf-8"))["state"],
                "released",
            )

    def test_live_legacy_owner_is_not_stolen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            lock_path.write_text(
                json.dumps(
                    {"pid": os.getpid(), "created_at": int(auto_integrator.time.time())}
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                auto_integrator.IntegrationLockHeld, "legacy lock has a live owner"
            ):
                with auto_integrator.lock_file(lock_path):
                    self.fail("live legacy owner was stolen")

    def test_unwritable_lock_parent_fails_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            with mock.patch.object(
                auto_integrator, "_directory_is_writable", return_value=False
            ):
                with self.assertRaisesRegex(
                    auto_integrator.IntegrationLockError, "lock parent is not writable"
                ):
                    with auto_integrator.lock_file(lock_path):
                        self.fail("unwritable lock parent was accepted")


class IntegrationReceiptWiringTests(unittest.TestCase):
    """DTG-INT-01: the auto-integrator's own call sites into
    ``rewrite.integration_receipt``. Schema/authority/mutation correctness is
    covered exhaustively in ``.orchestrator/rewrite/test_integration_receipt.py``;
    these tests only prove the wiring -- when the write is attempted, with
    what identity, and that it never turns a real merge/reconciliation
    outcome into a failure.
    """

    def _receipted_task(self, **overrides) -> dict[str, Any]:
        task = {
            "id": "ABC-001",
            "status": "review_approved",
            "generation": 1,
            "owner": "Codex",
            "reviewer": "Claude",
            "review_binding": {
                "pr": 44,
                "head_sha": APPROVED_HEAD,
                "head_branch": "task/ABC-001",
                "base": "dev",
            },
        }
        task.update(overrides)
        return task

    def test_candidates_skip_a_row_with_a_matching_integration_receipt(self) -> None:
        receipt = {
            "version": 1,
            "result": "landed",
            "observation": "performed_merge",
            "task_generation": 1,
            "repository": "ajoe734/pantheon",
            "target_branch": "dev",
            "pr": 44,
            "head_sha": APPROVED_HEAD,
            "merge_commit_sha": "b" * 40,
            "observed_at": "2026-06-12T01:01:07Z",
            "source": "canonical_auto_integrator",
        }
        state = {"tasks": [self._receipted_task(integration_receipt=receipt)]}
        candidates = auto_integrator.integration_candidates(state)
        self.assertEqual(candidates, [])

    def test_candidates_still_include_a_row_whose_receipt_is_stale(self) -> None:
        stale_receipt = {
            "version": 1,
            "result": "landed",
            "observation": "performed_merge",
            "task_generation": 1,
            "repository": "ajoe734/pantheon",
            "target_branch": "dev",
            "pr": 44,
            "head_sha": "c" * 40,  # a different, no-longer-current approved head
            "merge_commit_sha": "b" * 40,
            "observed_at": "2026-06-12T01:01:07Z",
            "source": "canonical_auto_integrator",
        }
        state = {"tasks": [self._receipted_task(integration_receipt=stale_receipt)]}
        candidates = auto_integrator.integration_candidates(state)
        self.assertEqual([c.task_id for c in candidates], ["ABC-001"])

    def test_event_path_resolves_from_config_when_env_unset(self) -> None:
        """Regression test for a live-canary finding (2026-08-30): the
        cron-launched auto-integrator does not inherit
        PANTHEON_TASK_STATE_STORE_MODE/PANTHEON_TASK_STATE_EVENT_LOG the way a
        supervisor-spawned worker does. Without a config fallback, every
        receipt landed only in the flat ai-status.json projection; the very
        next governed ai_status.py command (which reads from the V2 journal)
        silently reverted it within seconds. The live config's own
        task_state_store block must be consulted directly."""

        config = {
            "task_state_store": {
                "mode": "authoritative",
                "event_log": "/home/lupin/pantheon-ci-deploy/runtime/task-state-events-v2.jsonl",
            }
        }
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_TASK_STATE_STORE_MODE", None)
            os.environ.pop("PANTHEON_TASK_STATE_EVENT_LOG", None)
            result = auto_integrator._canonical_task_state_event_path(config)
        self.assertEqual(
            result, Path("/home/lupin/pantheon-ci-deploy/runtime/task-state-events-v2.jsonl")
        )

    def test_event_path_env_var_still_wins_over_config(self) -> None:
        config = {
            "task_state_store": {
                "mode": "authoritative",
                "event_log": "/from-config.jsonl",
            }
        }
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                "PANTHEON_TASK_STATE_EVENT_LOG": "/from-env.jsonl",
            },
        ):
            result = auto_integrator._canonical_task_state_event_path(config)
        self.assertEqual(result, Path("/from-env.jsonl"))

    def test_event_path_none_without_config_or_env(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_TASK_STATE_STORE_MODE", None)
            os.environ.pop("PANTHEON_TASK_STATE_EVENT_LOG", None)
            self.assertIsNone(auto_integrator._canonical_task_state_event_path(None))
            self.assertIsNone(auto_integrator._canonical_task_state_event_path({}))

    def test_event_path_none_when_config_mode_not_authoritative(self) -> None:
        config = {"task_state_store": {"mode": "legacy", "event_log": "/x.jsonl"}}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_TASK_STATE_STORE_MODE", None)
            os.environ.pop("PANTHEON_TASK_STATE_EVENT_LOG", None)
            self.assertIsNone(auto_integrator._canonical_task_state_event_path(config))

    def test_execute_merge_persists_receipt_through_config_resolved_v2_journal(self) -> None:
        """End-to-end: with only the config (no env vars) carrying V2 store
        identity, a real merge's receipt must reach the V2 journal, not just
        the flat projection -- otherwise the very next governed command
        reverts it (the exact live bug this regression test targets)."""

        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44), merge_sha="e" * 40)

        with tempfile.TemporaryDirectory() as tmp_dir:
            status_file = self._fresh_state_file(tmp_dir)
            event_path = Path(tmp_dir) / "task-state-events-v2.jsonl"
            # Seed the V2 journal with the same initial state the flat status
            # file carries, as production keeps both in sync before any
            # receipt write is attempted.
            task_state_store.append_state_commit(
                event_path,
                json.loads(status_file.read_text(encoding="utf-8")),
                source="test-seed",
            )
            lock_path = Path(tmp_dir) / "auto-integrator.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "schema": auto_integrator.LOCK_SCHEMA,
                        "state": "held",
                        "pid": os.getpid(),
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "paths": {"status_file": str(status_file)},
                "task_state_store": {"mode": "authoritative", "event_log": str(event_path)},
            }
            with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
                auto_integrator.integration_receipt,
                "validate_status_command_runtime",
                return_value={},
            ):
                # This test's own worktree is not named after its HEAD sha (unlike a
                # promoted command-runtimes/<sha> checkout), so the promoted-runtime
                # identity check -- already exhaustively covered in
                # test_integration_receipt.py -- is bypassed here; this test's own
                # focus is V2 persistence, not that plumbing.
                os.environ.pop("PANTHEON_TASK_STATE_STORE_MODE", None)
                os.environ.pop("PANTHEON_TASK_STATE_EVENT_LOG", None)
                result = auto_integrator.integrate_candidate(
                    candidate,
                    auto_integrator.Settings(smoke_commands=("true",), lock_path=lock_path),
                    runner,
                    execute=True,
                    gate=approved_gate(),
                    config=config,
                    canonical_state_file=status_file,
                    status_root=status_file.parent,
                )

            self.assertEqual(result.action, "merged")
            self.assertTrue(event_path.exists())
            events = [
                json.loads(line) for line in event_path.read_text().splitlines() if line.strip()
            ]
            self.assertEqual(len(events), 2)  # the seed commit, then the receipt commit
            self.assertEqual(events[-1]["source"], "canonical_auto_integrator")
            snapshot = task_state_store.load_snapshot(event_path)
            committed_task = next(
                t for t in snapshot["state"]["tasks"] if t["id"] == "ABC-001"
            )
            self.assertIn("integration_receipt", committed_task)

    @staticmethod
    def _fresh_state_file(tmp_dir: str, task_id: str = "ABC-001") -> Path:
        """A real status file matching ``approved_gate``'s fixture state, so
        the final pre-merge revalidation (which re-reads this file from disk)
        finds the same approved row the dry-run planning stage saw."""

        path = Path(tmp_dir) / "ai-status.json"
        path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": task_id,
                            "title": "Ready",
                            "status": "review_approved",
                            "owner": "Codex",
                            "reviewer": "Claude",
                            "generation": 1,
                            "review_binding": {
                                "pr": 44,
                                "head_sha": APPROVED_HEAD,
                                "head_branch": f"task/{task_id}",
                                "base": "dev",
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_execute_merge_writes_a_performed_merge_receipt(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44), merge_sha="e" * 40)

        with tempfile.TemporaryDirectory() as tmp_dir:
            status_file = self._fresh_state_file(tmp_dir)
            config = {"paths": {"status_file": str(status_file)}}
            with mock.patch.object(
                auto_integrator.integration_receipt, "record_integration_receipt"
            ) as stub:
                result = auto_integrator.integrate_candidate(
                    candidate,
                    auto_integrator.Settings(smoke_commands=("true",)),
                    runner,
                    execute=True,
                    gate=approved_gate(),
                    config=config,
                    canonical_state_file=status_file,
                )

        self.assertEqual(result.action, "merged")
        stub.assert_called_once()
        kwargs = stub.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "ABC-001")
        self.assertEqual(kwargs["merge_commit_sha"], "e" * 40)
        self.assertEqual(
            kwargs["observation"], auto_integrator.integration_receipt.RECEIPT_OBSERVATION_PERFORMED_MERGE
        )
        self.assertEqual(kwargs["expected_delivery_binding"].pr, 44)
        self.assertEqual(kwargs["expected_delivery_binding"].head_sha, APPROVED_HEAD)
        self.assertEqual(kwargs["config"], config)

    def test_execute_already_merged_writes_a_reconciled_receipt(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        reconciled_pr = merged_pr(number=55)
        reconciled_pr["mergeCommit"] = {"oid": "d" * 40}
        runner = FakeRunner(pr=None, merged_pr=reconciled_pr)
        config = {"paths": {"status_file": "/tmp/does-not-matter/ai-status.json"}}

        with mock.patch.object(
            auto_integrator.integration_receipt, "record_integration_receipt"
        ) as stub:
            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(),
                runner,
                execute=True,
                gate=approved_gate(pr_number=55),
                config=config,
                canonical_state_file=Path("/tmp/does-not-matter/ai-status.json"),
            )

        self.assertEqual(result.action, "already_merged")
        stub.assert_called_once()
        kwargs = stub.call_args.kwargs
        self.assertEqual(
            kwargs["observation"], auto_integrator.integration_receipt.RECEIPT_OBSERVATION_RECONCILED
        )
        self.assertEqual(kwargs["merge_commit_sha"], "d" * 40)

    def test_dry_run_never_writes_a_receipt(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44))
        config = {"paths": {"status_file": "/tmp/does-not-matter/ai-status.json"}}

        with mock.patch.object(
            auto_integrator.integration_receipt, "record_integration_receipt"
        ) as stub:
            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(smoke_commands=("true",)),
                runner,
                execute=False,
                gate=approved_gate(),
                config=config,
            )

        self.assertIn(result.action, {"would_merge", "merged"})
        stub.assert_not_called()

    def test_execute_without_config_never_writes_a_receipt(self) -> None:
        """Every pre-existing caller in this file omits ``config`` -- that
        must keep behaving exactly as before this feature existed."""

        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44))

        with mock.patch.object(
            auto_integrator.integration_receipt, "record_integration_receipt"
        ) as stub:
            result = auto_integrator.integrate_candidate(
                candidate,
                auto_integrator.Settings(smoke_commands=("true",)),
                runner,
                execute=True,
                gate=approved_gate(),
            )

        self.assertEqual(result.action, "merged")
        stub.assert_not_called()

    def test_receipt_write_failure_does_not_fail_a_real_merge_result(self) -> None:
        candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        runner = FakeRunner(pr=green_pr(number=44), merge_sha="e" * 40)

        with tempfile.TemporaryDirectory() as tmp_dir:
            status_file = self._fresh_state_file(tmp_dir)
            config = {"paths": {"status_file": str(status_file)}}
            with mock.patch.object(
                auto_integrator.integration_receipt,
                "record_integration_receipt",
                side_effect=auto_integrator.integration_receipt.IntegrationReceiptAuthorityError("boom"),
            ):
                result = auto_integrator.integrate_candidate(
                    candidate,
                    auto_integrator.Settings(smoke_commands=("true",)),
                    runner,
                    execute=True,
                    gate=approved_gate(),
                    config=config,
                    canonical_state_file=status_file,
                )

        self.assertEqual(result.action, "merged")
        self.assertIn("left ABC-001 in review_approved for owner finalization", result.detail)


if __name__ == "__main__":
    unittest.main()
