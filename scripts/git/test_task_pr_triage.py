#!/usr/bin/env python3
"""Focused tests for task_pr_triage.py safety and classification rules."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "task_pr_triage", HERE / "task_pr_triage.py"
)
assert SPEC and SPEC.loader
triage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage)


def pr(**overrides):
    item = {
        "number": 10,
        "state": "OPEN",
        "title": "TASK-001: example",
        "url": "https://github.com/ajoe734/pantheon/pull/10",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-02T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
        "draft": False,
        "merge_state": "BEHIND",
        "head_ref": "task/TASK-001",
        "head_sha": "a" * 40,
        "base_ref": "dev",
        "comments": [],
    }
    item.update(overrides)
    return item


def archive(
    *,
    task_id="TASK-001",
    outcome="superseded",
    next_message="Superseded by replacement task.",
    **task_fields,
):
    task = {
        "id": task_id,
        "status": "done",
        "terminal_outcome": outcome,
        "owner": "Codex2",
        "reviewer": "Antigravity",
        "next": next_message,
        **task_fields,
    }
    return {
        "task_id": task_id,
        "terminal_status": "done",
        "terminal_outcome": outcome,
        "archived_at": "2026-07-01T00:00:00Z",
        "task": task,
        "_path": f"ai-task-archive/tasks/{task_id}.json",
    }


class PullRequestClassificationTests(unittest.TestCase):
    def classify(self, item=None, *, archives=None, history=None, active=None, reachable=False):
        return triage.classify_pr(
            item or pr(),
            triage_task_id="OPS-TASK-PR-TRIAGE-002",
            repository="ajoe734/pantheon",
            dev_reachable=reachable,
            trailers={
                "Task-ID": "TASK-001",
                "LLM-Agent": "Claude",
                "Reviewer": "Codex",
            },
            active_tasks=active or {},
            archives=archives or {},
            history_by_number={record["number"]: record for record in (history or [])},
        )

    def test_terminal_superseded_archive_authorizes_closure(self):
        result = self.classify(archives={"task-001": archive()})
        self.assertEqual(result["disposition"], "superseded")
        self.assertTrue(result["close_authorized"])
        self.assertEqual(result["owner"], "Codex2")
        self.assertIn("dry-run retention manifest", result["closure_comment"])

    def test_completed_archive_requires_different_merged_pr(self):
        completed = archive(
            outcome="completed", next_message="Delivered by Pantheon PR #9."
        )
        merged = {
            "number": 9,
            "state": "MERGED",
            "url": "https://github.com/ajoe734/pantheon/pull/9",
            "title": "TASK-001-REPLACEMENT: deliver task",
            "head_ref": "task/TASK-001-REPLACEMENT",
            "merged_at": "2026-07-01T00:00:00Z",
            "merge_commit_sha": "b" * 40,
        }
        result = self.classify(
            archives={"task-001": completed}, history=[merged]
        )
        self.assertEqual(result["disposition"], "superseded")
        self.assertTrue(result["close_authorized"])
        self.assertEqual(result["replacement_prs"][0]["number"], 9)

    def test_completed_archive_without_verified_replacement_stays_open(self):
        completed = archive(
            outcome="completed", next_message="Claims PR #9 but no live merge record."
        )
        result = self.classify(archives={"task-001": completed})
        self.assertEqual(result["disposition"], "active-repair")
        self.assertFalse(result["close_authorized"])

    def test_unrelated_merged_pr_number_in_archive_is_not_replacement(self):
        completed = archive(
            outcome="completed",
            next_message="Related frontend notes mention PR #9.",
        )
        unrelated = {
            "number": 9,
            "state": "MERGED",
            "url": "https://github.com/ajoe734/pantheon/pull/9",
            "title": "OTHER-001: unrelated task",
            "head_ref": "task/OTHER-001",
            "merged_at": "2026-07-01T00:00:00Z",
            "merge_commit_sha": "b" * 40,
        }
        result = self.classify(
            archives={"task-001": completed}, history=[unrelated]
        )
        self.assertEqual(result["disposition"], "active-repair")
        self.assertFalse(result["close_authorized"])

    def test_draft_is_protected_even_when_dirty(self):
        result = self.classify(pr(draft=True, merge_state="DIRTY"))
        self.assertEqual(result["disposition"], "protected-retain")
        self.assertFalse(result["close_authorized"])

    def test_dirty_non_draft_needs_owner(self):
        result = self.classify(pr(merge_state="DIRTY"))
        self.assertEqual(result["disposition"], "conflict-needs-owner")
        self.assertEqual(result["owner"], "Claude")

    def test_behind_non_draft_is_active_repair(self):
        result = self.classify(pr(merge_state="BEHIND"))
        self.assertEqual(result["disposition"], "active-repair")

    def test_merged_cohort_pr_is_merged_reachable(self):
        result = self.classify(
            pr(state="MERGED", merged_at="2026-07-01T00:00:00Z"), reachable=True
        )
        self.assertEqual(result["disposition"], "merged-reachable")
        self.assertFalse(result["close_authorized"])

    def test_closed_pr_with_merged_replacement_is_superseded(self):
        merged = {
            "number": 11,
            "state": "MERGED",
            "url": "https://github.com/ajoe734/pantheon/pull/11",
            "merged_at": "2026-07-02T00:00:00Z",
            "merge_commit_sha": "c" * 40,
        }
        result = self.classify(
            pr(
                state="CLOSED",
                comments=["Superseded by PR #11, which is the canonical repair."],
            ),
            history=[merged],
        )
        self.assertEqual(result["disposition"], "superseded")
        self.assertFalse(result["close_authorized"])


class BranchClassificationTests(unittest.TestCase):
    AS_OF = datetime(2026, 7, 22, tzinfo=timezone.utc)

    def classify(self, **overrides):
        item = {
            "branch": "task/TASK-001",
            "head_sha": "a" * 40,
            "committed_at": "2026-06-01T00:00:00+00:00",
            "last_commit_author": "example",
            "last_commit_subject": "example",
            "dev_reachable": True,
        }
        item.update(overrides)
        return triage.classify_branch(
            item,
            as_of=self.AS_OF,
            retention_days=30,
            open_pr=None,
            pr_disposition=None,
            history=[],
            active_tasks={},
            archives={},
        )

    def test_old_reachable_no_open_pr_is_deletion_dry_run_eligible(self):
        result = self.classify()
        self.assertEqual(result["disposition"], "merged-reachable")
        self.assertTrue(result["deletion_eligible"])

    def test_recent_reachable_branch_is_retained(self):
        result = self.classify(committed_at="2026-07-21T00:00:00+00:00")
        self.assertFalse(result["deletion_eligible"])
        self.assertIn("inside-retention-window", result["deletion_exclusion_reasons"])

    def test_old_ahead_branch_is_abandoned_unproven_and_not_deleted(self):
        result = self.classify(dev_reachable=False)
        self.assertEqual(result["disposition"], "abandoned-unproven")
        self.assertFalse(result["deletion_eligible"])
        self.assertIn("head-not-dev-reachable", result["deletion_exclusion_reasons"])

    def test_active_task_is_never_deletion_eligible(self):
        item = {
            "branch": "task/TASK-001",
            "head_sha": "a" * 40,
            "committed_at": "2026-06-01T00:00:00+00:00",
            "last_commit_author": "example",
            "last_commit_subject": "example",
            "dev_reachable": True,
        }
        result = triage.classify_branch(
            item,
            as_of=self.AS_OF,
            retention_days=30,
            open_pr=None,
            pr_disposition=None,
            history=[],
            active_tasks={
                "task-001": {
                    "id": "TASK-001",
                    "status": "in_progress",
                    "owner": "Codex2",
                }
            },
            archives={},
        )
        self.assertEqual(result["disposition"], "active-repair")
        self.assertFalse(result["deletion_eligible"])
        self.assertIn("active-task", result["deletion_exclusion_reasons"])


class ValidationTests(unittest.TestCase):
    def test_snapshot_time_round_trip_preserves_branch_age(self):
        captured = triage._snapshot_time(
            datetime(2026, 7, 22, 18, 0, 0, 999999, tzinfo=timezone.utc)
        )
        published = triage._parse_datetime(triage._iso(captured))
        self.assertEqual(captured, published)

        branch = {
            "branch": "task/TASK-001",
            "head_sha": "a" * 40,
            "committed_at": "2026-06-01T00:00:00+00:00",
            "last_commit_author": "example",
            "last_commit_subject": "example",
            "dev_reachable": True,
        }
        first = triage.classify_branch(
            branch,
            as_of=captured,
            retention_days=30,
            open_pr=None,
            pr_disposition=None,
            history=[],
            active_tasks={},
            archives={},
        )
        reproduced = triage.classify_branch(
            branch,
            as_of=published,
            retention_days=30,
            open_pr=None,
            pr_disposition=None,
            history=[],
            active_tasks={},
            archives={},
        )
        self.assertEqual(first["age_days"], reproduced["age_days"])
        self.assertEqual(first["deletion_eligible"], reproduced["deletion_eligible"])

    def test_recent_open_pr_is_excluded_from_fixed_overdue_cohort_summary(self):
        old_pr = pr()
        recent_pr = pr(
            number=11,
            title="TASK-002: recent work",
            url="https://github.com/ajoe734/pantheon/pull/11",
            created_at="2026-07-22T12:00:00Z",
            updated_at="2026-07-22T12:00:00Z",
            head_ref="task/TASK-002",
            head_sha="b" * 40,
        )
        branches = [
            {
                "branch": item["head_ref"],
                "head_sha": item["head_sha"],
                "committed_at": item["updated_at"],
                "last_commit_author": "example",
                "last_commit_subject": item["title"],
                "dev_reachable": False,
            }
            for item in (old_pr, recent_pr)
        ]

        with mock.patch.object(
            triage,
            "git_commit_trailers",
            return_value={"Task-ID": "TASK-001", "LLM-Agent": "Claude"},
        ):
            report, manifest = triage.build_report(
                task_id="OPS-TASK-PR-TRIAGE-002",
                repository="ajoe734/pantheon",
                remote="origin",
                base_ref="origin/dev",
                base_sha="c" * 40,
                as_of=datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
                overdue_hours=24,
                retention_days=30,
                expected_cohort_count=1,
                included_prs=[],
                history=[],
                open_prs=[old_pr, recent_pr],
                included_details={},
                branches=branches,
                active_tasks={},
                archives={},
            )

        self.assertEqual([item["number"] for item in report["cohort_prs"]], [10])
        self.assertEqual(report["summary"]["cohort_open_pr_count"], 1)
        self.assertEqual(report["summary"]["cohort_resolved_pr_count"], 0)
        self.assertEqual(report["summary"]["global_open_task_pr_count"], 2)
        markdown = triage.render_markdown(report, manifest)
        self.assertIn("1 remain open and 0 are now closed or merged", markdown)
        self.assertIn("Repository-wide, **2** task PRs are open", markdown)
        self.assertNotIn("2 remain open and 0 are now closed or merged", markdown)
        self.assertEqual(report["task_id"], "OPS-TASK-PR-TRIAGE-002")
        self.assertEqual(manifest["task_id"], "OPS-TASK-PR-TRIAGE-002")
        self.assertIn("# OPS-TASK-PR-TRIAGE-002 evidence report", markdown)

    def test_closure_comment_names_the_invoking_triage_task(self):
        result = triage.classify_pr(
            pr(),
            triage_task_id="OPS-TASK-PR-TRIAGE-002",
            repository="ajoe734/pantheon",
            dev_reachable=False,
            trailers={
                "Task-ID": "TASK-001",
                "LLM-Agent": "Claude",
                "Reviewer": "Codex",
            },
            active_tasks={},
            archives={"task-001": archive()},
            history_by_number={},
        )

        self.assertTrue(result["close_authorized"])
        self.assertTrue(
            result["closure_comment"].startswith(
                "OPS-TASK-PR-TRIAGE-002 evidence-based disposition"
            )
        )

    def test_refresh_completes_before_base_sha_capture(self):
        events = []

        def refresh(remote):
            events.append(("refresh", remote))

        def run(command, **_kwargs):
            events.append(("run", command))
            return SimpleNamespace(stdout="b" * 40 + "\n", returncode=0)

        with mock.patch.object(
            triage, "_refresh_refs", side_effect=refresh
        ), mock.patch.object(triage, "_run", side_effect=run):
            base_sha = triage.capture_base_snapshot("origin", "origin/dev", True)

        self.assertEqual(base_sha, "b" * 40)
        self.assertEqual(events[0], ("refresh", "origin"))
        self.assertEqual(
            events[1],
            ("run", ["git", "rev-parse", "--verify", "origin/dev^{commit}"]),
        )

    def test_branch_collection_uses_immutable_base_sha(self):
        commands = []

        def run(command, **_kwargs):
            commands.append(command)
            return SimpleNamespace(stdout="", returncode=0)

        base_sha = "b" * 40
        with mock.patch.object(triage, "_run", side_effect=run):
            self.assertEqual(triage.collect_branches("origin", base_sha), [])

        self.assertTrue(
            any(f"--merged={base_sha}" in command for command in commands)
        )

    def test_rejects_ancestry_mismatch_against_report_base_sha(self):
        head_sha = "a" * 40
        report = {
            "base_sha": "b" * 40,
            "branches": [
                {
                    "branch": "task/TASK-001",
                    "head_sha": head_sha,
                    "dev_reachable": False,
                }
            ],
            "cohort_prs": [],
        }
        with mock.patch.object(
            triage, "_snapshot_reachability", return_value={head_sha: True}
        ):
            with self.assertRaisesRegex(triage.TriageError, "ancestry decision"):
                triage.validate_report_ancestry(report)

    def test_apply_closure_requires_explicit_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text(json.dumps({"closure_candidates": []}))
            args = SimpleNamespace(
                report=str(report),
                apply=True,
                only=[],
                repository="ajoe734/pantheon",
            )
            with self.assertRaisesRegex(triage.TriageError, "explicit --only"):
                triage.cmd_close_superseded(args)

    def test_rejects_open_pr_remote_ref_snapshot_race(self):
        with self.assertRaisesRegex(triage.TriageError, "snapshots raced"):
            triage.validate_open_ref_consistency(
                {
                    "task/TASK-001": {
                        "number": 10,
                        "head_sha": "a" * 40,
                    }
                },
                {
                    "task/TASK-001": {
                        "branch": "task/TASK-001",
                        "head_sha": "b" * 40,
                    }
                },
            )

    def test_rejects_non_dry_run_manifest(self):
        report = {
            "cohort_prs": [],
            "branches": [],
        }
        manifest = {"mode": "apply", "candidate_count": 0, "candidates": []}
        with self.assertRaisesRegex(triage.TriageError, "dry-run-only"):
            triage.validate_report(report, manifest, 0)

    def test_rejects_mismatched_triage_task_identity(self):
        report = {
            "task_id": "OPS-TASK-PR-TRIAGE-002",
            "cohort_prs": [],
            "branches": [],
            "summary": {
                "cohort_pr_count": 0,
                "cohort_open_pr_count": 0,
                "cohort_resolved_pr_count": 0,
                "global_open_task_pr_count": 0,
            },
        }
        manifest = {
            "task_id": "OPS-TASK-PR-TRIAGE-001",
            "mode": "dry-run-only",
            "candidate_count": 0,
            "candidates": [],
        }
        with self.assertRaisesRegex(triage.TriageError, "task_id"):
            triage.validate_report(report, manifest, 0)

    def test_rejects_candidate_with_open_pr(self):
        report = {
            "cohort_prs": [],
            "branches": [
                {
                    "branch": "task/TASK-001",
                    "deletion_eligible": True,
                    "open_pr": 10,
                    "dev_reachable": True,
                }
            ],
        }
        manifest = {
            "mode": "dry-run-only",
            "candidate_count": 1,
            "candidates": [{"branch": "task/TASK-001"}],
        }
        with self.assertRaisesRegex(triage.TriageError, "open PR"):
            triage.validate_report(report, manifest, 0)

    def test_pr_reference_parser_keeps_external_repository(self):
        refs = triage.extract_pr_references(
            "execute-plans PR #218, execute-plans PRs #265/#267, and Pantheon PR #3057 merged; see "
            "https://github.com/ajoe734/pantheon/pull/3058",
            "ajoe734/pantheon",
        )
        self.assertIn(
            {"repository": "ajoe734/execute-plans", "number": 218, "source": "execute-plans PR #218"},
            refs,
        )
        self.assertTrue(
            any(ref["repository"] == "ajoe734/pantheon" and ref["number"] == 3057 for ref in refs)
        )
        self.assertFalse(
            any(ref["repository"] == "ajoe734/pantheon" and ref["number"] == 265 for ref in refs)
        )

    def test_pr_reference_parser_recognizes_superseded_by_number(self):
        refs = triage.extract_pr_references(
            "Superseded by #3948, which merged the canonical repair.",
            "ajoe734/pantheon",
        )
        self.assertEqual(refs[0]["number"], 3948)
        self.assertEqual(refs[0]["repository"], "ajoe734/pantheon")


if __name__ == "__main__":
    unittest.main()
