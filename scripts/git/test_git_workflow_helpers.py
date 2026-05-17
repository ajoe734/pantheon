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
import sys
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


class PublishPromoteTests(unittest.TestCase):
    def test_discover_skips_recent_tags(self) -> None:
        now = datetime.now(timezone.utc)
        recent = (now.replace(microsecond=0))
        old = recent.replace(year=recent.year - 1)
        with (
            mock.patch.object(
                publish_promote,
                "list_release_tags",
                return_value=[("v2026.20.0", recent), ("v2025.10.0", old)],
            ),
            mock.patch.object(publish_promote, "fetch_blocking_labels", return_value=[]),
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            # is-ancestor → non-zero (not merged); pr list → empty
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            cands = publish_promote.discover(
                input_version=None,
                soak_days=3,
                prefix="regression/",
                block_labels=[],
                publish_prefix="publish/",
            )
        versions = [c["version"] for c in cands]
        self.assertIn("v2025.10.0", versions)
        self.assertNotIn("v2026.20.0", versions)

    def test_discover_includes_recent_when_version_forced(self) -> None:
        now = datetime.now(timezone.utc)
        with (
            mock.patch.object(
                publish_promote, "list_release_tags",
                return_value=[("v2026.20.0", now)],
            ),
            mock.patch.object(publish_promote, "fetch_blocking_labels", return_value=[]),
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            cands = publish_promote.discover(
                input_version="v2026.20.0",
                soak_days=3,
                prefix="regression/",
                block_labels=[],
                publish_prefix="publish/",
            )
        self.assertEqual([c["version"] for c in cands], ["v2026.20.0"])

    def test_discover_skips_already_merged(self) -> None:
        now = datetime.now(timezone.utc)
        old = now.replace(year=now.year - 1)
        with (
            mock.patch.object(
                publish_promote, "list_release_tags",
                return_value=[("v2025.10.0", old)],
            ),
            mock.patch.object(publish_promote, "fetch_blocking_labels", return_value=[]),
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            # is-ancestor returns 0 → already merged.
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            cands = publish_promote.discover(
                input_version=None,
                soak_days=3,
                prefix="regression/",
                block_labels=[],
                publish_prefix="publish/",
            )
        self.assertEqual(cands, [])

    def test_discover_records_blockers(self) -> None:
        now = datetime.now(timezone.utc)
        old = now.replace(year=now.year - 1)
        with (
            mock.patch.object(
                publish_promote, "list_release_tags",
                return_value=[("v2025.10.0", old)],
            ),
            mock.patch.object(
                publish_promote, "fetch_blocking_labels",
                return_value=["#42 regression in X"],
            ),
            mock.patch.object(publish_promote.subprocess, "run") as run,
        ):
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            cands = publish_promote.discover(
                input_version=None,
                soak_days=3,
                prefix="regression/",
                block_labels=[],
                publish_prefix="publish/",
            )
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["blockers"], ["#42 regression in X"])


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
