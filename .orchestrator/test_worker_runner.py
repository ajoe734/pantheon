"""Tests for OPS-REVIEW-DISPATCH-DIRTY-PR-HOLD-001 Part B.

On 2026-09-17, tasks CB02/CB05/CB07 were in ``review`` status with a bound
GitHub PR that had already gone ``mergeStateStatus=DIRTY``. The dispatched
reviewer worker's own ``approve``/``reopen`` attempts were correctly rejected
by the canonical review-merge gate (scripts/git/github_review_bridge.py), but
that rejection happened inside the wrapped agent process -- invisible to
``worker_runner.py`` -- so the worker exited in a way the supervisor's
heartbeat/lease reaper read as ``worker_process_missing``, triggering
lost-lease recovery and a redispatch storm (345 ``worker_lost_lease`` events
in 4 hours).

This module tests the fix: ``worker_runner.py`` polls the same live
``mergeStateStatus`` fact itself while a reviewer worker is running, and when
it reports ``DIRTY`` stops the child through an explicit, named, non-terminal
path (``_record_review_pr_dirty_hold``) instead of an uncaught exception or a
bare abrupt exit -- the same shape of governed stop
``_record_dispatch_binding_revoked`` already uses for a superseded dispatch
binding.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_P = os.path.join(os.path.dirname(__file__), "worker_runner.py")
_spec = importlib.util.spec_from_file_location("worker_runner", _P)
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)

# Reuse the real subprocess/fixture harness (coordination-root bootstrap,
# canonical task-state journal, receipt publication) rather than
# reimplementing it -- ``test_worker_runner_heartbeat.py`` already builds and
# exercises this exact wrapper contract for the sibling
# ``_record_dispatch_binding_revoked`` governed-stop path.
import test_worker_runner_heartbeat as heartbeat_tests


class ReviewPrMergeStateHelperTests(unittest.TestCase):
    """Unit coverage for the pure/best-effort helpers, independent of any
    real subprocess, ``gh`` binary, or GitHub network access."""

    def test_conflicted_only_for_dirty(self) -> None:
        for benign in ("UNKNOWN", "BEHIND", "UNSTABLE", "BLOCKED", "CLEAN", "", None):
            with self.subTest(merge_state=benign):
                self.assertFalse(wr._review_pr_merge_state_is_conflicted(benign))
        self.assertTrue(wr._review_pr_merge_state_is_conflicted("DIRTY"))
        self.assertTrue(wr._review_pr_merge_state_is_conflicted("dirty"))

    def test_live_state_fails_open_without_task_record(self) -> None:
        with mock.patch.object(wr, "_get_task_record", return_value=None):
            self.assertIsNone(
                wr._live_review_pr_merge_state_for_task(Path("/nonexistent"), "TASK-1")
            )

    def test_live_state_fails_open_without_review_binding(self) -> None:
        with mock.patch.object(wr, "_get_task_record", return_value={"id": "TASK-1"}):
            self.assertIsNone(
                wr._live_review_pr_merge_state_for_task(Path("/nonexistent"), "TASK-1")
            )

    def test_live_state_fails_open_with_non_positive_pr(self) -> None:
        task = {"id": "TASK-1", "review_binding": {"pr": 0}}
        with mock.patch.object(wr, "_get_task_record", return_value=task):
            self.assertIsNone(
                wr._live_review_pr_merge_state_for_task(Path("/nonexistent"), "TASK-1")
            )

    def test_live_state_resolves_dirty_from_gh_payload(self) -> None:
        task = {
            "id": "TASK-1",
            "review_binding": {
                "pr": 42,
                "head_sha": "a" * 40,
                "head_branch": "task/TASK-1",
                "base": "dev",
            },
        }

        class FakeRunner:
            def run_json(self, args, **kwargs):
                return {"number": 42, "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}

        fake_module = type(sys)("github_review_bridge")
        fake_module.GhJsonRunner = FakeRunner
        with (
            mock.patch.object(wr, "_get_task_record", return_value=task),
            mock.patch.dict(sys.modules, {"github_review_bridge": fake_module}),
        ):
            result = wr._live_review_pr_merge_state_for_task(
                Path(tempfile.mkdtemp()), "TASK-1"
            )
        self.assertIsNotNone(result)
        repository, pr, merge_state = result
        self.assertEqual(pr, 42)
        self.assertEqual(merge_state, "DIRTY")
        self.assertEqual(repository, "ajoe734/pantheon")
        self.assertTrue(wr._review_pr_merge_state_is_conflicted(merge_state))

    def test_live_state_fails_open_when_gh_call_raises(self) -> None:
        task = {
            "id": "TASK-1",
            "review_binding": {
                "pr": 42,
                "head_sha": "a" * 40,
                "head_branch": "task/TASK-1",
                "base": "dev",
            },
        }

        class RaisingRunner:
            def run_json(self, args, **kwargs):
                raise RuntimeError("GitHub CLI `gh` is not installed")

        fake_module = type(sys)("github_review_bridge")
        fake_module.GhJsonRunner = RaisingRunner
        with (
            mock.patch.object(wr, "_get_task_record", return_value=task),
            mock.patch.dict(sys.modules, {"github_review_bridge": fake_module}),
        ):
            result = wr._live_review_pr_merge_state_for_task(
                Path(tempfile.mkdtemp()), "TASK-1"
            )
        self.assertIsNone(result)

    def test_live_state_mergeable_is_not_conflicted(self) -> None:
        task = {
            "id": "TASK-1",
            "review_binding": {
                "pr": 42,
                "head_sha": "a" * 40,
                "head_branch": "task/TASK-1",
                "base": "dev",
            },
        }

        class FakeRunner:
            def run_json(self, args, **kwargs):
                return {"number": 42, "mergeStateStatus": "MERGEABLE", "mergeable": "MERGEABLE"}

        fake_module = type(sys)("github_review_bridge")
        fake_module.GhJsonRunner = FakeRunner
        with (
            mock.patch.object(wr, "_get_task_record", return_value=task),
            mock.patch.dict(sys.modules, {"github_review_bridge": fake_module}),
        ):
            result = wr._live_review_pr_merge_state_for_task(
                Path(tempfile.mkdtemp()), "TASK-1"
            )
        self.assertIsNotNone(result)
        self.assertFalse(wr._review_pr_merge_state_is_conflicted(result[2]))


class RecordReviewPrDirtyHoldTests(unittest.TestCase):
    """``_record_review_pr_dirty_hold`` is the explicit, named exit path --
    not an uncaught exception -- a reviewer worker takes when it discovers
    its bound PR is DIRTY. This checks its direct effects on the in-memory
    status and the on-disk status file."""

    def test_marks_status_and_writes_status_file(self) -> None:
        root = Path(tempfile.mkdtemp())
        status_path = root / "status.json"
        status: dict[str, object] = {"run_id": "run-1", "status": "running"}

        wr._record_review_pr_dirty_hold(
            status=status,
            status_path=status_path,
            coordination_root=None,
            task_id="TASK-1",
            run_id="run-1",
            agent="Codex",
            merge_state="DIRTY",
            pr_reference="ajoe734/pantheon#42",
        )

        self.assertTrue(status["review_pr_dirty_hold"])
        self.assertEqual(status["review_pr_merge_state"], "DIRTY")
        self.assertEqual(
            status["review_pr_dirty_reason"], "review_pr_dirty:ajoe734/pantheon#42:DIRTY"
        )
        on_disk = json.loads(status_path.read_text())
        self.assertTrue(on_disk["review_pr_dirty_hold"])
        self.assertEqual(on_disk["review_pr_dirty_reason"], status["review_pr_dirty_reason"])

    def test_never_raises_when_status_path_is_unwritable(self) -> None:
        status: dict[str, object] = {"run_id": "run-1", "status": "running"}
        # A directory in place of the status file makes the write fail; the
        # governed-stop marker must never crash the wrapper's own shutdown.
        root = Path(tempfile.mkdtemp())
        unwritable = root / "status.json"
        unwritable.mkdir()
        wr._record_review_pr_dirty_hold(
            status=status,
            status_path=unwritable,
            coordination_root=None,
            task_id="TASK-1",
            run_id="run-1",
            agent="Codex",
            merge_state="DIRTY",
            pr_reference="ajoe734/pantheon#42",
        )
        self.assertTrue(status["review_pr_dirty_hold"])


class ReviewerDirtyPrGovernedExitProcessTests(unittest.TestCase):
    """End-to-end: a real wrapper process wrapping a real (sleeping) child,
    with a stubbed ``gh`` reporting the bound PR as DIRTY, must stop the
    child and publish the governed, non-terminal status -- not ``failed`` --
    and must never raise an uncaught exception out of ``main``."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="worker-review-dirty-pr-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.central = self.root / "central"
        self.command_root = self.root / "command-runtime"
        self.workspace = self.root / "task-worktree"
        for repo_root in (self.central, self.command_root, self.workspace):
            heartbeat_tests._init_repo(repo_root)
        heartbeat_tests._write_status(self.central)
        self.heartbeat = self.central / ".orchestrator/worker-runtime/heartbeats/run.json"
        self.runner_status = self.central / ".orchestrator/worker-runtime/status/run.json"

        # A fake `gh` on PATH stands in for GitHub: the canonical review-merge
        # gate's own GhJsonRunner shells out to ``gh pr view ... --json ...``,
        # and this deterministically reports the bound PR as DIRTY with no
        # network or authentication dependency.
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        gh_script = self.fake_bin / "gh"
        gh_script.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "print(json.dumps({'number': 777, 'mergeStateStatus': 'DIRTY', "
            "'mergeable': 'CONFLICTING'}))\n"
        )
        gh_script.chmod(0o755)

        self.env = {
            **os.environ,
            **heartbeat_tests._command_runtime_env(self.command_root),
            "PANTHEON_STATUS_ROOT": str(self.central),
            "PANTHEON_WORKTREE_ROOT": str(self.workspace),
            "ORCH_WORKSPACE_PATH": str(self.workspace),
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        self.task = {
            "id": "OPS-REVIEW-DIRTY-001",
            "title": "Reviewer dirty-PR fixture",
            "owner": "Codex2",
            "reviewer": "Codex",
            "target_repo": "pantheon",
            "generation": 1,
            "status": "review",
            "review_binding": {
                "pr": 777,
                "head_sha": "a" * 40,
                "head_branch": "task/OPS-REVIEW-DIRTY-001",
                "base": "dev",
            },
        }

    @staticmethod
    def _make_reviewer(worker: dict[str, object]) -> None:
        worker["agent_id"] = worker["logical_agent_id"] = "codex"
        worker["request_snapshot"].update(agent_id="codex", reason="review_ready_dispatch")

    def test_reviewer_worker_exits_via_governed_hold_when_bound_pr_is_dirty(self) -> None:
        code = (
            "from pathlib import Path; import time; "
            "Path('ready').write_text('ready'); time.sleep(10)"
        )
        argv = [
            sys.executable,
            heartbeat_tests._P,
            "--run-id",
            "codex-20260917T000000Z-fixture",
            "--heartbeat-path",
            str(self.heartbeat),
            "--status-path",
            str(self.runner_status),
            "--heartbeat-interval-seconds",
            "1",
            "--",
            sys.executable,
            "-c",
            code,
        ]
        proc = heartbeat_tests._run_fixture_worker(
            argv,
            env=self.env,
            task=self.task,
            local_stub=True,
            mutate_receipt=self._make_reviewer,
        )

        # A governed SIGTERM stop, never an uncaught exception (which would
        # surface as a Python traceback on stderr and/or a bare `return 1`).
        self.assertEqual(proc.returncode, 143, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

        status = json.loads(self.runner_status.read_text())
        self.assertEqual(status["status"], "review_pr_dirty_hold")
        self.assertTrue(status["review_pr_dirty_hold"])
        self.assertEqual(status["review_pr_merge_state"], "DIRTY")
        self.assertIn("DIRTY", status["review_pr_dirty_reason"])
        self.assertIn("777", status["review_pr_dirty_reason"])

        activity_log = self.central / "ai-activity-log.jsonl"
        self.assertTrue(activity_log.exists())
        events = [
            json.loads(line) for line in activity_log.read_text().splitlines() if line.strip()
        ]
        hold_events = [e for e in events if e.get("type") == "worker_review_pr_dirty_hold"]
        self.assertTrue(hold_events, "expected a durable worker_review_pr_dirty_hold event")
        self.assertEqual(hold_events[-1]["task_id"], self.task["id"])
        self.assertEqual(hold_events[-1]["merge_state"], "DIRTY")
        self.assertIn("DIRTY", hold_events[-1]["message"])

    def test_owner_worker_ignores_bound_pr_dirty_state(self) -> None:
        """The live PR check is scoped to the reviewer role only -- an owner
        dispatched onto the same task (e.g. after reopen) must run normally
        and must never be held by a DIRTY PR that is not its concern."""

        owner_task = dict(self.task)
        owner_task["status"] = "in_progress"
        code = "from pathlib import Path; Path('owner-effect').write_text('ok')"
        argv = [
            sys.executable,
            heartbeat_tests._P,
            "--run-id",
            "codex2-20260917T000000Z-fixture",
            "--heartbeat-path",
            str(self.heartbeat),
            "--status-path",
            str(self.runner_status),
            "--heartbeat-interval-seconds",
            "1",
            "--",
            sys.executable,
            "-c",
            code,
        ]
        proc = heartbeat_tests._run_fixture_worker(
            argv,
            env=self.env,
            task=owner_task,
            local_stub=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        status = json.loads(self.runner_status.read_text())
        self.assertEqual(status["status"], "completed")
        self.assertNotIn("review_pr_dirty_hold", status)


if __name__ == "__main__":
    unittest.main()
