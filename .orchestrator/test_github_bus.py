#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import github_bus
from github_command_parser import GitHubCommand


class GitHubBusCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "github_bus": {
                "reviewers": {
                    "Claude": ["ajoe734"],
                    "Codex": ["ajoe734"],
                }
            }
        }
        self.bus_state = {"tasks": {}}

    def test_apply_bus_command_review_approve_uses_reviewer_actor(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "LIN-001",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "next": "ready for review",
                }
            ]
        }
        command = GitHubCommand(verb="approve", target="LIN-001", raw="/approve LIN-001")

        with (
            mock.patch.object(github_bus, "run_ai_status") as run_ai_status,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed, reply = github_bus.apply_bus_command(
                self.config,
                self.bus_state,
                status,
                "ajoe734/pantheon",
                command,
                "ajoe734",
                issue_number=4,
            )

        self.assertTrue(changed)
        self.assertEqual(reply, "Applied `/approve` to `LIN-001`.")
        run_ai_status.assert_called_once_with(
            "approve",
            "LIN-001",
            "GitHub approval bus approved via issue #4 by @ajoe734.",
            actor="Claude",
        )

    def test_apply_bus_command_retry_uses_human_ops_actor(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "LIN-001",
                    "status": "blocked",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "waiting_for": "Human/Ops",
                    "next": "waiting for operator retry",
                }
            ]
        }
        command = GitHubCommand(verb="retry", target="LIN-001", raw="/retry LIN-001")

        with (
            mock.patch.object(github_bus, "run_ai_status") as run_ai_status,
            mock.patch.object(github_bus, "queue_resume_for_task", return_value=True) as queue_resume,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed, reply = github_bus.apply_bus_command(
                self.config,
                self.bus_state,
                status,
                "ajoe734/pantheon",
                command,
                "ajoe734",
                issue_number=4,
            )

        self.assertTrue(changed)
        self.assertEqual(reply, "Queued retry for `LIN-001`.")
        run_ai_status.assert_called_once_with(
            "reopen",
            "LIN-001",
            "GitHub retry requested via issue #4 by @ajoe734.",
            actor="Human/Ops",
        )
        queue_resume.assert_called_once_with(self.config, status["tasks"][0])

    def test_queue_resume_marks_github_retry_as_isolated_task_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            queue_path = root / "event-queue.jsonl"
            activity_path = root / "activity-log.jsonl"
            config = {
                "paths": {
                    "event_queue": str(queue_path),
                    "activity_log": str(activity_path),
                },
                "agents": {
                    "codex": {
                        "id": "codex",
                        "display_name": "Codex",
                        "provider": "codex",
                    }
                },
            }
            task = {
                "id": "OPS-RETRY-001",
                "owner": "Codex",
                "artifacts": [".orchestrator/supervisor.py"],
                "next": "retry",
            }
            with (
                mock.patch.object(github_bus, "render_wakeup_message", return_value="wake"),
                mock.patch.object(
                    github_bus,
                    "execution_context_files",
                    return_value=["AI_COLLABORATION_GUIDE.md"],
                ),
                mock.patch.object(github_bus, "write_activity_log"),
            ):
                queued = github_bus.queue_resume_for_task(config, task)

            events = github_bus.load_jsonl(queue_path)

        self.assertTrue(queued)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "github_retry")
        self.assertEqual(events[0]["metadata"]["workspace_task_id"], "OPS-RETRY-001")
        self.assertTrue(events[0]["metadata"]["require_isolated_worktree"])
        self.assertEqual(events[0]["metadata"]["explicit_retry_source"], "github_bus")

    def test_poll_pr_reviews_approved_uses_reviewer_approval(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "LIN-001",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "next": "ready for review",
                }
            ]
        }
        bus_state = {
            "processed_review_ids": [],
            "tasks": {
                "LIN-001": {
                    "review_pr": {"number": 12},
                }
            },
        }

        with (
            mock.patch.object(
                github_bus,
                "gh_json",
                side_effect=[
                    [
                        {
                            "id": 999,
                            "state": "APPROVED",
                            "body": "looks good",
                            "user": {"login": "ajoe734"},
                        }
                    ],
                    {
                        "statusCheckRollup": [],
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "state": "OPEN",
                        "mergedAt": None,
                    },
                ],
            ),
            mock.patch.object(github_bus, "run_ai_status") as run_ai_status,
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.poll_pr_reviews(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertTrue(changed)
        run_ai_status.assert_called_once_with(
            "approve",
            "LIN-001",
            "GitHub PR approved via PR #12 by @ajoe734.",
            actor="Claude",
        )
        self.assertEqual(bus_state["processed_review_ids"], ["review:999"])
        write_activity_log.assert_called_once()

    def test_poll_pr_reviews_batches_with_cursor(self) -> None:
        self.config["github_bus"]["poll_batch_sizes"] = {"pr_reviews": 2}
        status = {
            "tasks": [
                {"id": "LIN-001", "reviewer": "Claude"},
                {"id": "LIN-002", "reviewer": "Claude"},
                {"id": "LIN-003", "reviewer": "Claude"},
            ]
        }
        bus_state = {
            "processed_review_ids": [],
            "poll_cursors": {"pr_reviews": 0},
            "tasks": {
                "LIN-001": {"review_pr": {"number": 11}},
                "LIN-002": {"review_pr": {"number": 12}},
                "LIN-003": {"review_pr": {"number": 13}},
            },
        }

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_pr_reviews(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertFalse(changed)
        review_calls = [call.args[0][-1] for call in gh_json.call_args_list if call.args[0][0] == "api"]
        self.assertEqual(
            review_calls,
            [
                "repos/ajoe734/pantheon/pulls/11/reviews?per_page=100",
                "repos/ajoe734/pantheon/pulls/12/reviews?per_page=100",
            ],
        )
        self.assertEqual(bus_state["poll_cursors"]["pr_reviews"], 2)

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_pr_reviews(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertFalse(changed)
        review_calls = [call.args[0][-1] for call in gh_json.call_args_list if call.args[0][0] == "api"]
        self.assertEqual(
            review_calls,
            ["repos/ajoe734/pantheon/pulls/13/reviews?per_page=100"],
        )
        self.assertEqual(bus_state["poll_cursors"]["pr_reviews"], 0)

    def test_poll_issue_comments_batches_with_cursor(self) -> None:
        self.config["github_bus"]["poll_batch_sizes"] = {"issue_comments": 2}
        status = {
            "tasks": [
                {"id": "LIN-001", "reviewer": "Claude"},
                {"id": "LIN-002", "reviewer": "Claude"},
                {"id": "LIN-003", "reviewer": "Claude"},
            ]
        }
        bus_state = {
            "processed_comment_ids": [],
            "poll_cursors": {"issue_comments": 0},
            "tasks": {
                "LIN-001": {"ops_issue": {"number": 21}},
                "LIN-002": {"ops_issue": {"number": 22}},
                "LIN-003": {"ops_issue": {"number": 23}},
            },
        }

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_issue_comments(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertFalse(changed)
        self.assertEqual(
            [call.args[0][-1] for call in gh_json.call_args_list],
            [
                "repos/ajoe734/pantheon/issues/21/comments?per_page=100",
                "repos/ajoe734/pantheon/issues/22/comments?per_page=100",
            ],
        )
        self.assertEqual(bus_state["poll_cursors"]["issue_comments"], 2)

    def test_poll_coordination_issue_comments_batches_with_cursor(self) -> None:
        self.config["github_bus"]["poll_batch_sizes"] = {"coordination_comments": 2}
        bus_state = {
            "processed_comment_ids": [],
            "poll_cursors": {"coordination_comments": 0},
            "coordination": {
                "ajoe734/pantheon:F-001": {"repo": "ajoe734/pantheon", "issue": {"number": 31}},
                "ajoe734/pantheon:F-002": {"repo": "ajoe734/pantheon", "issue": {"number": 32}},
                "ajoe734/front-ai-trading-system:F-003": {
                    "repo": "ajoe734/front-ai-trading-system",
                    "issue": {"number": 33},
                },
            },
        }

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_coordination_issue_comments(
                self.config,
                bus_state,
                {"tasks": []},
                runtime_state={},
            )

        self.assertFalse(changed)
        self.assertEqual(
            [call.args[0][-1] for call in gh_json.call_args_list],
            [
                "repos/ajoe734/pantheon/issues/31/comments?per_page=100",
                "repos/ajoe734/pantheon/issues/32/comments?per_page=100",
            ],
        )
        self.assertEqual(bus_state["poll_cursors"]["coordination_comments"], 2)

    def test_upsert_review_pr_create_uses_create_label_flags(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
                "auto_request_reviewers": True,
                "reviewers": {"Claude": ["ajoe734"]},
                "labels": {"review": ["pantheon-bus", "pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": ["foo.md"],
            "next": "ready for review",
        }

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=[]),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    "https://github.com/ajoe734/pantheon/pull/12\n",
                    "",
                ),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertTrue(changed)
        args = run_gh.call_args.args[0]
        self.assertIn("--label", args)
        self.assertNotIn("--add-label", args)
        self.assertEqual(args[args.index("--base") + 1], "dev")
        self.assertEqual(args[args.index("--head") + 1], "task/LIN-001")

    def test_upsert_review_pr_skips_unpublished_remote_branch(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
                "labels": {"review": ["pantheon-bus", "pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": ["foo.md"],
            "next": "ready for review",
        }

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=[]),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None),
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertTrue(changed)
        entry = bus_state["tasks"]["LIN-001"]["review_pr"]
        self.assertEqual(entry["state"], "skipped_unpublished_branch")
        self.assertEqual(entry["branch"], "task/LIN-001")
        self.assertEqual(entry["head_sha"], "abc123")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "github_review_pr_skipped")

    def test_upsert_review_pr_skips_recent_remote_recheck_for_unpublished_branch(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
                "unpublished_branch_recheck_seconds": 300,
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        skip_hash = '{"base": "dev", "branch": "task/LIN-001", "head_sha": "abc123", "state": "skipped_unpublished_branch", "task_id": "LIN-001"}'
        bus_state = {
            "tasks": {
                "LIN-001": {
                    "review_pr": {
                        "title": "[ReviewBus] LIN-001 Lineage task",
                        "branch": "task/LIN-001",
                        "state": "skipped_unpublished_branch",
                        "head_sha": "abc123",
                        "last_remote_branch_check_at": github_bus.utc_now(),
                    },
                    "last_review_hash": skip_hash,
                }
            }
        }

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=[]),
            mock.patch.object(github_bus, "remote_branch_head_sha") as remote_branch_head_sha,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertFalse(changed)
        remote_branch_head_sha.assert_not_called()

    def test_upsert_review_pr_rechecks_unpublished_branch_after_ttl(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
                "unpublished_branch_recheck_seconds": 300,
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        skip_hash = '{"base": "dev", "branch": "task/LIN-001", "head_sha": "abc123", "state": "skipped_unpublished_branch", "task_id": "LIN-001"}'
        bus_state = {
            "tasks": {
                "LIN-001": {
                    "review_pr": {
                        "title": "[ReviewBus] LIN-001 Lineage task",
                        "branch": "task/LIN-001",
                        "state": "skipped_unpublished_branch",
                        "head_sha": "abc123",
                        "last_remote_branch_check_at": "2026-04-22T00:00:00Z",
                    },
                    "last_review_hash": skip_hash,
                }
            }
        }

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=[]),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None) as remote_branch_head_sha,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertFalse(changed)
        remote_branch_head_sha.assert_called_once_with("task/LIN-001")

    def test_delivery_base_uses_dev_workflow_instead_of_legacy_master(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
            },
            "branch_workflow": {
                "dev_branch": "dev",
            },
        }

        self.assertEqual(
            github_bus.delivery_base_branch(config, "ajoe734/pantheon"),
            "dev",
        )

    def test_review_branch_is_exact_task_branch_not_agent_or_current_branch(self) -> None:
        config = {
            "branch_workflow": {
                "task_branch_prefix": "task/",
            }
        }
        status = {
            "agents": [{"name": "Codex", "branch": "feature/shared-owner-branch"}],
        }
        task = {
            "id": "LIN-001",
            "owner": "Codex",
        }

        branch = github_bus.review_branch_for_task(config, status, task)

        self.assertEqual(branch, "task/LIN-001")

    def test_upsert_review_pr_binds_exact_merged_dev_evidence(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        task = {
            "id": "OPS-EXACT-001",
            "title": "Exact merged evidence",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        candidates = [
            {
                "number": 4018,
                "title": "[ReviewBus] OPS-EXACT-001 Exact merged evidence",
                "url": "https://github.com/ajoe734/pantheon/pull/4018",
                "state": "CLOSED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "c6784286",
                "baseRefName": "master",
                "mergedAt": None,
                "mergeCommit": None,
            },
            {
                "number": 4017,
                "title": "OPS-EXACT-001: implement exact evidence",
                "url": "https://github.com/ajoe734/pantheon/pull/4017",
                "state": "MERGED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "c6784286",
                "baseRefName": "dev",
                "mergedAt": "2026-07-24T00:21:18Z",
                "mergeCommit": {"oid": "f4f5f8fc"},
            },
        ]

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="c6784286"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=candidates),
            mock.patch.object(
                github_bus,
                "remote_branch_head_sha",
                return_value="c6784286",
            ) as remote_branch_head_sha,
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                {"agents": []},
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        evidence = bus_state["tasks"]["OPS-EXACT-001"]["review_pr"]
        self.assertEqual(evidence["number"], 4017)
        self.assertEqual(evidence["state"], "merged")
        self.assertEqual(evidence["base_branch"], "dev")
        self.assertEqual(evidence["head_sha"], "c6784286")
        self.assertEqual(evidence["merge_commit"], "f4f5f8fc")
        self.assertEqual(evidence["evidence_kind"], "merged_task_pr")
        self.assertEqual(
            write_activity_log.call_args.args[1]["type"],
            "github_review_pr_bound",
        )
        remote_branch_head_sha.assert_called_once_with("task/OPS-EXACT-001")
        run_gh.assert_not_called()

    def test_upsert_review_pr_uses_unique_merged_head_when_local_ref_is_stale(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        task = {
            "id": "OPS-EXACT-001",
            "title": "Exact merged evidence",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        candidates = [
            {
                "number": 4024,
                "title": "OPS-EXACT-001: implement exact evidence",
                "url": "https://github.com/ajoe734/pantheon/pull/4024",
                "state": "MERGED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "updated-pr-head",
                "baseRefName": "dev",
                "mergedAt": "2026-07-24T01:17:51Z",
                "mergeCommit": {"oid": "merged-to-dev"},
            }
        ]

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="stale-local-head"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=candidates),
            mock.patch.object(
                github_bus,
                "remote_branch_head_sha",
                return_value=None,
            ) as remote_branch_head_sha,
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                {"agents": []},
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        evidence = bus_state["tasks"]["OPS-EXACT-001"]["review_pr"]
        self.assertEqual(evidence["number"], 4024)
        self.assertEqual(evidence["head_sha"], "updated-pr-head")
        self.assertEqual(evidence["merge_commit"], "merged-to-dev")
        self.assertEqual(evidence["evidence_kind"], "merged_task_pr")
        remote_branch_head_sha.assert_called_once_with("task/OPS-EXACT-001")
        run_gh.assert_not_called()

    def test_upsert_review_pr_uses_published_head_for_latest_merged_followup(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        task = {
            "id": "OPS-EXACT-001",
            "title": "Exact merged evidence",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        candidates = [
            {
                "number": 4024,
                "title": "OPS-EXACT-001: initial delivery",
                "url": "https://github.com/ajoe734/pantheon/pull/4024",
                "state": "MERGED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "initial-pr-head",
                "baseRefName": "dev",
                "mergedAt": "2026-07-24T01:17:51Z",
                "mergeCommit": {"oid": "initial-dev-merge"},
            },
            {
                "number": 4027,
                "title": "OPS-EXACT-001: follow-up delivery",
                "url": "https://github.com/ajoe734/pantheon/pull/4027",
                "state": "MERGED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "followup-pr-head",
                "baseRefName": "dev",
                "mergedAt": "2026-07-24T01:45:35Z",
                "mergeCommit": {"oid": "followup-dev-merge"},
            },
        ]

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="stale-local-head"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=candidates),
            mock.patch.object(
                github_bus,
                "remote_branch_head_sha",
                return_value="followup-pr-head",
            ) as remote_branch_head_sha,
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                {"agents": []},
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        evidence = bus_state["tasks"]["OPS-EXACT-001"]["review_pr"]
        self.assertEqual(evidence["number"], 4027)
        self.assertEqual(evidence["head_sha"], "followup-pr-head")
        self.assertEqual(evidence["merge_commit"], "followup-dev-merge")
        self.assertEqual(evidence["evidence_kind"], "merged_task_pr")
        remote_branch_head_sha.assert_called_once_with("task/OPS-EXACT-001")
        run_gh.assert_not_called()

    def test_upsert_review_pr_fails_closed_when_explicit_head_mismatches_pr(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        task = {
            "id": "OPS-EXACT-001",
            "title": "Exact merged evidence",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "github": {"head_sha": "expected-task-head"},
        }
        candidates = [
            {
                "number": 4024,
                "title": "OPS-EXACT-001: stale evidence",
                "url": "https://github.com/ajoe734/pantheon/pull/4024",
                "state": "MERGED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "different-task-head",
                "baseRefName": "dev",
                "mergedAt": "2026-07-24T01:17:51Z",
                "mergeCommit": {"oid": "merged-to-dev"},
            }
        ]

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="stale-local-head"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=candidates),
            mock.patch.object(github_bus, "remote_branch_head_sha") as remote_branch_head_sha,
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                {"agents": []},
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        evidence = bus_state["tasks"]["OPS-EXACT-001"]["review_pr"]
        self.assertEqual(evidence["state"], "skipped_head_mismatch")
        self.assertEqual(evidence["evidence_kind"], "fail_closed")
        self.assertIn("different-task-head", evidence["diagnostic"])
        self.assertEqual(evidence["candidates"][0]["head_sha"], "different-task-head")
        remote_branch_head_sha.assert_not_called()
        run_gh.assert_not_called()

    def test_upsert_review_pr_fails_closed_on_legacy_base_mismatch(self) -> None:
        config = {
            "github_bus": {
                "repo": "ajoe734/pantheon",
                "default_branch": "master",
            },
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }
        bus_state = {"tasks": {}}
        task = {
            "id": "OPS-EXACT-001",
            "title": "Exact merged evidence",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        candidates = [
            {
                "number": 4018,
                "title": "[ReviewBus] OPS-EXACT-001 Exact merged evidence",
                "url": "https://github.com/ajoe734/pantheon/pull/4018",
                "state": "CLOSED",
                "headRefName": "task/OPS-EXACT-001",
                "headRefOid": "c6784286",
                "baseRefName": "master",
                "mergedAt": None,
                "mergeCommit": None,
            }
        ]

        with (
            mock.patch.object(github_bus, "branch_head_sha", return_value="c6784286"),
            mock.patch.object(github_bus, "find_task_pr_candidates", return_value=candidates),
            mock.patch.object(
                github_bus,
                "remote_branch_head_sha",
                return_value="c6784286",
            ) as remote_branch_head_sha,
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                {"agents": []},
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        evidence = bus_state["tasks"]["OPS-EXACT-001"]["review_pr"]
        self.assertEqual(evidence["state"], "skipped_base_mismatch")
        self.assertEqual(evidence["base_branch"], "dev")
        self.assertEqual(evidence["evidence_kind"], "fail_closed")
        self.assertIn("#4018 -> master", evidence["diagnostic"])
        self.assertIn("synthetic integration PR", evidence["diagnostic"])
        self.assertEqual(
            write_activity_log.call_args.args[1]["type"],
            "github_review_pr_skipped",
        )
        remote_branch_head_sha.assert_called_once_with("task/OPS-EXACT-001")
        run_gh.assert_not_called()


class PrReconciliationCandidateTests(unittest.TestCase):
    """SUP-REVIEW-PIPELINE-INTEGRITY-20260804: PR-upsert eligibility must be
    a reconciled invariant (branch+diff exists) rather than a one-shot side
    effect of `status == "review"` at scan time, or a task that leaves
    review before a PR is opened is permanently PR-less."""

    def setUp(self) -> None:
        self.config = {
            "branch_workflow": {
                "dev_branch": "dev",
                "task_branch_prefix": "task/",
            },
        }

    def test_review_status_task_is_always_a_candidate(self) -> None:
        status = {"tasks": [{"id": "SUP-A", "status": "review"}]}
        bus_state = {"tasks": {}}
        candidates = github_bus._pr_reconciliation_candidates(self.config, bus_state, status)
        self.assertEqual([task["id"] for task in candidates], ["SUP-A"])

    def test_non_review_task_with_remote_branch_and_no_pr_is_reconsidered(self) -> None:
        status = {"tasks": [{"id": "SUP-B", "status": "review_approved"}]}
        bus_state = {"tasks": {}}
        with mock.patch.object(github_bus, "remote_branch_head_sha", return_value="deadbeef"):
            candidates = github_bus._pr_reconciliation_candidates(self.config, bus_state, status)
        self.assertEqual([task["id"] for task in candidates], ["SUP-B"])

    def test_non_review_task_without_remote_branch_is_not_a_candidate(self) -> None:
        status = {"tasks": [{"id": "SUP-C", "status": "todo"}]}
        bus_state = {"tasks": {}}
        with mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None):
            candidates = github_bus._pr_reconciliation_candidates(self.config, bus_state, status)
        self.assertEqual(candidates, [])

    def test_task_with_already_resolved_pr_evidence_is_not_reprobed(self) -> None:
        status = {"tasks": [{"id": "SUP-D", "status": "blocked"}]}
        bus_state = {
            "tasks": {
                "SUP-D": {"review_pr": {"evidence_kind": "open_task_pr", "number": 42}},
            }
        }
        with mock.patch.object(github_bus, "remote_branch_head_sha") as remote_branch_head_sha:
            candidates = github_bus._pr_reconciliation_candidates(self.config, bus_state, status)
        remote_branch_head_sha.assert_not_called()
        self.assertEqual(candidates, [])

    def test_terminal_status_tasks_are_never_candidates(self) -> None:
        status = {
            "tasks": [
                {"id": "SUP-E", "status": "done"},
                {"id": "SUP-F", "status": "archived"},
                {"id": "SUP-G", "status": "superseded"},
            ]
        }
        bus_state = {"tasks": {}}
        with mock.patch.object(github_bus, "remote_branch_head_sha") as remote_branch_head_sha:
            candidates = github_bus._pr_reconciliation_candidates(self.config, bus_state, status)
        remote_branch_head_sha.assert_not_called()
        self.assertEqual(candidates, [])

    def test_sync_outbound_reaches_upsert_for_non_review_reconciled_task(self) -> None:
        # Regression for the exact 2026-08-04 failure mode: a task that left
        # `"review"` before a PR was ever opened (crashed closeout worker,
        # reassignment, etc.) must still get a PR once it has a branch+diff.
        config = dict(self.config)
        config["github_bus"] = {"repo": "ajoe734/pantheon"}
        status = {"tasks": [{"id": "SUP-H", "status": "in_progress"}]}
        bus_state = {"tasks": {}}
        runtime_state = {}

        with (
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value="cafef00d"),
            mock.patch.object(github_bus, "upsert_review_pr", return_value=True) as upsert_review_pr,
            mock.patch.object(github_bus, "upsert_ops_issue", return_value=False),
        ):
            changed = github_bus.sync_outbound(config, bus_state, status, runtime_state, "ajoe734/pantheon")

        self.assertTrue(changed)
        upsert_review_pr.assert_called_once()
        self.assertEqual(upsert_review_pr.call_args.args[-1]["id"], "SUP-H")


class GitHubBusProcessTests(unittest.TestCase):
    def test_run_gh_process_kills_process_group_on_timeout(self) -> None:
        class FakePopen:
            def __init__(self) -> None:
                self.pid = 4321
                self.returncode = None
                self.wait_calls: list[float | None] = []

            def wait(self, timeout: float | None = None) -> int:
                self.wait_calls.append(timeout)
                raise subprocess.TimeoutExpired(cmd=["gh", "api"], timeout=timeout)

        fake_process = FakePopen()

        with (
            mock.patch.object(github_bus.subprocess, "Popen", return_value=fake_process),
            mock.patch.object(github_bus.os, "killpg") as killpg,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                github_bus.run_gh_process(["api", "repos/ajoe734/pantheon/issues/4/comments"], timeout_seconds=1.0)

        killpg.assert_called_once_with(4321, github_bus.signal.SIGKILL)
        self.assertEqual(fake_process.wait_calls, [1.0, 0.2])

    def test_run_gh_uses_vendored_wrapper_when_system_gh_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            vendored = root / ".orchestrator" / "bin" / "gh"
            vendored.parent.mkdir(parents=True, exist_ok=True)
            vendored.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            vendored.chmod(0o755)

            with (
                mock.patch.object(github_bus, "ROOT", root),
                mock.patch.object(github_bus, "command_exists", return_value=None),
                mock.patch.object(
                    github_bus,
                    "run_gh_process",
                    return_value=subprocess.CompletedProcess([str(vendored), "auth", "status"], 0, "", ""),
                ) as run_gh_process,
            ):
                github_bus.run_gh(["auth", "status"], allow_offline=False)

            self.assertEqual(run_gh_process.call_args.kwargs["gh_binary"], str(vendored))


class GitHubCoordinationCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.pantheon = root / "pantheon"
        (self.pantheon / "docs-site").mkdir(parents=True, exist_ok=True)
        (self.pantheon / ".orchestrator").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "ai-status.json").write_text('{"tasks":[],"handoffs":[]}\n', encoding="utf-8")
        (self.pantheon / "current-work.md").write_text("# current work\n", encoding="utf-8")
        (self.pantheon / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
        (self.pantheon / "docs-site" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        self.config = {
            "paths": {
                "status_file": str(self.pantheon / "ai-status.json"),
                "activity_log": str(self.pantheon / "ai-activity-log.jsonl"),
                "current_work": str(self.pantheon / "current-work.md"),
                "dashboard": str(self.pantheon / "docs-site" / "index.html"),
                "event_queue": str(self.pantheon / ".orchestrator" / "event-queue.jsonl"),
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude", "adapter": "claude_cli"},
            },
            "coordination": {
                "enabled": True,
                "worker_routes": {
                    "pantheon-bff-worker": {"target_agent": "Codex"},
                    "engine-worker": {"target_agent": "Claude", "requires_human_approval": True},
                },
            },
        }
        self.bus_state = {"tasks": {}, "coordination": {}}
        self.status = {"tasks": []}

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_dispatch_command_queues_coordination_event(self) -> None:
        command = GitHubCommand(verb="dispatch", target="pantheon-bff", raw="/dispatch pantheon-bff F-042", args=("pantheon-bff", "F-042"))
        changed, reply = github_bus.apply_bus_command(
            self.config,
            self.bus_state,
            self.status,
            "ajoe734/pantheon",
            command,
            "ajoe734",
            runtime_state={"coordination": {"features": {"F-042": {"feature_id": "F-042"}}}},
        )

        self.assertTrue(changed)
        self.assertEqual(reply, "Queued `pantheon-bff-worker` for `F-042`.")
        queue = github_bus.load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "pantheon-bff-worker")

    def test_approve_engine_command_bypasses_manual_gate(self) -> None:
        command = GitHubCommand(verb="approve-engine", target="F-042", raw="/approve-engine F-042", args=("F-042",))
        changed, reply = github_bus.apply_bus_command(
            self.config,
            self.bus_state,
            self.status,
            "ajoe734/pantheon",
            command,
            "ajoe734",
            runtime_state={"coordination": {"features": {"F-042": {"feature_id": "F-042"}}}},
        )

        self.assertTrue(changed)
        self.assertEqual(reply, "Queued engine worker for `F-042`.")
        queue = github_bus.load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "engine-worker")


if __name__ == "__main__":
    unittest.main()
