#!/usr/bin/env python3
"""Tests for the wave/git-workflow helper scripts.

Run with:
    python3 -m pytest scripts/git/test_git_workflow_helpers.py
or with unittest discovery:
    python3 -m unittest scripts/git/test_git_workflow_helpers.py
"""

from __future__ import annotations

import importlib.util
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
    def test_uses_explicit_base_when_it_is_available_and_ancestor(self) -> None:
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
        self.assertEqual(rev_range, "dev-base..head")

    def test_ignores_available_before_sha_when_it_is_not_an_ancestor(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="push",
            base_sha="rewritten-before",
            head_sha="head",
            ref_name="task/example",
            pr_base_ref="",
            commit_exists=lambda rev: rev in {"rewritten-before", "head", "origin/dev", "dev-base"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: "dev-base" if ref == "origin/dev" else None,
        )
        self.assertEqual(rev_range, "dev-base..head")

    def test_pull_request_uses_base_ref_merge_base_when_base_sha_is_unavailable(self) -> None:
        rev_range = resolve_range.resolve_commit_range(
            event="pull_request",
            base_sha="missing-base",
            head_sha="head",
            ref_name="123/merge",
            pr_base_ref="dev",
            commit_exists=lambda rev: rev in {"head", "origin/dev", "dev-base"},
            is_ancestor=lambda base, head: False,
            merge_base=lambda ref, head: "dev-base" if ref == "origin/dev" else None,
        )
        self.assertEqual(rev_range, "dev-base..head")

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


class PublishPromoteTests(unittest.TestCase):
    SETTINGS = {
        "main_branch": "master",
        "publish_branch_prefix": "publish/",
        "release_tag_prefix": "release/",
        "soak_days": 0,
        "regression_label_prefix": "regression/",
        "block_labels": [],
        "promote_pr_label": "auto-promote",
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

    def test_common_history_content_conflict_stays_fail_closed(self) -> None:
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
            self._git(repo, "checkout", "master")
            (repo / "state.txt").write_text("master\n")
            self._git(repo, "commit", "-am", "master")
            master = self._git(repo, "rev-parse", "HEAD").stdout.strip()
            with mock.patch.object(publish_promote, "ROOT", repo):
                mode, _detail = publish_promote.assess_promotion_mode(master, release)
            self.assertEqual(mode, "conflicted")

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
            mock.patch.object(publish_promote, "find_open_promote_pr", return_value=(None, None)),
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            result = publish_promote.open_candidate(candidate, self.SETTINGS)

        self.assertEqual(result["disposition"], "pr_opened")
        run_git.assert_any_call("fetch", "origin", "master", "--tags")
        run_git.assert_any_call("push", "-u", "origin", "promote/v2026.20.0")
        for call in run_git.call_args_list:
            self.assertNotIn("--force", call.args)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["gh", "pr", "merge", "promote/v2026.20.0", "--auto", "--merge"], commands)
        self.assertIn(
            ["gh", "pr", "edit", "promote/v2026.20.0", "--add-label", "auto-promote"],
            commands,
        )

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
                return_value=({"number": 42, "url": "https://example.invalid/42"}, None),
            ),
        ):
            result = publish_promote.open_candidate(candidate, self.SETTINGS)
        self.assertEqual(result["disposition"], "existing_pr")
        self.assertFalse(any(call.args[0] == "push" for call in run_git.call_args_list))

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
