#!/usr/bin/env python3
"""Tests for the wave/git-workflow helper scripts.

Run with:
    python3 -m pytest scripts/git/test_git_workflow_helpers.py
or with unittest discovery:
    python3 -m unittest scripts/git/test_git_workflow_helpers.py
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _load(modname: str, path: Path):
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_trailers = _load("check_commit_trailers", HERE / "check_commit_trailers.py")
resolve_range = _load("resolve_commit_trailer_range", HERE / "resolve_commit_trailer_range.py")
publish_promote = _load("publish_promote", HERE / "publish_promote.py")
notify_orchestrator = _load("notify_orchestrator", HERE / "notify_orchestrator.py")


class CheckCommitTrailersTests(unittest.TestCase):
    REQ = ("LLM-Agent", "Task-ID", "Reviewer", "Wave")

    def test_accepts_well_formed_message(self) -> None:
        msg = (
            "EP5-FOO-001: example task closeout\n"
            "\n"
            "Body.\n"
            "\n"
            "LLM-Agent: Claude\n"
            "Task-ID: EP5-FOO-001\n"
            "Reviewer: Codex\n"
            "Wave: 2026-W21\n"
        )
        self.assertEqual(check_trailers.check_message(msg, self.REQ, True), [])

    def test_rejects_subject_without_task_id(self) -> None:
        msg = "no task id prefix here\n\nLLM-Agent: X\nTask-ID: T\nReviewer: R\nWave: W\n"
        problems = check_trailers.check_message(msg, self.REQ, True)
        self.assertTrue(any("subject must start with TASK-ID" in p for p in problems))

    def test_rejects_missing_trailer(self) -> None:
        msg = (
            "EP5-FOO-002: thing\n\n"
            "LLM-Agent: Claude\nTask-ID: EP5-FOO-002\nReviewer: Codex\n"
        )
        problems = check_trailers.check_message(msg, self.REQ, True)
        self.assertIn("missing trailer: Wave", problems)

    def test_rejects_empty_trailer_value(self) -> None:
        msg = (
            "EP5-FOO-003: thing\n\n"
            "LLM-Agent: Claude\nTask-ID: EP5-FOO-003\nReviewer:    \nWave: 2026-W21\n"
        )
        problems = check_trailers.check_message(msg, self.REQ, True)
        # "Reviewer:    " has whitespace value → the regex requires `.+` so it does
        # not parse as a trailer at all, surfacing as "missing trailer".
        self.assertTrue(
            any("Reviewer" in p for p in problems),
            f"expected a Reviewer-related problem in {problems!r}",
        )

    def test_exempts_merge_subject(self) -> None:
        msg = "Merge pull request #1234 from promote/v2026.20.0\n\npromote: v2026.20.0\n"
        self.assertEqual(check_trailers.check_message(msg, self.REQ, True), [])

    def test_exempts_wave_merge_subject(self) -> None:
        msg = "wave-merge: claude EP5-FOO-001\n"
        self.assertEqual(check_trailers.check_message(msg, self.REQ, True), [])

    def test_rejects_overlong_subject(self) -> None:
        long_subject = "EP5-FOO-004: " + ("x" * 80)
        msg = long_subject + "\n\nLLM-Agent: A\nTask-ID: EP5-FOO-004\nReviewer: B\nWave: 2026-W21\n"
        problems = check_trailers.check_message(msg, self.REQ, True)
        self.assertTrue(any("exceeds 72 chars" in p for p in problems))


class ResolveCommitTrailerRangeTests(unittest.TestCase):
    def test_uses_explicit_base_when_the_integration_target_is_unavailable(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="base",
            head_sha="head",
            ref_name="task/example",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"base", "head"},
            is_ancestor=lambda base, head: (base, head) == ("base", "head"),
            merge_base=lambda ref, head: None,
        )
        self.assertEqual(rev_range, "base..head")

    def test_task_push_measures_against_dev_not_the_previous_branch_tip(self) -> None:
        # Shape of failed run 30219364096 on task/SUP-WORKER-TRUTH-RECONCILE-001:
        # `before` is a real ancestor, but the push also carried the dev commits
        # the worker merged in while syncing the branch.
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="previous-branch-tip",
            head_sha="head",
            ref_name="task/SUP-WORKER-TRUTH-RECONCILE-001",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"previous-branch-tip", "head", "origin/dev"},
            is_ancestor=lambda base, head: True,
            merge_base=lambda ref, head: "dev-base",
        )
        self.assertEqual(rev_range, "origin/dev..head")

    def test_hotfix_push_measures_against_dev_as_well(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="previous-branch-tip",
            head_sha="head",
            ref_name="hotfix/urgent",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"previous-branch-tip", "head", "origin/dev"},
            is_ancestor=lambda base, head: True,
            merge_base=lambda ref, head: "dev-base",
        )
        self.assertEqual(rev_range, "origin/dev..head")

    def test_falls_back_to_origin_dev_when_force_push_before_sha_is_missing(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="old-before",
            head_sha="head",
            ref_name="task/example",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"head", "origin/dev", "dev-base"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: "dev-base" if ref == "origin/dev" else None,
        )
        self.assertEqual(rev_range, "origin/dev..head")

    def test_task_push_falls_back_to_the_merge_base_without_a_remote_tracking_ref(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="rewritten-before",
            head_sha="head",
            ref_name="task/example",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"rewritten-before", "head", "dev-base"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: "dev-base" if ref == "origin/dev" else None,
        )
        self.assertEqual(rev_range, "dev-base..head")

    def test_publish_push_keeps_measuring_from_the_previous_branch_tip(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="previous-publish-tip",
            head_sha="head",
            ref_name="publish/v2026.07.20.0",
            pr_base_ref="",
            commit_exists=lambda rev: rev
            in {"previous-publish-tip", "head", "origin/master", "origin/dev"},
            is_ancestor=lambda base, head: True,
            merge_base=lambda ref, head: "master-base",
        )
        self.assertEqual(rev_range, "previous-publish-tip..head")

    def test_dev_push_keeps_measuring_from_the_previous_branch_tip(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="previous-dev-tip",
            head_sha="head",
            ref_name="dev",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"previous-dev-tip", "head", "origin/dev"},
            is_ancestor=lambda base, head: True,
            merge_base=lambda ref, head: "dev-base",
        )
        self.assertEqual(rev_range, "previous-dev-tip..head")

    def test_pull_request_excludes_base_branch_and_ignores_synthetic_merge(self) -> None:
        # Shape of failed run 30219467575 on PR #4211: the event carries a
        # stale base.sha and github.sha is the synthetic merge commit that
        # already contains dev commits owned by other tasks.
        rev_range = resolve_range.resolve_commit_range(
            event="pull_request",
            base_sha="stale-dev-tip",
            head_sha="synthetic-merge",
            ref_name="4211/merge",
            pr_base_ref="dev",
            pr_head_sha="pr-head",
            commit_exists=lambda rev: rev
            in {"pr-head", "synthetic-merge", "stale-dev-tip", "origin/dev"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: "fork-point",
        )
        self.assertEqual(rev_range, "origin/dev..pr-head")
        self.assertNotIn("synthetic-merge", rev_range)

    def test_pull_request_falls_back_to_base_sha_when_base_ref_is_unavailable(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="pull_request",
            base_sha="base-sha",
            head_sha="synthetic-merge",
            ref_name="4211/merge",
            pr_base_ref="dev",
            pr_head_sha="pr-head",
            commit_exists=lambda rev: rev in {"pr-head", "base-sha", "synthetic-merge"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: None,
        )
        self.assertEqual(rev_range, "base-sha..pr-head")

    def test_pull_request_fails_closed_without_a_pr_head_sha(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_range.resolve_commit_range(
                event="pull_request",
                base_sha="base-sha",
                head_sha="synthetic-merge",
                ref_name="4211/merge",
                pr_base_ref="dev",
                pr_head_sha="",
                commit_exists=lambda rev: True,
                is_ancestor=lambda base, head: True,
                merge_base=lambda ref, head: "fork-point",
            )
        self.assertIn("synthetic merge commit", str(ctx.exception))

    def test_pull_request_fails_closed_when_the_head_object_is_missing(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_range.resolve_commit_range(
                event="pull_request",
                base_sha="base-sha",
                head_sha="synthetic-merge",
                ref_name="4211/merge",
                pr_base_ref="dev",
                pr_head_sha="force-pushed-away",
                commit_exists=lambda rev: rev in {"base-sha", "origin/dev"},
                is_ancestor=lambda base, head: False,
                merge_base=lambda ref, head: "fork-point",
            )
        self.assertIn("head commit is not available", str(ctx.exception))

    def test_pull_request_fails_closed_when_no_base_candidate_resolves(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_range.resolve_commit_range(
                event="pull_request",
                base_sha=resolve_range.ZERO_SHA,
                head_sha="synthetic-merge",
                ref_name="4211/merge",
                pr_base_ref="dev",
                pr_head_sha="pr-head",
                commit_exists=lambda rev: rev == "pr-head",
                is_ancestor=lambda base, head: False,
                merge_base=lambda ref, head: None,
            )
        self.assertIn("base is not available", str(ctx.exception))

    def test_pull_request_never_falls_back_to_the_head_parent(self) -> None:
        # head^ would be a base-branch commit on a synthetic merge and the
        # fork point on a real head; neither is an acceptable silent default.
        with self.assertRaises(ValueError):
            resolve_range.resolve_commit_range(
                event="pull_request",
                base_sha="",
                head_sha="synthetic-merge",
                ref_name="4211/merge",
                pr_base_ref="",
                pr_head_sha="pr-head",
                commit_exists=lambda rev: rev in {"pr-head", "pr-head^"},
                is_ancestor=lambda base, head: False,
                merge_base=lambda ref, head: None,
            )

    def test_publish_push_still_measures_against_master_first(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha=resolve_range.ZERO_SHA,
            head_sha="head",
            ref_name="publish/v2026.07.20.0",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"head", "origin/master", "origin/dev"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: {
                "origin/master": "master-base",
                "origin/dev": "dev-base",
            }.get(ref),
        )
        self.assertEqual(rev_range, "master-base..head")

    def test_dev_push_without_usable_base_checks_head_parent(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha=resolve_range.ZERO_SHA,
            head_sha="head",
            ref_name="dev",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"head", "head^"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: None,
        )
        self.assertEqual(rev_range, "head^..head")

    def test_root_commit_without_usable_base_checks_head_revision(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha=resolve_range.ZERO_SHA,
            head_sha="head",
            ref_name="dev",
            pr_base_ref="",
            commit_exists=lambda rev: rev == "head",
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: None,
        )
        self.assertEqual(rev_range, "head")


class PullRequestTrailerRangeLiveRegressionTests(unittest.TestCase):
    """End-to-end regression on a real git repo shaped like PR #4211 / #4215.

    dev carries ``DEV_BAD``, a squash-merge commit owned by another task whose
    subject is 79 chars. Both task branches were cut *before* it landed, so it
    is not reachable from either PR head -- yet the old contract scanned
    ``base.sha..github.sha`` where ``github.sha`` is the synthetic merge
    commit, which does contain it.
    """

    # 79 chars, mirroring dev commit 0410a89f0.
    DEV_BAD_SUBJECT = (
        "OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001: record isolation evidence (#4213)"
    )

    def _git(self, repo: Path, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args], cwd=repo, check=check, capture_output=True, text=True
        )

    def _rev(self, repo: Path, rev: str = "HEAD") -> str:
        return self._git(repo, "rev-parse", rev).stdout.strip()

    def _commit(self, repo: Path, name: str, message: str) -> str:
        (repo / name).write_text(f"{name}\n")
        self._git(repo, "add", name)
        self._git(repo, "commit", "-m", message)
        return self._rev(repo)

    @staticmethod
    def _task_message(task_id: str, summary: str, *, reviewer: str = "Codex2") -> str:
        return (
            f"{task_id}: {summary}\n\n"
            "Body.\n\n"
            "LLM-Agent: Claude\n"
            f"Task-ID: {task_id}\n"
            f"Reviewer: {reviewer}\n"
        )

    def _build_repo(self, tmp: str) -> tuple[Path, dict[str, str]]:
        repo = Path(tmp)
        self._git(repo, "init", "-b", "dev")
        self._git(repo, "config", "user.name", "fixture")
        self._git(repo, "config", "user.email", "fixture@example.invalid")

        self._commit(repo, "root.txt", "Initial commit")
        # The integration base recorded in pull_request.base.sha.
        base = self._commit(
            repo,
            "base.txt",
            "Merge pull request #4210 from ajoe734/task/L12-SIGNOFF-001",
        )

        heads: dict[str, str] = {"base": base}
        branches = {
            "pr4211": (
                "task/OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001",
                self._task_message(
                    "OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001", "anchor infra authority"
                ),
            ),
            "pr4215": (
                "task/SUP-WORKER-TRUTH-RECONCILE-001",
                self._task_message(
                    "SUP-WORKER-TRUTH-RECONCILE-001", "reconcile worker truth"
                ),
            ),
        }
        for key, (branch, message) in branches.items():
            self._git(repo, "checkout", "-b", branch, base)
            heads[key] = self._commit(repo, f"{key}.txt", message)

        # A genuinely malformed task head: no Reviewer trailer.
        self._git(repo, "checkout", "-b", "task/BROKEN-001", base)
        heads["broken"] = self._commit(
            repo,
            "broken.txt",
            "BROKEN-001: land without a reviewer\n\nLLM-Agent: Claude\nTask-ID: BROKEN-001\n",
        )

        # dev advances with another task's already-merged, overlong commit.
        self._git(repo, "checkout", "dev")
        heads["dev_bad"] = self._commit(
            repo,
            "isolation.txt",
            self.DEV_BAD_SUBJECT
            + "\n\nLLM-Agent: Codex2\nTask-ID: OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001\n"
            "Reviewer: Claude\n",
        )
        # actions/checkout with fetch-depth: 0 provides this remote-tracking ref.
        self._git(repo, "update-ref", "refs/remotes/origin/dev", heads["dev_bad"])

        # Concurrent-dev-advance shape: a task branch that merged dev back in.
        self._git(repo, "checkout", "-b", "task/MERGED-DEV-001", base)
        heads["merged_dev_before"] = self._commit(
            repo,
            "merged.txt",
            self._task_message("MERGED-DEV-001", "own one layer"),
        )
        self._git(repo, "merge", "--no-ff", heads["dev_bad"], "-m", "Merge dev into task")
        heads["merged_dev"] = self._rev(repo)

        # GitHub's synthetic refs/pull/N/merge commits.
        for key in ("pr4211", "pr4215", "broken", "merged_dev"):
            self._git(repo, "checkout", "-B", f"synthetic/{key}", heads["dev_bad"])
            self._git(
                repo,
                "merge",
                "--no-ff",
                heads[key],
                "-m",
                f"Merge {heads[key]} into dev",
                check=False,
            )
            heads[f"synthetic_{key}"] = self._rev(repo)

        self._git(repo, "checkout", "dev")
        return repo, heads

    def _resolve(self, repo: Path, **kwargs) -> str:
        with mock.patch.object(resolve_range, "ROOT", repo):
            return resolve_range.resolve_commit_range(**kwargs)

    def _scan(self, repo: Path, rev_range: str) -> tuple[int, list[str]]:
        """Run the real trailer gate over a range; return (exit code, shas)."""
        argv = ["check_commit_trailers.py", "--range", rev_range, "--skip-merge"]
        with (
            mock.patch.object(check_trailers, "ROOT", repo),
            mock.patch.object(check_trailers, "CONFIG_FILE", repo / "no-config.json"),
            mock.patch.dict(os.environ, {"PANTHEON_TRAILER_CHECK_DISABLED": "0"}),
            mock.patch.object(sys, "argv", argv),
        ):
            shas = [sha for sha, _ in check_trailers.collect_messages_from_range(rev_range)]
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = check_trailers.main()
        return code, shas

    def _pr_range(self, repo: Path, heads: dict[str, str], key: str, number: str) -> str:
        return self._resolve(
            repo,
            event="pull_request",
            base_sha=heads["base"],
            head_sha=heads[f"synthetic_{key}"],
            ref_name=f"{number}/merge",
            pr_base_ref="dev",
            pr_head_sha=heads[key],
        )

    def test_old_range_contract_reproduces_the_dev_commit_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            for key in ("pr4211", "pr4215"):
                stale_range = f"{heads['base']}..{heads[f'synthetic_{key}']}"
                code, shas = self._scan(repo, stale_range)
                self.assertIn(
                    heads["dev_bad"],
                    shas,
                    f"{key}: expected the stale contract to scan the dev commit",
                )
                self.assertEqual(code, 1, f"{key}: expected the stale contract to fail")

    def test_repaired_range_judges_both_task_heads_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            for key, number in (("pr4211", "4211"), ("pr4215", "4215")):
                rev_range = self._pr_range(repo, heads, key, number)
                self.assertEqual(rev_range, f"origin/dev..{heads[key]}")
                code, shas = self._scan(repo, rev_range)
                self.assertEqual(shas, [heads[key]], f"{key}: scanned {shas}")
                self.assertNotIn(heads["dev_bad"], shas)
                self.assertNotIn(heads[f"synthetic_{key}"], shas)
                self.assertEqual(code, 0, f"{key}: expected the task head to pass")

    def test_repaired_range_still_fails_a_malformed_task_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            rev_range = self._pr_range(repo, heads, "broken", "9001")
            code, shas = self._scan(repo, rev_range)
            self.assertEqual(shas, [heads["broken"]])
            self.assertEqual(code, 1)

    def test_concurrent_dev_advance_merged_into_the_task_branch_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            rev_range = self._pr_range(repo, heads, "merged_dev", "9002")
            code, shas = self._scan(repo, rev_range)
            self.assertNotIn(heads["dev_bad"], shas)
            self.assertEqual(code, 0)

    def test_old_push_contract_reproduces_the_4215_contamination(self) -> None:
        # Run 30219364096: `before..github.sha` on a task branch that had just
        # synced dev in, so dev commit 0410a89f was inside the pushed range.
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            stale_range = f"{heads['merged_dev_before']}..{heads['merged_dev']}"
            code, shas = self._scan(repo, stale_range)
            self.assertIn(heads["dev_bad"], shas)
            self.assertEqual(code, 1)

    def test_repaired_push_range_drops_the_synced_dev_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            rev_range = self._resolve(
                repo,
                event="push",
                base_sha=heads["merged_dev_before"],
                head_sha=heads["merged_dev"],
                ref_name="task/MERGED-DEV-001",
                pr_base_ref="",
            )
            self.assertEqual(rev_range, f"origin/dev..{heads['merged_dev']}")
            code, shas = self._scan(repo, rev_range)
            self.assertNotIn(heads["dev_bad"], shas)
            self.assertIn(heads["merged_dev_before"], shas)
            self.assertEqual(code, 0)

    def test_stale_base_sha_alone_would_still_admit_the_merged_dev_commit(self) -> None:
        # Documents why the live base tip is preferred over base.sha.
        with tempfile.TemporaryDirectory() as tmp:
            repo, heads = self._build_repo(tmp)
            _code, shas = self._scan(
                repo, f"{heads['base']}..{heads['merged_dev']}"
            )
            self.assertIn(heads["dev_bad"], shas)

    def test_workflow_passes_the_pr_head_sha_and_never_the_synthetic_merge(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "branch-ci.yml").read_text()
        self.assertIn("PR_HEAD_SHA: ${{ github.event.pull_request.head.sha || '' }}", workflow)
        self.assertIn('--pr-head-sha "$PR_HEAD_SHA"', workflow)
        self.assertIn('--range "$RANGE"', workflow)
        self.assertNotIn('--range "${{ steps.range.outputs.range }}"', workflow)

    def test_workflow_deduplicates_push_and_pr_checks_by_head_branch(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "branch-ci.yml").read_text()
        self.assertIn("github.head_ref || github.ref_name", workflow)
        self.assertIn("github.event.pull_request.labels.*.name", workflow)
        self.assertNotIn("github.event.head_commit.message, 'Delivery-Type: tooling'", workflow)
        self.assertIn('--delivery-class "$DELIVERY_CLASS"', workflow)


class PublishPromoteTests(unittest.TestCase):
    SETTINGS = {
        "main_branch": "master",
        "publish_branch_prefix": "publish/",
        "release_tag_prefix": "release/",
        "soak_days": 0,
        "regression_label_prefix": "regression/",
        "block_labels": [],
        "promote_pr_label": "auto-promote",
        "version_format": "vYYYY.MM.DD.N",
    }

    def _discover(self, tags, *, input_version=None, blockers=None, ancestor=None, existing=None, mode=None):
        blockers = blockers or {}
        ancestor = ancestor or (lambda _left, _right: False)
        existing = existing or {}
        mode = mode or (lambda _main, _release: ("clean_merge", "clean"))
        with (
            mock.patch.object(publish_promote, "fetch_promote_refs"),
            mock.patch.object(publish_promote, "list_release_tags", return_value=tags),
            mock.patch.object(publish_promote, "publish_ref_matches_tag", return_value=True),
            mock.patch.object(publish_promote, "git_is_ancestor", side_effect=ancestor),
            mock.patch.object(
                publish_promote,
                "fetch_blocking_issue_map",
                return_value=(blockers, [], None),
            ),
            mock.patch.object(
                publish_promote,
                "list_open_promote_prs",
                return_value=(existing, None),
            ),
            mock.patch.object(publish_promote, "assess_promotion_mode", side_effect=mode),
        ):
            return publish_promote.discover(
                input_version=input_version,
                soak_days=3,
                prefix="regression/",
                block_labels=[],
                publish_prefix="publish/",
            )

    def test_discover_reports_recent_tags_as_soaking(self) -> None:
        now = datetime.now(timezone.utc)
        recent = now.replace(microsecond=0)
        old = recent.replace(year=recent.year - 1)
        cands = self._discover([("v2026.07.20.0", recent), ("v2025.07.10.0", old)])
        dispositions = {c["version"]: c["disposition"] for c in cands}
        self.assertEqual(dispositions["v2026.07.20.0"], "soaking")
        self.assertEqual(dispositions["v2025.07.10.0"], "eligible")

    def test_discover_includes_recent_when_version_forced(self) -> None:
        now = datetime.now(timezone.utc)
        cands = self._discover([("v2026.07.20.0", now)], input_version="v2026.07.20.0")
        self.assertEqual(cands[0]["disposition"], "eligible")

    def test_forced_historical_version_remains_superseded_by_later_snapshot(self) -> None:
        now = datetime.now(timezone.utc)
        older = now.replace(year=now.year - 2)
        newer = now.replace(year=now.year - 1)
        cands = self._discover(
            [("v2024.07.10.0", older), ("v2025.07.10.0", newer)],
            input_version="v2024.07.10.0",
            ancestor=lambda left, right: left.endswith("v2024.07.10.0")
            and right.endswith("v2025.07.10.0"),
        )
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["disposition"], "superseded")
        self.assertEqual(cands[0]["superseded_by"], "v2025.07.10.0")

    def test_discover_reports_legacy_weekly_tags_without_promoting(self) -> None:
        old = datetime.now(timezone.utc).replace(year=2025)
        cands = self._discover([("v2025.25.0", old)])
        self.assertEqual(cands[0]["disposition"], "legacy_format")

    def test_discover_reports_already_reachable(self) -> None:
        now = datetime.now(timezone.utc)
        old = now.replace(year=now.year - 1)
        cands = self._discover(
            [("v2025.07.10.0", old)],
            ancestor=lambda left, right: left.startswith("refs/tags/") and right == "origin/master",
        )
        self.assertEqual(cands[0]["disposition"], "already_reachable")

    def test_discover_records_blockers(self) -> None:
        now = datetime.now(timezone.utc)
        old = now.replace(year=now.year - 1)
        cands = self._discover(
            [("v2025.07.10.0", old)],
            blockers={"v2025.07.10.0": ["#42 regression in X"]},
        )
        self.assertEqual(cands[0]["disposition"], "blocked")
        self.assertEqual(cands[0]["blockers"], ["#42 regression in X"])

    def test_discover_marks_older_ancestor_superseded(self) -> None:
        now = datetime.now(timezone.utc)
        older = now.replace(year=now.year - 2)
        newer = now.replace(year=now.year - 1)
        cands = self._discover(
            [("v2024.07.10.0", older), ("v2025.07.10.0", newer)],
            ancestor=lambda left, right: left.endswith("v2024.07.10.0")
            and right.endswith("v2025.07.10.0"),
        )
        dispositions = {c["version"]: c for c in cands}
        self.assertEqual(dispositions["v2024.07.10.0"]["disposition"], "superseded")
        self.assertEqual(dispositions["v2024.07.10.0"]["superseded_by"], "v2025.07.10.0")
        self.assertEqual(dispositions["v2025.07.10.0"]["disposition"], "eligible")

    def test_discover_schedules_only_maximal_existing_pr_for_exact_check_lookup(self) -> None:
        now = datetime.now(timezone.utc)
        older = now.replace(year=now.year - 2)
        newer = now.replace(year=now.year - 1)
        existing = {
            "promote/v2024.07.10.0": {
                "number": 40,
            },
            "promote/v2025.07.10.0": {
                "number": 41,
            },
        }
        cands = self._discover(
            [("v2024.07.10.0", older), ("v2025.07.10.0", newer)],
            ancestor=lambda left, right: left.endswith("v2024.07.10.0")
            and right.endswith("v2025.07.10.0"),
            existing=existing,
        )
        dispositions = {c["version"]: c for c in cands}
        self.assertEqual(dispositions["v2024.07.10.0"]["disposition"], "superseded")
        self.assertEqual(dispositions["v2025.07.10.0"]["disposition"], "existing_pr")

    def test_discover_bulk_lookup_does_not_need_status_rollup(self) -> None:
        old = datetime.now(timezone.utc).replace(year=2025)
        cands = self._discover(
            [("v2025.07.10.0", old)],
            existing={
                "promote/v2025.07.10.0": {
                    "number": 41,
                }
            },
        )
        self.assertEqual(cands[0]["disposition"], "existing_pr")

    def test_cmd_discover_sends_existing_pr_to_exact_action_step(self) -> None:
        candidate = {
            "version": "v2025.07.10.0",
            "disposition": "existing_pr",
        }
        with (
            tempfile.NamedTemporaryFile() as output,
            mock.patch.object(publish_promote, "load_promote_settings", return_value=self.SETTINGS),
            mock.patch.object(publish_promote, "discover", return_value=[candidate]),
            mock.patch.object(publish_promote, "append_step_summary"),
        ):
            rc = publish_promote.cmd_discover(
                argparse.Namespace(version=None, github_output=output.name)
            )
            output.seek(0)
            rendered = output.read().decode()
        self.assertEqual(rc, 0)
        self.assertIn("candidate_count=1", rendered)
        self.assertIn('"disposition": "existing_pr"', rendered)

    def test_discover_reports_content_conflict(self) -> None:
        old = datetime.now(timezone.utc).replace(year=2024)
        cands = self._discover(
            [("v2024.07.10.0", old)],
            mode=lambda _main, _release: ("conflicted", "both modified config"),
        )
        self.assertEqual(cands[0]["disposition"], "conflicted")

    def _git(self, repo: Path, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args], cwd=repo, check=check, capture_output=True, text=True
        )

    def test_v2026_07_15_0_fixture_reproduces_unrelated_history_and_bridges_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "master")
            self._git(repo, "config", "user.name", "fixture")
            self._git(repo, "config", "user.email", "fixture@example.invalid")
            (repo / "state.txt").write_text("master snapshot\n")
            self._git(repo, "add", "state.txt")
            self._git(repo, "commit", "-m", "re-rooted master")
            master = self._git(repo, "rev-parse", "HEAD").stdout.strip()
            master_tree = self._git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()

            self._git(repo, "checkout", "--orphan", "publish/v2026.07.15.0")
            self._git(repo, "rm", "-rf", ".")
            (repo / "state.txt").write_text("accepted dev snapshot\n")
            self._git(repo, "add", "state.txt")
            self._git(repo, "commit", "-m", "publish snapshot")
            self._git(repo, "tag", "release/v2026.07.15.0")
            release_tree = self._git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
            self._git(repo, "checkout", "master")

            failed = self._git(
                repo, "merge", "--no-ff", "release/v2026.07.15.0", check=False
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("unrelated histories", failed.stderr)

            with mock.patch.object(publish_promote, "ROOT", repo):
                mode, detail = publish_promote.assess_promotion_mode(
                    master, "release/v2026.07.15.0"
                )
                self.assertEqual((mode, detail), ("snapshot_bridge", "histories are unrelated"))
                self._git(repo, "checkout", "-B", "promote/v2026.07.15.0", master)
                commit = publish_promote.create_snapshot_bridge(
                    "v2026.07.15.0", master, "release/v2026.07.15.0"
                )

            parents = self._git(repo, "show", "-s", "--format=%P", commit).stdout.split()
            self.assertEqual(parents[0], master)
            self.assertEqual(len(parents), 2)
            self.assertEqual(self._git(repo, "rev-parse", f"{commit}^{{tree}}").stdout.strip(), release_tree)
            self._git(repo, "revert", "-m", "1", commit, "--no-edit")
            self.assertEqual(
                self._git(repo, "rev-parse", "HEAD^{tree}").stdout.strip(), master_tree
            )

    def test_common_history_conflict_uses_exact_snapshot_and_is_rollback_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "master")
            self._git(repo, "config", "user.name", "fixture")
            self._git(repo, "config", "user.email", "fixture@example.invalid")
            (repo / "state.txt").write_text("base\n")
            self._git(repo, "add", "state.txt")
            self._git(repo, "commit", "-m", "base")
            self._git(repo, "checkout", "-b", "publish")
            (repo / "state.txt").write_text("publish\n")
            self._git(repo, "commit", "-am", "publish")
            release = self._git(repo, "rev-parse", "HEAD").stdout.strip()
            release_tree = self._git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
            self._git(repo, "checkout", "master")
            (repo / "state.txt").write_text("master\n")
            self._git(repo, "commit", "-am", "master")
            master = self._git(repo, "rev-parse", "HEAD").stdout.strip()
            master_tree = self._git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
            with mock.patch.object(publish_promote, "ROOT", repo):
                mode, _detail = publish_promote.assess_promotion_mode(master, release)
                self.assertEqual(mode, "snapshot_replace")
                merge_commit = publish_promote.create_snapshot_bridge(
                    "v2026.07.22.2", master, release
                )
            parents = self._git(repo, "show", "-s", "--format=%P", merge_commit).stdout.split()
            self.assertEqual(parents, [master, release])
            self.assertEqual(
                self._git(repo, "rev-parse", f"{merge_commit}^{{tree}}").stdout.strip(),
                release_tree,
            )
            self._git(repo, "revert", "-m", "1", merge_commit, "--no-edit")
            self.assertEqual(
                self._git(repo, "rev-parse", "HEAD^{tree}").stdout.strip(), master_tree
            )

    def test_empty_successful_merge_base_is_portably_unrelated(self) -> None:
        no_base = subprocess.CompletedProcess(
            ["git", "merge-base"], returncode=0, stdout="", stderr=""
        )
        with mock.patch.object(publish_promote, "_run_git_result", return_value=no_base):
            mode, detail = publish_promote.assess_promotion_mode("master", "release")
        self.assertEqual((mode, detail), ("snapshot_bridge", "histories are unrelated"))

    def test_open_candidate_uses_normal_push_and_protected_auto_merge(self) -> None:
        candidate = {
            "version": "v2026.20.0",
            "publish_branch": "publish/v2026.20.0",
            "age_days": 1.25,
            "blockers": [],
            "promote_branch": "promote/v2026.20.0",
            "promotion_mode": "clean_merge",
        }
        with (
            mock.patch.object(publish_promote, "run_git") as run_git,
            mock.patch.object(publish_promote, "publish_ref_matches_tag", return_value=True),
            mock.patch.object(
                publish_promote,
                "find_open_promote_pr",
                side_effect=[
                    (None, None),
                    (
                        {
                            "number": 42,
                            "headRefOid": "a" * 40,
                            "statusCheckRollup": [],
                        },
                        None,
                    ),
                ],
            ),
            mock.patch.object(
                publish_promote,
                "promote_ref_supports_ci_dispatch",
                return_value=(True, None),
            ),
            mock.patch.object(
                publish_promote,
                "request_verified_auto_merge",
                return_value={"auto_merge_enabled": True, "merged": False},
            ) as auto_merge,
            mock.patch.object(
                publish_promote,
                "rerun_action_required_branch_ci",
                return_value=1234,
            ) as rerun_branch_ci,
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            run_git.side_effect = lambda *args: "a" * 40 if args == ("rev-parse", "HEAD") else ""
            result = publish_promote.open_candidate(candidate, self.SETTINGS)

        self.assertEqual(result["disposition"], "pr_opened")
        run_git.assert_any_call("fetch", "origin", "master", "--tags")
        run_git.assert_any_call("push", "-u", "origin", "promote/v2026.20.0")
        for call in run_git.call_args_list:
            self.assertNotIn("--force", call.args)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            ["gh", "pr", "edit", "promote/v2026.20.0", "--add-label", "auto-promote"],
            commands,
        )
        self.assertIn(
            [
                "gh",
                "workflow",
                "run",
                "branch-ci.yml",
                "--ref",
                "promote/v2026.20.0",
                "-f",
                f"expected_head_sha={'a' * 40}",
                "-f",
                "promote_pr_number=42",
            ],
            commands,
        )
        rerun_branch_ci.assert_called_once_with("a" * 40, 42)
        self.assertEqual(result["pull_request_ci_rerun"], 1234)
        auto_merge.assert_called_once_with("promote/v2026.20.0", 42)

    def test_open_candidate_is_idempotent_when_pr_exists(self) -> None:
        candidate = {
            "version": "v2026.20.0",
            "publish_branch": "publish/v2026.20.0",
            "age_days": 1.25,
            "blockers": [],
            "promote_branch": "promote/v2026.20.0",
            "promotion_mode": "clean_merge",
        }
        with (
            mock.patch.object(publish_promote, "run_git") as run_git,
            mock.patch.object(publish_promote, "publish_ref_matches_tag", return_value=True),
            mock.patch.object(
                publish_promote,
                "find_open_promote_pr",
                return_value=(
                    {
                        "number": 42,
                        "url": "https://example.invalid/42",
                        "headRefOid": "b" * 40,
                        "statusCheckRollup": [
                            {"name": "Commit trailers"},
                            {"name": "Runtime mirror guard"},
                            {"name": "Smoke acceptance"},
                        ],
                    },
                    None,
                ),
            ),
            mock.patch.object(
                publish_promote,
                "promote_ref_supports_ci_dispatch",
                return_value=(True, None),
            ),
            mock.patch.object(
                publish_promote,
                "request_verified_auto_merge",
                return_value={"auto_merge_enabled": True, "merged": False},
            ) as auto_merge,
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            result = publish_promote.open_candidate(candidate, self.SETTINGS)
        self.assertEqual(result["disposition"], "existing_pr")
        self.assertFalse(any(call.args[0] == "push" for call in run_git.call_args_list))
        self.assertFalse(run.called)

    def test_open_candidate_repairs_existing_pr_with_zero_checks(self) -> None:
        candidate = {
            "version": "v2026.20.0",
            "publish_branch": "publish/v2026.20.0",
            "age_days": 1.25,
            "blockers": [],
            "promote_branch": "promote/v2026.20.0",
            "promotion_mode": "clean_merge",
        }
        with (
            mock.patch.object(publish_promote, "run_git"),
            mock.patch.object(publish_promote, "publish_ref_matches_tag", return_value=True),
            mock.patch.object(
                publish_promote,
                "find_open_promote_pr",
                return_value=(
                    {
                        "number": 42,
                        "url": "https://example.invalid/42",
                        "headRefOid": "c" * 40,
                        "statusCheckRollup": [],
                    },
                    None,
                ),
            ),
            mock.patch.object(
                publish_promote,
                "promote_ref_supports_ci_dispatch",
                return_value=(True, None),
            ),
            mock.patch.object(
                publish_promote,
                "request_verified_auto_merge",
                return_value={"auto_merge_enabled": True, "merged": False},
            ) as auto_merge,
            mock.patch.object(
                publish_promote,
                "rerun_action_required_branch_ci",
                return_value=5678,
            ) as rerun_branch_ci,
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            result = publish_promote.open_candidate(candidate, self.SETTINGS)
        self.assertEqual(result["disposition"], "ci_dispatched")
        self.assertEqual(result["head_sha"], "c" * 40)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "gh",
                "workflow",
                "run",
                "branch-ci.yml",
                "--ref",
                "promote/v2026.20.0",
                "-f",
                f"expected_head_sha={'c' * 40}",
                "-f",
                "promote_pr_number=42",
            ],
            commands,
        )
        rerun_branch_ci.assert_called_once_with("c" * 40, 42)
        self.assertEqual(result["pull_request_ci_rerun"], 5678)
        auto_merge.assert_called_once_with("promote/v2026.20.0", 42)

    def test_reruns_exact_action_required_branch_ci_placeholder(self) -> None:
        row = {
            "id": 30451895166,
            "path": ".github/workflows/branch-ci.yml",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "action_required",
            "head_sha": "d" * 40,
            "pull_requests": [{"number": 4378}],
        }
        completed = subprocess.CompletedProcess(
            ["gh", "api"], returncode=0, stdout="{}", stderr=""
        )
        with (
            mock.patch.object(
                publish_promote,
                "_gh_api_rows",
                return_value=([row], None),
            ) as lookup,
            mock.patch.object(
                publish_promote.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            run_id = publish_promote.rerun_action_required_branch_ci(
                "d" * 40,
                4378,
                discovery_attempts=1,
                discovery_interval=0,
            )

        self.assertEqual(run_id, 30451895166)
        self.assertIn("event=pull_request", lookup.call_args.args[0])
        self.assertIn("head_sha=" + "d" * 40, lookup.call_args.args[0])
        self.assertEqual(
            run.call_args.args[0],
            [
                "gh",
                "api",
                "--method",
                "POST",
                "repos/{owner}/{repo}/actions/runs/30451895166/rerun",
            ],
        )

    def test_placeholder_rerun_ignores_a_different_pr_or_head(self) -> None:
        rows = [
            {
                "id": 9,
                "path": ".github/workflows/branch-ci.yml",
                "event": "pull_request",
                "conclusion": "action_required",
                "head_sha": "e" * 40,
                "pull_requests": [{"number": 99}],
            },
            {
                "id": 8,
                "path": ".github/workflows/branch-ci.yml",
                "event": "pull_request",
                "conclusion": "success",
                "head_sha": "d" * 40,
                "pull_requests": [{"number": 42}],
            },
        ]
        with (
            mock.patch.object(
                publish_promote,
                "_gh_api_rows",
                return_value=(rows, None),
            ),
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            run_id = publish_promote.rerun_action_required_branch_ci(
                "d" * 40,
                42,
                discovery_attempts=1,
                discovery_interval=0,
            )

        self.assertIsNone(run_id)
        self.assertFalse(run.called)

    def test_existing_legacy_promote_ref_is_retained_without_dispatch_error(self) -> None:
        candidate = {
            "version": "v2026.07.26.2",
            "publish_branch": "publish/v2026.07.26.2",
            "age_days": 3.0,
            "blockers": [],
            "promote_branch": "promote/v2026.07.26.2",
            "promotion_mode": "clean_merge",
        }
        with (
            mock.patch.object(publish_promote, "run_git"),
            mock.patch.object(publish_promote, "publish_ref_matches_tag", return_value=True),
            mock.patch.object(
                publish_promote,
                "find_open_promote_pr",
                return_value=(
                    {
                        "number": 4138,
                        "url": "https://example.invalid/4138",
                        "headRefOid": "c" * 40,
                        "statusCheckRollup": [],
                    },
                    None,
                ),
            ),
            mock.patch.object(
                publish_promote,
                "promote_ref_supports_ci_dispatch",
                return_value=(False, None),
            ),
            mock.patch.object(
                publish_promote, "dispatch_promote_ci"
            ) as dispatch,
            mock.patch.object(
                publish_promote, "request_verified_auto_merge"
            ) as auto_merge,
        ):
            result = publish_promote.open_candidate(candidate, self.SETTINGS)
        self.assertEqual(result["disposition"], "legacy_ci_contract")
        self.assertEqual(result["head_sha"], "c" * 40)
        self.assertFalse(dispatch.called)
        self.assertFalse(auto_merge.called)

    def test_promote_ref_dispatch_contract_is_read_from_exact_head(self) -> None:
        workflow = (
            "on:\n  workflow_dispatch:\n    inputs:\n"
            "      expected_head_sha:\n      promote_pr_number:\n"
        )
        with mock.patch.object(
            publish_promote,
            "_gh_api_object",
            return_value=(
                {
                    "content": base64.b64encode(workflow.encode()).decode(),
                },
                None,
            ),
        ) as api:
            supported, error = publish_promote.promote_ref_supports_ci_dispatch(
                "d" * 40
            )
        self.assertEqual((supported, error), (True, None))
        self.assertIn("?ref=" + "d" * 40, api.call_args.args[0])

    def test_verified_auto_merge_fails_when_rest_cannot_observe_it(self) -> None:
        completed = subprocess.CompletedProcess(
            ["gh", "pr", "merge"], returncode=0, stdout="", stderr=""
        )
        with (
            mock.patch.object(
                publish_promote.subprocess, "run", return_value=completed
            ) as run,
            mock.patch.object(
                publish_promote,
                "_gh_api_object",
                return_value=({"auto_merge": None, "merged_at": None}, None),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "was not observable"):
                publish_promote.request_verified_auto_merge(
                    "promote/v2026.20.0", 42
                )
        self.assertTrue(run.call_args.kwargs["check"])

    def test_branch_ci_exposes_exact_head_promote_dispatch(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "branch-ci.yml").read_text()
        publish_workflow = (
            ROOT / ".github" / "workflows" / "publish-promote.yml"
        ).read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("expected_head_sha:", workflow)
        self.assertEqual(workflow.count("Validate explicit promote dispatch"), 2)
        self.assertIn('[[ "$REF_NAME" != promote/* ]]', workflow)
        self.assertIn('[[ "$HEAD_SHA" != "$EXPECTED_HEAD_SHA" ]]', workflow)
        self.assertIn(
            '[[ "$EVENT" == "workflow_dispatch" && "$REF_NAME" == promote/* ]]',
            workflow,
        )
        self.assertIn(
            "Skipping commit trailer re-scan for exact-head promote dispatch",
            workflow,
        )
        self.assertIn("actions: write", publish_workflow)
        self.assertIn("checks: read", publish_workflow)

    def test_bulk_promote_pr_lookup_omits_expensive_status_rollup(self) -> None:
        completed = subprocess.CompletedProcess(
            ["gh", "api"], returncode=0, stdout="", stderr=""
        )
        with mock.patch.object(
            publish_promote.subprocess, "run", return_value=completed
        ) as run:
            rows, error = publish_promote.list_open_promote_prs("master")
        self.assertEqual((rows, error), ({}, None))
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["gh", "api"])
        self.assertIn("--paginate", command)
        self.assertNotIn("graphql", command)
        self.assertNotIn("statusCheckRollup", " ".join(command))

    def test_exact_promote_lookup_uses_rest_check_runs(self) -> None:
        pull = {
            "number": 42,
            "html_url": "https://example.invalid/42",
            "head": {"ref": "promote/v2026.20.0", "sha": "d" * 40},
        }
        checks = [{"name": "Commit trailers", "status": "completed"}]
        with (
            mock.patch.object(
                publish_promote,
                "_list_open_pull_rows",
                return_value=([pull], None),
            ),
            mock.patch.object(
                publish_promote,
                "_required_check_rollup",
                return_value=(checks, None),
            ) as check_lookup,
        ):
            pr, error = publish_promote.find_open_promote_pr("promote/v2026.20.0")
        self.assertIsNone(error)
        self.assertEqual(pr["number"], 42)
        self.assertEqual(pr["statusCheckRollup"], checks)
        check_lookup.assert_called_once_with("d" * 40)

    def test_regression_issue_lookup_uses_rest_and_ignores_pull_requests(self) -> None:
        issues = [
            {
                "number": 7,
                "title": "block release",
                "labels": [{"name": "regression/v2026.20.0"}],
                "pull_request": None,
            },
            {
                "number": 8,
                "title": "PR label is not a blocker issue",
                "labels": [{"name": "regression/v2026.20.0"}],
                "pull_request": {"url": "https://example.invalid/pr/8"},
            },
        ]
        with (
            mock.patch.dict(os.environ, {"GH_TOKEN": "test-token"}),
            mock.patch.object(
                publish_promote,
                "_gh_api_rows",
                return_value=(issues, None),
            ) as api,
        ):
            by_version, global_blockers, error = (
                publish_promote.fetch_blocking_issue_map("regression/", [])
            )
        self.assertIsNone(error)
        self.assertEqual(global_blockers, [])
        self.assertEqual(by_version, {"v2026.20.0": ["#7 block release"]})
        self.assertIn("/issues?", api.call_args.args[0])
        self.assertNotIn("graphql", api.call_args.args[0])

    def test_open_prs_continues_after_candidate_conflict(self) -> None:
        candidates = [{"version": "v1"}, {"version": "v2"}]
        with (
            mock.patch.dict(os.environ, {"PROMOTE_CANDIDATES": json.dumps(candidates)}),
            mock.patch.object(publish_promote, "load_promote_settings", return_value=self.SETTINGS),
            mock.patch.object(publish_promote, "ensure_git_identity"),
            mock.patch.object(
                publish_promote,
                "open_candidate",
                side_effect=[
                    publish_promote.PromotionConflict("historical conflict"),
                    {"version": "v2", "disposition": "pr_opened"},
                ],
            ) as open_candidate,
        ):
            rc = publish_promote.cmd_open_prs(mock.Mock())
        self.assertEqual(rc, 0)
        self.assertEqual(open_candidate.call_count, 2)


class NotifyOrchestratorClassifyTests(unittest.TestCase):
    def _set_env(self, **env):
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_release_tag_classifies_as_publish_cut(self) -> None:
        self._set_env(
            GH_EVENT="push",
            REF="refs/tags/release/v2026.20.0",
            REFNAME="release/v2026.20.0",
            REFTYPE="tag",
            SHA="deadbeef",
            ACTOR="bot",
        )
        ev = notify_orchestrator.classify_event()
        self.assertEqual(ev["event"], "publish_cut")
        self.assertEqual(ev["version"], "v2026.20.0")

    def test_prod_tag_classifies_as_promote_merged(self) -> None:
        self._set_env(
            GH_EVENT="push",
            REF="refs/tags/prod/v2026.20.0",
            REFNAME="prod/v2026.20.0",
            REFTYPE="tag",
            SHA="deadbeef",
            ACTOR="bot",
        )
        ev = notify_orchestrator.classify_event()
        self.assertEqual(ev["event"], "promote_merged")
        self.assertEqual(ev["version"], "v2026.20.0")

    def test_wave_branch_push_classifies_with_wave_id(self) -> None:
        self._set_env(
            GH_EVENT="push",
            REF="refs/heads/wave/2026-W21",
            REFNAME="wave/2026-W21",
            REFTYPE="branch",
            SHA="deadbeef",
            ACTOR="codex",
        )
        ev = notify_orchestrator.classify_event()
        self.assertEqual(ev["event"], "wave_push")
        self.assertEqual(ev["wave_id"], "2026-W21")

    def test_archive_tag_classifies_as_branch_archived(self) -> None:
        self._set_env(
            GH_EVENT="push",
            REF="refs/tags/archive/wave-2026-W21-2026-05-22",
            REFNAME="archive/wave-2026-W21-2026-05-22",
            REFTYPE="tag",
            SHA="deadbeef",
            ACTOR="bot",
        )
        ev = notify_orchestrator.classify_event()
        self.assertEqual(ev["event"], "branch_archived")
        self.assertEqual(ev["wave_id"], "2026-W21")

    def test_pr_labeled_event(self) -> None:
        self._set_env(
            GH_EVENT="pull_request",
            REF="refs/heads/promote/v2026.20.0",
            REFNAME="promote/v2026.20.0",
            REFTYPE="branch",
            SHA="abc",
            ACTOR="bot",
            PR_NUMBER="42",
            PR_TITLE="Promote v2026.20.0 to master",
            PR_LABEL="auto-promote",
            PR_ACTION="labeled",
        )
        ev = notify_orchestrator.classify_event()
        self.assertEqual(ev["event"], "pr_labeled")
        self.assertEqual(ev["pr"]["number"], 42)
        self.assertEqual(ev["pr"]["label"], "auto-promote")
        self.assertEqual(ev["version"], "v2026.20.0")


class NotifyOrchestratorSendTests(unittest.TestCase):
    def test_send_skips_when_url_missing(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            rc = notify_orchestrator.send_payload({"event": "x", "ts": 1})
        self.assertEqual(rc, 0)

    def test_send_signs_when_secret_present(self) -> None:
        captured = {}

        class _FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return _FakeResp()

        with (
            mock.patch.dict(
                os.environ,
                {"SYNC_URL": "https://example.test/hook", "SYNC_SECRET": "shh"},
                clear=True,
            ),
            mock.patch.object(notify_orchestrator.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            rc = notify_orchestrator.send_payload({"event": "wave_open", "ts": 1, "wave_id": "2026-W21"})
        self.assertEqual(rc, 0)
        self.assertEqual(captured["url"], "https://example.test/hook")
        # Header names are normalized to title case by urllib.
        sig = captured["headers"].get("X-pantheon-signature") or captured["headers"].get(
            "X-Pantheon-Signature"
        )
        self.assertIsNotNone(sig)
        self.assertTrue(sig.startswith("sha256="))


if __name__ == "__main__":
    unittest.main()
