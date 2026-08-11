#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import io
import json
import contextlib
import multiprocessing
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_status
import task_archive


def audited_reassignment_event(
    *,
    task_id: str = "REG-002",
    old_owner: str = "Codex2",
    new_owner: str = "Antigravity",
    old_reviewer: str = "Claude",
    new_reviewer: str = "Claude",
    timestamp: str = "2026-07-19T23:52:06Z",
    message: str = "canonical owner reassignment",
) -> dict[str, str]:
    """Build a `task_reassigned` line shaped exactly like the supervisor writes it.

    The reassignment gates only trust events carrying the `Orchestrator` actor
    and the deterministic `event_id` digest that `persist_task_reassignment`
    stamps, so fixtures have to be built the same way or they prove nothing.
    """

    event = {
        "ts": timestamp,
        "agent": "Orchestrator",
        "type": "task_reassigned",
        "task_id": task_id,
        "old_owner": old_owner,
        "new_owner": new_owner,
        "old_reviewer": old_reviewer,
        "new_reviewer": new_reviewer,
        "message": message,
    }
    event["event_id"] = ai_status._supervisor_reassignment_event_id(event)
    return event

from canonical_writer_guard import assert_isolated_legacy_write_target
from rewrite.task_state_store import load_events, verify_projection


REVIEW_BINDING_ENV_KEYS = (
    "REVIEW_PR",
    "REVIEW_HEAD_SHA",
    "REVIEW_BASE",
    "REVIEW_HEAD_BRANCH",
)


def _setup_test_isolation(test_case):
    test_case._inherited_review_binding_env = {
        key: os.environ.pop(key)
        for key in REVIEW_BINDING_ENV_KEYS
        if key in os.environ
    }
    test_case._test_temp_dir = tempfile.TemporaryDirectory(prefix="ai-status-test-")
    test_case._test_root = Path(test_case._test_temp_dir.name)
    test_case._test_status_file = test_case._test_root / "ai-status.json"
    test_case._test_log_file = test_case._test_root / "ai-activity-log.jsonl"

    test_case._test_status_file.write_text("{}\n", encoding="utf-8")
    test_case._test_log_file.write_text("", encoding="utf-8")

    test_case._orig_paths = {
        "STATUS_ROOT": ai_status.STATUS_ROOT,
        "STATUS_FILE": ai_status.STATUS_FILE,
        "LOG_FILE": ai_status.LOG_FILE,
        "CURRENT_WORK_FILE": ai_status.CURRENT_WORK_FILE,
        "DOCS_SITE_DIR": ai_status.DOCS_SITE_DIR,
        "PLANNING_STATE_FILE": ai_status.PLANNING_STATE_FILE,
        "ORCHESTRATOR_STATE_FILE": ai_status.ORCHESTRATOR_STATE_FILE,
        "APPROVAL_QUEUE_FILE": ai_status.APPROVAL_QUEUE_FILE,
        "DASHBOARD_BUNDLE_FILE": ai_status.DASHBOARD_BUNDLE_FILE,
        "TA_STATUS_ROOT": task_archive.STATUS_ROOT,
        "TA_STATUS_FILE": task_archive.STATUS_FILE,
        "TA_ARCHIVE_DIR": task_archive.ARCHIVE_DIR,
        "TA_ARCHIVE_TASKS_DIR": task_archive.ARCHIVE_TASKS_DIR,
        "TA_ARCHIVE_INDEX_FILE": task_archive.ARCHIVE_INDEX_FILE,
    }

    ai_status.configure_status_root_paths(test_case._test_root)
    task_archive.ARCHIVE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    task_archive.ARCHIVE_INDEX_FILE.write_text("{}\n", encoding="utf-8")


def _teardown_test_isolation(test_case):
    for key in REVIEW_BINDING_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(test_case._inherited_review_binding_env)

    paths = test_case._orig_paths
    ai_status.STATUS_ROOT = paths["STATUS_ROOT"]
    ai_status.STATUS_FILE = paths["STATUS_FILE"]
    ai_status.LOG_FILE = paths["LOG_FILE"]
    ai_status.CURRENT_WORK_FILE = paths["CURRENT_WORK_FILE"]
    ai_status.DOCS_SITE_DIR = paths["DOCS_SITE_DIR"]
    ai_status.PLANNING_STATE_FILE = paths["PLANNING_STATE_FILE"]
    ai_status.ORCHESTRATOR_STATE_FILE = paths["ORCHESTRATOR_STATE_FILE"]
    ai_status.APPROVAL_QUEUE_FILE = paths["APPROVAL_QUEUE_FILE"]
    ai_status.DASHBOARD_BUNDLE_FILE = paths["DASHBOARD_BUNDLE_FILE"]

    task_archive.STATUS_ROOT = paths["TA_STATUS_ROOT"]
    task_archive.STATUS_FILE = paths["TA_STATUS_FILE"]
    task_archive.ARCHIVE_DIR = paths["TA_ARCHIVE_DIR"]
    task_archive.ARCHIVE_TASKS_DIR = paths["TA_ARCHIVE_TASKS_DIR"]
    task_archive.ARCHIVE_INDEX_FILE = paths["TA_ARCHIVE_INDEX_FILE"]

    try:
        test_case._test_temp_dir.cleanup()
    except Exception:
        pass


def _locked_status_update(
    status_file: str,
    task_id: str,
    message: str,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event | None,
) -> None:
    """Process helper that exercises the production stable task-state lock."""

    ai_status.STATUS_FILE = Path(status_file)
    with ai_status.canonical_task_state_lock(shared=False):
        state = ai_status.load_state()
        entered.set()
        if release is not None and not release.wait(timeout=10):
            raise RuntimeError("timed out waiting to release task-state transaction")
        task = ai_status.get_task(state, task_id)
        if task is None:
            raise RuntimeError(f"missing fixture task: {task_id}")
        task["next"] = message
        ai_status.save_state(state)


def _hold_task_state_lock(
    status_file: str,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    ai_status.STATUS_FILE = Path(status_file)
    with ai_status.canonical_task_state_lock(shared=False):
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("timed out waiting to release task-state lock")


def _recover_pending_status_outbox(
    status_file: str,
    log_file: str,
    rotate_max_bytes: int,
    rotate_keep_lines: int,
) -> None:
    """Process helper that represents restart recovery after an abrupt exit."""

    ai_status.STATUS_FILE = Path(status_file)
    ai_status.LOG_FILE = Path(log_file)
    ai_status.LOG_ROTATE_MAX_BYTES = rotate_max_bytes
    ai_status.LOG_ROTATE_KEEP_LINES = rotate_keep_lines
    with ai_status.canonical_task_state_lock(shared=False):
        state = ai_status.load_state()
        if not ai_status.recover_status_activity_outbox(state):
            raise RuntimeError("fixture did not contain a pending status activity outbox")


def _recover_pending_archive_outbox(status_file: str, status_root: str) -> None:
    root = Path(status_root)
    ai_status.configure_status_root_paths(root)
    ai_status.STATUS_FILE = Path(status_file)
    task_archive.STATUS_ROOT = root
    task_archive.STATUS_FILE = Path(status_file)
    task_archive.ARCHIVE_DIR = root / "ai-task-archive"
    task_archive.ARCHIVE_TASKS_DIR = task_archive.ARCHIVE_DIR / "tasks"
    task_archive.ARCHIVE_INDEX_FILE = task_archive.ARCHIVE_DIR / "index.json"
    with ai_status.canonical_task_state_lock(shared=False):
        state = ai_status.load_state()
        if not ai_status.recover_status_archive_outbox(state):
            raise RuntimeError("fixture did not contain a pending status archive outbox")


def _commit_terminal_archive_with_sigkill(
    status_file: str,
    status_root: str,
    point: str,
) -> None:
    root = Path(status_root)
    ai_status.configure_status_root_paths(root)
    ai_status.STATUS_FILE = Path(status_file)
    task_archive.STATUS_ROOT = root
    task_archive.STATUS_FILE = Path(status_file)
    task_archive.ARCHIVE_DIR = root / "ai-task-archive"
    task_archive.ARCHIVE_TASKS_DIR = task_archive.ARCHIVE_DIR / "tasks"
    task_archive.ARCHIVE_INDEX_FILE = task_archive.ARCHIVE_DIR / "index.json"
    os.environ["LOOP_TEST_ARCHIVE_SIGKILL_AFTER"] = point
    with ai_status.canonical_task_state_lock(shared=False):
        state = ai_status.load_state()
        terminal = ai_status.get_task(state, "LOCK-ONE")
        if terminal is None:
            raise RuntimeError("missing terminal fixture task")
        ai_status.archive_terminal_task_from_state(
            state,
            terminal,
            archived_at="2026-07-14T00:03:00Z",
        )
        ai_status.commit_state_with_activity_outbox(state, [])


class StrictActivityTailParsingTests(unittest.TestCase):
    def test_derived_activity_tail_readers_reject_duplicate_keys(self) -> None:
        ambiguous_rows = (
            '{"event_id":"first","event_id":"second",'
            '"type":"task_helper_claimed"}\n',
            '{"event_id":"nested","type":"task_helper_claimed",'
            '"metadata":{"role":"a","role":"b"}}\n',
        )
        readers = (
            ("load_logs", ai_status.load_logs),
            ("recent_helper_claims", ai_status.recent_helper_claims),
        )
        original_log_file = ai_status.LOG_FILE
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                ai_status.LOG_FILE = Path(tmpdir) / "ai-activity-log.jsonl"
                for shape, ambiguous in enumerate(ambiguous_rows):
                    for reader_name, reader in readers:
                        with self.subTest(shape=shape, reader=reader_name):
                            ai_status.LOG_FILE.write_text(
                                ambiguous,
                                encoding="utf-8",
                            )
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "duplicate JSON key",
                            ):
                                reader()
        finally:
            ai_status.LOG_FILE = original_log_file


class TaskStateShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_save_state_appends_owner_only_shadow_commit(self) -> None:
        journal = self._test_root / "runtime" / "task-state-events.jsonl"
        state = {"sprint": "shadow", "tasks": [{"id": "STATE-001", "status": "todo"}]}

        with mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "shadow",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(journal),
                "ORCH_RUN_ID": "run-shadow-001",
            },
            clear=False,
        ):
            ai_status.save_state(state)

        events = load_events(journal)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "run-shadow-001")
        self.assertEqual(events[0]["state"], state)
        self.assertTrue(verify_projection(journal, state)["ok"])
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)

    def test_shadow_failure_warns_after_incumbent_write_succeeds(self) -> None:
        real_parent = self._test_root / "real-runtime"
        real_parent.mkdir()
        linked_parent = self._test_root / "linked-runtime"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        state = {"sprint": "shadow", "tasks": [{"id": "STATE-002", "status": "todo"}]}
        stderr = io.StringIO()

        with (
            mock.patch.dict(
                os.environ,
                {
                    ai_status.TASK_STATE_STORE_MODE_ENV: "shadow",
                    ai_status.TASK_STATE_EVENT_LOG_ENV: str(linked_parent / "events.jsonl"),
                },
                clear=False,
            ),
            mock.patch.object(ai_status.sys, "stderr", stderr),
        ):
            ai_status.save_state(state)

        self.assertEqual(json.loads(self._test_status_file.read_text(encoding="utf-8")), state)
        self.assertIn("task-state shadow append failed", stderr.getvalue())
        self.assertFalse((real_parent / "events.jsonl").exists())

    def test_authoritative_load_ignores_divergent_file_and_save_advances_journal(self) -> None:
        journal = self._test_root / "runtime" / "task-state-events.jsonl"
        first = {"sprint": "authoritative", "tasks": [{"id": "STATE-003", "status": "todo"}]}
        second = {
            "sprint": "authoritative",
            "tasks": [{"id": "STATE-003", "status": "in_progress"}],
        }
        ai_status.append_state_commit(journal, first, source="migration")
        self._test_status_file.write_text(
            json.dumps({"tasks": [{"id": "ROGUE", "status": "done"}]}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(journal),
                "AI_NAME": "Human/Ops",
            },
            clear=False,
        ):
            loaded_task = ai_status.load_state()["tasks"][0]
            self.assertEqual(
                {"id": loaded_task["id"], "status": loaded_task["status"]},
                first["tasks"][0],
            )
            ai_status.save_state(second)

        self.assertEqual(load_events(journal)[-1]["state"], second)
        self.assertEqual(json.loads(self._test_status_file.read_text(encoding="utf-8")), second)

    def test_authoritative_transaction_advances_each_save_from_one_stable_head(self) -> None:
        journal = self._test_root / "runtime" / "task-state-events.jsonl"
        first = {
            "sprint": "authoritative",
            "tasks": [{"id": "STATE-TX", "status": "todo"}],
        }
        ai_status.append_state_commit(journal, first, source="migration")

        with mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(journal),
                "AI_NAME": "Codex",
            },
            clear=False,
        ):
            with ai_status.authoritative_task_state_transaction():
                second = ai_status.load_state()
                second["tasks"][0]["status"] = "in_progress"
                ai_status.save_state(second)
                third = ai_status.load_state()
                third["tasks"][0]["next"] = "activity outbox cleared"
                ai_status.save_state(third)

        events = load_events(journal)
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3])
        self.assertEqual(
            events[1]["previous_event_sha256"],
            events[0]["event_sha256"],
        )
        self.assertEqual(
            events[2]["previous_event_sha256"],
            events[1]["event_sha256"],
        )
        self.assertEqual(events[-1]["state"], third)
        self.assertEqual(
            json.loads(self._test_status_file.read_text(encoding="utf-8")),
            third,
        )

    def test_derived_views_skip_a_stale_projection(self) -> None:
        current = {"tasks": [{"id": "STATE-VIEW", "status": "review"}]}
        stale = {"tasks": [{"id": "STATE-VIEW", "status": "in_progress"}]}
        self._test_status_file.write_text(
            json.dumps(current) + "\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            ai_status,
            "refresh_derived_status_views",
        ) as refresh:
            self.assertFalse(
                ai_status.refresh_derived_status_views_if_current(stale)
            )
            self.assertTrue(
                ai_status.refresh_derived_status_views_if_current(current)
            )

        refresh.assert_called_once_with(current)

    def test_authoritative_append_failure_preserves_existing_projection(self) -> None:
        real_parent = self._test_root / "real-authoritative-runtime"
        real_parent.mkdir()
        linked_parent = self._test_root / "linked-authoritative-runtime"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        original = {"tasks": [{"id": "STATE-004", "status": "todo"}]}
        self._test_status_file.write_text(json.dumps(original) + "\n", encoding="utf-8")

        with mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(linked_parent / "events.jsonl"),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                ai_status.save_state({"tasks": [{"id": "STATE-004", "status": "done"}]})

        self.assertEqual(json.loads(self._test_status_file.read_text(encoding="utf-8")), original)

    def test_authoritative_store_rejects_in_process_projection_rebinding(self) -> None:
        journal = self._test_root / "runtime" / "task-state-events.jsonl"
        canonical = {
            "sprint": "authoritative",
            "tasks": [{"id": "LIVE-001", "status": "in_progress"}],
        }
        fixture = {
            "sprint": "test-fixture",
            "tasks": [{"id": "LOCK-ONE", "status": "in_progress"}],
        }
        ai_status.append_state_commit(journal, canonical, source="fixture-migration")
        rebound = self._test_root / "test-helper" / "ai-status.json"

        with (
            mock.patch.dict(
                os.environ,
                {
                    ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                    ai_status.TASK_STATE_EVENT_LOG_ENV: str(journal),
                    "ORCH_RUN_ID": "worker-running-tests",
                },
                clear=False,
            ),
            mock.patch.object(ai_status, "STATUS_FILE", rebound),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "task-state projection binding mismatch",
            ):
                ai_status.load_state()
            with self.assertRaisesRegex(
                RuntimeError,
                "task-state projection binding mismatch",
            ):
                ai_status.save_state(fixture)

        events = load_events(journal)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[-1]["state"], canonical)
        self.assertFalse(rebound.exists())


class LogicalActorNormalizationTests(unittest.TestCase):
    def test_distinct_logical_lanes_do_not_collapse_numeric_suffixes(self) -> None:
        self.assertEqual(ai_status.normalize_logical_actor("Codex"), "codex")
        self.assertEqual(ai_status.normalize_logical_actor("Codex2"), "codex2")
        self.assertEqual(ai_status.normalize_logical_actor("Claude"), "claude")
        self.assertEqual(ai_status.normalize_logical_actor("Claude2"), "claude2")
        self.assertNotEqual(
            ai_status.normalize_logical_actor("Codex"),
            ai_status.normalize_logical_actor("Codex2"),
        )

    def test_codex_dispatch_slots_map_to_their_logical_actor(self) -> None:
        self.assertEqual(ai_status.normalize_logical_actor("codex1_4"), "codex")
        self.assertEqual(ai_status.normalize_logical_actor("codex2_1"), "codex2")
        self.assertEqual(ai_status.normalize_logical_actor("codex2-3"), "codex2")


class StatusCommandLeaseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="status-lease-test-")
        self.root = Path(self.temporary.name)
        self.status_file = self.root / "ai-status.json"
        self.task_id = "LEASE-SYNC-001"
        self.run_id = "codex-lease-sync-run"
        self.workspace = self.root / "task-worktree"
        self.command_root = self.root / "command-runtime"
        self.workspace.mkdir()
        self.command_root.mkdir()
        self.issued_runtime = {
            "command_root": str(self.command_root),
            "source_sha": "lease-sync-runtime-sha",
        }
        self.status_file.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": self.task_id,
                            "owner": "Codex",
                            "reviewer": "Claude",
                            "status": "in_progress",
                        }
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        ai_status._clear_status_command_lease_binding()
        self.temporary.cleanup()

    def _runtime_state(
        self,
        *,
        logical_agent_id: str = "codex",
        lease_expires_at: str = "2999-01-01T00:00:00Z",
        task_id: str | None = None,
        status_root: Path | None = None,
    ) -> dict[str, object]:
        worker_task_id = task_id or self.task_id
        worker_status_root = status_root or self.root
        queue_event_id = "queue-lease-sync"
        pid = 4242
        pid_start_ticks = 987654
        process_generation = ai_status.status_worker_process_generation_id(
            task_id=worker_task_id,
            worker_run_id=self.run_id,
            queue_event_id=queue_event_id,
            pid=pid,
            pid_start_ticks=pid_start_ticks,
        )
        return {
            "workers": {
                self.run_id: {
                    "run_id": self.run_id,
                    "logical_agent_id": logical_agent_id,
                    "task_id": worker_task_id,
                    "status": "running",
                    "lease_expires_at": lease_expires_at,
                    "workspace_path": str(self.workspace),
                    "status_root": str(worker_status_root),
                    "status_command_runtime": self.issued_runtime,
                    "queue_event_id": queue_event_id,
                    "pid": pid,
                    "pid_start_ticks": pid_start_ticks,
                    "process_generation": process_generation,
                }
            },
            "worker_worktrees": {
                "leases": {
                    worker_task_id: {
                        "task_id": worker_task_id,
                        "path": str(self.workspace),
                        "status_root": str(worker_status_root),
                    }
                }
            },
        }

    def _validate(
        self,
        command: str,
        args: list[str],
        *,
        actor: str,
        runtime_state: dict[str, object] | None = None,
        env_task_id: str | None = None,
        workspace_env: dict[str, str] | None = None,
    ) -> None:
        env = {
            "AI_NAME": actor,
            "ORCH_RUN_ID": self.run_id,
            "PANTHEON_WORKTREE_ROOT": str(self.workspace),
            "ORCH_WORKSPACE_PATH": str(self.workspace),
        }
        if workspace_env is not None:
            env.update(workspace_env)
        if env_task_id is not None:
            env["ORCH_TASK_ID"] = env_task_id
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(ai_status, "STATUS_ROOT", self.root),
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(ai_status, "load_config", return_value={}),
            mock.patch.object(
                ai_status,
                "load_runtime_state_snapshot",
                return_value=runtime_state or self._runtime_state(),
            ),
            mock.patch.object(
                ai_status,
                "status_command_metadata",
                return_value=self.issued_runtime,
            ),
        ):
            ai_status.validate_active_status_command_lease(command, args)

    def test_accepts_valid_owner_and_reviewer_dispatch_leases(self) -> None:
        self._validate(
            "progress",
            [self.task_id, "owner progress"],
            actor="Codex",
            env_task_id=self.task_id,
        )
        owner_binding = ai_status._STATUS_COMMAND_LEASE_LOCAL.binding
        self.assertEqual(owner_binding["worker_run_id"], self.run_id)
        self.assertEqual(owner_binding["task_id"], self.task_id)
        self.assertEqual(owner_binding["queue_event_id"], "queue-lease-sync")
        self.assertEqual(owner_binding["pid"], 4242)
        self.assertEqual(owner_binding["pid_start_ticks"], 987654)
        self.assertTrue(
            owner_binding["process_generation"].startswith(
                ai_status.STATUS_WORKER_PROCESS_GENERATION_PREFIX
            )
        )
        with mock.patch.object(
            ai_status,
            "status_command_metadata",
            return_value=self.issued_runtime | {"worker_lease": owner_binding},
        ):
            event = ai_status._activity_event(
                {
                    "ts": "2026-08-02T09:45:00Z",
                    "agent": "Codex",
                    "type": "handoff",
                    "task_id": self.task_id,
                    "message": "ready",
                }
            )
        self.assertEqual(event["status_command"]["worker_lease"], owner_binding)
        self._validate(
            "approve",
            [self.task_id, "review approved"],
            actor="Claude",
            env_task_id=self.task_id,
            runtime_state=self._runtime_state(logical_agent_id="claude"),
        )

    def test_accepts_refreshed_process_generation_after_resume_pid_replacement(self) -> None:
        runtime_state = self._runtime_state()
        worker = runtime_state["workers"][self.run_id]
        old_generation = worker["process_generation"]
        worker["pid"] = 5252
        worker["pid_start_ticks"] = 1234567
        worker["process_generation"] = ai_status.status_worker_process_generation_id(
            task_id=self.task_id,
            worker_run_id=self.run_id,
            queue_event_id=worker["queue_event_id"],
            pid=worker["pid"],
            pid_start_ticks=worker["pid_start_ticks"],
        )

        self._validate(
            "progress",
            [self.task_id, "resumed owner progress"],
            actor="Codex",
            env_task_id=self.task_id,
            runtime_state=runtime_state,
        )

        binding = ai_status._STATUS_COMMAND_LEASE_LOCAL.binding
        self.assertEqual(binding["pid"], 5252)
        self.assertEqual(binding["pid_start_ticks"], 1234567)
        self.assertEqual(binding["process_generation"], worker["process_generation"])
        self.assertNotEqual(binding["process_generation"], old_generation)

    def test_rejects_invalid_process_generation(self) -> None:
        runtime_state = self._runtime_state()
        runtime_state["workers"][self.run_id]["process_generation"] = "forged"
        with self.assertRaisesRegex(RuntimeError, "invalid process generation"):
            self._validate(
                "handoff",
                [self.task_id, "Claude", "ready for review"],
                actor="Codex",
                env_task_id=self.task_id,
                runtime_state=runtime_state,
            )

    def test_rejects_auto_worker_without_run_lease(self) -> None:
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=True),
            mock.patch.object(ai_status, "STATUS_ROOT", self.root),
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "status command lease required for auto worker: Codex",
            ):
                ai_status.validate_active_status_command_lease(
                    "progress", [self.task_id, "missing lease"]
                )

    def test_rejects_expired_run_lease(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "status command lease .* is expired"):
            self._validate(
                "progress",
                [self.task_id, "expired lease"],
                actor="Codex",
                env_task_id=self.task_id,
                runtime_state=self._runtime_state(
                    lease_expires_at="2000-01-01T00:00:00Z"
                ),
            )

    def test_rejects_cross_task_authority(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "worker task LEASE-SYNC-001 != command task OTHER-TASK-001",
        ):
            self._validate(
                "progress",
                ["OTHER-TASK-001", "cross-task attempt"],
                actor="Codex",
            )

    def test_rejects_cross_root_authority(self) -> None:
        other_root = self.root / "other-status-root"
        other_root.mkdir()
        with self.assertRaisesRegex(RuntimeError, "status command root mismatch"):
            self._validate(
                "progress",
                [self.task_id, "cross-root attempt"],
                actor="Codex",
                env_task_id=self.task_id,
                runtime_state=self._runtime_state(status_root=other_root),
            )

    def test_rejects_erased_workspace_binding_under_valid_run_lease(self) -> None:
        """Unsetting both workspace variables must not widen a valid lease."""

        for label, overrides in (
            ("missing", {"PANTHEON_WORKTREE_ROOT": "", "ORCH_WORKSPACE_PATH": ""}),
            ("blank", {"PANTHEON_WORKTREE_ROOT": "   ", "ORCH_WORKSPACE_PATH": "  "}),
        ):
            with self.subTest(binding=label):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "status command workspace binding is required",
                ):
                    self._validate(
                        "progress",
                        [self.task_id, "erased workspace binding"],
                        actor="Codex",
                        env_task_id=self.task_id,
                        workspace_env=overrides,
                    )

    def test_rejects_erased_workspace_binding_from_worktree_lease_alone(self) -> None:
        """The worktree lease path is authoritative even without worker metadata."""

        runtime_state = self._runtime_state()
        del runtime_state["workers"][self.run_id]["workspace_path"]  # type: ignore[index]

        with self.assertRaisesRegex(
            RuntimeError,
            "status command workspace binding is required",
        ):
            self._validate(
                "progress",
                [self.task_id, "lease-only workspace authority"],
                actor="Codex",
                env_task_id=self.task_id,
                runtime_state=runtime_state,
                workspace_env={
                    "PANTHEON_WORKTREE_ROOT": "",
                    "ORCH_WORKSPACE_PATH": "",
                },
            )

    def test_active_lease_workspace_roots_survive_erased_environment(self) -> None:
        """The canonical boundary comes from runtime state, not the candidate."""

        env = {
            "AI_NAME": "Codex",
            "ORCH_RUN_ID": self.run_id,
            "ORCH_TASK_ID": self.task_id,
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(ai_status, "STATUS_ROOT", self.root),
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(ai_status, "load_config", return_value={}),
            mock.patch.object(
                ai_status,
                "load_runtime_state_snapshot",
                return_value=self._runtime_state(),
            ),
        ):
            roots = ai_status.active_lease_workspace_roots()

        self.assertEqual(roots, (self.workspace.resolve(),))

    def test_active_lease_workspace_roots_are_empty_without_run_lease(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=True):
            self.assertEqual(ai_status.active_lease_workspace_roots(), ())

    def test_explicit_batch_snapshots_avoid_per_mutation_runtime_reload(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "ORCH_RUN_ID": self.run_id,
            "ORCH_TASK_ID": self.task_id,
            "PANTHEON_WORKTREE_ROOT": str(self.workspace),
            "ORCH_WORKSPACE_PATH": str(self.workspace),
        }
        config: dict[str, object] = {}
        runtime_snapshot = self._runtime_state()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(ai_status, "STATUS_ROOT", self.root),
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(ai_status, "load_config", side_effect=AssertionError("config reloaded")),
            mock.patch.object(
                ai_status,
                "load_runtime_state_snapshot",
                side_effect=AssertionError("runtime reloaded"),
            ),
            mock.patch.object(
                ai_status,
                "status_command_metadata",
                return_value=self.issued_runtime,
            ),
        ):
            ai_status.validate_active_status_command_lease(
                "progress",
                [self.task_id, "batch progress"],
                runtime_state_snapshot=runtime_snapshot,
                config_snapshot=config,
            )

        self.assertEqual(
            ai_status._STATUS_COMMAND_LEASE_LOCAL.binding["worker_run_id"],
            self.run_id,
        )


class SupervisorDispatchBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.addCleanup(_teardown_test_isolation, self)
        self.state = ai_status.default_state()
        self.state["tasks"] = [
            {
                "id": "BATCH-ONE",
                "title": "First dispatch",
                "phase": "test",
                "owner": "Codex",
                "reviewer": "Human/Ops",
                "status": "todo",
                "depends_on": [],
                "artifacts": [],
                "acceptance": [],
                "next": "queued",
            },
            {
                "id": "BATCH-TWO",
                "title": "Second dispatch",
                "phase": "test",
                "owner": "Codex2",
                "reviewer": "Human/Ops",
                "status": "todo",
                "depends_on": [],
                "artifacts": [],
                "acceptance": [],
                "next": "queued",
            },
        ]
        self.mutations = [
            {
                "actor": "Codex",
                "command": "start",
                "expected_statuses": ["todo"],
                "message": "first started",
                "run_id": "run-one",
                "task_id": "BATCH-ONE",
                "workspace_path": str(self._test_root / "one"),
            },
            {
                "actor": "Codex2",
                "command": "start",
                "expected_statuses": ["todo"],
                "message": "second started",
                "run_id": "run-two",
                "task_id": "BATCH-TWO",
                "workspace_path": str(self._test_root / "two"),
            },
        ]

    def _run_main(self, mutations: list[dict[str, object]], *, sync_all=None) -> int:
        sync_all = sync_all or mock.Mock()
        with (
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(ai_status, "load_supervisor_dispatch_batch", return_value=mutations),
            mock.patch.object(ai_status, "load_config", return_value={}),
            mock.patch.object(ai_status, "runtime_state_lock", return_value=contextlib.nullcontext()),
            mock.patch.object(ai_status, "load_runtime_state_snapshot", return_value={"workers": {}}) as runtime_load,
            mock.patch.object(ai_status, "canonical_task_state_lock", return_value=contextlib.nullcontext()),
            mock.patch.object(ai_status, "authoritative_task_state_transaction", return_value=contextlib.nullcontext()),
            mock.patch.object(ai_status, "load_state", return_value=self.state) as state_load,
            mock.patch.object(ai_status, "recover_status_archive_outbox", return_value=False),
            mock.patch.object(ai_status, "recover_status_activity_outbox", return_value=False),
            mock.patch.object(ai_status, "validate_active_status_command_lease"),
            mock.patch.object(ai_status, "sync_all", side_effect=sync_all) as sync_mock,
            mock.patch.object(ai_status, "refresh_derived_status_views_if_current"),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            self.sync_mock = sync_mock
            result = ai_status.main(
                ["ai_status.py", ai_status.SUPERVISOR_DISPATCH_BATCH_COMMAND, "/tmp/batch.json"]
            )
        self.assertEqual(runtime_load.call_count, 1)
        self.assertEqual(state_load.call_count, 1)
        return result

    def test_batch_uses_one_runtime_and_canonical_snapshot(self) -> None:
        result = self._run_main(self.mutations)

        self.assertEqual(result, 0)
        self.assertEqual(self.sync_mock.call_count, 1)
        self.assertEqual(ai_status.get_task(self.state, "BATCH-ONE")["status"], "in_progress")
        self.assertEqual(ai_status.get_task(self.state, "BATCH-TWO")["status"], "in_progress")

    def test_late_cas_failure_prevents_the_single_commit(self) -> None:
        invalid = deepcopy(self.mutations)
        invalid[1]["expected_statuses"] = ["review_approved"]

        with self.assertRaisesRegex(RuntimeError, "status CAS failed for BATCH-TWO"):
            self._run_main(invalid)

        self.assertEqual(self.sync_mock.call_count, 0)

    def test_batch_activity_outbox_recovers_after_post_state_crash(self) -> None:
        """Both rows survive a crash between canonical save and audit append."""

        store_env = mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "",
                ai_status.TASK_STATE_EVENT_LOG_ENV: "",
            },
        )
        store_env.start()
        self.addCleanup(store_env.stop)
        ai_status.save_state(deepcopy(self.state))
        working = ai_status.load_state()
        with (
            mock.patch.object(ai_status, "validate_active_status_command_lease"),
            ai_status.buffer_activity_events() as events,
        ):
            ai_status.run_supervisor_dispatch_batch(
                working,
                self.mutations,
                commands={
                    "start": ai_status.command_start,
                    "progress": ai_status.command_progress,
                    "note": ai_status.command_note,
                },
                runtime_snapshot={"workers": {}},
                config={},
            )
            self.assertEqual(len(events), 2)
            with mock.patch.object(
                ai_status,
                "recover_status_activity_outbox",
                side_effect=RuntimeError("simulated post-state crash"),
            ):
                with self.assertRaisesRegex(RuntimeError, "post-state crash"):
                    ai_status.commit_state_with_activity_outbox(working, events)

        pending = ai_status.load_state()
        self.assertEqual(
            [task["status"] for task in pending["tasks"]],
            ["in_progress", "in_progress"],
        )
        outbox = pending[ai_status.STATUS_ACTIVITY_OUTBOX_KEY]
        self.assertEqual(len(outbox["events"]), 2)
        self.assertEqual(ai_status.LOG_FILE.read_text(encoding="utf-8"), "")

        self.assertTrue(ai_status.recover_status_activity_outbox(pending))
        recovered = ai_status.load_state()
        self.assertIsNone(recovered[ai_status.STATUS_ACTIVITY_OUTBOX_KEY])
        audit_rows = [
            json.loads(line)
            for line in ai_status.LOG_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [row["task_id"] for row in audit_rows],
            ["BATCH-ONE", "BATCH-TWO"],
        )
        self.assertEqual(
            len({row["event_id"] for row in audit_rows}),
            2,
        )

    def test_payload_parser_rejects_duplicate_tasks_and_unbounded_rows(self) -> None:
        payload = self._test_root / "batch.json"
        payload.write_text(
            json.dumps(
                {
                    "schema_version": ai_status.SUPERVISOR_DISPATCH_BATCH_SCHEMA_VERSION,
                    "mutations": [self.mutations[0], self.mutations[0]],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SystemExit, "repeats task mutation"):
            ai_status.load_supervisor_dispatch_batch(str(payload))

    def test_lock_order_contract_is_explicit(self) -> None:
        self.assertEqual(
            ai_status.GLOBAL_STATUS_LOCK_ORDER,
            ("runtime_admission", "task_state", "activity_audit"),
        )


class DevBridgeMaterializeBatchTests(unittest.TestCase):
    """Covers SUP-CANONICAL-PACKET-ATOMIC-MATERIALIZATION-20260811.

    The dispatcher used to call ``assign`` once per packet task, so a second
    (or later) row failing left the earlier rows already committed -- exactly
    the partial materialization incident recorded in
    ``.orchestrator/assistant-dev-packets/receipts/pkt-two-blocker-dev-closeout-20260809T060036Z.json``.
    ``dev-bridge-materialize-batch`` applies every row to one in-memory
    snapshot and reaches the journal through exactly one commit.
    """

    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.addCleanup(_teardown_test_isolation, self)
        self.journal = self._test_root / "runtime" / "task-state-events.jsonl"
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(self.journal),
                "AI_NAME": "Human/Ops",
                "ORCH_RUN_ID": "",
                "ORCH_TASK_ID": "",
                "ORCH_AGENT_ID": "",
                "ORCH_PROVIDER": "",
                "ORCH_SESSION_ID": "",
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        ai_status.append_state_commit(
            self.journal,
            ai_status.default_state(),
            source="test-bootstrap",
        )

    def _task_spec(self, task_id: str, *, owner: str = "Codex", reviewer: str = "Antigravity") -> dict[str, Any]:
        return {
            "id": task_id,
            "title": f"Title for {task_id}",
            "owner": owner,
            "reviewer": reviewer,
            "phase": "Canonical Task Materialization",
            "depends_on": [],
            "artifacts": ["docs/deployment/evidence/" + task_id + "/"],
            "acceptance": ["Do the thing"],
            "summary": f"Summary for {task_id}",
        }

    def _task_row(
        self,
        task_id: str,
        *,
        packet_id: str = "pkt-test-20260811T000000Z",
        packet_digest: str | None = None,
        owner: str = "Codex",
        reviewer: str = "Antigravity",
    ) -> dict[str, Any]:
        spec = self._task_spec(task_id, owner=owner, reviewer=reviewer)
        spec_hash = hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        digest = packet_digest or hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        return {
            "task_id": task_id,
            "owner": owner,
            "reviewer": reviewer,
            "title": spec["title"],
            "assignment_next": None,
            "task_metadata": {
                "dev_bridge": {
                    "packet_id": packet_id,
                    "packet_digest": digest,
                    "task_spec_hash": spec_hash,
                    "task_spec": spec,
                    "conversation_id": "conversation-20260811",
                    "source_turn_ids": ["turn-1"],
                    "documents": [{"kind": "GOVERNANCE", "path": "AGENTS.md"}],
                    "audit_conversation_href": None,
                    "emitted_at": "2026-08-11T12:06:12Z",
                    "intent": "governed_supervisor_architecture_cutover",
                    "mode": "supervisor_architecture_governed_delivery",
                    "actor": {"id": "Human/Ops", "roles": ["operator"], "capabilities": ["integration"]},
                }
            },
        }

    def _payload_path(self, rows: list[dict[str, Any]], *, packet_id: str, packet_digest: str) -> Path:
        payload = self._test_root / "batch.json"
        payload.write_text(
            json.dumps(
                {
                    "schema_version": ai_status.DEV_BRIDGE_BATCH_SCHEMA_VERSION,
                    "packet_id": packet_id,
                    "packet_digest": packet_digest,
                    "actor": "Human/Ops",
                    "tasks": rows,
                }
            ),
            encoding="utf-8",
        )
        return payload

    def _run_main(self, payload_path: Path) -> int:
        with (
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            return ai_status.main(
                ["ai_status.py", ai_status.DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND, str(payload_path)]
            )

    def _run_readback(self, payload_path: Path) -> dict[str, Any]:
        output = io.StringIO()
        with (
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(sys, "stdout", output),
        ):
            self.assertEqual(
                ai_status.main(
                    [
                        "ai_status.py",
                        ai_status.DEV_BRIDGE_BATCH_READBACK_COMMAND,
                        str(payload_path),
                    ]
                ),
                0,
            )
        return json.loads(output.getvalue())

    def test_batch_commits_every_task_in_exactly_one_journal_event(self) -> None:
        packet_id = "pkt-batch-ok-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [
            self._task_row("BATCH-ATOMIC-ONE", packet_id=packet_id, packet_digest=digest),
            self._task_row("BATCH-ATOMIC-TWO", packet_id=packet_id, packet_digest=digest),
        ]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        result = self._run_main(payload)

        self.assertEqual(result, 0)
        events = load_events(self.journal)
        # Exactly one new event relative to the bootstrap baseline: the
        # activity-log outbox flush is deferred so it can never become a
        # second canonical task-state commit for this batch. Both tasks are
        # already present together in that one event -- never one row, then
        # the other.
        self.assertEqual(len(events), 2)
        committed_state = events[1]["state"]
        batch_tasks = [
            task
            for task in committed_state["tasks"]
            if task["id"] in {"BATCH-ATOMIC-ONE", "BATCH-ATOMIC-TWO"}
        ]
        self.assertEqual(
            sorted(task["id"] for task in batch_tasks),
            ["BATCH-ATOMIC-ONE", "BATCH-ATOMIC-TWO"],
        )
        for task in batch_tasks:
            self.assertEqual(task["dev_bridge"]["packet_id"], packet_id)
            self.assertEqual(task["dev_bridge"]["packet_digest"], digest)

    def test_second_row_failure_commits_nothing_not_even_the_first_row(self) -> None:
        packet_id = "pkt-batch-partial-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [
            self._task_row("BATCH-PARTIAL-ONE", packet_id=packet_id, packet_digest=digest),
            # owner == reviewer is rejected by command_assign.
            self._task_row(
                "BATCH-PARTIAL-TWO",
                packet_id=packet_id,
                packet_digest=digest,
                owner="Codex",
                reviewer="Codex",
            ),
        ]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        with self.assertRaisesRegex(SystemExit, "Reviewer cannot equal owner"):
            self._run_main(payload)

        self.assertEqual(len(load_events(self.journal)), 1)
        state = json.loads(self._test_status_file.read_text(encoding="utf-8"))
        task_ids = {task["id"] for task in state.get("tasks", [])}
        self.assertNotIn("BATCH-PARTIAL-ONE", task_ids)
        self.assertNotIn("BATCH-PARTIAL-TWO", task_ids)

    def test_exact_duplicate_retry_is_idempotent(self) -> None:
        packet_id = "pkt-batch-retry-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [self._task_row("BATCH-RETRY-ONE", packet_id=packet_id, packet_digest=digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        self.assertEqual(self._run_main(payload), 0)
        # No recover, projection write, or other canonical mutation is allowed
        # between the first success and this retry. The first batch's activity
        # outbox is deliberately still pending and must not be flushed by the
        # exact replay.
        events_after_first = len(load_events(self.journal))
        first_state = ai_status.load_state()
        first_task = ai_status.get_task(first_state, "BATCH-RETRY-ONE")
        self.assertIsNotNone(first_task)
        self.assertIsNotNone(
            first_state.get(ai_status.STATUS_ACTIVITY_OUTBOX_KEY)
        )

        # An exact retry -- every row already matches its bound provenance --
        # adds zero journal events: it must not error, duplicate the row,
        # drift its bound provenance, or grow the journal at all.
        self.assertEqual(self._run_main(payload), 0)
        self.assertEqual(len(load_events(self.journal)), events_after_first)
        second_state = ai_status.load_state()
        second_task = ai_status.get_task(second_state, "BATCH-RETRY-ONE")
        self.assertEqual(
            len([t for t in second_state["tasks"] if t["id"] == "BATCH-RETRY-ONE"]),
            1,
        )
        self.assertEqual(second_task["owner"], first_task["owner"])
        self.assertEqual(second_task["reviewer"], first_task["reviewer"])
        self.assertEqual(second_task["dev_bridge"], first_task["dev_bridge"])

    def test_authoritative_readback_ignores_pending_audit_projection(self) -> None:
        packet_id = "pkt-batch-readback-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [self._task_row("BATCH-READBACK-ONE", packet_id=packet_id, packet_digest=digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)
        self.assertEqual(self._run_main(payload), 0)
        event_count = len(load_events(self.journal))

        readback = self._run_readback(payload)

        self.assertEqual(readback["status"], "verified")
        self.assertEqual(readback["packetId"], packet_id)
        self.assertEqual(readback["taskIds"], ["BATCH-READBACK-ONE"])
        self.assertEqual(
            readback["pendingAuditProjections"],
            [ai_status.STATUS_ACTIVITY_OUTBOX_KEY],
        )
        self.assertEqual(len(load_events(self.journal)), event_count)

    def test_partial_preexisting_packet_refuses_missing_suffix_with_zero_events(self) -> None:
        packet_id = "pkt-batch-prefix-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        first = self._task_row("BATCH-PREFIX-ONE", packet_id=packet_id, packet_digest=digest)
        first_payload = self._payload_path([first], packet_id=packet_id, packet_digest=digest)
        self.assertEqual(self._run_main(first_payload), 0)
        before = len(load_events(self.journal))

        second = self._task_row("BATCH-PREFIX-TWO", packet_id=packet_id, packet_digest=digest)
        full_payload = self._payload_path(
            [first, second], packet_id=packet_id, packet_digest=digest
        )
        with self.assertRaisesRegex(SystemExit, "partial pre-existing packet"):
            self._run_main(full_payload)

        self.assertEqual(len(load_events(self.journal)), before)
        state = ai_status.load_state()
        self.assertIsNone(ai_status.get_task(state, "BATCH-PREFIX-TWO"))

    def test_direct_assign_cannot_forge_bridge_provenance(self) -> None:
        packet_id = "pkt-direct-forgery-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        row = self._task_row(
            "BATCH-DIRECT-FORGERY", packet_id=packet_id, packet_digest=digest
        )
        before = len(load_events(self.journal))

        with (
            mock.patch.dict(
                os.environ,
                {"TASK_METADATA_JSON": json.dumps(row["task_metadata"])},
                clear=False,
            ),
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "validate_status_root_binding"),
            self.assertRaisesRegex(SystemExit, "can only be created by"),
        ):
            ai_status.main(
                [
                    "ai_status.py",
                    "assign",
                    row["task_id"],
                    row["owner"],
                    row["reviewer"],
                    row["title"],
                ]
            )

        self.assertEqual(len(load_events(self.journal)), before)

    def test_crash_before_commit_writes_zero_rows(self) -> None:
        packet_id = "pkt-batch-precommit-crash-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [self._task_row("BATCH-PRECOMMIT-ONE", packet_id=packet_id, packet_digest=digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        with (
            mock.patch.object(ai_status, "sync_all", side_effect=OSError("injected precommit crash")),
            self.assertRaisesRegex(OSError, "injected precommit crash"),
        ):
            self._run_main(payload)

        self.assertEqual(len(load_events(self.journal)), 1)
        self.assertIsNone(
            ai_status.get_task(ai_status.load_state(), "BATCH-PRECOMMIT-ONE")
        )

    def test_digest_mismatch_between_batch_and_row_fails_closed(self) -> None:
        packet_id = "pkt-batch-mismatch-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        other_digest = hashlib.sha256(b"different-payload").hexdigest()
        rows = [self._task_row("BATCH-MISMATCH-ONE", packet_id=packet_id, packet_digest=other_digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        with self.assertRaisesRegex(SystemExit, "packet_digest does not match the batch"):
            ai_status.load_dev_bridge_materialize_batch(str(payload))

    def test_owner_reassignment_after_materialization_then_replay_row_is_a_no_op(self) -> None:
        """A later Human/Ops reassignment must not be clobbered by an exact replay."""

        packet_id = "pkt-batch-reassign-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [self._task_row("BATCH-REASSIGN-ONE", packet_id=packet_id, packet_digest=digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)
        self.assertEqual(self._run_main(payload), 0)

        with mock.patch.object(ai_status, "validate_status_command_runtime_binding"),             mock.patch.object(ai_status, "validate_status_root_binding"):
            ai_status.main(
                ["ai_status.py", "assign", "BATCH-REASSIGN-ONE", "Claude", "Codex2"]
            )
        reassigned = ai_status.load_state()
        task = ai_status.get_task(reassigned, "BATCH-REASSIGN-ONE")
        self.assertEqual(task["owner"], "Claude")
        self.assertEqual(task["reviewer"], "Codex2")
        self.assertEqual(task["dev_bridge"]["task_spec"]["owner"], "Codex")

        self.assertEqual(self._run_main(payload), 0)
        state = ai_status.load_state()
        task = ai_status.get_task(state, "BATCH-REASSIGN-ONE")
        self.assertEqual(task["owner"], "Claude")
        self.assertEqual(task["reviewer"], "Codex2")


class StatusRootRoutingTests(unittest.TestCase):
    def _init_repo(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
        (path / ".gitkeep").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=path, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git"], cwd=path, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=path, check=True)

    def _write_status_state(self, root: Path, *, owner: str, next_value: str) -> None:
        state = ai_status.default_state()
        state["tasks"] = [
            {
                "id": "CENTRAL-ROOT-001",
                "title": "Central root routing",
                "phase": "test",
                "owner": owner,
                "reviewer": "Antigravity",
                "status": "in_progress",
                "depends_on": [],
                "artifacts": [],
                "acceptance": [],
                "next": next_value,
                "last_update": "2026-07-16T00:00:00Z",
            }
        ]
        (root / "ai-status.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _copy_status_tooling(self, destination: Path) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        for rel in (
            "scripts/ai_status.py",
            "scripts/ai-status.sh",
            "scripts/loop_done_guardrail.py",
            ".orchestrator/common.py",
            ".orchestrator/runtime_state.py",
            ".orchestrator/task_archive.py",
            ".orchestrator/multi_repo_registry.py",
            ".orchestrator/rewrite/__init__.py",
            ".orchestrator/rewrite/task_state_store.py",
            ".orchestrator/config.json",
        ):
            source = repo_root / rel
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        config_path = destination / ".orchestrator" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["task_state_store"]["mode"] = "shadow"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def _commit_all(self, repo: Path, message: str = "install status tooling") -> str:
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=repo, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    def test_load_local_coordination_payload_tolerates_missing_yaml(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-no-yaml-") as temp_dir:
            root = Path(temp_dir)
            (root / "payload.yaml").write_text("status: done\n", encoding="utf-8")
            (root / "payload.json").write_text('{"status": "done"}\n', encoding="utf-8")

            with (
                mock.patch.object(ai_status, "ROOT", root),
                mock.patch.object(ai_status, "yaml", None),
                mock.patch.object(ai_status, "YAML_ERROR_TYPES", ()),
            ):
                self.assertIsNone(ai_status.load_local_coordination_payload("payload.yaml"))
                self.assertEqual(
                    {"status": "done"},
                    ai_status.load_local_coordination_payload("payload.json"),
                )

    def test_load_config_routes_runtime_paths_to_status_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-routing-") as temp_dir:
            root = Path(temp_dir)
            code_root = root / "code"
            status_root = root / "status"
            config_file = code_root / ".orchestrator" / "config.json"
            config_file.parent.mkdir(parents=True)
            config_file.write_text(
                json.dumps(
                    {
                        "paths": {
                            "status_file": "ai-status.json",
                            "activity_log": "ai-activity-log.jsonl",
                            "state_file": ".orchestrator/state.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(ai_status, "CONFIG_FILE", config_file),
                mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                mock.patch.object(ai_status, "STATUS_FILE", status_root / "ai-status.json"),
                mock.patch.object(ai_status, "LOG_FILE", status_root / "ai-activity-log.jsonl"),
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", status_root / "current-work.md"),
                mock.patch.object(ai_status, "DOCS_SITE_DIR", status_root / "docs-site"),
                mock.patch.object(ai_status, "ORCHESTRATOR_STATE_FILE", status_root / ".orchestrator" / "state.json"),
                mock.patch.object(ai_status, "APPROVAL_QUEUE_FILE", status_root / ".orchestrator" / "approval-queue.json"),
            ):
                config = ai_status.load_config()

        self.assertEqual(config["paths"]["status_file"], str(status_root / "ai-status.json"))
        self.assertEqual(config["paths"]["activity_log"], str(status_root / "ai-activity-log.jsonl"))
        self.assertEqual(config["paths"]["state_file"], str(status_root / ".orchestrator" / "state.json"))
        self.assertEqual(config["paths"]["event_queue"], str(status_root / ".orchestrator" / "event-queue.jsonl"))

    def test_worktree_status_wrapper_reads_and_writes_only_central_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-central-routing-") as temp_dir:
            root = Path(temp_dir)
            central = root / "central"
            worktree = root / "task-worktree"
            self._init_repo(central)
            self._copy_status_tooling(central)
            self._init_repo(worktree)
            self._copy_status_tooling(worktree)
            stale_local_ai_status = worktree / "scripts" / "ai_status.py"
            stale_local_ai_status.write_text(
                "raise SystemExit('stale worktree-local ai_status.py executed')\n",
                encoding="utf-8",
            )
            self._write_status_state(
                central,
                owner="Codex2",
                next_value="central task is current",
            )
            self._write_status_state(
                worktree,
                owner="Gemini",
                next_value="stale worktree task must not be read",
            )
            central_log = central / "ai-activity-log.jsonl"
            worktree_log = worktree / "ai-activity-log.jsonl"
            central_log.write_text(
                json.dumps({"event_id": "central-seed", "type": "seed"}) + "\n",
                encoding="utf-8",
            )
            command_sha = self._commit_all(central)
            worktree_log.write_text(
                json.dumps({"event_id": "stale-seed", "type": "stale"}) + "\n",
                encoding="utf-8",
            )
            worktree_archive = worktree / "ai-task-archive" / "tasks" / "STALE.json"
            worktree_archive.parent.mkdir(parents=True)
            worktree_archive.write_text('{"task_id": "STALE"}\n', encoding="utf-8")
            worktree_current = worktree / "current-work.md"
            worktree_dashboard = worktree / "dashboard-bundle.json"
            worktree_docs_site = worktree / "docs-site"
            worktree_docs_status = worktree_docs_site / "ai-status.json"
            worktree_docs_current = worktree_docs_site / "current-work.md"
            worktree_docs_dashboard = worktree_docs_site / "dashboard-bundle.json"
            worktree_docs_site.mkdir(parents=True)
            worktree_current.write_text("stale current work\n", encoding="utf-8")
            worktree_dashboard.write_text('{"stale": true}\n', encoding="utf-8")
            worktree_docs_status.write_text('{"stale": "status"}\n', encoding="utf-8")
            worktree_docs_current.write_text("stale docs current\n", encoding="utf-8")
            worktree_docs_dashboard.write_text('{"stale": "dashboard"}\n', encoding="utf-8")
            worktree_task_lock = worktree / ".orchestrator" / "task-state.lock"
            worktree_activity_lock = worktree / ".orchestrator" / "activity-audit.lock"
            worktree_task_lock.write_text("stale-task-lock\n", encoding="utf-8")
            worktree_activity_lock.write_text("stale-activity-lock\n", encoding="utf-8")
            stale_before = {
                path: path.read_bytes()
                for path in (
                    worktree / "ai-status.json",
                    worktree_log,
                    worktree_archive,
                    worktree_current,
                    worktree_dashboard,
                    worktree_docs_status,
                    worktree_docs_current,
                    worktree_docs_dashboard,
                    worktree_task_lock,
                    worktree_activity_lock,
                )
            }
            central_runner_status = central / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            central_heartbeat = central / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"
            issued_runtime = {
                "command_root": str(central),
                "source_sha": command_sha,
                "remote": "ajoe734/pantheon",
                "base_ref": "origin/dev",
            }
            worker_pid = 4242
            worker_pid_start_ticks = 987654
            worker_process_generation = ai_status.status_worker_process_generation_id(
                task_id="CENTRAL-ROOT-001",
                worker_run_id="codex-test-run",
                queue_event_id="evt-codex-test-run",
                pid=worker_pid,
                pid_start_ticks=worker_pid_start_ticks,
            )
            central_state_path = central / ".orchestrator" / "state.json"
            central_state_path.parent.mkdir(parents=True, exist_ok=True)
            central_state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "workers": {
                            "codex-test-run": {
                                "run_id": "codex-test-run",
                                "provider": "codex",
                                "logical_agent_id": "codex2",
                                "agent_id": "codex2_1",
                                "task_id": "CENTRAL-ROOT-001",
                                "status": "running",
                                "lease_acquired_at": "2026-07-17T00:00:00Z",
                                "lease_expires_at": "2999-01-01T00:00:00Z",
                                "queue_event_id": "evt-codex-test-run",
                                "pid": worker_pid,
                                "pid_start_ticks": worker_pid_start_ticks,
                                "process_generation": worker_process_generation,
                                "workspace_path": str(worktree),
                                "status_root": str(central),
                                "status_command_runtime": issued_runtime,
                                "request_snapshot": {
                                    "metadata": {
                                        "workspace_task_id": "CENTRAL-ROOT-001",
                                        "workspace_path": str(worktree),
                                        "status_root": str(central),
                                        "status_command_runtime": issued_runtime,
                                    }
                                },
                            }
                        },
                        "worker_worktrees": {
                            "leases": {
                                "CENTRAL-ROOT-001": {
                                    "task_id": "CENTRAL-ROOT-001",
                                    "workspace_task_id": "CENTRAL-ROOT-001",
                                    "branch": "task/CENTRAL-ROOT-001",
                                    "path": str(worktree),
                                    "status_root": str(central),
                                    "last_queue_event_id": "evt-codex-test-run",
                                    "last_target_agent": "Codex2",
                                    "last_used_at": "2026-07-17T00:00:00Z",
                                }
                            }
                        },
                        "queue": {"events": {}},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            # The fixture installs a shadow-mode command runtime. Do not let an
            # auto-worker's live authoritative journal binding replace that isolated
            # test configuration in the child status commands.
            env.pop(ai_status.TASK_STATE_STORE_MODE_ENV, None)
            env.pop(ai_status.TASK_STATE_EVENT_LOG_ENV, None)
            env.update(
                {
                    "AI_NAME": "Codex2",
                    "PANTHEON_STATUS_ROOT": str(central),
                    "PANTHEON_WORKTREE_ROOT": str(worktree),
                    "ORCH_WORKSPACE_PATH": str(worktree),
                    "ORCH_RUN_ID": "codex-test-run",
                    "ORCH_TASK_ID": "CENTRAL-ROOT-001",
                    "ORCH_RUNNER_STATUS_PATH": str(central_runner_status),
                    "ORCH_HEARTBEAT_PATH": str(central_heartbeat),
                    "PANTHEON_COMMAND_ROOT": str(central),
                    "PANTHEON_COMMAND_RUNTIME_SHA": command_sha,
                    "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                    "PANTHEON_COMMAND_BASE_REF": "origin/dev",
                }
            )

            def run_status(args: list[str], *, actor: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                command_env = dict(env)
                command_env["AI_NAME"] = actor
                if actor.lower() != "codex2":
                    command_env.pop("ORCH_RUN_ID", None)
                if extra_env:
                    command_env.update(extra_env)
                return subprocess.run(
                    ["bash", str(worktree / "scripts" / "ai-status.sh"), *args],
                    cwd=worktree,
                    env=command_env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )

            # Verify lease validation fails when AI_NAME is mismatched
            mismatched_result = run_status(
                ["progress", "CENTRAL-ROOT-001", "mismatched progress"],
                actor="Antigravity",
                extra_env={"ORCH_RUN_ID": "codex-test-run"}
            )
            self.assertNotEqual(mismatched_result.returncode, 0)
            self.assertIn("status command lease AI identity mismatch", mismatched_result.stderr)

            wrong_lane_result = run_status(
                ["progress", "CENTRAL-ROOT-001", "wrong logical lane"],
                actor="Codex",
                extra_env={"ORCH_RUN_ID": "codex-test-run"},
            )
            self.assertNotEqual(wrong_lane_result.returncode, 0)
            self.assertIn("status command lease AI identity mismatch", wrong_lane_result.stderr)

            show = subprocess.run(
                ["bash", str(worktree / "scripts" / "ai-status.sh"), "show", "CENTRAL-ROOT-001"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(show.returncode, 0, show.stderr + show.stdout)
            self.assertIn("central task is current", show.stdout)
            self.assertNotIn("stale worktree task must not be read", show.stdout)

            for args in (
                ["progress", "CENTRAL-ROOT-001", "central progress only"],
                ["handoff", "CENTRAL-ROOT-001", "Antigravity", "central handoff only"],
            ):
                result = run_status(args, actor="Codex2")
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            reopen = run_status(
                ["reopen", "CENTRAL-ROOT-001", "review requested central changes only"],
                actor="Antigravity",
            )
            self.assertEqual(reopen.returncode, 0, reopen.stderr + reopen.stdout)
            second_handoff = run_status(
                ["handoff", "CENTRAL-ROOT-001", "Antigravity", "central second review only"],
                actor="Codex2",
            )
            self.assertEqual(second_handoff.returncode, 0, second_handoff.stderr + second_handoff.stdout)
            approve = run_status(
                ["approve", "CENTRAL-ROOT-001", "central approval only"],
                actor="Antigravity",
                extra_env={"REVIEW_NOTES_ZH": "central approve"},
            )
            self.assertEqual(approve.returncode, 0, approve.stderr + approve.stdout)
            done = run_status(
                ["done", "CENTRAL-ROOT-001", "central done and archive only"],
                actor="Codex2",
                extra_env={
                    "TASK_REQUIRE_COMMIT_HASH": "0",
                    "TASK_REQUIRE_GIT_CLEAN": "0",
                    "TASK_RECORD_REMOTE_STATUS": "0",
                    "TASK_REQUIRE_MERGED_PR": "0",
                },
            )
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)

            subprocess.run(["git", "add", "ai-task-archive/tasks/CENTRAL-ROOT-001.json"], cwd=central, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "commit archived task"], cwd=central, check=True)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys, pathlib; sys.path.append('.orchestrator'); import task_archive; task_archive.STATUS_ROOT=pathlib.Path(sys.argv[1]); task_archive.STATUS_FILE=task_archive.STATUS_ROOT/'ai-status.json'; task_archive.ARCHIVE_DIR=task_archive.STATUS_ROOT/'ai-task-archive'; task_archive.ARCHIVE_TASKS_DIR=task_archive.ARCHIVE_DIR/'tasks'; task_archive.ARCHIVE_INDEX_FILE=task_archive.ARCHIVE_DIR/'index.json'; task_archive.rebuild_archive_index()",
                    str(central),
                ],
                cwd=worktree,
                check=True,
            )

            central_state = json.loads((central / "ai-status.json").read_text(encoding="utf-8"))
            self.assertIsNone(ai_status.get_task(central_state, "CENTRAL-ROOT-001"))
            archived = json.loads(
                (central / "ai-task-archive" / "tasks" / "CENTRAL-ROOT-001.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(archived["task"]["status"], "done")
            archive_index = json.loads((central / "ai-task-archive" / "index.json").read_text(encoding="utf-8"))
            self.assertIn("CENTRAL-ROOT-001", archive_index["recent_terminal_ids"])
            central_events = [
                json.loads(line)
                for line in central_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(
                {"progress", "handoff", "reopen", "review_approved", "done"}.issubset(
                    {event.get("type") for event in central_events}
                )
            )
            mutating_events = [
                event
                for event in central_events
                if event.get("type") in {"progress", "done"}
            ]
            self.assertTrue(mutating_events)
            for event in mutating_events:
                self.assertEqual(event["status_command"]["command_root"], str(central.resolve()))
                self.assertEqual(event["status_command"]["source_sha"], command_sha)
                self.assertEqual(event["status_command"]["status_root"], str(central.resolve()))
                self.assertEqual(event["status_command"]["delivery_root"], str(worktree.resolve()))
                self.assertEqual(
                    event["status_command"]["worker_lease"],
                    {
                        "schema_version": 1,
                        "task_id": "CENTRAL-ROOT-001",
                        "worker_run_id": "codex-test-run",
                        "queue_event_id": "evt-codex-test-run",
                        "pid": worker_pid,
                        "pid_start_ticks": worker_pid_start_ticks,
                        "process_generation": worker_process_generation,
                    },
                )
            self.assertEqual(
                archived["task"]["delivery"]["repository_path"],
                str(worktree.resolve()),
            )
            self.assertEqual(
                archived["task"]["delivery"]["status_command_runtime"]["command_root"],
                str(central.resolve()),
            )
            self.assertTrue((central / ".orchestrator" / "task-state.lock").exists())
            self.assertTrue((central / ".orchestrator" / "activity-audit.lock").exists())
            self.assertTrue((central / "current-work.md").exists())
            self.assertTrue((central / "dashboard-bundle.json").exists())
            self.assertTrue((central / "docs-site" / "ai-status.json").exists())
            self.assertTrue((central / "docs-site" / "current-work.md").exists())
            self.assertTrue((central / "docs-site" / "dashboard-bundle.json").exists())
            for path, before in stale_before.items():
                self.assertEqual(
                    path.read_bytes(),
                    before,
                    f"stale worktree coordination file changed: {path}",
                )

    def test_status_root_validation_rejects_invalid_supervisor_bindings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-root-invalid-") as temp_dir:
            root = Path(temp_dir)
            code_root = root / "code"
            self._init_repo(code_root)
            self._copy_status_tooling(code_root)
            self._write_status_state(
                code_root,
                owner="Gemini",
                next_value="stale task worktree root",
            )
            command_sha = self._commit_all(code_root)
            valid = root / "central"
            self._init_repo(valid)
            self._write_status_state(valid, owner="Codex2", next_value="valid")
            other_valid = root / "other-central"
            self._init_repo(other_valid)
            self._write_status_state(other_valid, owner="Codex2", next_value="other valid")
            symlink = root / "central-link"
            symlink.symlink_to(valid, target_is_directory=True)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            component_target = real_parent / "component-central"
            self._init_repo(component_target)
            self._write_status_state(component_target, owner="Codex2", next_value="component")
            link_parent = root / "linked-parent"
            link_parent.symlink_to(real_parent, target_is_directory=True)
            component_symlink_root = link_parent / "component-central"
            runner_status = valid / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            heartbeat = valid / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"

            base_env = os.environ.copy()
            base_env.update(
                {
                    "AI_NAME": "Codex2",
                    "PANTHEON_WORKTREE_ROOT": str(code_root),
                    "ORCH_WORKSPACE_PATH": str(code_root),
                    "ORCH_RUN_ID": "codex-test-run",
                    "ORCH_RUNNER_STATUS_PATH": str(runner_status),
                    "ORCH_HEARTBEAT_PATH": str(heartbeat),
                    "PANTHEON_COMMAND_ROOT": str(code_root),
                    "PANTHEON_COMMAND_RUNTIME_SHA": command_sha,
                    "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                    "PANTHEON_COMMAND_BASE_REF": "origin/dev",
                }
            )
            cases = [
                ({}, "PANTHEON_STATUS_ROOT is required"),
                ({"PANTHEON_STATUS_ROOT": "relative-root"}, "absolute path"),
                ({"PANTHEON_STATUS_ROOT": str(root / "missing")}, "does not exist"),
                ({"PANTHEON_STATUS_ROOT": str(symlink)}, "symlink component"),
                ({"PANTHEON_STATUS_ROOT": str(component_symlink_root)}, "symlink component"),
                ({"PANTHEON_STATUS_ROOT": str(code_root)}, "not the isolated task worktree"),
                ({"PANTHEON_STATUS_ROOT": str(other_valid)}, "does not match"),
                (
                    {
                        "PANTHEON_STATUS_ROOT": str(valid),
                        "ORCH_WORKSPACE_PATH": str(other_valid),
                    },
                    "disagree on delivery worktree root",
                ),
                (
                    {
                        "PANTHEON_STATUS_ROOT": str(root),
                        "ORCH_RUNNER_STATUS_PATH": "",
                        "ORCH_HEARTBEAT_PATH": "",
                    },
                    "git repository root",
                ),
            ]
            for env_update, expected in cases:
                with self.subTest(expected=expected):
                    env = dict(base_env)
                    env.pop("PANTHEON_STATUS_ROOT", None)
                    env.update(env_update)
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(code_root / "scripts" / "ai_status.py"),
                            "show",
                            "CENTRAL-ROOT-001",
                        ],
                        cwd=code_root,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn(expected, proc.stderr + proc.stdout)

    def test_status_root_validation_rejects_symlinked_leaves_and_mirror_children(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ai-status-symlink-") as temp_dir:
            root = Path(temp_dir)
            code_root = root / "code"
            self._init_repo(code_root)
            self._copy_status_tooling(code_root)
            subprocess.run(["git", "add", "."], cwd=code_root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "add status tooling"], cwd=code_root, check=True)
            subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=code_root, check=True)
            command_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=code_root, text=True).strip()

            valid = root / "central"
            self._init_repo(valid)
            self._write_status_state(valid, owner="Codex2", next_value="valid")

            # 1. Test dangling symlink in central root
            dangling = valid / "current-work.md"
            if dangling.exists() or dangling.is_symlink():
                dangling.unlink()
            dangling.symlink_to(root / "nonexistent-target")

            runner_status = valid / ".orchestrator" / "worker-runtime" / "status" / "run.json"
            heartbeat = valid / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"

            env = os.environ.copy()
            env.update(
                {
                    "AI_NAME": "Codex2",
                    "PANTHEON_WORKTREE_ROOT": str(code_root),
                    "ORCH_WORKSPACE_PATH": str(code_root),
                    "ORCH_RUN_ID": "codex-test-run",
                    "ORCH_RUNNER_STATUS_PATH": str(runner_status),
                    "ORCH_HEARTBEAT_PATH": str(heartbeat),
                    "PANTHEON_STATUS_ROOT": str(valid),
                    "PANTHEON_COMMAND_ROOT": str(code_root),
                    "PANTHEON_COMMAND_RUNTIME_SHA": command_sha,
                    "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                    "PANTHEON_COMMAND_BASE_REF": "origin/dev",
                }
            )

            # Show command should reject due to dangling symlink
            proc = subprocess.run(
                [
                    sys.executable,
                    str(code_root / "scripts" / "ai_status.py"),
                    "show",
                    "CENTRAL-ROOT-001",
                ],
                cwd=code_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot be a symlink", proc.stderr)

            # Clean up dangling symlink
            dangling.unlink()

            # 2. Test mirror child symlink (e.g. docs-site/ai-status.json)
            docs_site = valid / "docs-site"
            docs_site.mkdir(parents=True, exist_ok=True)
            mirror_child = docs_site / "ai-status.json"
            mirror_child.symlink_to(root / "nonexistent-target-2")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(code_root / "scripts" / "ai_status.py"),
                    "show",
                    "CENTRAL-ROOT-001",
                ],
                cwd=code_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot be a symlink", proc.stderr)

            # Clean up mirror child
            mirror_child.unlink()

            # 3. Test symlink in ai-task-archive/tasks
            tasks_dir = valid / "ai-task-archive" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            archive_leaf = tasks_dir / "bad-leaf.json"
            archive_leaf.symlink_to(root / "nonexistent-target-3")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(code_root / "scripts" / "ai_status.py"),
                    "show",
                    "CENTRAL-ROOT-001",
                ],
                cwd=code_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot be a symlink", proc.stderr)

            archive_leaf.unlink()

            # 4. Test symlink in archive/logs
            logs_dir = valid / "archive" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_archive_leaf = logs_dir / "bad-log.gz"
            log_archive_leaf.symlink_to(root / "nonexistent-target-4")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(code_root / "scripts" / "ai_status.py"),
                    "show",
                    "CENTRAL-ROOT-001",
                ],
                cwd=code_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot be a symlink", proc.stderr)

            log_archive_leaf.unlink()

            # 5. Test symlink in .orchestrator/worker-runtime
            runtime_dir = valid / ".orchestrator" / "worker-runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            runtime_leaf = runtime_dir / "bad-leaf.json"
            runtime_leaf.symlink_to(root / "nonexistent-target-5")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(code_root / "scripts" / "ai_status.py"),
                    "show",
                    "CENTRAL-ROOT-001",
                ],
                cwd=code_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("cannot be a symlink", proc.stderr)

            runtime_leaf.unlink()


class CanonicalWriterGuardTests(unittest.TestCase):
    def test_isolated_override_never_bypasses_a_git_checkout(self) -> None:
        canonical_status = Path(__file__).resolve().parents[1] / "ai-status.json"
        with mock.patch.dict(
            os.environ,
            {"PANTHEON_ALLOW_ISOLATED_TEST_WRITES": "1"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "canonical state in a git checkout"):
                assert_isolated_legacy_write_target(
                    canonical_status,
                    tool="guard-test",
                )

    def test_configured_non_git_fixture_requires_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ai-status.json"
            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": temp_dir},
                clear=False,
            ):
                os.environ.pop("PANTHEON_ALLOW_ISOLATED_TEST_WRITES", None)
                with self.assertRaisesRegex(RuntimeError, "PANTHEON_STATUS_ROOT"):
                    assert_isolated_legacy_write_target(target, tool="guard-test")
                os.environ["PANTHEON_ALLOW_ISOLATED_TEST_WRITES"] = "1"
                assert_isolated_legacy_write_target(target, tool="guard-test")

    def test_legacy_override_cannot_authorize_configured_status_root_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ai-status.json"
            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_STATUS_ROOT": temp_dir,
                    "PANTHEON_ALLOW_ISOLATED_LEGACY_WRITES": "1",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "PANTHEON_STATUS_ROOT"):
                    assert_isolated_legacy_write_target(target, tool="guard-test")


class ReviewApprovedWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Gemini", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Copilot", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "REG-002",
                    "title": "Promotion gate",
                    "phase": "Epic C",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Awaiting review",
                    "last_update": "2026-04-06T15:00:00Z",
                }
            ],
            "handoffs": [
                {
                    "task_id": "REG-002",
                    "from": "Codex",
                    "to": "Claude",
                    "message": "Please review the promotion gate.",
                    "status": "pending",
                    "created_at": "2026-04-06T15:00:00Z",
                }
            ],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_review_evidence_file_committed_uses_exact_head_get_query(self) -> None:
        review_file = "docs/deployment/evidence/task/evidence.json"
        head_sha = "a" * 40
        with mock.patch.object(
            ai_status,
            "run_gh_json_command",
            return_value={"type": "file"},
        ) as gh_json:
            self.assertTrue(
                ai_status.review_evidence_file_committed(
                    repository="ajoe734/pantheon",
                    head_sha=head_sha,
                    review_file=review_file,
                )
            )

        gh_json.assert_called_once_with(
            [
                "api",
                "--method",
                "GET",
                (
                    "repos/ajoe734/pantheon/contents/"
                    "docs/deployment/evidence/task/evidence.json?ref="
                    f"{head_sha}"
                ),
            ]
        )

    def test_approve_creates_owner_finalize_handoff(self) -> None:
        self.state["tasks"][0]["failure_streak"] = 2
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude", "REVIEW_NOTES_ZH": "審查通過||交回 owner 收尾"}, clear=False):
            ai_status.command_approve(self.state, ["REG-002", "Review passed. Owner should finalize."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["failure_streak"], 0)
        self.assertEqual(task["review_notes_zh"], ["審查通過", "交回 owner 收尾"])

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertIn("finalize", pending[0]["message"].lower())

    def _approval_events(self) -> list[dict]:
        lines = self._test_log_file.read_text(encoding="utf-8").splitlines()
        return [
            json.loads(line)
            for line in lines
            if line.strip() and json.loads(line).get("type") == "review_approved"
        ]

    def test_approve_records_the_reviewed_pr_head_binding(self) -> None:
        """The merge gate compares these identities; free text is not a binding."""

        bridge_evidence = {
            "repository": "ajoe734/pantheon",
            "pr": 4218,
            "head_sha": "b" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
            "decision": "approve",
            "actor": "Claude",
            "mode": "required_commit_status",
            "status_id": 101,
            "status_context": ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT,
            "status_state": "success",
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_PR": "#4218",
                    "REVIEW_HEAD_SHA": "B" * 40,
                },
                clear=False,
            ),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                return_value=bridge_evidence,
            ) as github_bridge,
        ):
            ai_status.command_approve(
                self.state,
                ["REG-002", "Approved the exact head."],
            )

        expected = {
            "pr": 4218,
            "head_sha": "b" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["review_binding"], expected)
        self.assertEqual(task["github_review_bridge"], bridge_evidence)
        self.assertTrue(ai_status.github_review_bridge_evidence_matches(task))
        github_bridge.assert_called_once_with(
            task,
            actor="Claude",
            decision="approve",
            message="Approved the exact head.",
            binding=expected,
        )
        events = self._approval_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["review_binding"], expected)
        self.assertEqual(events[0]["github_review_bridge"], bridge_evidence)

    def test_approve_bridge_failure_preserves_review_state(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_PR": "4269",
                    "REVIEW_HEAD_SHA": "a" * 40,
                },
                clear=False,
            ),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                side_effect=SystemExit("GitHub still reports REVIEW_REQUIRED"),
            ),
            self.assertRaisesRegex(SystemExit, "REVIEW_REQUIRED"),
        ):
            ai_status.command_approve(
                self.state,
                ["REG-002", "Internal approval alone is insufficient."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("review_binding", task)
        self.assertNotIn("github_review_bridge", task)
        self.assertEqual(self._approval_events(), [])

    def test_approve_discovers_pr_binding_live_when_env_vars_absent(self) -> None:
        """SUP-APPROVAL-BINDING-LIVE-DISCOVERY-20260808: source_ref/github task
        metadata is never populated by the normal assign/handoff flow, so the
        exact-head safety gate must not depend on it. When REVIEW_PR/
        REVIEW_HEAD_SHA are absent, approve must ask GitHub directly whether an
        open PR exists at this task's branch instead of silently no-op'ing."""

        discovered = {
            "pr": 4218,
            "head_sha": "c" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        bridge_evidence = {
            "repository": "ajoe734/pantheon",
            "pr": 4218,
            "head_sha": "c" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
            "decision": "approve",
            "actor": "Claude",
            "mode": "required_commit_status",
            "status_id": 202,
            "status_context": ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT,
            "status_state": "success",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(ai_status, "repository_slug", return_value="ajoe734/pantheon"),
            mock.patch.object(
                ai_status, "discover_open_pr_binding", return_value=dict(discovered)
            ) as discover,
            mock.patch.object(
                ai_status, "bridge_github_review_decision", return_value=bridge_evidence
            ) as github_bridge,
        ):
            ai_status.command_approve(
                self.state,
                ["REG-002", "Approved via live-discovered head."],
            )

        discover.assert_called_once_with(
            "ajoe734/pantheon", head_branch="task/REG-002", base_branch="dev"
        )
        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["review_binding"], discovered)
        github_bridge.assert_called_once_with(
            task,
            actor="Claude",
            decision="approve",
            message="Approved via live-discovered head.",
            binding=discovered,
        )

    def test_approve_falls_back_to_unbound_warning_when_no_pr_is_discovered(self) -> None:
        """No open PR found live (and no metadata claims one either) must still
        behave exactly like the pre-existing internal-only approval path."""

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(ai_status, "repository_slug", return_value="ajoe734/pantheon"),
            mock.patch.object(ai_status, "discover_open_pr_binding", return_value=None),
        ):
            ai_status.command_approve(self.state, ["REG-002", "Approved."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertNotIn("review_binding", task)

    def test_discover_open_pr_binding_accepts_an_open_matching_pr(self) -> None:
        with mock.patch.object(
            ai_status,
            "run_gh_json_command",
            return_value={
                "number": 4218,
                "headRefOid": "D" * 40,
                "baseRefName": "dev",
                "state": "OPEN",
            },
        ) as run_gh:
            result = ai_status.discover_open_pr_binding(
                "ajoe734/pantheon", head_branch="task/REG-002", base_branch="dev"
            )

        self.assertEqual(
            result,
            {"pr": 4218, "head_sha": "d" * 40, "head_branch": "task/REG-002", "base": "dev"},
        )
        run_gh.assert_called_once_with(
            [
                "pr", "view", "task/REG-002",
                "--repo", "ajoe734/pantheon",
                "--json", "number,headRefOid,baseRefName,state",
            ]
        )

    def test_discover_open_pr_binding_ignores_a_closed_or_merged_pr(self) -> None:
        with mock.patch.object(
            ai_status,
            "run_gh_json_command",
            return_value={
                "number": 4218,
                "headRefOid": "d" * 40,
                "baseRefName": "dev",
                "state": "MERGED",
            },
        ):
            result = ai_status.discover_open_pr_binding(
                "ajoe734/pantheon", head_branch="task/REG-002", base_branch="dev"
            )
        self.assertIsNone(result)

    def test_discover_open_pr_binding_returns_none_when_gh_finds_nothing(self) -> None:
        with mock.patch.object(ai_status, "run_gh_json_command", return_value=None):
            result = ai_status.discover_open_pr_binding(
                "ajoe734/pantheon", head_branch="task/REG-002", base_branch="dev"
            )
        self.assertIsNone(result)

    def test_approve_without_a_binding_warns_but_still_approves(self) -> None:
        """Not every task has a PR, so the refusal belongs to the merge gate."""

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            ai_status.command_approve(self.state, ["REG-002", "Approved."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertNotIn("review_binding", task)
        self.assertNotIn("review_binding", self._approval_events()[0])

    def test_approve_refuses_internal_only_verdict_for_pr_backed_task(self) -> None:
        self.state["tasks"][0]["source_ref"] = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "base": "dev",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            self.assertRaisesRegex(SystemExit, "internal activity approval alone"),
        ):
            ai_status.command_approve(
                self.state,
                ["REG-002", "No exact head supplied."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("review_binding", task)
        self.assertEqual(self._approval_events(), [])

    def test_fixture_clears_inherited_review_inputs_for_no_binding_approval(
        self,
    ) -> None:
        inherited = {
            "REVIEW_PR": "4254",
            "REVIEW_HEAD_SHA": "a" * 40,
            "REVIEW_BASE": "inherited-base",
            "REVIEW_HEAD_BRANCH": "task/inherited-head",
        }
        nested_fixture = unittest.TestCase()

        with mock.patch.dict(os.environ, inherited, clear=False):
            _setup_test_isolation(nested_fixture)
            try:
                self.assertTrue(
                    all(key not in os.environ for key in REVIEW_BINDING_ENV_KEYS)
                )
                state = deepcopy(self.state)
                with mock.patch.dict(
                    os.environ,
                    {"AI_NAME": "Claude"},
                    clear=False,
                ):
                    ai_status.command_approve(
                        state,
                        ["REG-002", "Approved without a PR binding."],
                    )

                task = ai_status.get_task(state, "REG-002")
                self.assertEqual(task["status"], "review_approved")
                self.assertNotIn("review_binding", task)
                events = [
                    json.loads(line)
                    for line in nested_fixture._test_log_file.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
                self.assertNotIn("review_binding", events[-1])
            finally:
                _teardown_test_isolation(nested_fixture)

            self.assertEqual(
                {key: os.environ.get(key) for key in REVIEW_BINDING_ENV_KEYS},
                inherited,
            )

    def test_approve_refuses_an_abbreviated_head_sha(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Claude",
                "REVIEW_PR": "4218",
                "REVIEW_HEAD_SHA": "190bf7fe8",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "40-hex"):
                ai_status.command_approve(
                    self.state,
                    ["REG-002", "Approved."],
                )

        self.assertEqual(ai_status.get_task(self.state, "REG-002")["status"], "review")

    def test_approve_refuses_a_head_sha_without_a_pr_number(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AI_NAME": "Claude", "REVIEW_HEAD_SHA": "b" * 40},
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "REVIEW_PR"):
                ai_status.command_approve(
                    self.state,
                    ["REG-002", "Approved."],
                )

        self.assertEqual(ai_status.get_task(self.state, "REG-002")["status"], "review")

    def test_approve_protected_task_verifies_before_review_approved(self) -> None:
        self.state["tasks"][0]["requires_human_ops_signoff"] = True
        verdict_ref = {
            "verdict_id": "pclose-001",
            "ledger_entry_id": "pclose-issue-001",
            "verifier_capability_sha256": "a" * 64,
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_FILE": "docs/evidence/closeout.json",
                },
                clear=False,
            ),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                return_value=verdict_ref,
            ) as protected,
        ):
            ai_status.command_approve(
                self.state,
                ["REG-002", "Protected review passed."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["review_file"], "docs/evidence/closeout.json")
        self.assertEqual(task["protected_closeout_verdict"], verdict_ref)
        candidate = protected.call_args.args[0]
        self.assertEqual(candidate["status"], "review")
        self.assertEqual(candidate["review_file"], "docs/evidence/closeout.json")
        self.assertEqual(
            protected.call_args.kwargs,
            {"transition": "review_approved"},
        )

    def test_approve_protected_task_failure_does_not_change_state(self) -> None:
        self.state["tasks"][0]["requires_human_ops_signoff"] = True
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                side_effect=SystemExit("protected verdict missing"),
            ),
            self.assertRaisesRegex(SystemExit, "verdict missing"),
        ):
            ai_status.command_approve(
                self.state,
                ["REG-002", "Attempted protected approval."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("protected_closeout_verdict", task)

    def test_approve_protected_task_end_to_end_across_split_roots(self) -> None:
        """Governed reviewer approval with a real Human/Ops verdict.

        The reviewer command runs from the immutable command root while the
        reviewed manifest exists only in the supervisor-bound task worktree, so
        nothing in the protected path may assume the central status root holds
        the artifact.
        """

        import loop_done_guardrail as guardrail

        module = guardrail._load_product_closeout_module()
        temp = tempfile.TemporaryDirectory(prefix="split-root-approve-")
        self.addCleanup(temp.cleanup)
        temp_root = Path(temp.name)
        status_root = temp_root / "status-root"
        status_root.mkdir()
        worktree_root = temp_root / "task-worktree"
        manifest_path = worktree_root / "docs" / "evidence" / "closeout.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "deployment": {
                        "environment": "pantheon-lupin-dev",
                        "identity_admission": {
                            "target_environment": "pantheon-lupin-dev",
                            "frontend_sha": "c" * 40,
                            "bff_sha": "d" * 40,
                        },
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        protected_root = temp_root / "protected-runtime"
        protected_root.mkdir()
        private_key = module.generate_private_key()
        key_id = "human-ops-key-split-root"
        policy = module.VerdictPolicy(
            policy_version="product-closeout-v1",
            public_keys={key_id: module.public_key_bytes(private_key)},
        )
        ledger = module.ProtectedVerdictLedger(protected_root / "verdict-ledger.jsonl")
        policy_path = protected_root / "verdict-policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "policy_version": policy.policy_version,
                    "ledger_path": str(ledger.path),
                    "public_keys": {
                        key_id: module.public_key_to_base64(policy.public_keys[key_id])
                    },
                    "max_verdict_age_seconds": 3600,
                    "max_ttl_seconds": 3600,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        catalog = json.loads(
            guardrail._PRODUCT_CLOSEOUT_CATALOG.read_text(encoding="utf-8")
        )
        verdict = module.ProductCloseoutVerdictService(
            ledger=ledger,
            policy=policy,
            private_key=private_key,
            key_id=key_id,
        ).issue(
            module.CloseoutBinding(
                program_id=catalog["program_id"],
                catalog_sha256=module.canonical_json_sha256(catalog),
                task_id="L12-CLOSE-001",
                closeout_manifest_sha256=guardrail._sha256_file(manifest_path),
                target_environment="pantheon-lupin-dev",
                frontend_sha="c" * 40,
                bff_sha="d" * 40,
            ),
            module.HumanOpsIdentity(
                actor_id="ops-human-001",
                actor_role="ops",
                authenticated=True,
                mfa_verified=True,
            ),
            decision="approved",
            ttl_seconds=900,
        )

        task = self.state["tasks"][0]
        task["id"] = "L12-CLOSE-001"
        task["requires_human_ops_signoff"] = True
        self.state["handoffs"][0]["task_id"] = "L12-CLOSE-001"

        with (
            mock.patch.object(module, "DEFAULT_PROTECTED_POLICY_PATH", policy_path),
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_FILE": "docs/evidence/closeout.json",
                    "PANTHEON_STATUS_ROOT": str(status_root),
                    "PANTHEON_WORKTREE_ROOT": str(worktree_root),
                    "ORCH_WORKSPACE_PATH": str(worktree_root),
                    # The bound worktree under test is the fixture one, so the
                    # ambient dispatch lease must not also be consulted.
                    "ORCH_RUN_ID": "",
                    "PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID": verdict["verdict_id"],
                },
                clear=False,
            ),
        ):
            ai_status.command_approve(
                self.state,
                ["L12-CLOSE-001", "Independent protected review passed."],
            )

        approved = ai_status.get_task(self.state, "L12-CLOSE-001")
        self.assertEqual(approved["status"], "review_approved")
        self.assertEqual(approved["review_file"], "docs/evidence/closeout.json")
        self.assertEqual(
            approved["protected_closeout_verdict"]["verdict_id"],
            verdict["verdict_id"],
        )

    def test_approve_protected_task_rejects_manifest_outside_trusted_roots(
        self,
    ) -> None:
        """A manifest in neither the status root nor the bound worktree fails."""

        temp = tempfile.TemporaryDirectory(prefix="split-root-approve-reject-")
        self.addCleanup(temp.cleanup)
        temp_root = Path(temp.name)
        status_root = temp_root / "status-root"
        status_root.mkdir()
        worktree_root = temp_root / "task-worktree"
        worktree_root.mkdir()
        untrusted = temp_root / "untrusted"
        (untrusted / "docs" / "evidence").mkdir(parents=True)
        (untrusted / "docs" / "evidence" / "closeout.json").write_text(
            "{}", encoding="utf-8"
        )

        task = self.state["tasks"][0]
        task["id"] = "L12-CLOSE-001"
        task["requires_human_ops_signoff"] = True
        self.state["handoffs"][0]["task_id"] = "L12-CLOSE-001"

        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_FILE": "docs/evidence/closeout.json",
                    "PANTHEON_STATUS_ROOT": str(status_root),
                    "PANTHEON_WORKTREE_ROOT": str(worktree_root),
                    "ORCH_WORKSPACE_PATH": str(worktree_root),
                    "ORCH_RUN_ID": "",
                    "PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID": "pclose-absent",
                },
                clear=False,
            ),
            self.assertRaisesRegex(SystemExit, "missing or not regular"),
        ):
            ai_status.command_approve(
                self.state,
                ["L12-CLOSE-001", "Manifest outside the trusted roots."],
            )

        blocked = ai_status.get_task(self.state, "L12-CLOSE-001")
        self.assertEqual(blocked["status"], "review")
        self.assertNotIn("protected_closeout_verdict", blocked)

    def test_restore_approved_revalidates_protected_verdict(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task["review_notes_zh"] = ["Prior independent approval."]
        task["requires_human_ops_signoff"] = True
        verdict_ref = {
            "verdict_id": "pclose-restore-001",
            "ledger_entry_id": "pclose-issue-restore-001",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                return_value=verdict_ref,
            ) as protected,
        ):
            ai_status.command_restore_approved(
                self.state,
                ["REG-002", "Restore independently approved state."],
            )

        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["protected_closeout_verdict"], verdict_ref)
        protected.assert_called_once_with(
            task,
            transition="review_approved",
        )

    def test_restore_approved_protected_failure_keeps_in_progress(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task["review_notes_zh"] = ["Prior independent approval."]
        task["requires_human_ops_signoff"] = True
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                side_effect=SystemExit("protected verdict expired"),
            ),
            self.assertRaisesRegex(SystemExit, "verdict expired"),
        ):
            ai_status.command_restore_approved(
                self.state,
                ["REG-002", "Attempt stale restore."],
            )

        self.assertEqual(task["status"], "in_progress")

    def test_progress_promotes_todo_to_in_progress(self) -> None:
        self.state["tasks"][0]["status"] = "todo"

        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_progress(self.state, ["REG-002", "Implementation started"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["next"], "Implementation started")

    def test_progress_preserves_review_approved_for_owner_finalize(self) -> None:
        self.state["tasks"][0]["status"] = "review_approved"

        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_progress(self.state, ["REG-002", "Checking status before finalization"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["next"], "Checking status before finalization")

    def test_done_requires_owner_and_review_approved(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_done(self.state, ["REG-002", "Attempted direct completion"])

        self.state["tasks"][0]["status"] = "review_approved"

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_done(self.state, ["REG-002", "Reviewer cannot finalize"])

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value={}),
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_done(self.state, ["REG-002", "Owner finalized approved task"])

        terminal = ai_status.get_task(self.state, "REG-002")
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal["status"], "done")
        self.assertTrue(self.state["handoffs"])
        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["terminal_outcome"], "completed")

    def test_done_records_missing_review_file_from_owner_closeout_env(self) -> None:
        self.state["tasks"][0]["status"] = "review_approved"
        self.state["tasks"][0].pop("review_file", None)
        review_file = ".orchestrator/reviews/REG-002-review-claude.md"

        with (
            mock.patch.dict(
                os.environ,
                {"AI_NAME": "Codex", "REVIEW_FILE": review_file},
                clear=False,
            ),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value={}),
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_done(
                self.state,
                ["REG-002", "Owner finalized with the reviewed evidence manifest"],
            )

        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["review_file"], review_file)

    def test_done_refuses_review_file_added_after_approval(self) -> None:
        """SUP-REVIEW-EVIDENCE-BINDING-ENFORCEMENT-20260804: an owner cannot bind
        a manifest that only exists in a commit pushed after the reviewer's
        approved head -- that is precisely the SHA-shifting closeout loop
        diagnosed in SUP-REVIEW-PIPELINE-INTEGRITY-20260804."""

        task = self.state["tasks"][0]
        task["status"] = "review_approved"
        task.pop("review_file", None)
        task["review_binding"] = {
            "pr": 4218,
            "head_sha": "b" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        review_file = ".orchestrator/task-briefs/reg_002_review.md"

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex", "REVIEW_FILE": review_file}, clear=False),
            mock.patch.object(ai_status, "review_evidence_file_committed", return_value=False) as check,
            self.assertRaisesRegex(SystemExit, "was not present at"),
        ):
            ai_status.command_done(self.state, ["REG-002", "Owner adds evidence post-approval"])

        check.assert_called_once_with(
            repository=mock.ANY,
            head_sha="b" * 40,
            review_file=review_file,
        )
        # The task must not have silently accepted the unverified manifest.
        self.assertEqual(ai_status.get_task(self.state, "REG-002")["status"], "review_approved")
        self.assertNotIn("review_file", ai_status.get_task(self.state, "REG-002"))

    def test_done_accepts_review_file_present_at_the_approved_head(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "review_approved"
        task.pop("review_file", None)
        task["review_binding"] = {
            "pr": 4218,
            "head_sha": "b" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        review_file = ".orchestrator/task-briefs/reg_002_review.md"

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex", "REVIEW_FILE": review_file}, clear=False),
            mock.patch.object(ai_status, "review_evidence_file_committed", return_value=True),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value={}),
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_done(self.state, ["REG-002", "Owner binds the already-reviewed manifest"])

        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["review_file"], review_file)

    def test_approve_refuses_a_review_file_not_present_at_the_reviewed_head(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_PR": "4218",
                    "REVIEW_HEAD_SHA": "b" * 40,
                    "REVIEW_FILE": ".orchestrator/task-briefs/reg_002_review.md",
                },
                clear=False,
            ),
            mock.patch.object(ai_status, "review_evidence_file_committed", return_value=False),
            self.assertRaisesRegex(SystemExit, "was not found at the reviewed"),
        ):
            ai_status.command_approve(self.state, ["REG-002", "Claiming evidence that is not really there"])

        self.assertEqual(ai_status.get_task(self.state, "REG-002")["status"], "review")

    def test_approve_accepts_a_review_file_present_at_the_reviewed_head(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "REVIEW_PR": "4218",
                    "REVIEW_HEAD_SHA": "b" * 40,
                    "REVIEW_FILE": ".orchestrator/task-briefs/reg_002_review.md",
                },
                clear=False,
            ),
            mock.patch.object(ai_status, "review_evidence_file_committed", return_value=True),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                return_value={},
            ),
        ):
            ai_status.command_approve(self.state, ["REG-002", "Evidence verified present at the head"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["review_file"], ".orchestrator/task-briefs/reg_002_review.md")

    def test_done_consumes_protected_verdict_before_terminal_mutation(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "review_approved"
        task["requires_human_ops_signoff"] = True
        consumed_ref = {
            "verdict_id": "pclose-001",
            "ledger_entry_id": "pclose-issue-001",
            "verifier_capability_sha256": "a" * 64,
            "consumption_record_id": "pclose-consume-001",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "validate_loop_completion_claim"),
            mock.patch.object(
                ai_status,
                "collect_done_delivery_metadata",
                return_value={},
            ),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                return_value=consumed_ref,
            ) as protected,
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_done(
                self.state,
                ["REG-002", "Protected owner closeout complete."],
            )

        terminal = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(terminal["status"], "done")
        self.assertEqual(terminal["protected_closeout_verdict"], consumed_ref)
        self.assertEqual(
            protected.call_args.kwargs,
            {
                "transition": "done",
                "consume": True,
                "transition_actor": "Codex",
            },
        )

    def test_done_protected_verdict_failure_remains_review_approved(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "review_approved"
        task["requires_human_ops_signoff"] = True
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "validate_loop_completion_claim"),
            mock.patch.object(
                ai_status,
                "collect_done_delivery_metadata",
                return_value={},
            ),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                side_effect=SystemExit("protected verdict replay"),
            ),
            self.assertRaisesRegex(SystemExit, "verdict replay"),
        ):
            ai_status.command_done(
                self.state,
                ["REG-002", "Attempted replayed closeout."],
            )

        self.assertEqual(task["status"], "review_approved")
        self.assertNotIn("terminal_outcome", task)
        self.assertNotIn(ai_status.STATUS_ARCHIVE_OUTBOX_KEY, self.state)

    def test_reconcile_merged_done_requires_human_ops_or_reviewer(self) -> None:
        """The owner (Codex) is neither Human/Ops nor the current reviewer
        (Claude) and must still be rejected -- self-service is for the
        independent reviewer who already verified the evidence, not for the
        owner whose own delivery is being reconciled."""

        self.state["tasks"][0]["status"] = "blocked"
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "Only Human/Ops or the task's current reviewer"):
                ai_status.command_reconcile_merged_done(
                    self.state,
                    ["REG-002", "Merged delivery reconciled"],
                )

    def test_reconcile_merged_done_allows_current_reviewer_self_service(self) -> None:
        """Once validate_merged_done_evidence's automated chain verification
        exists (added alongside this change), the reviewer who already
        independently verified the evidence should not have to wait on a
        human to press the same button -- that was the whole point of
        automating the verification in the first place."""

        self.state["tasks"][0]["status"] = "blocked"
        delivery = {
            "reconciled_from_merged_evidence": True,
            "commit": "a" * 40,
        }
        consumed_ref = {
            "verdict_id": "pclose-reconcile-reviewer-001",
            "consumption_record_id": "pclose-consume-reconcile-reviewer-001",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(ai_status, "validate_merged_done_evidence", return_value=delivery),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                return_value=consumed_ref,
            ),
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_reconcile_merged_done(
                self.state,
                ["REG-002", "Reviewer self-service reconcile"],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["terminal_outcome"], "completed")

    def test_reconcile_merged_done_rejects_stale_reviewer_after_drift(self) -> None:
        """If the task's reviewer has itself since drifted away, the old
        reviewer identity must not retain self-service access -- only the
        *current* reviewer field is trusted, same as Human/Ops always was."""

        self.state["tasks"][0]["status"] = "blocked"
        self.state["tasks"][0]["reviewer"] = "Gemini"
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "Only Human/Ops or the task's current reviewer"):
                ai_status.command_reconcile_merged_done(
                    self.state,
                    ["REG-002", "Merged delivery reconciled"],
                )

    def test_reconcile_merged_done_archives_verified_delivery(self) -> None:
        self.state["tasks"][0]["status"] = "blocked"
        self.state["tasks"][0]["waiting_for"] = "Claude"
        self.state["tasks"][0]["requires_human_ops_signoff"] = True
        self.state["blockers"] = [
            {
                "task_id": "REG-002",
                "owner": "Codex",
                "waiting_for": "Claude",
                "message": "stale blocker",
                "status": "open",
                "created_at": "2026-04-06T15:00:00Z",
            }
        ]
        delivery = {
            "reconciled_from_merged_evidence": True,
            "commit": "a" * 40,
        }
        consumed_ref = {
            "verdict_id": "pclose-reconcile-001",
            "consumption_record_id": "pclose-consume-reconcile-001",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False),
            mock.patch.object(ai_status, "validate_merged_done_evidence", return_value=delivery),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                return_value=consumed_ref,
            ) as protected,
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_reconcile_merged_done(
                self.state,
                ["REG-002", "Merged delivery reconciled"],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["terminal_outcome"], "completed")
        self.assertNotIn("waiting_for", task)
        self.assertEqual(task["delivery"]["commit"], "a" * 40)
        self.assertEqual(task["protected_closeout_verdict"], consumed_ref)
        self.assertEqual(self.state["blockers"][0]["status"], "resolved")
        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(
            protected.call_args.kwargs,
            {
                "transition": "done",
                "consume": True,
                "transition_actor": "Human/Ops",
            },
        )

    def test_reconcile_merged_done_protected_failure_is_non_mutating(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "review_approved"
        task["requires_human_ops_signoff"] = True
        delivery = {
            "reconciled_from_merged_evidence": True,
            "commit": "a" * 40,
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False),
            mock.patch.object(
                ai_status,
                "validate_merged_done_evidence",
                return_value=delivery,
            ),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                side_effect=SystemExit("protected verdict missing"),
            ),
            self.assertRaisesRegex(SystemExit, "verdict missing"),
        ):
            ai_status.command_reconcile_merged_done(
                self.state,
                ["REG-002", "Attempt protected recovery."],
            )

        self.assertEqual(task["status"], "review_approved")
        self.assertNotIn("terminal_outcome", task)

    def _init_repo(self, root: Path, *, remote: str, files: dict[str, str]) -> str:
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "dev"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=root, check=True)
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "test merged evidence"], cwd=root, check=True, capture_output=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/dev", sha],
            cwd=root,
            check=True,
        )
        return sha

    def test_validates_review_identity_and_both_dev_merges(self) -> None:
        with tempfile.TemporaryDirectory(prefix="merged-done-evidence-") as temp_dir:
            base = Path(temp_dir)
            delivery_root = base / "execute-plans"
            delivery_sha = self._init_repo(
                delivery_root,
                remote="https://github.com/ajoe734/execute-plans.git",
                files={"src/delivery.ts": "export const delivered = true;\n"},
            )
            evidence_root = base / "pantheon"
            evidence_file = ".orchestrator/task-briefs/reg_002.md"
            evidence_text = (
                "# Task Brief: REG-002\n\n"
                "- Status: review_approved\n"
                "- Owner: Codex\n"
                "- Reviewer: Claude\n\n"
                "Delivery repository: ajoe734/execute-plans\n"
                f"Delivery commit: {delivery_sha}\n"
            )
            evidence_sha = self._init_repo(
                evidence_root,
                remote="https://github.com/ajoe734/pantheon.git",
                files={evidence_file: evidence_text},
            )
            task = {
                "id": "REG-002",
                "owner": "Codex",
                "reviewer": "Claude",
                "artifacts": ["execute-plans:src/delivery.ts"],
            }
            config = {
                "coordination": {
                    "enabled": True,
                    "repositories": {
                        "execute_plans": {
                            "repo": "ajoe734/execute-plans",
                            "local_path": str(delivery_root),
                        }
                    },
                }
            }
            env = {
                "RECONCILE_EVIDENCE_FILE": evidence_file,
                "RECONCILE_EVIDENCE_COMMIT": evidence_sha,
                "RECONCILE_DELIVERY_REPOSITORY": "ajoe734/execute-plans",
                "RECONCILE_DELIVERY_ROOT": str(delivery_root),
                "RECONCILE_DELIVERY_COMMIT": delivery_sha,
            }
            with (
                mock.patch.object(ai_status, "ROOT", evidence_root),
                mock.patch.object(ai_status, "load_config", return_value=config),
                mock.patch.dict(os.environ, env, clear=False),
            ):
                result = ai_status.validate_merged_done_evidence(task)

            self.assertTrue(result["reconciled_from_merged_evidence"])
            self.assertEqual(result["commit"], delivery_sha)
            self.assertEqual(result["review_evidence"]["commit"], evidence_sha)
            self.assertEqual(result["review_evidence"]["reviewer"], "Claude")

    def test_rejects_review_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="merged-done-reviewer-") as temp_dir:
            base = Path(temp_dir)
            delivery_root = base / "execute-plans"
            delivery_sha = self._init_repo(
                delivery_root,
                remote="https://github.com/ajoe734/execute-plans.git",
                files={"src/delivery.ts": "export const delivered = true;\n"},
            )
            evidence_root = base / "pantheon"
            evidence_file = ".orchestrator/task-briefs/reg_002.md"
            evidence_sha = self._init_repo(
                evidence_root,
                remote="https://github.com/ajoe734/pantheon.git",
                files={
                    evidence_file: (
                        "# Task Brief: REG-002\n\n"
                        "- Status: review_approved\n"
                        "- Owner: Codex\n"
                        "- Reviewer: Gemini\n\n"
                        "Delivery repository: ajoe734/execute-plans\n"
                        f"Delivery commit: {delivery_sha}\n"
                    )
                },
            )
            env = {
                "RECONCILE_EVIDENCE_FILE": evidence_file,
                "RECONCILE_EVIDENCE_COMMIT": evidence_sha,
                "RECONCILE_DELIVERY_REPOSITORY": "ajoe734/execute-plans",
                "RECONCILE_DELIVERY_ROOT": str(delivery_root),
                "RECONCILE_DELIVERY_COMMIT": delivery_sha,
            }
            with (
                mock.patch.object(ai_status, "ROOT", evidence_root),
                mock.patch.dict(os.environ, env, clear=False),
            ):
                with self.assertRaisesRegex(SystemExit, "reviewer metadata"):
                    ai_status.validate_merged_done_evidence(
                        {
                            "id": "REG-002",
                            "owner": "Codex",
                            "reviewer": "Claude",
                            "artifacts": ["execute-plans:src/delivery.ts"],
                        }
                    )

    def test_validate_merged_done_evidence_accepts_owner_reassignment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="merged-done-owner-") as temp_dir:
            base = Path(temp_dir)
            delivery_root = base / "execute-plans"
            delivery_sha = self._init_repo(
                delivery_root,
                remote="https://github.com/ajoe734/execute-plans.git",
                files={"src/delivery.ts": "export const delivered = true;\n"},
            )
            evidence_root = base / "pantheon"
            evidence_file = ".orchestrator/task-briefs/reg_002.md"
            evidence_text = (
                "# Task Brief: REG-002\n\n"
                "- Status: review_approved\n"
                "- Owner: Codex2\n"
                "- Reviewer: Claude\n\n"
                "Delivery repository: ajoe734/execute-plans\n"
                f"Delivery commit: {delivery_sha}\n"
            )
            evidence_sha = self._init_repo(
                evidence_root,
                remote="https://github.com/ajoe734/pantheon.git",
                files={evidence_file: evidence_text},
            )
            task = {
                "id": "REG-002",
                "owner": "Antigravity",
                "reviewer": "Claude",
                "artifacts": ["execute-plans:src/delivery.ts"],
            }
            config = {
                "coordination": {
                    "enabled": True,
                    "repositories": {
                        "execute_plans": {
                            "repo": "ajoe734/execute-plans",
                            "local_path": str(delivery_root),
                        }
                    },
                }
            }
            env = {
                "RECONCILE_EVIDENCE_FILE": evidence_file,
                "RECONCILE_EVIDENCE_COMMIT": evidence_sha,
                "RECONCILE_DELIVERY_REPOSITORY": "ajoe734/execute-plans",
                "RECONCILE_DELIVERY_ROOT": str(delivery_root),
                "RECONCILE_DELIVERY_COMMIT": delivery_sha,
            }
            reassign_event = audited_reassignment_event(
                timestamp="2026-07-20T00:00:00Z",
                message="owner reassignment",
            )
            log_file = evidence_root / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(reassign_event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "ROOT", evidence_root),
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                mock.patch.object(ai_status, "load_config", return_value=config),
                mock.patch.dict(os.environ, env, clear=False),
            ):
                result = ai_status.validate_merged_done_evidence(task)

            self.assertTrue(result["reconciled_from_merged_evidence"])
            self.assertEqual(result["review_evidence"]["owner"], "Codex2")
            self.assertEqual(result["review_evidence"]["canonical_owner"], "Antigravity")
            self.assertIn("owner_reassignment", result["review_evidence"])

    def test_accepts_exact_audited_reviewer_reassignment(self) -> None:
        message = (
            "Auto-reassigned REG-002 away from unavailable lane Claude; "
            "reviewer Claude -> Codex2."
        )
        event = audited_reassignment_event(
            old_owner="Codex",
            new_owner="Codex",
            new_reviewer="Codex2",
            message=message,
        )
        self._test_log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
        result = ai_status._verified_reviewer_reassignment(
            {
                "id": "REG-002",
                "owner": "Codex",
                "reviewer": "Codex2",
                "last_update": event["ts"],
                "next": message,
            },
            evidence_reviewer="Claude",
            current_reviewer="Codex2",
        )

        self.assertEqual(result["event_id"], event["event_id"])
        self.assertEqual(result["old_reviewer"], "Claude")
        self.assertEqual(result["new_reviewer"], "Codex2")

    def test_owner_reassignment_verifies_exact_task_reassigned_audit_event(self) -> None:
        message = "canonical owner reassignment"
        event = audited_reassignment_event(message=message)
        self._test_log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
        result = ai_status._verified_owner_reassignment(
            {
                "id": "REG-002",
                "owner": "Antigravity",
                "reviewer": "Claude",
                "last_update": event["ts"],
                "next": message,
            },
            evidence_owner="Codex2",
            current_owner="Antigravity",
        )

        self.assertEqual(result["event_id"], event["event_id"])
        self.assertEqual(result["old_owner"], "Codex2")
        self.assertEqual(result["new_owner"], "Antigravity")

    def test_owner_reassignment_requires_exact_task_readback(self) -> None:
        event = audited_reassignment_event()
        self._test_log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "no exact task_reassigned audit event chain"):
            ai_status._verified_owner_reassignment(
                {
                    "id": "REG-002",
                    "owner": "Antigravity",
                    "reviewer": "Claude",
                    "last_update": event["ts"],
                    "next": "different readback",
                },
                evidence_owner="NonExistentOwner",
                current_owner="Antigravity",
            )

    def test_reviewer_reassignment_requires_exact_task_readback(self) -> None:
        event = {
            "event_id": "supervisor-reassign-test",
            "agent": "Orchestrator",
            "ts": "2026-07-19T23:52:06Z",
            "type": "task_reassigned",
            "task_id": "REG-002",
            "old_owner": "Codex",
            "new_owner": "Codex",
            "old_reviewer": "Claude",
            "new_reviewer": "Codex2",
            "message": "canonical reassignment",
        }
        self._test_log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "no exact task_reassigned audit event chain"):
            ai_status._verified_reviewer_reassignment(
                {
                    "id": "REG-002",
                    "owner": "Codex",
                    "reviewer": "Codex2",
                    "last_update": event["ts"],
                    "next": "different readback",
                },
                evidence_reviewer="NonExistentReviewer",
                current_reviewer="Codex2",
            )

    def test_reviewer_reassignment_found_after_rotation_into_archive(self) -> None:
        """Regression: the audited reassignment event can be rotated out of the
        live tail into an immutable archive before the chain walk runs.
        _verified_reviewer_reassignment must still find it there -- rotation
        only moved the row, it did not invalidate it."""
        event = audited_reassignment_event(
            task_id="REG-002",
            old_owner="Codex",
            new_owner="Codex",
            old_reviewer="Claude",
            new_reviewer="Codex2",
            message=(
                "Auto-reassigned REG-002 away from unavailable lane Claude; "
                "reviewer Claude -> Codex2."
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1),
                mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 0),
            ):
                archive = ai_status.maybe_rotate_activity_log()
                self.assertIsNotNone(
                    archive, "expected the event to be rotated into an archive"
                )
                self.assertNotIn(event["event_id"].encode(), log_file.read_bytes())

                result = ai_status._verified_reviewer_reassignment(
                    {
                        "id": "REG-002",
                        "owner": "Codex",
                        "reviewer": "Codex2",
                        "last_update": event["ts"],
                        "next": event["message"],
                    },
                    evidence_reviewer="Claude",
                    current_reviewer="Codex2",
                )
        self.assertEqual(result["event_id"], event["event_id"])
        self.assertEqual(result["old_reviewer"], "Claude")
        self.assertEqual(result["new_reviewer"], "Codex2")

    def test_multihop_owner_reassignment_chain(self) -> None:
        events = [
            audited_reassignment_event(
                task_id="MULTIHOP-001",
                old_owner="Codex2",
                new_owner="Codex",
                timestamp="2026-07-19T23:00:00Z",
                message="hop 1",
            ),
            audited_reassignment_event(
                task_id="MULTIHOP-001",
                old_owner="Codex",
                new_owner="Antigravity",
                timestamp="2026-07-19T23:10:00Z",
                message="hop 2",
            ),
        ]
        log_content = "\n".join(json.dumps(e) for e in events) + "\n"
        self._test_log_file.write_text(log_content, encoding="utf-8")
        result = ai_status._verified_owner_reassignment(
            {
                "id": "MULTIHOP-001",
                "owner": "Antigravity",
                "reviewer": "Claude",
                "last_update": "2026-07-19T23:20:00Z",
                "next": "post-reassignment progress",
            },
            evidence_owner="Codex2",
            current_owner="Antigravity",
        )
        self.assertEqual(result["hops"], 2)
        self.assertEqual(result["old_owner"], "Codex2")
        self.assertEqual(result["new_owner"], "Antigravity")
        self.assertEqual(result["event_id"], events[1]["event_id"])

    def test_broken_owner_reassignment_chain_fails(self) -> None:
        events = [
            audited_reassignment_event(
                task_id="BROKEN-001",
                old_owner="Codex2",
                new_owner="Gemini",
                timestamp="2026-07-19T23:00:00Z",
                message="hop 1",
            ),
            audited_reassignment_event(
                task_id="BROKEN-001",
                # Disconnected: the chain left off at Gemini, not Codex.
                old_owner="Codex",
                new_owner="Antigravity",
                timestamp="2026-07-19T23:10:00Z",
                message="hop 2",
            ),
        ]
        log_content = "\n".join(json.dumps(e) for e in events) + "\n"
        self._test_log_file.write_text(log_content, encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "no exact task_reassigned audit event chain"):
            ai_status._verified_owner_reassignment(
                {
                    "id": "BROKEN-001",
                    "owner": "Antigravity",
                    "reviewer": "Claude",
                },
                evidence_owner="Codex2",
                current_owner="Antigravity",
            )

    def test_combined_owner_and_reviewer_swap_chain(self) -> None:
        event = audited_reassignment_event(
            task_id="SWAP-001",
            new_reviewer="Codex",
            timestamp="2026-07-19T23:00:00Z",
            message="combined swap",
        )
        self._test_log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
        owner_res = ai_status._verified_owner_reassignment(
            {"id": "SWAP-001", "owner": "Antigravity", "reviewer": "Codex"},
            evidence_owner="Codex2",
            current_owner="Antigravity",
        )
        reviewer_res = ai_status._verified_reviewer_reassignment(
            {"id": "SWAP-001", "owner": "Antigravity", "reviewer": "Codex"},
            evidence_reviewer="Claude",
            current_reviewer="Codex",
        )
        self.assertEqual(owner_res["event_id"], event["event_id"])
        self.assertEqual(reviewer_res["event_id"], event["event_id"])

    def test_reassignment_chain_rejects_unaudited_event(self) -> None:
        """A `task_reassigned` line the supervisor did not write proves nothing.

        The chain walk is what removes the Human/Ops sign-off, so the events it
        walks have to be unforgeable. Strip the `Orchestrator` actor and the
        digest no longer matches, which must fail closed rather than reconcile.
        """

        forged = audited_reassignment_event(task_id="FORGED-001")
        forged["agent"] = "Claude"
        self._test_log_file.write_text(json.dumps(forged) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "no exact task_reassigned audit event chain"):
            ai_status._verified_owner_reassignment(
                {"id": "FORGED-001", "owner": "Antigravity", "reviewer": "Claude"},
                evidence_owner="Codex2",
                current_owner="Antigravity",
            )

    def test_reassignment_chain_rejects_tampered_event_payload(self) -> None:
        """Editing a real audited event breaks its digest and must be rejected."""

        tampered = audited_reassignment_event(task_id="TAMPER-001")
        tampered["new_owner"] = "Gemini"
        self._test_log_file.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "no exact task_reassigned audit event chain"):
            ai_status._verified_owner_reassignment(
                {"id": "TAMPER-001", "owner": "Gemini", "reviewer": "Claude"},
                evidence_owner="Codex2",
                current_owner="Gemini",
            )

    def test_handoff_must_go_from_owner_to_reviewer(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Claude", "Wrong actor"])

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Gemini", "Wrong reviewer"])

        self.state["tasks"][0]["failure_streak"] = 2
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_handoff(self.state, ["REG-002", "Claude", "Ready for review"])

        self.assertEqual(self.state["tasks"][0]["status"], "review")
        self.assertEqual(self.state["tasks"][0]["failure_streak"], 0)

    def test_reviewer_reopen_creates_handoff_back_to_owner(self) -> None:
        self.state["tasks"][0]["status"] = "review"
        self.state["tasks"][0]["failure_streak"] = 2
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            ai_status.command_reopen(self.state, ["REG-002", "Please address the requested changes"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["failure_streak"], 0)
        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")

    def test_reviewer_reopen_bridges_existing_exact_head_rejection(self) -> None:
        binding = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        bridge_evidence = {
            "repository": "ajoe734/pantheon",
            **binding,
            "decision": "reopen",
            "actor": "Claude",
            "mode": "required_commit_status",
            "status_id": 102,
            "status_context": ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT,
            "status_state": "failure",
        }
        self.state["tasks"][0]["status"] = "review_approved"
        self.state["tasks"][0]["review_binding"] = binding
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                return_value=bridge_evidence,
            ) as github_bridge,
        ):
            ai_status.command_reopen(
                self.state,
                ["REG-002", "GitHub gate must remain blocked."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["github_review_bridge"], bridge_evidence)
        github_bridge.assert_called_once_with(
            task,
            actor="Claude",
            decision="reopen",
            message="GitHub gate must remain blocked.",
            binding=binding,
        )

    def test_reviewer_reopen_refuses_unbound_pr_rejection(self) -> None:
        self.state["tasks"][0]["source_ref"] = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "base": "dev",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            self.assertRaisesRegex(SystemExit, "rejection.*GitHub review gate"),
        ):
            ai_status.command_reopen(
                self.state,
                ["REG-002", "Changes are required."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertFalse(
            [
                handoff
                for handoff in self.state["handoffs"]
                if handoff.get("from") == "Claude"
            ]
        )

    def test_human_ops_reopen_clears_blocker_without_impersonating_worker(self) -> None:
        self.state["tasks"][0]["status"] = "blocked"
        self.state["tasks"][0]["waiting_for"] = "Human/Ops"
        self.state["blockers"] = [
            {
                "task_id": "REG-002",
                "owner": "Codex",
                "waiting_for": "Human/Ops",
                "message": "Awaiting operator retry",
                "status": "open",
                "created_at": "2026-04-06T15:00:00Z",
            }
        ]

        with mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False):
            ai_status.command_reopen(
                self.state,
                ["REG-002", "Allowlisted GitHub operator requested retry"],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn("waiting_for", task)
        self.assertEqual(self.state["blockers"][0]["status"], "resolved")
        self.assertFalse(
            [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        )

    def test_blocker_without_check_kind_remains_a_legacy_human_gate(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_blocker(
                self.state,
                ["REG-002", "Need an operator judgement", "Claude"],
            )

        blocker = self.state["blockers"][-1]
        self.assertNotIn("check_kind", blocker)
        self.assertEqual(blocker["task_id"], "REG-002")

    def test_blocker_records_structured_pr_ci_and_dependency_checks(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_blocker(
                self.state,
                ["REG-002", "Awaiting PR CI", "Claude", "github_pr_ci", "4582"],
            )
            ai_status.command_blocker(
                self.state,
                [
                    "REG-002",
                    "Awaiting schema task",
                    "Claude",
                    "task_dependency",
                    "SUP-TASK-FAILURE-STREAK-SCHEMA-20260804",
                    "done",
                ],
            )

        pr_blocker, dependency_blocker = self.state["blockers"][-2:]
        self.assertEqual(
            {"check_kind": pr_blocker["check_kind"], "pr_number": pr_blocker["pr_number"]},
            {"check_kind": "github_pr_ci", "pr_number": 4582},
        )
        self.assertEqual(dependency_blocker["task_id"], "REG-002")
        self.assertEqual(
            dependency_blocker["check_params"],
            {
                "task_id": "SUP-TASK-FAILURE-STREAK-SCHEMA-20260804",
                "required_status": "done",
            },
        )

    def test_normalize_handoffs_adds_finalize_handoff_for_approved_task(self) -> None:
        self.state["tasks"][0]["status"] = "review_approved"
        self.state["handoffs"] = []

        ai_status.normalize_handoffs(self.state)

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertEqual(pending[0]["from"], "Claude")

    def test_supersede_closes_legacy_blocker_and_resolves_blocker_entries(self) -> None:
        self.state["tasks"][0]["status"] = "blocked"
        self.state["tasks"][0]["waiting_for"] = "Gemini"
        self.state["blockers"] = [
            {
                "task_id": "REG-002",
                "owner": "Codex",
                "waiting_for": "Gemini",
                "message": "Legacy lane replaced by newer execution slice",
                "status": "open",
                "created_at": "2026-04-06T15:05:00Z",
            }
        ]

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
        ):
            ai_status.command_supersede(self.state, ["REG-002", "Superseded by REG-010 after accepted consensus.", "REG-010"])

        terminal = ai_status.get_task(self.state, "REG-002")
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal["status"], "done")
        self.assertTrue(self.state["blockers"])
        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["terminal_outcome"], "superseded")
        self.assertEqual(archive_task["superseded_by"], "REG-010")
        self.assertNotIn("waiting_for", archive_task)


class SupervisorReassignmentEventIdCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def _audited_events(
        self,
        *events: dict[str, str],
        task_id: str = "REG-002",
    ) -> list[tuple[Any, dict[str, Any]]]:
        self._test_log_file.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        return ai_status._audited_reassignment_events(
            task_id,
            source="test reassignment audit",
            unavailable_message="test reassignment audit unavailable",
        )

    @staticmethod
    def _with_prefix(event: dict[str, str], prefix: str) -> dict[str, str]:
        event = dict(event)
        digest = ai_status._supervisor_reassignment_event_id(event).removeprefix(
            "supervisor-reassign-"
        )
        event["event_id"] = f"{prefix}{digest}"
        return event

    def test_accepts_both_canonical_event_id_prefixes(self) -> None:
        for prefix in ("supervisor-reassign-", "supervisor-task-reassigned-"):
            with self.subTest(prefix=prefix):
                event = self._with_prefix(audited_reassignment_event(), prefix)
                audited = self._audited_events(event)

                self.assertEqual(len(audited), 1)
                self.assertEqual(audited[0][1]["event_id"], event["event_id"])

    def test_accepts_existing_closeout_blocker_event(self) -> None:
        event = {
            "agent": "Orchestrator",
            "event_id": (
                "supervisor-task-reassigned-"
                "dac420278948aad12f1f32f6804d6af63f2adace105fbaf8ed2212a26f510778"
            ),
            "message": (
                "Auto-reassigned review from Claude to Antigravity after repeated "
                "Claude terminal: {\"is_error\":true,\"duration_api_ms\":0,"
                "\"num_turns\":1,\"stop_reason\":\"stop_sequence\","
                "\"session_id\":\"c467821a-0723-4d23-a700-488d42a6d679\","
                "\"total_cost_usd\":0,\"usage\":{\"input_tokens\":0,\"c"
            ),
            "new_owner": "Codex",
            "new_reviewer": "Antigravity",
            "old_owner": "Codex",
            "old_reviewer": "Claude",
            "task_id": "OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001",
            "ts": "2026-08-09T05:20:22Z",
            "type": "task_reassigned",
        }
        self.assertEqual(
            ai_status._supervisor_reassignment_event_id(event),
            (
                "supervisor-reassign-"
                "dac420278948aad12f1f32f6804d6af63f2adace105fbaf8ed2212a26f510778"
            ),
        )
        self._audited_events(event, task_id=event["task_id"])

        result = ai_status._verified_done_reviewer_reassignment(
            {
                "id": event["task_id"],
                "owner": "Codex",
                "reviewer": "Antigravity",
            },
            commit_reviewer="Claude",
            current_reviewer="Antigravity",
            commit_timestamp="2026-08-09T05:00:00+00:00",
        )

        self.assertEqual(result["event_id"], event["event_id"])

    def test_rejects_wrong_digest_and_wrong_prefix(self) -> None:
        base_event = audited_reassignment_event()
        digest = ai_status._supervisor_reassignment_event_id(base_event).removeprefix(
            "supervisor-reassign-"
        )
        cases = {
            "wrong digest": "supervisor-task-reassigned-" + "0" * 64,
            "wrong prefix": "supervisor-task-reassign-" + digest,
        }
        for label, event_id in cases.items():
            with self.subTest(case=label):
                event = dict(base_event)
                event["event_id"] = event_id
                self.assertEqual(self._audited_events(event), [])

    def test_rejects_forged_actor(self) -> None:
        event = self._with_prefix(
            audited_reassignment_event(), "supervisor-task-reassigned-"
        )
        event["agent"] = "Codex"

        self.assertEqual(self._audited_events(event), [])

    def test_rejects_missing_required_fields(self) -> None:
        for field in ("event_id", "old_owner", "new_owner", "ts"):
            with self.subTest(field=field):
                event = audited_reassignment_event()
                event.pop(field)
                if field != "event_id":
                    event = self._with_prefix(event, "supervisor-task-reassigned-")
                self.assertEqual(self._audited_events(event), [])

    def test_rejects_cross_task_event(self) -> None:
        event = self._with_prefix(
            audited_reassignment_event(task_id="OTHER-001"),
            "supervisor-task-reassigned-",
        )

        self.assertEqual(self._audited_events(event, task_id="REG-002"), [])

    def test_rejects_ambiguous_chain_with_compatible_prefixes(self) -> None:
        timestamp = "2026-08-09T05:20:22Z"
        first = audited_reassignment_event(
            old_owner="Codex2",
            new_owner="Codex",
            timestamp=timestamp,
            message="first same-time hop",
        )
        second = self._with_prefix(
            audited_reassignment_event(
                old_owner="Codex",
                new_owner="Antigravity",
                timestamp=timestamp,
                message="second same-time hop",
            ),
            "supervisor-task-reassigned-",
        )
        self._audited_events(first, second)

        with self.assertRaisesRegex(SystemExit, "ordering is ambiguous"):
            ai_status._verified_owner_reassignment(
                {"id": "REG-002", "owner": "Antigravity", "reviewer": "Claude"},
                evidence_owner="Codex2",
                current_owner="Antigravity",
            )


class DeliveryMetadataValidationTests(unittest.TestCase):
    @staticmethod
    def _owner_reassignment_event(
        *,
        task_id: str = "REG-002",
        old_owner: str = "Codex2",
        new_owner: str = "Codex",
        reviewer: str = "Antigravity",
        timestamp: str = "2026-07-31T16:22:48Z",
        message: str = "Supervisor reassigned finalization owner.",
    ) -> dict[str, str]:
        digest = hashlib.sha256(
            (
                f"{task_id}\0{timestamp}\0{old_owner}\0{new_owner}\0"
                f"{reviewer}\0{reviewer}\0{message}"
            ).encode("utf-8")
        ).hexdigest()
        return {
            "event_id": f"supervisor-reassign-{digest}",
            "ts": timestamp,
            "agent": "Orchestrator",
            "type": "task_reassigned",
            "task_id": task_id,
            "old_owner": old_owner,
            "new_owner": new_owner,
            "old_reviewer": reviewer,
            "new_reviewer": reviewer,
            "message": message,
        }

    def test_collect_done_accepts_prior_owner_from_latest_audited_reassignment(self) -> None:
        event = self._owner_reassignment_event()
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Antigravity",
            "status": "review_approved",
        }

        def fake_run_git_command(args: list[str], **_kwargs: object) -> str:
            responses = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "task/REG-002",
                ("rev-parse", "HEAD"): "a" * 40,
                ("show", "-s", "--format=%s", "HEAD"): "REG-002: finish delivery",
                ("show", "-s", "--format=%b", "HEAD"): (
                    "LLM-Agent: Codex2\nTask-ID: REG-002\nReviewer: Antigravity\n"
                ),
                ("show", "-s", "--format=%an", "HEAD"): "Codex2",
                ("show", "-s", "--format=%ae", "HEAD"): "codex2@example.com",
                ("show", "-s", "--format=%cI", "HEAD"): "2026-07-31T16:20:00+00:00",
                ("status", "--porcelain"): "",
                ("remote",): "",
            }
            return responses[tuple(args)]

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.dict(
                    os.environ,
                    {"TASK_REQUIRE_MERGED_PR": "false"},
                    clear=False,
                ),
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                mock.patch.object(
                    ai_status,
                    "run_git_command",
                    side_effect=fake_run_git_command,
                ),
            ):
                delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertEqual(delivery["commit_metadata"]["LLM-Agent"], "Codex2")
        self.assertEqual(
            delivery["commit_owner_reassignment"]["event_id"],
            event["event_id"],
        )
        self.assertEqual(delivery["commit_owner_reassignment"]["old_owner"], "Codex2")
        self.assertEqual(delivery["commit_owner_reassignment"]["new_owner"], "Codex")

    def test_prior_owner_reassignment_found_after_rotation_into_archive(self) -> None:
        """Regression: this reproduces the real production incident where
        SUP-TASK-FAILURE-STREAK-SCHEMA-20260804 got permanently stuck at
        `done` -- rotation moved its audited task_reassigned event out of
        LOG_FILE into archive/logs/*.gz before `done` ran, and the old
        LOG_FILE-only read could never find it again.
        _verified_done_owner_reassignment must search archives too."""
        event = self._owner_reassignment_event()
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Antigravity",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1),
                mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 0),
            ):
                archive = ai_status.maybe_rotate_activity_log()
                self.assertIsNotNone(
                    archive, "expected the event to be rotated into an archive"
                )
                # The live tail is down to the lineage-head control record only.
                active_lines = log_file.read_bytes().splitlines()
                self.assertEqual(len(active_lines), 1)
                self.assertNotIn(event["event_id"].encode(), log_file.read_bytes())

                result = ai_status._verified_done_owner_reassignment(
                    task,
                    commit_owner="Codex2",
                    current_owner="Codex",
                    commit_timestamp="2026-07-31T16:20:00+00:00",
                )
        self.assertEqual(result["event_id"], event["event_id"])
        self.assertEqual(result["old_owner"], "Codex2")
        self.assertEqual(result["new_owner"], "Codex")

    def test_prior_owner_reassignment_rejects_forged_audit_identity(self) -> None:
        event = self._owner_reassignment_event()
        event["event_id"] = "supervisor-reassign-forged"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(SystemExit, "exact audited supervisor task_reassigned"),
            ):
                ai_status._verified_done_owner_reassignment(
                    {
                        "id": "REG-002",
                        "owner": "Codex",
                        "reviewer": "Antigravity",
                    },
                    commit_owner="Codex2",
                    current_owner="Codex",
                    commit_timestamp="2026-07-31T16:20:00+00:00",
                )

    def test_prior_owner_reassignment_rejects_event_before_commit(self) -> None:
        event = self._owner_reassignment_event(timestamp="2026-07-31T16:19:59Z")
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(SystemExit, "must follow the delivered commit"),
            ):
                ai_status._verified_done_owner_reassignment(
                    {
                        "id": "REG-002",
                        "owner": "Codex",
                        "reviewer": "Antigravity",
                    },
                    commit_owner="Codex2",
                    current_owner="Codex",
                    commit_timestamp="2026-07-31T16:20:00+00:00",
                )

    def test_prior_owner_reassignment_rejects_stale_matching_event(self) -> None:
        stale = self._owner_reassignment_event()
        latest = self._owner_reassignment_event(
            old_owner="Claude",
            timestamp="2026-07-31T16:23:00Z",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(
                json.dumps(stale) + "\n" + json.dumps(latest) + "\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(SystemExit, "latest audited owner reassignment"),
            ):
                ai_status._verified_done_owner_reassignment(
                    {
                        "id": "REG-002",
                        "owner": "Codex",
                        "reviewer": "Antigravity",
                    },
                    commit_owner="Codex2",
                    current_owner="Codex",
                    commit_timestamp="2026-07-31T16:20:00+00:00",
                )

    def test_prior_owner_reassignment_requires_reviewer_continuity(self) -> None:
        owner_change = self._owner_reassignment_event()
        reviewer_change = self._owner_reassignment_event(
            old_owner="Codex",
            new_owner="Codex",
            reviewer="Antigravity",
            timestamp="2026-07-31T16:23:00Z",
            message="Supervisor reassigned reviewer continuity.",
        )
        reviewer_change["new_reviewer"] = "Claude"
        reviewer_change["event_id"] = ai_status._supervisor_reassignment_event_id(
            reviewer_change
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(
                json.dumps(owner_change) + "\n" + json.dumps(reviewer_change) + "\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(SystemExit, "reviewer continuity"),
            ):
                ai_status._verified_done_owner_reassignment(
                    {
                        "id": "REG-002",
                        "owner": "Codex",
                        "reviewer": "Antigravity",
                    },
                    commit_owner="Codex2",
                    current_owner="Codex",
                    commit_timestamp="2026-07-31T16:20:00+00:00",
                )

    # The audited pair of swaps the supervisor actually recorded for
    # OPS-CLOSEOUT-OWNER-REASSIGN-NO-HUMAN-SIGNOFF-20260805: two provider
    # outages, each moving owner and reviewer together.
    _SWAP_HOP_ONE = dict(
        task_id="REG-002",
        old_owner="Codex2",
        new_owner="Antigravity",
        old_reviewer="Codex",
        new_reviewer="Claude",
        timestamp="2026-08-05T11:59:31Z",
        message="Auto-reassigned ownership from Codex2 to Antigravity.",
    )
    _SWAP_HOP_TWO = dict(
        task_id="REG-002",
        old_owner="Antigravity",
        new_owner="Claude",
        old_reviewer="Claude",
        new_reviewer="Antigravity",
        timestamp="2026-08-05T12:26:28Z",
        message="Auto-reassigned ownership from Antigravity to Claude.",
    )

    @staticmethod
    def _fake_git(*, llm_agent: str, reviewer: str, commit_timestamp: str):
        def fake_run_git_command(args: list[str], **_kwargs: object) -> str:
            responses = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "task/REG-002",
                ("rev-parse", "HEAD"): "a" * 40,
                ("show", "-s", "--format=%s", "HEAD"): "REG-002: finish delivery",
                ("show", "-s", "--format=%b", "HEAD"): (
                    f"LLM-Agent: {llm_agent}\nTask-ID: REG-002\nReviewer: {reviewer}\n"
                ),
                ("show", "-s", "--format=%an", "HEAD"): llm_agent,
                ("show", "-s", "--format=%ae", "HEAD"): "worker@example.com",
                ("show", "-s", "--format=%cI", "HEAD"): commit_timestamp,
                ("status", "--porcelain"): "",
                ("remote",): "",
            }
            return responses[tuple(args)]

        return fake_run_git_command

    def _collect_with_audit(
        self,
        *,
        events: list[dict[str, str]],
        task: dict[str, Any],
        actor: str,
        llm_agent: str,
        reviewer: str,
        commit_timestamp: str,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(
                    os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False
                ),
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                mock.patch.object(
                    ai_status,
                    "run_git_command",
                    side_effect=self._fake_git(
                        llm_agent=llm_agent,
                        reviewer=reviewer,
                        commit_timestamp=commit_timestamp,
                    ),
                ),
            ):
                return ai_status.collect_done_delivery_metadata(task, actor)

    def test_collect_done_accepts_combined_owner_and_reviewer_swap(self) -> None:
        """The case that used to force a Human/Ops `reconcile_merged_done`.

        The delivery merged under the Antigravity/Claude pair, then a provider
        outage swapped both roles to Claude/Antigravity. Both commit trailers
        are now stale at once, which previously failed closed with no fallback.
        """

        events = [
            audited_reassignment_event(**self._SWAP_HOP_ONE),
            audited_reassignment_event(**self._SWAP_HOP_TWO),
        ]
        delivery = self._collect_with_audit(
            events=events,
            task={
                "id": "REG-002",
                "owner": "Claude",
                "reviewer": "Antigravity",
                "status": "review_approved",
            },
            actor="Claude",
            llm_agent="Antigravity",
            reviewer="Claude",
            commit_timestamp="2026-08-05T12:13:52+00:00",
        )

        owner_proof = delivery["commit_owner_reassignment"]
        self.assertEqual(owner_proof["old_owner"], "Antigravity")
        self.assertEqual(owner_proof["new_owner"], "Claude")
        self.assertEqual(owner_proof["hops"], 1)
        self.assertEqual(owner_proof["event_id"], events[1]["event_id"])
        self.assertEqual(owner_proof["reviewer_hops"], 1)

        reviewer_proof = delivery["commit_reviewer_reassignment"]
        self.assertEqual(reviewer_proof["old_reviewer"], "Claude")
        self.assertEqual(reviewer_proof["new_reviewer"], "Antigravity")
        self.assertEqual(reviewer_proof["event_id"], events[1]["event_id"])

    def test_collect_done_accepts_multi_hop_owner_chain(self) -> None:
        """Two consecutive reassignments still close out without a human."""

        events = [
            audited_reassignment_event(**self._SWAP_HOP_ONE),
            audited_reassignment_event(**self._SWAP_HOP_TWO),
        ]
        delivery = self._collect_with_audit(
            events=events,
            task={
                "id": "REG-002",
                "owner": "Claude",
                "reviewer": "Antigravity",
                "status": "review_approved",
            },
            actor="Claude",
            llm_agent="Codex2",
            reviewer="Codex",
            commit_timestamp="2026-08-05T11:00:00+00:00",
        )

        owner_proof = delivery["commit_owner_reassignment"]
        self.assertEqual(owner_proof["old_owner"], "Codex2")
        self.assertEqual(owner_proof["new_owner"], "Claude")
        self.assertEqual(owner_proof["hops"], 2)
        self.assertEqual(delivery["commit_reviewer_reassignment"]["hops"], 2)

    def test_collect_done_rejects_owner_drift_with_no_audit(self) -> None:
        """An owner trailer no audited reassignment explains still fails closed."""

        events = [audited_reassignment_event(**self._SWAP_HOP_ONE)]
        with self.assertRaisesRegex(SystemExit, "latest audited owner reassignment"):
            self._collect_with_audit(
                events=events,
                task={
                    "id": "REG-002",
                    "owner": "Claude",
                    "reviewer": "Antigravity",
                    "status": "review_approved",
                },
                actor="Claude",
                llm_agent="Gemini",
                reviewer="Claude",
                commit_timestamp="2026-08-05T11:00:00+00:00",
            )

    def test_done_reviewer_reassignment_rejects_unaudited_event(self) -> None:
        forged = audited_reassignment_event(
            task_id="REG-002", new_reviewer="Antigravity"
        )
        forged["agent"] = "Antigravity"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(forged) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(
                    SystemExit, "audited reviewer reassignment chain does not"
                ),
            ):
                ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Antigravity", "reviewer": "Antigravity"},
                    commit_reviewer="Claude",
                    current_reviewer="Antigravity",
                    commit_timestamp="2026-07-19T20:00:00+00:00",
                )

    def test_done_reviewer_reassignment_rejects_event_before_commit(self) -> None:
        event = audited_reassignment_event(
            task_id="REG-002",
            old_owner="Antigravity",
            new_owner="Antigravity",
            new_reviewer="Codex2",
            timestamp="2026-07-19T23:52:06Z",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(
                    SystemExit, "reviewer reassignment must follow the delivered commit"
                ),
            ):
                ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Antigravity", "reviewer": "Codex2"},
                    commit_reviewer="Claude",
                    current_reviewer="Codex2",
                    commit_timestamp="2026-07-20T00:00:00+00:00",
                )

    def test_collect_done_delivery_metadata_reports_all_missing_trailers_at_once(self) -> None:
        responses = iter(
            [
                "feat/bg-006",
                "abc123",
                "BG-006 finalize operator acceptance matrix",
                "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
                "Claude",
                "noreply@anthropic.com",
            ]
        )
        task = {
            "id": "BG-006",
            "owner": "Claude",
            "reviewer": "Codex",
            "status": "review_approved",
        }

        with mock.patch.object(ai_status, "run_git_command", side_effect=lambda *args, **kwargs: next(responses)):
            with self.assertRaises(SystemExit) as exc_info:
                ai_status.collect_done_delivery_metadata(task, "Claude")

        message = str(exc_info.exception)
        self.assertIn("`LLM-Agent: ...`", message)
        self.assertIn("`Task-ID: ...`", message)
        self.assertIn("`Reviewer: ...`", message)

    def test_collect_done_delivery_metadata_skips_trailers_for_merge_commit(self) -> None:
        responses = iter(
            [
                "task/REG-002",
                "merge123",
                "Merge pull request #1259 from ajoe734/task/REG-002",
                "REG-002: deliver reviewed task\n",
                "GitHub",
                "noreply@github.com",
                "",
                "",
            ]
        )
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
        }

        with (
            mock.patch.dict(os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False),
            mock.patch.object(ai_status, "run_git_command", side_effect=lambda *args, **kwargs: next(responses)),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertEqual(delivery["commit"], "merge123")
        self.assertEqual(delivery["commit_trailer_skip_reason"], "Merge")
        self.assertTrue(delivery["commit_trailer_check_skipped"])

    def test_collect_done_delivery_metadata_uses_execute_plans_artifact_repo(self) -> None:
        responses = iter(
            [
                "bff-luv-fe-006-dev-deploy",
                "abc123",
                "FE-INT-GATE-DUMMY finalize execute-plans artifact",
                "LLM-Agent: Codex2\nTask-ID: FE-INT-GATE-DUMMY\nReviewer: Claude\n",
                "Codex2",
                "codex2@example.com",
                "",
                "",
            ]
        )
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("cwd") if isinstance(kwargs.get("cwd"), Path) else None))
            return next(responses)

        task = {
            "id": "FE-INT-GATE-DUMMY",
            "owner": "Codex2",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": ["execute-plans/e2e/dummy.spec.ts"],
        }
        with (
            mock.patch.dict(os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        execute_plans_root = Path(delivery["repository_path"])
        self.assertEqual(delivery["repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_path"], str(execute_plans_root))
        self.assertEqual(delivery["repository_slug"], "ajoe734/execute-plans")
        self.assertEqual(delivery["branch"], "bff-luv-fe-006-dev-deploy")
        self.assertTrue(calls)
        self.assertTrue(all(cwd == execute_plans_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_falls_back_to_pantheon_for_missing_mixed_repo(self) -> None:
        responses = iter(
            [
                "task/BFF-PM12-002",
                "abc123",
                "BFF-PM12-002: refresh closeout gate",
                "LLM-Agent: Codex2\nTask-ID: BFF-PM12-002\nReviewer: Claude2\n",
                "Codex2",
                "codex2@example.com",
                "",
                "",
            ]
        )
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            calls.append((args, kwargs.get("cwd") if isinstance(kwargs.get("cwd"), Path) else None))
            return next(responses)

        task = {
            "id": "BFF-PM12-002",
            "owner": "Codex2",
            "reviewer": "Claude2",
            "status": "review_approved",
            "artifacts": [
                "execute-plans/src/lib/bff-v1/management.ts",
                "services/control-plane/bff/main.py",
            ],
        }
        pantheon_root = Path("/tmp/pantheon-task-worktree")
        # Use a path guaranteed absent rather than a fixed literal: a real
        # worker layout with an active execute-plans checkout would make a
        # hardcoded literal path like that actually exist, silently turning
        # this into the "active" branch instead of exercising the
        # missing-repo fallback.
        with tempfile.TemporaryDirectory() as missing_repo_parent:
            missing_execute_plans_root = Path(missing_repo_parent) / "execute-plans"

            def fake_repository_local_path(_config: dict[str, object], repo_id: str | None) -> Path | None:
                if repo_id == "execute_plans":
                    return missing_execute_plans_root
                if repo_id == "pantheon":
                    return pantheon_root
                return None

            with (
                mock.patch.dict(os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False),
                mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
                mock.patch.object(ai_status, "repository_local_path", side_effect=fake_repository_local_path),
            ):
                delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        self.assertEqual(delivery["repository_id"], "pantheon")
        self.assertEqual(delivery["repository_path"], str(pantheon_root))
        self.assertEqual(delivery["branch"], "task/BFF-PM12-002")
        self.assertEqual(delivery["repository_fallback"]["from_repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_fallback"]["missing_repository_path"], str(missing_execute_plans_root))
        self.assertTrue(calls)
        self.assertTrue(all(cwd == pantheon_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_blocks_unmerged_task_pr(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "task/REG-002"
            if args == ["rev-parse", "HEAD"]:
                return "abc123"
            if args == ["show", "-s", "--format=%s", "HEAD"]:
                return "REG-002 finalize"
            if args == ["show", "-s", "--format=%b", "HEAD"]:
                return "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
            if args == ["show", "-s", "--format=%an", "HEAD"]:
                return "Codex"
            if args == ["show", "-s", "--format=%ae", "HEAD"]:
                return "codex@example.com"
            if args == ["status", "--porcelain"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return "origin/task/REG-002"
            if args == ["rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "devsha"
            raise AssertionError(f"unexpected git command: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", return_value=False),
            mock.patch.object(
                ai_status,
                "pull_request_status_for_branch",
                return_value={
                    "number": 152,
                    "state": "OPEN",
                    "mergeStateStatus": "BEHIND",
                    "autoMergeRequest": {"mergeMethod": "MERGE"},
                    "url": "https://github.com/ajoe734/pantheon/pull/152",
                },
            ),
        ):
            with self.assertRaises(SystemExit) as exc_info:
                ai_status.collect_done_delivery_metadata(task, "Codex")

        message = str(exc_info.exception)
        self.assertIn("not merged into `origin/dev`", message)
        self.assertIn("PR #152", message)
        self.assertIn("mergeState=BEHIND", message)
        self.assertIn("review_approved", message)

    def test_collect_done_delivery_metadata_allows_head_merged_to_dev(self) -> None:
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": [],
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "task/REG-002"
            if args == ["rev-parse", "HEAD"]:
                return "abc123"
            if args == ["show", "-s", "--format=%s", "HEAD"]:
                return "REG-002 finalize"
            if args == ["show", "-s", "--format=%b", "HEAD"]:
                return "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
            if args == ["show", "-s", "--format=%an", "HEAD"]:
                return "Codex"
            if args == ["show", "-s", "--format=%ae", "HEAD"]:
                return "codex@example.com"
            if args == ["status", "--porcelain"]:
                return ""
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
                return "origin/task/REG-002"
            if args == ["rev-list", "--left-right", "--count", "origin/task/REG-002...HEAD"]:
                return "0 0"
            if args == ["fetch", "origin", "dev"]:
                return ""
            if args == ["rev-parse", "--verify", "origin/dev"]:
                return "devsha"
            raise AssertionError(f"unexpected git command: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(ai_status, "git_command_succeeds", return_value=True),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertEqual(delivery["merge_target_branch"], "dev")
        self.assertEqual(delivery["merge_target_ref"], "origin/dev")
        self.assertEqual(delivery["merge_target_sha"], "devsha")
        self.assertTrue(delivery["head_merged_to_target"])

    def test_delivery_merge_target_branch_uses_dev_for_execute_plans(self) -> None:
        # execute-plans mirrors Pantheon's per-task-PR-into-dev model; its
        # GitHub-configured default branch is `dev`, not `main`. Regression
        # guard for OPS-EP-BRANCH-TARGETING-001: a stale `main` value here
        # made the done-finalize gate check ancestry against the wrong
        # branch, which pushed FE task PRs to target `main` directly and
        # drift it out of sync with `dev`.
        self.assertEqual(
            ai_status.delivery_merge_target_branch({}, "execute_plans"),
            "dev",
        )


class ArchiveWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "REG-100",
                    "title": "Archived completion candidate",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "done",
                    "terminal_outcome": "completed",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Completed",
                    "last_update": "2026-04-14T02:00:00Z",
                },
                {
                    "id": "REG-101",
                    "title": "Still active",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": ["REG-100"],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Waiting on archived dependency",
                    "last_update": "2026-04-14T02:00:00Z",
                },
            ],
            "handoffs": [
                {
                    "task_id": "REG-100",
                    "from": "Claude",
                    "to": "Codex",
                    "message": "Finalize complete",
                    "status": "done",
                    "created_at": "2026-04-14T01:50:00Z",
                }
            ],
            "blockers": [
                {
                    "task_id": "REG-100",
                    "owner": "Codex",
                    "waiting_for": "Claude",
                    "message": "Resolved blocker snapshot",
                    "status": "resolved",
                    "created_at": "2026-04-14T01:45:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_archive_migrate_moves_terminal_tasks_out_of_active_state(self) -> None:
        ai_status.command_archive_migrate(self.state, [])

        self.assertEqual(
            [task["id"] for task in self.state["tasks"]],
            ["REG-100", "REG-101"],
        )
        self.assertTrue(self.state["handoffs"])
        self.assertTrue(self.state["blockers"])
        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["id"], "REG-100")

    def test_archive_migrate_normalizes_only_missing_legacy_done_outcome(self) -> None:
        legacy = self.state["tasks"][0]
        legacy.pop("terminal_outcome")

        ai_status.command_archive_migrate(self.state, [])

        snapshot = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]
        self.assertEqual(snapshot["terminal_outcome"], "completed")
        self.assertNotIn("terminal_outcome", snapshot["task"])
        self.assertTrue(ai_status._status_archive_snapshot_is_valid(snapshot))

    def test_archive_migrate_rejects_present_invalid_terminal_outcome(self) -> None:
        invalid_outcomes = (None, "", "failed", "COMPLETED")
        for invalid_outcome in invalid_outcomes:
            with self.subTest(terminal_outcome=invalid_outcome):
                state = deepcopy(self.state)
                state["tasks"][0]["terminal_outcome"] = invalid_outcome

                with self.assertRaisesRegex(
                    RuntimeError,
                    "terminal task has invalid archive outcome: REG-100",
                ):
                    ai_status.command_archive_migrate(state, [])

                self.assertNotIn(ai_status.STATUS_ARCHIVE_OUTBOX_KEY, state)

    def test_archive_migrate_preserves_canonical_superseded_outcome(self) -> None:
        superseded = self.state["tasks"][0]
        superseded["terminal_outcome"] = "superseded"
        superseded["superseded_by"] = "REG-101"

        ai_status.command_archive_migrate(self.state, [])

        snapshot = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]
        self.assertEqual(snapshot["terminal_outcome"], "superseded")
        self.assertEqual(snapshot["task"]["terminal_outcome"], "superseded")

    def _write_modern_archive_with_absolute_review_file(
        self,
        *,
        task_id: str = "REG-100",
        target: str = "docs/evidence/REG-100/evidence.json",
    ) -> tuple[Path, str]:
        task = deepcopy(self.state["tasks"][0])
        task["id"] = task_id
        task["review_file"] = (
            f"/tmp/pantheon-worker-worktrees/pantheon/"
            f"{task_id.lower()}/{target}"
        )
        snapshot = {
            "version": 1,
            "task_id": task_id,
            "archived_at": "2026-04-14T02:01:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": task,
            "handoffs": [],
            "blockers": [],
        }
        path = task_archive.archive_task_path(task_id)
        path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        return path, target

    def test_archive_review_file_correction_is_durable_and_idempotent(self) -> None:
        path, target = self._write_modern_archive_with_absolute_review_file()
        digest = "a" * 64

        corrected = task_archive.correct_archived_task_review_file(
            "REG-100",
            target,
            actor="Human/Ops",
            reason="Replace disposable worker path with merged evidence path.",
            evidence_sha256=digest,
            corrected_at="2026-04-14T03:00:00Z",
        )
        replayed = task_archive.correct_archived_task_review_file(
            "REG-100",
            target,
            actor="Human/Ops",
            reason="Replace disposable worker path with merged evidence path.",
            evidence_sha256=digest,
        )

        self.assertEqual(corrected, replayed)
        self.assertEqual(corrected["task"]["review_file"], target)
        self.assertEqual(
            corrected["correction_context"],
            {
                "version": 1,
                "corrected_at": "2026-04-14T03:00:00Z",
                "actor": "Human/Ops",
                "reason": "Replace disposable worker path with merged evidence path.",
                "field": "task.review_file",
                "from": (
                    "/tmp/pantheon-worker-worktrees/pantheon/"
                    "reg-100/docs/evidence/REG-100/evidence.json"
                ),
                "to": target,
                "evidence_sha256": digest,
            },
        )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), corrected)
        self.assertTrue(task_archive.is_valid_modern_contract(corrected))

    def test_archive_review_file_correction_rejects_unsafe_sources_and_targets(self) -> None:
        path, target = self._write_modern_archive_with_absolute_review_file()
        digest = "b" * 64
        with self.assertRaisesRegex(RuntimeError, "repository-relative"):
            task_archive.correct_archived_task_review_file(
                "REG-100",
                "../evidence.json",
                actor="Human/Ops",
                reason="unsafe target",
                evidence_sha256=digest,
            )

        snapshot = json.loads(path.read_text(encoding="utf-8"))
        snapshot["task"]["review_file"] = f"/tmp/untrusted/{target}"
        path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "disposable worker path"):
            task_archive.correct_archived_task_review_file(
                "REG-100",
                target,
                actor="Human/Ops",
                reason="unsafe source",
                evidence_sha256=digest,
            )

    def test_archive_review_file_command_is_human_ops_only_and_rejects_active_task(
        self,
    ) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "Only Human/Ops"):
                ai_status.command_archive_correct_review_file(
                    self.state,
                    ["REG-100", "docs/evidence/REG-100/evidence.json", "reason"],
                )

        with mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "while REG-100 is active"):
                ai_status.command_archive_correct_review_file(
                    self.state,
                    ["REG-100", "docs/evidence/REG-100/evidence.json", "reason"],
                )

    def test_archive_review_file_command_validates_and_records_correction(self) -> None:
        self.state["tasks"] = [self.state["tasks"][1]]
        corrected = {
            "correction_context": {"corrected_at": "2026-04-14T03:00:00Z"}
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False),
            mock.patch.object(
                ai_status,
                "validate_archive_review_file_target",
                return_value=("docs/evidence/REG-100/evidence.json", "c" * 64),
            ) as validate_target,
            mock.patch.object(
                task_archive,
                "correct_archived_task_review_file",
                return_value=corrected,
            ) as correct_archive,
            mock.patch.object(ai_status, "append_log") as append_log,
        ):
            ai_status.command_archive_correct_review_file(
                self.state,
                [
                    "REG-100",
                    "docs/evidence/REG-100/evidence.json",
                    "Durable governed correction.",
                ],
            )

        validate_target.assert_called_once_with(
            "REG-100", "docs/evidence/REG-100/evidence.json"
        )
        correct_archive.assert_called_once_with(
            "REG-100",
            "docs/evidence/REG-100/evidence.json",
            actor="Human/Ops",
            reason="Durable governed correction.",
            evidence_sha256="c" * 64,
            canonical_lock_held=True,
        )
        append_log.assert_called_once()
        self.assertEqual(
            append_log.call_args.args[0]["type"],
            "archive_review_file_corrected",
        )

    def test_prune_archived_active_tasks_removes_duplicate_active_rows(self) -> None:
        terminal = deepcopy(self.state["tasks"][0])
        related_handoffs = deepcopy(self.state["handoffs"])
        related_blockers = deepcopy(self.state["blockers"])

        def fake_archived_snapshot(task_id: str):
            if task_id != "REG-100":
                return None
            return {
                "version": 1,
                "task_id": task_id,
                "archived_at": "2026-04-14T02:01:00Z",
                "terminal_status": "done",
                "terminal_outcome": "completed",
                "task": terminal,
                "handoffs": related_handoffs,
                "blockers": related_blockers,
            }

        with mock.patch.object(ai_status, "archived_task_snapshot", side_effect=fake_archived_snapshot):
            pruned = ai_status.prune_archived_active_tasks(self.state)

        self.assertEqual(pruned, ["REG-100"])
        self.assertEqual([task["id"] for task in self.state["tasks"]], ["REG-101"])
        self.assertEqual(self.state["handoffs"], [])
        self.assertEqual(self.state["blockers"], [])

    def test_reopen_rejects_archived_task(self) -> None:
        self.state["tasks"] = []
        with mock.patch.object(ai_status, "archived_task_snapshot", return_value={"task_id": "REG-100"}):
            with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
                with self.assertRaises(SystemExit) as exc_info:
                    ai_status.command_reopen(self.state, ["REG-100", "Resume work"])

        self.assertIn("archived", str(exc_info.exception))
        self.assertIn("follow-up", str(exc_info.exception))

    def test_show_reads_archive_snapshot(self) -> None:
        self.state["tasks"] = []
        snapshot = {
            "task_id": "REG-100",
            "archived_at": "2026-04-14T02:00:00Z",
            "terminal_outcome": "completed",
            "task": {
                "id": "REG-100",
                "status": "done",
                "title": "Archived completion candidate",
            },
        }
        with (
            mock.patch.object(ai_status, "archived_task_snapshot", return_value=snapshot),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ai_status.command_show(self.state, ["REG-100"])

        rendered = stdout.getvalue()
        self.assertIn('"source": "archive"', rendered)
        self.assertIn('"task_id": "REG-100"', rendered)
        self.assertIn("ai-task-archive/tasks", rendered)

    def test_read_only_main_fails_closed_without_recovering_pending_outbox(self) -> None:
        class NullLock:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        pending_state = dict(self.state)
        pending_state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = {
            "schema_version": 1,
            "transaction_id": "pending",
            "events": [{"event_id": "pending-event"}],
        }

        with (
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(
                ai_status,
                "canonical_task_state_lock",
                return_value=NullLock(),
            ),
            mock.patch.object(ai_status, "load_state", return_value=pending_state),
            mock.patch.object(ai_status, "recover_status_archive_outbox") as archive_recovery,
            mock.patch.object(ai_status, "recover_status_activity_outbox") as activity_recovery,
            mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            rc = ai_status.main(["ai_status.py", "show", "REG-100"])

        self.assertEqual(rc, 2)
        rendered = json.loads(stderr.getvalue())
        self.assertEqual(rendered["status"], "fail_closed")
        diagnostic = rendered["diagnostic"]
        self.assertEqual(diagnostic["invariant"], "status_recovery_pending")
        archive_recovery.assert_not_called()
        activity_recovery.assert_not_called()

    def test_recover_main_replays_pending_outboxes_without_worker_lease(self) -> None:
        class NullLock:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        pending_state = dict(self.state)
        pending_state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = {
            "schema_version": 1,
            "transaction_id": "pending",
            "events": [{"event_id": "pending-event"}],
        }
        logs = [{"event_id": "pending-event"}]

        with (
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "canonical_task_state_lock", return_value=NullLock()) as lock_mock,
            mock.patch.object(ai_status, "load_state", return_value=pending_state),
            mock.patch.object(ai_status, "recover_status_archive_outbox", return_value=False) as archive_recovery,
            mock.patch.object(ai_status, "recover_status_activity_outbox", return_value=True) as activity_recovery,
            mock.patch.object(ai_status, "load_logs", return_value=logs),
            mock.patch.object(ai_status, "write_current_work") as current_work,
            mock.patch.object(ai_status, "write_dashboard_bundle") as dashboard,
            mock.patch.object(ai_status, "sync_docs_site") as docs_site,
            mock.patch.object(ai_status, "validate_active_status_command_lease") as lease_validation,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = ai_status.main(["ai_status.py", "recover"])

        self.assertEqual(rc, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "status": "recovered",
                "archive_outbox_recovered": False,
                "activity_outbox_recovered": True,
            },
        )
        lock_mock.assert_called_once_with(shared=False, nonblocking=True)
        archive_recovery.assert_called_once_with(pending_state)
        activity_recovery.assert_called_once_with(pending_state)
        current_work.assert_called_once_with(pending_state, logs)
        dashboard.assert_called_once_with(pending_state)
        docs_site.assert_called_once_with(pending_state)
        lease_validation.assert_not_called()

    def test_recover_main_is_noop_without_pending_outbox(self) -> None:
        class NullLock:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        with (
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "canonical_task_state_lock", return_value=NullLock()),
            mock.patch.object(ai_status, "load_state", return_value=self.state),
            mock.patch.object(ai_status, "recover_status_archive_outbox", return_value=False),
            mock.patch.object(ai_status, "recover_status_activity_outbox", return_value=False),
            mock.patch.object(ai_status, "refresh_derived_status_views") as refresh_views,
            mock.patch.object(ai_status, "validate_active_status_command_lease") as lease_validation,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = ai_status.main(["ai_status.py", "recover"])

        self.assertEqual(rc, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "status": "no_pending_recovery",
                "archive_outbox_recovered": False,
                "activity_outbox_recovered": False,
            },
        )
        refresh_views.assert_not_called()
        lease_validation.assert_not_called()

    def test_status_write_pending_indicators_feature_flag_and_per_task_counting(self) -> None:
        state = deepcopy(self.state)
        # Add tasks A, B, C
        state["tasks"] = [
            {"id": "TASK-A", "status": "in_progress"},
            {"id": "TASK-B", "status": "in_progress"},
            {"id": "TASK-C", "status": "in_progress"},
        ]
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = {
            "schema_version": 1,
            "transaction_id": "tx1",
            "events": [
                {"event_id": "e1", "task_id": "TASK-A"},
                {"event_id": "e2", "task_id": "TASK-A"},
                {"event_id": "e3", "task_id": "TASK-B"},
            ],
        }

        # Test 1: Feature flag OFF (default) -> no indicator stamped
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED", None)
            ai_status._update_pending_outbox_indicators(state)
            for task in state["tasks"]:
                self.assertNotIn("status_write_pending", task)
                self.assertNotIn("status_write_pending_count", task)

        # Test 2: Feature flag ON -> per-task count stamped
        with mock.patch.dict(os.environ, {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "1"}, clear=False):
            ai_status._update_pending_outbox_indicators(state)
            task_a = next(t for t in state["tasks"] if t["id"] == "TASK-A")
            task_b = next(t for t in state["tasks"] if t["id"] == "TASK-B")
            task_c = next(t for t in state["tasks"] if t["id"] == "TASK-C")

            self.assertTrue(task_a.get("status_write_pending"))
            self.assertEqual(task_a.get("status_write_pending_count"), 2)

            self.assertTrue(task_b.get("status_write_pending"))
            self.assertEqual(task_b.get("status_write_pending_count"), 1)

            self.assertNotIn("status_write_pending", task_c)
            self.assertNotIn("status_write_pending_count", task_c)

        # Test 3: Unbound event without task_id does NOT mark untouched tasks as pending
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY]["events"].append({"event_id": "e4"}) # no task_id (e.g. wave event)
        with mock.patch.dict(os.environ, {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "1"}, clear=False):
            ai_status._update_pending_outbox_indicators(state)
            task_a = next(t for t in state["tasks"] if t["id"] == "TASK-A")
            task_c = next(t for t in state["tasks"] if t["id"] == "TASK-C")

            self.assertTrue(task_a.get("status_write_pending"))
            self.assertEqual(task_a.get("status_write_pending_count"), 2) # still exact count for A

            # TASK-C must NOT be marked as pending (preventing whole-board false positives)
            self.assertNotIn("status_write_pending", task_c)
            self.assertNotIn("status_write_pending_count", task_c)

    def test_recover_main_fails_closed_when_task_lock_is_busy(self) -> None:
        with (
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(
                ai_status,
                "canonical_task_state_lock",
                side_effect=BlockingIOError,
            ),
            mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            rc = ai_status.main(["ai_status.py", "recover"])

        self.assertEqual(rc, 75)
        self.assertEqual(
            json.loads(stderr.getvalue())["diagnostic"]["invariant"],
            "status_task_lock_busy",
        )

    def test_real_show_is_bounded_and_does_not_mutate_pending_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir)
            subprocess.run(
                ["git", "init", "-q", str(status_root)],
                check=True,
            )
            (status_root / ".orchestrator").mkdir()
            (status_root / ".orchestrator" / "task-state.lock").write_bytes(b"")
            state = deepcopy(self.state)
            state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = {
                "schema_version": 1,
                "transaction_id": "pending-read-only-fixture",
                "events": [{"event_id": "pending-read-only-event"}],
            }
            status_file = status_root / "ai-status.json"
            status_file.write_text(
                json.dumps(state, indent=2) + "\n",
                encoding="utf-8",
            )
            event_log = status_root / "runtime" / "task-state-events.jsonl"
            ai_status.append_state_commit(event_log, state, source="test-fixture")
            before = status_file.read_bytes()
            env = os.environ.copy()
            for name in (
                "ORCH_RUN_ID",
                "PANTHEON_WORKTREE_ROOT",
                "ORCH_WORKSPACE_PATH",
                "ORCH_RUNNER_STATUS_PATH",
                "ORCH_HEARTBEAT_PATH",
            ):
                env.pop(name, None)
            env.update(
                {
                    "AI_NAME": "Codex",
                    "PANTHEON_STATUS_ROOT": str(status_root),
                    ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                    ai_status.TASK_STATE_EVENT_LOG_ENV: str(event_log),
                }
            )

            started = time.monotonic()
            result = subprocess.run(
                [
                    "bash",
                    str(Path(__file__).resolve().parents[1] / "scripts" / "ai-status.sh"),
                    "show",
                    "REG-100",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            self.assertLess(elapsed, 6.0)
            rendered = json.loads(result.stderr)
            self.assertEqual(
                rendered["diagnostic"]["invariant"],
                "status_recovery_pending",
            )
            self.assertEqual(status_file.read_bytes(), before)
            self.assertFalse(
                (status_root / ".orchestrator" / "activity-audit.lock").exists()
            )

    def test_real_show_returns_immediately_when_writer_holds_task_lock(self) -> None:
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as tmpdir:
            status_root = Path(tmpdir)
            subprocess.run(["git", "init", "-q", str(status_root)], check=True)
            (status_root / ".orchestrator").mkdir()
            (status_root / ".orchestrator" / "task-state.lock").write_bytes(b"")
            status_file = status_root / "ai-status.json"
            status_file.write_text(
                json.dumps(self.state, indent=2) + "\n",
                encoding="utf-8",
            )
            event_log = status_root / "runtime" / "task-state-events.jsonl"
            ai_status.append_state_commit(event_log, self.state, source="test-fixture")
            before = status_file.read_bytes()
            entered = context.Event()
            release = context.Event()
            holder = context.Process(
                target=_hold_task_state_lock,
                args=(str(status_file), entered, release),
            )
            holder.start()
            self.assertTrue(entered.wait(timeout=5))
            env = os.environ.copy()
            for name in (
                "ORCH_RUN_ID",
                "PANTHEON_WORKTREE_ROOT",
                "ORCH_WORKSPACE_PATH",
                "ORCH_RUNNER_STATUS_PATH",
                "ORCH_HEARTBEAT_PATH",
            ):
                env.pop(name, None)
            env.update(
                {
                    "AI_NAME": "Codex",
                    "PANTHEON_STATUS_ROOT": str(status_root),
                    ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                    ai_status.TASK_STATE_EVENT_LOG_ENV: str(event_log),
                }
            )
            try:
                started = time.monotonic()
                result = subprocess.run(
                    [
                        "bash",
                        str(Path(__file__).resolve().parents[1] / "scripts" / "ai-status.sh"),
                        "show",
                        "REG-100",
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                elapsed = time.monotonic() - started
            finally:
                release.set()
                holder.join(timeout=5)
                if holder.is_alive():
                    holder.kill()
                    holder.join(timeout=5)
            self.assertEqual(holder.exitcode, 0)
            self.assertEqual(result.returncode, 75, result.stderr + result.stdout)
            self.assertLess(elapsed, 6.0)
            self.assertEqual(
                json.loads(result.stderr)["diagnostic"]["invariant"],
                "status_task_lock_busy",
            )
            self.assertEqual(status_file.read_bytes(), before)


class SidecarTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Gemini", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Copilot", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Qwen", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    @staticmethod
    def _artifact_guard(task_id: str, path: str, allowed: list[str]) -> dict:
        return {
            "schema_version": 1,
            "program_id": "test-program",
            "catalog_sha256": "a" * 64,
            "task_id": task_id,
            "artifact_scope": [{"repo": "pantheon", "path": path}],
            "allowed_overlap_task_ids": allowed,
        }

    def test_assign_supports_sidecar_metadata_from_env(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_PHASE": "Phase 5: Persona and Application Surfaces",
            "TASK_TITLE": "Prepare APP-001 BFF handoff packet",
            "TASK_SUMMARY_ZH": "平行支援 APP-001，整理 BFF handoff materials。",
            "TASK_DEPENDS_ON": "PER-001",
            "TASK_ARTIFACTS": "support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md",
            "TASK_CLASS": "sidecar",
            "TASK_HELPER_PARENT": "APP-001",
            "TASK_HELPER_KIND": "bff_handoff_packet",
            "TASK_AUTO_GENERATED": "true",
            "TASK_MUTATES_CANONICAL": "false",
            "TASK_AUTO_CREATED_BY": "supervisor-underutilization",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_assign(self.state, ["APP-001-SIDECAR-BFF-HANDOFF", "Gemini", "Copilot"])

        task = ai_status.get_task(self.state, "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertIsNotNone(task)
        self.assertEqual(task["task_class"], "sidecar")
        self.assertTrue(task["auto_generated"])
        self.assertEqual(task["helper_parent"], "APP-001")
        self.assertEqual(task["helper_kind"], "bff_handoff_packet")
        self.assertFalse(task["mutates_canonical"])
        self.assertEqual(task["auto_created_by"], "supervisor-underutilization")
        self.assertEqual(task["depends_on"], ["PER-001"])

    def test_assign_preserves_antigravity_runtime_agent_names(self) -> None:
        ai_status.command_assign(self.state, ["APP-002-SIDECAR-REVIEW", "Antigravity2", "Claude"])

        task = ai_status.get_task(self.state, "APP-002-SIDECAR-REVIEW")
        self.assertIsNotNone(task)
        self.assertEqual(task["owner"], "Antigravity2")
        self.assertEqual(task["reviewer"], "Claude")

    def test_create_only_assign_rejects_existing_task_without_overwrite(self) -> None:
        ai_status.command_assign(
            self.state,
            ["APP-003", "Codex", "Claude", "Original contract"],
        )
        before = deepcopy(ai_status.get_task(self.state, "APP-003"))

        with mock.patch.dict(
            os.environ,
            {"TASK_ASSIGN_CREATE_ONLY": "true"},
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "create-only assignment refused"):
                ai_status.command_assign(
                    self.state,
                    ["APP-003", "Gemini", "Copilot", "Conflicting contract"],
                )

        self.assertEqual(ai_status.get_task(self.state, "APP-003"), before)

    def test_assign_preserves_explicit_next_instruction(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TASK_NEXT": "Wait for the governed deployment lease"},
            clear=False,
        ):
            ai_status.command_assign(
                self.state,
                ["APP-004", "Codex", "Claude", "Lease-gated task"],
            )

        task = ai_status.get_task(self.state, "APP-004")
        self.assertEqual(task["next"], "Wait for the governed deployment lease")

    def test_protected_existing_scope_rejects_later_rogue_assignment(self) -> None:
        protected = {
            "id": "CATALOG-BFF-001",
            "status": "todo",
            "artifacts": ["services/control-plane/bff"],
            "target_repo": "pantheon",
            "artifact_conflict_guard": self._artifact_guard(
                "CATALOG-BFF-001",
                "services/control-plane/bff",
                ["PPL-ALLOC-009"],
            ),
        }
        self.state["tasks"].append(protected)

        with mock.patch.dict(
            os.environ,
            {"TASK_ARTIFACTS": "services/control-plane/bff/main.py"},
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "rejected overlap"):
                ai_status.command_assign(
                    self.state,
                    ["ROGUE-BFF-001", "Codex", "Claude", "Rogue overlap"],
                )

        self.assertIsNone(ai_status.get_task(self.state, "ROGUE-BFF-001"))

    def test_protected_incoming_scope_allows_declared_external_owner(self) -> None:
        self.state["tasks"].append(
            {
                "id": "PPL-ALLOC-009",
                "status": "blocked",
                "artifacts": ["services/control-plane/bff"],
            }
        )
        guard = self._artifact_guard(
            "CATALOG-BFF-002",
            "services/control-plane/bff/main.py",
            ["PPL-ALLOC-009"],
        )
        with mock.patch.dict(
            os.environ,
            {
                "TASK_ARTIFACTS": "services/control-plane/bff/main.py",
                "TASK_METADATA_JSON": json.dumps(
                    {
                        "target_repo": "pantheon",
                        "artifacts": ["services/control-plane/bff/main.py"],
                        "artifact_conflict_guard": guard,
                    }
                ),
            },
            clear=False,
        ):
            ai_status.command_assign(
                self.state,
                ["CATALOG-BFF-002", "Codex", "Claude", "Protected task"],
            )

        self.assertIsNotNone(ai_status.get_task(self.state, "CATALOG-BFF-002"))

    def test_existing_artifact_conflict_guard_is_immutable(self) -> None:
        guard = self._artifact_guard(
            "CATALOG-BFF-003",
            "services/control-plane/bff/main.py",
            [],
        )
        self.state["tasks"].append(
            {
                "id": "CATALOG-BFF-003",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "title": "Protected task",
                "artifacts": ["services/control-plane/bff/main.py"],
                "target_repo": "pantheon",
                "artifact_conflict_guard": guard,
            }
        )
        tampered = deepcopy(guard)
        tampered["allowed_overlap_task_ids"] = ["ROGUE-BFF-001"]

        with mock.patch.dict(
            os.environ,
            {"TASK_METADATA_JSON": json.dumps({"artifact_conflict_guard": tampered})},
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "guard is immutable"):
                ai_status.command_assign(
                    self.state,
                    ["CATALOG-BFF-003", "Codex", "Claude", "Protected task"],
                )

    def test_human_ops_can_revise_only_the_catalog_sha_and_assignment(self) -> None:
        guard = self._artifact_guard(
            "CATALOG-BFF-004",
            "services/control-plane/bff/main.py",
            [],
        )
        self.state["tasks"].append(
            {
                "id": "CATALOG-BFF-004",
                "program_id": "test-program",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Claude",
                "title": "Protected task",
                "artifacts": ["services/control-plane/bff/main.py"],
                "target_repo": "pantheon",
                "artifact_conflict_guard": guard,
            }
        )
        self.state["tasks"].append(
            {
                "id": "LATER-OVERLAP-001",
                "status": "in_progress",
                "artifacts": ["services/control-plane/bff"],
            }
        )
        revised = deepcopy(guard)
        revised["catalog_sha256"] = "b" * 64
        metadata = {
            "program_id": "test-program",
            "catalog_task_contract_sha256": "c" * 64,
            "artifact_conflict_guard": revised,
        }

        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Human/Ops",
                "TASK_METADATA_JSON": json.dumps(metadata),
                "TASK_ASSIGN_CATALOG_REVISION_FROM_SHA": "a" * 64,
                "TASK_ASSIGN_CATALOG_REVISION_TO_SHA": "b" * 64,
                "TASK_NEXT": "Preserve current implementation progress.",
            },
            clear=False,
        ):
            ai_status.command_assign(
                self.state,
                ["CATALOG-BFF-004", "Claude", "Codex", "Protected task"],
            )

        task = ai_status.get_task(self.state, "CATALOG-BFF-004")
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["owner"], "Claude")
        self.assertEqual(task["reviewer"], "Codex")
        self.assertEqual(task["artifact_conflict_guard"], revised)
        self.assertEqual(task["catalog_task_contract_sha256"], "c" * 64)

    def test_catalog_revision_rejects_scope_or_allowlist_change(self) -> None:
        guard = self._artifact_guard(
            "CATALOG-BFF-005",
            "services/control-plane/bff/main.py",
            [],
        )
        self.state["tasks"].append(
            {
                "id": "CATALOG-BFF-005",
                "program_id": "test-program",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "title": "Protected task",
                "artifacts": ["services/control-plane/bff/main.py"],
                "target_repo": "pantheon",
                "artifact_conflict_guard": guard,
            }
        )
        tampered = deepcopy(guard)
        tampered["catalog_sha256"] = "b" * 64
        tampered["allowed_overlap_task_ids"] = ["ROGUE-BFF-001"]
        metadata = {
            "program_id": "test-program",
            "catalog_task_contract_sha256": "c" * 64,
            "artifact_conflict_guard": tampered,
        }

        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Human/Ops",
                "TASK_METADATA_JSON": json.dumps(metadata),
                "TASK_ASSIGN_CATALOG_REVISION_FROM_SHA": "a" * 64,
                "TASK_ASSIGN_CATALOG_REVISION_TO_SHA": "b" * 64,
            },
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "cannot change artifact scope"):
                ai_status.command_assign(
                    self.state,
                    ["CATALOG-BFF-005", "Claude", "Codex", "Protected task"],
                )

    def test_display_task_title_marks_sidecar_parent(self) -> None:
        title = ai_status.display_task_title(
            {
                "title": "Prepare APP-001 BFF handoff packet",
                "task_class": "sidecar",
                "auto_generated": True,
                "helper_parent": "APP-001",
            }
        )

        self.assertEqual(title, "[Sidecar] [Auto] [Parent APP-001] Prepare APP-001 BFF handoff packet")


class HumanOpsAgentTests(unittest.TestCase):
    def test_human_gate_can_belong_to_human_ops_without_blocking_worker(self) -> None:
        state = {
            "agents": [
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "PROD-WRITES-001-V2",
                    "title": "Enable production real writes",
                    "phase": "Phase 8 / EPIC-LIVE-GATE",
                    "owner": "human/ops",
                    "reviewer": "Codex",
                    "status": "blocked",
                    "waiting_for": "ops",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": ["Human risk-owner + operator signoff"],
                    "next": "Awaiting risk-owner and operator signoff",
                    "last_update": "2026-06-01T00:00:00Z",
                    "task_class": "human_gate",
                    "non_dispatchable": True,
                    "allowed_workers": [],
                }
            ],
            "handoffs": [],
            "blockers": [
                {
                    "task_id": "PROD-WRITES-001-V2",
                    "owner": "human/ops",
                    "waiting_for": "ops",
                    "message": "Awaiting risk-owner and operator signoff",
                    "status": "open",
                    "created_at": "2026-06-01T00:00:00Z",
                }
            ],
            "workload": {},
            "workload_summary": {},
        }

        ai_status.validate_state(state)
        ai_status.recompute_agents(state)
        ai_status.recompute_workload(state)

        task = ai_status.get_task(state, "PROD-WRITES-001-V2")
        self.assertEqual(task["owner"], "Human/Ops")
        self.assertEqual(task["waiting_for"], "Human/Ops")
        self.assertEqual(state["blockers"][0]["owner"], "Human/Ops")
        self.assertEqual(state["blockers"][0]["waiting_for"], "Human/Ops")

        human_ops = ai_status.get_agent(state, "Human/Ops")
        self.assertEqual(human_ops["status"], "blocked")
        self.assertEqual(human_ops["current_task_ids"], ["PROD-WRITES-001-V2"])
        self.assertEqual(ai_status.get_agent(state, "Claude")["status"], "idle")
        self.assertEqual(state["workload"]["Human/Ops"], 0)
        self.assertEqual(state["workload_summary"]["Human/Ops"]["blocked"], 1)


class RuntimeWorkerLivenessTests(unittest.TestCase):
    def test_pid_is_alive_rejects_zombie_processes(self) -> None:
        with mock.patch.object(ai_status, "proc_pid_state", return_value="Z"):
            self.assertFalse(ai_status.pid_is_alive(1234))

    def test_normalize_runtime_workers_marks_zombie_running_worker_stale(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Review stale runtime",
                    "summary_zh": "確認 zombie worker 不會被 dashboard 當成 live。",
                    "owner": "Codex",
                    "reviewer": "Gemini2",
                    "status": "review_approved",
                    "depends_on": [],
                    "next": "Owner finalize",
                    "last_update": "2026-05-17T11:00:00Z",
                }
            ]
        }
        orchestrator_state = {
            "workers": {
                "gemini2-run": {
                    "task_id": "TASK-001",
                    "provider": "gemini2",
                    "logical_agent_id": "gemini2",
                    "status": "running",
                    "pid": 1234,
                    "last_event_at": "2026-05-17T11:03:15Z",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                }
            }
        }

        with mock.patch.object(ai_status, "proc_pid_state", return_value="Z"):
            workers = ai_status.normalize_runtime_workers(state, orchestrator_state)

        self.assertEqual(workers[0]["bucket"], "stale")
        self.assertFalse(workers[0]["is_live_runtime"])
        self.assertFalse(workers[0]["pid_alive"])
        self.assertEqual(workers[0]["pid_state"], "Z")


class PortableStateRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_default_canonical_document_layers_exclude_review_and_session_records(self) -> None:
        layers = ai_status.default_canonical_document_layers()
        flattened = ai_status.flatten_canonical_document_layers(layers)

        self.assertIn("DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md", flattened)
        self.assertIn("WORKBENCH_DELIVERY_BACKLOG.md", flattened)
        self.assertIn("DELIVERY_CLOSURE_AND_LOOP_STATES.md", flattened)
        self.assertIn("EXECUTION_PROOF_AND_MATURITY_LEVELS.md", flattened)
        self.assertNotIn("docs/reviews/2026-04-17-next-wave-implementation-plan.md", flattened)
        self.assertNotIn(
            "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md",
            flattened,
        )

    def test_sync_canonical_document_metadata_migrates_current_work_to_derived_layer(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            }
        }

        ai_status.sync_canonical_document_metadata(state)

        self.assertEqual(
            state["canonical_document_layers"]["L0 Collaboration & State"],
            [
                "AI_COLLABORATION_GUIDE.md",
                "ai-status.json",
                "ai-activity-log.jsonl",
            ],
        )
        self.assertEqual(
            state["canonical_document_layers"]["L0.5 Derived Narrative"],
            ["current-work.md"],
        )

    def test_sync_canonical_document_metadata_backfills_new_default_l2_documents(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "CANONICAL_DOCUMENT_MAP.md",
                    "DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md",
                    "ROADMAP.md",
                    "DEVELOPMENT_WORKBREAKDOWN.md",
                    "OSS_INTEGRATION_CHECKLIST.md",
                ],
            }
        }

        ai_status.sync_canonical_document_metadata(state)

        self.assertIn(
            "WORKBENCH_DELIVERY_BACKLOG.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )
        self.assertIn(
            "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )
        self.assertIn(
            "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
            state["canonical_document_layers"]["L2 Planning & Execution"],
        )

    def test_build_onboarding_prompt_follows_state_canonical_files(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            }
        }

        prompt = ai_status.build_onboarding_prompt(state)

        self.assertIn("Read AI_COLLABORATION_GUIDE.md, ai-status.json", prompt)
        self.assertIn("Use current-work.md as a human summary only", prompt)
        self.assertIn("Use ai-activity-log.jsonl only when you need targeted recent history.", prompt)
        self.assertIn("TARGET_ARCHITECTURE.md", prompt)

    def test_write_current_work_uses_generic_delivery_sections(self) -> None:
        state = {
            "updated_at": "2026-04-10T00:00:00Z",
            "objective": "Stand up a portable delivery system.",
            "sprint": "2026-04-10-bootstrap",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L1 Runtime & Dashboard": [
                    "docs-site/index.html",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": ["review"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "DEMO-001",
                    "title": "First migrated task",
                    "summary_zh": "建立第一個遷移任務。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Implement foundation task",
                    "last_update": "2026-04-10T00:00:00Z",
                },
                {
                    "id": "OSS-001",
                    "title": "Verify external integration",
                    "summary_zh": "驗證外部整合。",
                    "phase": "Integration",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Queue external validation",
                    "last_update": "2026-04-10T00:00:00Z",
                },
                {
                    "id": "P2-OSS-ACTIVATE-001",
                    "title": "Research OSS production activation after fail-closed gates",
                    "summary_zh": "確認外部資料串接 activation gate。",
                    "phase": "P2 Wave 7",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "status": "todo",
                    "depends_on": ["P0-CI-BOUNDED-001"],
                    "artifacts": ["services/source_ingestion", "services/search"],
                    "next": "Assignment created",
                    "last_update": "2026-04-10T00:00:00Z",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": "2026-04-10T01:00:00Z",
                        "counts": {"total": 1, "completed": 1, "superseded": 0},
                        "recent_terminal_ids": ["DONE-001"],
                    },
                ),
                mock.patch.object(
                    ai_status,
                    "recent_terminal_summaries",
                    return_value=[
                        {
                            "task_id": "DONE-001",
                            "title": "Executed task",
                            "phase": "Archive",
                            "owner": "Codex",
                            "terminal_outcome": "completed",
                            "archived_at": "2026-04-10T01:00:00Z",
                            "snapshot_path": "ai-task-archive/tasks/DONE-001.json",
                        }
                    ],
                ),
            ):
                ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("### Primary Project Work", content)
        self.assertIn("### External / Upstream Integration Work", content)
        self.assertIn("`P2-OSS-ACTIVATE-001`", content)
        self.assertIn("## Recently Executed Tasks", content)
        self.assertIn("`DONE-001`", content)
        self.assertIn("`ai-task-archive/tasks/DONE-001.json`", content)
        self.assertNotIn("### Pantheon Product Work", content)
        self.assertIn("Canonical map", content)
        self.assertIn("Workbench backlog", content)
        self.assertIn("Loop closure", content)
        self.assertIn("Execution proof", content)
        self.assertIn("- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Runtime & Dashboard`", content)

    def test_write_current_work_flags_status_writes_queued_behind_integrity_block(
        self,
    ) -> None:
        state = {
            "updated_at": "2026-08-06T00:00:00Z",
            "objective": "Keep the board honest about queued status writes.",
            "sprint": "2026-08-06-outbox",
            "canonical_document_layers": {
                "L0 Collaboration & State": ["ai-status.json"],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": ["review"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "STALE-001",
                    "title": "Task whose write-back is queued",
                    "summary_zh": "狀態寫入排隊中。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "-",
                    "last_update": "2026-08-06T00:00:00Z",
                    "status_write_pending": True,
                    "status_write_pending_count": 2,
                },
                {
                    "id": "QUIET-001",
                    "title": "Task nobody touched",
                    "summary_zh": "沒有人動過。",
                    "phase": "Foundation",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "-",
                    "last_update": "2026-08-06T00:00:00Z",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        content = self._render_current_work(state)

        # A reader can tell a stale row from an untouched one.
        self.assertIn("## Status Write Backlog", content)
        self.assertIn("| `STALE-001` | Codex | in_progress | 2 |", content)
        self.assertIn("in_progress (stale: 2 writes queued)", content)
        self.assertNotIn("QUIET-001` | Claude | in_progress | ", content)

    def test_write_current_work_omits_backlog_section_without_pending_writes(
        self,
    ) -> None:
        state = {
            "updated_at": "2026-08-06T00:00:00Z",
            "objective": "Keep the board honest about queued status writes.",
            "sprint": "2026-08-06-outbox",
            "canonical_document_layers": {
                "L0 Collaboration & State": ["ai-status.json"],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": ["review"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "QUIET-001",
                    "title": "Task nobody touched",
                    "summary_zh": "沒有人動過。",
                    "phase": "Foundation",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "-",
                    "last_update": "2026-08-06T00:00:00Z",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        content = self._render_current_work(state)

        self.assertNotIn("## Status Write Backlog", content)
        self.assertNotIn("stale:", content)

    def _render_current_work(self, state: dict) -> str:
        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": "2026-08-06T00:00:00Z",
                        "counts": {"total": 0, "completed": 0, "superseded": 0},
                        "recent_terminal_ids": [],
                    },
                ),
                mock.patch.object(
                    ai_status, "recent_terminal_summaries", return_value=[]
                ),
            ):
                ai_status.write_current_work(state, [])
            return output_path.read_text(encoding="utf-8")

    def test_write_current_work_formats_absolute_times_in_taiwan_time(self) -> None:
        state = {
            "updated_at": "2026-04-10T00:00:00Z",
            "objective": "Track the queue and resume work before 2026-04-10T01:30:00Z.",
            "sprint": "2026-04-10-bootstrap",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "Resume at 2026-04-10T01:45:00Z.", "last_update": None},
            ],
            "tasks": [
                {
                    "id": "DEMO-002",
                    "title": "Timezone rendering",
                    "summary_zh": "確認人類可讀時間會轉成台灣時間。",
                    "phase": "Foundation",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "review",
                    "depends_on": [],
                    "next": "Waiting until 2026-04-10T02:15:00Z.",
                    "last_update": "2026-04-10T02:00:00Z",
                    "review_notes_zh": ["Reviewer checked the handoff at 2026-04-10T02:30:00Z."],
                    "review_file": "reviews/demo-002.md",
                },
            ],
            "handoffs": [
                {
                    "task_id": "DEMO-002",
                    "from": "Codex",
                    "to": "Claude",
                    "message": "Please review before 2026-04-10T02:20:00Z.",
                    "status": "pending",
                    "created_at": "2026-04-10T02:05:00Z",
                }
            ],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        logs = [
            {
                "ts": "2026-04-10T02:10:00Z",
                "agent": "Codex",
                "task_id": "DEMO-002",
                "message": "Paused until 2026-04-10T02:40:00Z.",
            },
            {
                "ts": "2026-04-10T02:11:00Z",
                "agent": "Orchestrator",
                "type": "worker_started",
                "task_id": "DEMO-002",
            },
        ]

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-taipei-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                ai_status.write_current_work(state, logs)

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("Absolute times below use 台灣時間 (UTC+8).", content)
        self.assertIn("Last updated: 2026-04-10 08:00:00", content)
        self.assertIn("Track the queue and resume work before 2026-04-10 09:30:00.", content)
        self.assertIn("Resume at 2026-04-10 09:45:00.", content)
        self.assertIn("| `DEMO-002` | Foundation | Timezone rendering |", content)
        self.assertIn("| review | - | 2026-04-10 10:00:00 | Waiting until 2026-04-10 10:15:00. |", content)
        self.assertIn("| `DEMO-002` | Codex | Claude | Please review before 2026-04-10 10:20:00. | pending | 2026-04-10 10:05:00 |", content)
        self.assertIn("Reviewer checked the handoff at 2026-04-10 10:30:00.", content)
        self.assertIn("- 2026-04-10 10:10:00 Codex: `DEMO-002` Paused until 2026-04-10 10:40:00.", content)
        self.assertIn("- 2026-04-10 10:11:00 Orchestrator: `DEMO-002` worker_started", content)

    def test_write_current_work_tolerates_structured_log_entries_without_message(self) -> None:
        state = {
            "updated_at": "2026-05-17T16:24:00Z",
            "objective": "Keep generated status views robust.",
            "sprint": "2026-05-17-status-sync",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
            },
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        logs = [
            {
                "ts": "2026-05-17T16:24:21Z",
                "agent": "Codex2",
                "type": "worker_commit",
                "task_id": "OODA-E2E-002",
                "commit": "abcdef1234567890",
                "scope": ["tests/e2e/test_demo.py"],
            }
        ]

        with tempfile.TemporaryDirectory(prefix="ai-status-current-work-structured-log-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with (
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path),
                mock.patch.object(
                    ai_status,
                    "load_archive_index",
                    return_value={
                        "updated_at": None,
                        "counts": {"total": 0, "completed": 0, "superseded": 0},
                        "recent_terminal_ids": [],
                    },
                ),
                mock.patch.object(ai_status, "recent_terminal_summaries", return_value=[]),
            ):
                ai_status.write_current_work(state, logs)

            content = output_path.read_text(encoding="utf-8")

        self.assertIn(
            "- 2026-05-18 00:24:21 Codex2: `OODA-E2E-002` "
            "worker_commit: commit abcdef123456; scope `tests/e2e/test_demo.py`",
            content,
        )

    def test_build_onboarding_prompt_mentions_active_planning(self) -> None:
        state = {
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "docs/02-architecture/consensus/phase1/README.md",
                    "docs/02-architecture/consensus/phase1/planning-session.json",
                ],
            }
        }

        with mock.patch.object(ai_status, "load_planning_state", return_value={"status": "active"}):
            prompt = ai_status.build_onboarding_prompt(state)

        self.assertIn("Discussion planning is active", prompt)
        self.assertIn("docs/02-architecture/consensus/phase1/README.md", prompt)
        self.assertIn("docs/02-architecture/consensus/phase1/planning-session.json", prompt)

    def test_write_current_work_includes_planning_snapshot(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Stand up a planning-aware control plane.",
            "sprint": "2026-04-11-planning-mode",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
                "L2 Planning & Execution": [
                    "docs/02-architecture/consensus/phase1/README.md",
                    "docs/02-architecture/consensus/phase1/planning-session.json",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "baton_owner": "Codex",
            "current_round": 1,
            "consensus_status": "draft",
            "human_gate_status": "pending",
            "switch_gate": {
                "ready_for_human": False,
                "ready_to_materialize": False,
            },
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-planning-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("## Discussion Planning", content)
        self.assertIn("phase1-2026-04-11", content)
        self.assertIn("`active`", content)

    def test_write_current_work_keeps_active_planning_session_out_of_canonical_files(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Keep planning records separate from blueprint truth.",
            "sprint": "2026-04-11-planning-boundary",
            "canonical_document_layers": ai_status.default_canonical_document_layers(),
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {
            "session_id": "phase6-2026-04-16-oss-ecosystem-closure",
            "status": "accepted",
            "artifacts": {
                "planning_readme": {
                    "path": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md"
                },
                "planning_session": {
                    "path": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json"
                },
            },
            "session_file": "docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json",
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-planning-boundary-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn(
            "- Planning mode: `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md`",
            content,
        )
        self.assertNotIn(
            "`docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/README.md`,",
            content.split("- Canonical files: ", 1)[1].split("\n", 1)[0],
        )

    def test_write_current_work_includes_lovable_coordination_snapshot(self) -> None:
        state = {
            "updated_at": "2026-04-11T00:00:00Z",
            "objective": "Track cross-repo Lovable delivery.",
            "sprint": "2026-04-11-lovable-loop",
            "canonical_document_layers": {
                "L0 Collaboration & State": [
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-status.json",
                    "ai-activity-log.jsonl",
                ],
                "L0.5 Derived Narrative": [
                    "current-work.md",
                ],
            },
            "agents": [
                {"name": "Codex", "capability_lane": ["integration"], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
            ],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }

        orchestrator_state = {
            "coordination": {
                "last_scan_at": "2026-04-11T02:30:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/F-042-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback bundle ready"},
                                "updated_at": "2026-04-11T02:30:00Z",
                            }
                        },
                        "responses_by_type": {
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-11T02:00:00Z",
                            }
                        },
                    }
                },
            }
        }

        with tempfile.TemporaryDirectory(prefix="ai-status-lovable-current-work-") as temp_dir:
            output_path = Path(temp_dir) / "current-work.md"
            with mock.patch.object(ai_status, "CURRENT_WORK_FILE", output_path):
                with (
                    mock.patch.object(ai_status, "load_json_file", return_value=orchestrator_state),
                    mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
                    mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
                ):
                    ai_status.write_current_work(state, [])

            content = output_path.read_text(encoding="utf-8")

        self.assertIn("## Lovable Coordination", content)
        self.assertIn("Lovable-ready packets: `1`", content)
        self.assertIn("Frontend feedback returned: `1`", content)
        self.assertIn("| `F-042` | promotion-review | `frontend_feedback_received` | yes | yes | no | yes |", content)

    def test_build_dashboard_bundle_summarizes_truth_layers(self) -> None:
        state = {
            "updated_at": "2026-04-11T13:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "title": "Publish front-end handoff packet",
                    "summary_zh": "整理交接封包。",
                    "phase": "Planning Materialized",
                    "owner": "Copilot",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Assignment created",
                    "last_update": "2026-04-11T13:00:00Z",
                },
                {
                    "id": "APP-002-W2-READ-INCIDENT",
                    "title": "Incident response read surfaces",
                    "summary_zh": "補 incident read view。",
                    "phase": "Planning Materialized",
                    "owner": "Qwen",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "next": "Reviewer validating read model",
                    "last_update": "2026-04-11T13:00:00Z",
                },
            ],
        }
        planning_state = {
            "status": "accepted",
            "session_id": "phase2-2026-04-13-blueprint-gap",
            "planning_dir": "docs/02-architecture/consensus/phase2",
            "session_file": "docs/02-architecture/consensus/phase2/planning-session.json",
            "runtime_mode": "supervisor_managed_execution",
            "consensus_status": "accepted",
            "human_gate_status": "approved",
            "counts": {"readouts_resolved": 5, "open_items": 0},
            "artifacts": {
                "consensus_packet": {"path": "docs/02-architecture/consensus/phase2/consensus-packet.md"},
                "execution_materialization": {"path": "docs/02-architecture/consensus/phase2/execution-materialization.md"},
            },
            "materialization_contract": {
                "source_plane": "planning",
                "session_id": "phase2-2026-04-13-blueprint-gap",
                "phase": "phase2",
                "planning_dir": "docs/02-architecture/consensus/phase2",
                "session_file": "docs/02-architecture/consensus/phase2/planning-session.json",
                "consensus_packet": "docs/02-architecture/consensus/phase2/consensus-packet.md",
                "execution_materialization": "docs/02-architecture/consensus/phase2/execution-materialization.md",
            },
            "proposed_execution_tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "source_plane": "planning",
                    "source_ref": {"session_id": "phase2-2026-04-13-blueprint-gap"},
                },
                {
                    "id": "APP-002-W2-READ-INCIDENT",
                    "source_plane": "planning",
                    "source_ref": {"session_id": "phase2-2026-04-13-blueprint-gap"},
                },
                {"id": "APP-002-W5-SSE-LIVE"},
            ],
        }
        orchestrator_state = {
            "supervisor": {"pid": 294672, "last_heartbeat_at": "2026-04-11T13:08:22Z"},
            "queue": {"events": {}},
            "workers": {
                "copilot-run-1": {
                    "task_id": "APP-002-W1-FRONT-HANDOFF",
                    "queue_event_id": "evt-1",
                    "agent_id": "copilot",
                    "provider": "copilot",
                    "status": "running",
                    "last_event_at": "2026-04-11T13:08:21Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {
            "APP-002-W1-FRONT-HANDOFF": state["tasks"][0],
            "APP-002-W2-READ-INCIDENT": state["tasks"][1],
        }
        resolver.dependency_status.side_effect = lambda task_id: "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: False
        resolver.get.side_effect = lambda task_id: {
            "APP-002-W1-FRONT-HANDOFF": state["tasks"][0],
            "APP-002-W2-READ-INCIDENT": state["tasks"][1],
        }.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["focus_mode"], "execution")
        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["runtime_summary"]["dispatch_targets"]["Codex"], 35)
        self.assertEqual(bundle["runtime_summary"]["dispatch_targets"]["Gemini"], 5)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["in_review"], 1)
        self.assertEqual(bundle["planning_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["source_plane"], "planning")
        self.assertEqual(bundle["bridge_summary"]["session_id"], "phase2-2026-04-13-blueprint-gap")
        self.assertEqual(bundle["bridge_summary"]["materialized_count"], 2)
        self.assertEqual(bundle["bridge_summary"]["pending_materialization_count"], 1)
        self.assertEqual(bundle["bridge_summary"]["consensus_packet"], "docs/02-architecture/consensus/phase2/consensus-packet.md")
        self.assertEqual(len(bundle["truth_mismatches"]), 1)
        self.assertEqual({item["type"] for item in bundle["truth_mismatches"]}, {"running_worker_on_todo"})
        mismatch_hints = {item["type"]: item["resolution_hint"] for item in bundle["truth_mismatches"]}
        self.assertIn("先把 task 狀態推成 in_progress", mismatch_hints["running_worker_on_todo"])
        self.assertEqual(bundle["worker_task_links"][0]["task_id"], "APP-002-W1-FRONT-HANDOFF")
        self.assertEqual(bundle["worker_task_links"][0]["task_title"], "Publish front-end handoff packet")
        self.assertEqual(bundle["worker_task_links"][0]["task_summary"], "整理交接封包。")
        self.assertEqual(bundle["worker_task_links"][0]["queue_status"], None)
        self.assertEqual(bundle["worker_task_links"][0]["mismatch_count"], 1)
        self.assertIn("running_worker_on_todo", bundle["worker_task_links"][0]["mismatch_flags"])
        self.assertTrue(bundle["worker_task_links"][0]["resolution_hints"])

    def test_dashboard_flags_internal_approval_without_github_gate_evidence(self) -> None:
        binding = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "head_branch": "task/AUDIT-001",
            "base": "dev",
        }
        task = {
            "id": "AUDIT-001",
            "owner": "Codex",
            "reviewer": "Codex2",
            "status": "review_approved",
            "review_binding": binding,
            "last_update": "2026-07-27T21:21:10Z",
        }
        resolver = mock.Mock()
        resolver.source.return_value = "active"

        _workers, mismatches = ai_status.detect_truth_mismatches(
            {"tasks": [task]},
            [],
            [],
            {"pending": []},
            resolver,
            {},
        )

        mismatch = next(
            item for item in mismatches
            if item["type"] == "github_review_gate_missing"
        )
        self.assertEqual(
            mismatch["id"],
            "github-review-gate-missing:AUDIT-001",
        )
        self.assertEqual(mismatch["severity"], "high")
        self.assertIn("不得把 internal review_approved", mismatch["resolution_hint"])

    def test_dashboard_accepts_matching_branch_policy_review_evidence(self) -> None:
        binding = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "head_branch": "task/AUDIT-001",
            "base": "dev",
        }
        task = {
            "id": "AUDIT-001",
            "owner": "Codex",
            "reviewer": "Codex2",
            "status": "review_approved",
            "review_binding": binding,
            "github_review_bridge": {
                "repository": "ajoe734/pantheon",
                **binding,
                "decision": "approve",
                "actor": "Codex2",
                "mode": "required_commit_status",
                "status_id": 101,
                "status_context": ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT,
                "status_state": "success",
            },
            "last_update": "2026-07-27T21:21:10Z",
        }
        resolver = mock.Mock()
        resolver.source.return_value = "active"

        _workers, mismatches = ai_status.detect_truth_mismatches(
            {"tasks": [task]},
            [],
            [],
            {"pending": []},
            resolver,
            {},
        )

        self.assertNotIn(
            "github_review_gate_missing",
            {item["type"] for item in mismatches},
        )

    def test_dashboard_flags_merged_delivery_that_still_needs_closeout(self) -> None:
        task = {
            "id": "L12-BFF-001",
            "owner": "Codex2",
            "reviewer": "Codex",
            "status": "in_progress",
            "source_ref": {
                "pr": 4274,
                "head_sha": "c" * 40,
                "base": "dev",
            },
            "next": (
                "Human/Ops delivery update: PR #4274 exact head "
                f"{'4' * 40} passed local focused suite; merged to dev as "
                f"{'7' * 40} at 2026-07-27T22:14:45Z. Canonical task "
                "still needs formal closeout evidence/archive."
            ),
            "last_update": "2026-07-27T22:15:09Z",
        }
        resolver = mock.Mock()
        resolver.source.return_value = "active"

        _workers, mismatches = ai_status.detect_truth_mismatches(
            {"tasks": [task]},
            [],
            [],
            {"pending": []},
            resolver,
            {},
        )

        by_type = {item["type"]: item for item in mismatches}
        self.assertEqual(
            by_type["delivery_merged_needs_closeout"]["id"],
            "delivery-merged-needs-closeout:L12-BFF-001",
        )
        self.assertEqual(
            by_type["delivery_merged_needs_closeout"]["delivery_evidence"]["merge_commit"],
            "7" * 40,
        )
        self.assertIn("正式 closeout", by_type["delivery_merged_needs_closeout"]["resolution_hint"])
        self.assertEqual(
            by_type["delivery_binding_stale"]["delivery_evidence"]["recorded_head_sha"],
            "c" * 40,
        )
        self.assertEqual(
            by_type["delivery_binding_stale"]["delivery_evidence"]["evidence_head_sha"],
            "4" * 40,
        )

    def test_dashboard_uses_structured_delivery_evidence_for_closeout_drift(self) -> None:
        task = {
            "id": "L12-DIST-001",
            "owner": "Codex2",
            "reviewer": "Codex",
            "status": "review",
            "delivery": {
                "head_merged_to_target": True,
                "merge_target_branch": "dev",
                "merge_target_sha": "1" * 40,
            },
            "last_update": "2026-07-27T22:10:42Z",
        }
        resolver = mock.Mock()
        resolver.source.return_value = "active"

        _workers, mismatches = ai_status.detect_truth_mismatches(
            {"tasks": [task]},
            [],
            [],
            {"pending": []},
            resolver,
            {},
        )

        mismatch = next(
            item for item in mismatches
            if item["type"] == "delivery_merged_needs_closeout"
        )
        self.assertEqual(mismatch["severity"], "high")
        self.assertEqual(mismatch["delivery_evidence"]["source"], "delivery")
        self.assertEqual(mismatch["delivery_evidence"]["merge_commit"], "1" * 40)

    def test_related_live_sidecar_worker_does_not_flag_parent_as_without_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T15:32:45Z",
            "agents": [
                {"name": "Codex", "status": "busy", "current_task_ids": ["BP5-SVC-001"], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
                {"name": "Claude", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
                {"name": "Gemini", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "title": "Lock the deployable service baseline and single-VM topology",
                    "summary_zh": "主線 baseline 定義。",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Supervisor auto-started BP5-SVC-001 after successful dispatch.",
                    "last_update": "2026-04-15T15:29:37Z",
                },
                {
                    "id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "title": "Prepare BP5-SVC-001 acceptance packet and dependency map",
                    "summary_zh": "Sidecar review 中。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": ["support/sidecars/BP5-SVC-001/BP5-SVC-001-SIDECAR-ACCEPTANCE.md"],
                    "acceptance": [],
                    "next": "Acceptance packet handed off to Codex for review.",
                    "last_update": "2026-04-15T15:32:28Z",
                    "task_class": "sidecar",
                    "auto_generated": True,
                    "helper_parent": "BP5-SVC-001",
                    "helper_kind": "acceptance_packet",
                },
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T15:32:45Z"},
            "queue": {"events": {"evt-1": {"status": "started", "run_id": "codex-20260415T153233Z-97359030", "processed_at": "2026-04-15T15:32:33Z"}}},
            "workers": {
                "codex-20260415T153233Z-97359030": {
                    "run_id": "codex-20260415T153233Z-97359030",
                    "task_id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "queue_event_id": "evt-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "last_event_at": "2026-04-15T15:32:47Z",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "done"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-001", mismatch_ids)

    def test_paused_reviewer_does_not_flag_review_task_without_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T16:35:29Z",
            "agents": [
                {"name": "Claude", "status": "working", "current_task_ids": ["BP5-SVC-002"], "branch": "", "next": "", "last_update": "2026-04-15T16:35:29Z"},
                {"name": "Qwen", "status": "blocked", "current_task_ids": ["BP5-LUV-001"], "branch": "", "next": "", "last_update": "2026-04-15T16:35:29Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-002",
                    "title": "Registry review",
                    "summary_zh": "等待 reviewer 檢查。",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Ready for Qwen review.",
                    "last_update": "2026-04-15T16:35:29Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T16:35:46Z"},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2099-04-15T16:38:40Z",
                        "summary": "Capacity / rate limit failure",
                    }
                }
            },
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "review" if task_id == "BP5-SVC-002" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-002", mismatch_ids)

    def test_review_task_without_live_worker_does_not_flag_truth_mismatch(self) -> None:
        state = {
            "updated_at": "2026-04-16T06:50:44Z",
            "agents": [
                {"name": "Claude", "status": "reviewing", "current_task_ids": ["BP5-LUV-007"], "branch": "", "next": "", "last_update": "2026-04-16T06:50:44Z"},
                {"name": "Codex", "status": "working", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-16T06:50:44Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-LUV-007",
                    "title": "Lovable lineage review",
                    "summary_zh": "等待 reviewer 接手。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Review notes already prepared; waiting in review queue.",
                    "last_update": "2026-04-16T06:50:44Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-16T06:53:55Z"},
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "review" if task_id == "BP5-LUV-007" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-LUV-007", mismatch_ids)

    def test_coordination_worker_missing_taskboard_entry_does_not_flag_truth_mismatch(self) -> None:
        state = {
            "updated_at": "2026-04-16T06:53:55Z",
            "agents": [],
            "tasks": [],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-16T06:53:55Z"},
            "queue": {"events": {}},
            "workers": {
                "codex-1": {
                    "run_id": "codex-1",
                    "task_id": "PKT-002-incident-action-drawer",
                    "queue_event_id": "coord-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "last_event_at": "2026-04-16T06:53:55Z",
                    "request_snapshot": {
                        "reason": "coordination:bff-gap",
                        "metadata": {
                            "coordination": {
                                "feature_id": "PKT-002-incident-action-drawer",
                                "payload_type": "bff-gap",
                            }
                        },
                    },
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {}
        resolver.dependency_status.side_effect = lambda task_id: "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: False
        resolver.get.side_effect = lambda task_id: None
        resolver.source.side_effect = lambda task_id: None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("worker-task-missing:codex-1", mismatch_ids)

    def test_pending_approval_task_does_not_flag_without_live_worker(self) -> None:
        state = {
            "updated_at": "2026-04-15T16:41:31Z",
            "agents": [
                {"name": "Claude", "status": "working", "current_task_ids": ["BP5-SVC-003"], "branch": "", "next": "", "last_update": "2026-04-15T16:41:31Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-003",
                    "title": "Governance API fixup",
                    "summary_zh": "等待 approval 後續跑。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                    "next": "Waiting for safe verification approval.",
                    "last_update": "2026-04-15T16:41:31Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "workload": {},
            "workload_summary": {},
        }
        planning_state = {"mode": "execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 123, "last_heartbeat_at": "2026-04-15T16:41:31Z"},
            "queue": {"events": {}},
            "workers": {},
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "BP5-SVC-003",
                    "worker_run_id": "claude-run-1",
                }
            ],
            "history": [],
        }

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {task["id"]: task for task in state["tasks"]}
        resolver.dependency_status.side_effect = lambda task_id: "in_progress" if task_id == "BP5-SVC-003" else "missing"
        resolver.dependency_satisfied.side_effect = lambda task_id: True
        resolver.get.side_effect = lambda task_id: {task["id"]: task for task in state["tasks"]}.get(task_id)
        resolver.source.side_effect = lambda task_id: "active" if task_id in resolver.active_task_map.return_value else None

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-SVC-003", mismatch_ids)

    def test_write_dashboard_bundle_persists_json_artifact(self) -> None:
        state = {
            "updated_at": "2026-04-11T13:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "APP-002-W1-FRONT-HANDOFF",
                    "title": "Publish front-end handoff packet",
                    "summary_zh": "整理交接封包。",
                    "phase": "Planning Materialized",
                    "owner": "Copilot",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Working",
                    "last_update": "2026-04-11T13:00:00Z",
                },
            ],
        }
        config = {"paths": {"state_file": ".orchestrator/state.json", "event_queue": ".orchestrator/event-queue.jsonl"}}
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {"supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-11T13:08:22Z"}, "queue": {"events": {}}, "workers": {}}
        approval_state = {"pending": [], "history": []}

        with tempfile.TemporaryDirectory(prefix="ai-status-dashboard-bundle-") as temp_dir:
            output_path = Path(temp_dir) / "dashboard-bundle.json"
            with mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", output_path):
                with mock.patch.object(ai_status, "load_config", return_value=config):
                    with mock.patch.object(ai_status, "load_planning_state", return_value=planning_state):
                        with mock.patch.object(ai_status, "load_runtime_state", return_value=orchestrator_state) as load_runtime_state:
                            with mock.patch.object(ai_status, "load_json_file", return_value=approval_state) as load_json_file:
                                ai_status.write_dashboard_bundle(state)

            bundle = json.loads(output_path.read_text(encoding="utf-8"))

        load_runtime_state.assert_called_once_with(config)
        load_json_file.assert_called_once_with(ai_status.APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
        self.assertEqual(bundle["runtime_summary"]["supervisor_pid"], 1)
        self.assertEqual(bundle["execution_summary"]["in_progress"], 1)
        self.assertEqual(bundle["focus_mode"], "execution")
        self.assertIn("worker_task_links", bundle)
        self.assertIn("truth_mismatches", bundle)

    def test_build_dashboard_bundle_reads_terminal_counts_from_archive_summary(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "REG-101",
                    "title": "Still active",
                    "summary_zh": "等待已封存依賴。",
                    "phase": "Epic X",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": ["REG-100"],
                    "next": "Ready to start",
                    "last_update": "2026-04-14T02:00:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "recent_terminal_tasks": [
                {
                    "task_id": "REG-100",
                    "terminal_outcome": "completed",
                    "archived_at": "2026-04-14T01:59:00Z",
                }
            ],
        }
        approval_state = {"pending": [], "history": []}

        resolver = mock.Mock()
        resolver.active_task_map.return_value = {"REG-101": state["tasks"][0]}
        resolver.dependency_status.side_effect = lambda task_id: "done" if task_id == "REG-100" else "todo"
        resolver.dependency_satisfied.side_effect = lambda task_id: task_id == "REG-100"
        resolver.get.side_effect = lambda task_id: state["tasks"][0] if task_id == "REG-101" else {"id": "REG-100", "status": "done"}
        resolver.source.side_effect = lambda task_id: "active" if task_id == "REG-101" else "archive"

        with (
            mock.patch.object(ai_status, "task_resolver", return_value=resolver),
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={
                    "updated_at": "2026-04-14T02:00:00Z",
                    "counts": {"total": 3, "completed": 2, "superseded": 1},
                    "recent_terminal_ids": ["REG-100"],
                },
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["done"], 2)
        self.assertEqual(bundle["execution_summary"]["superseded"], 1)
        self.assertEqual(bundle["archive_summary"]["recent_terminal_ids"], ["REG-100"])
        self.assertEqual(bundle["archive_summary"]["recent_terminal_tasks"][0]["task_id"], "REG-100")

    def test_build_dashboard_bundle_distinguishes_dependency_ready_from_dispatchable_ready(self) -> None:
        state = {
            "updated_at": "2026-04-16T08:50:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "RUN-001",
                    "title": "Running task",
                    "summary_zh": "Claude lane 已占用。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Still running",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CLAUDE",
                    "title": "Ready but owner busy",
                    "summary_zh": "依賴都完成，但 Claude 已忙碌。",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CODEX",
                    "title": "Ready but provider paused",
                    "summary_zh": "依賴都完成，但 Codex 正在 pause。",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z", "focus_mode": "execution"},
            "queue": {"events": {}},
            "workers": {
                "claude-run": {
                    "run_id": "claude-run",
                    "provider": "claude",
                    "task_id": "RUN-001",
                    "status": "running",
                    "last_event_at": "2026-04-16T08:50:04Z",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                }
            },
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "paused_at": "2026-04-16T08:45:00Z",
                        "blocked_until": "2026-04-16T09:00:00Z",
                        "reason": "402 You have no quota",
                    }
                }
            },
        }
        approval_state = {"pending": [], "history": []}
        config = {"ready_dispatcher": {"max_tasks_per_agent_by_agent": {"Claude": 1}}}

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 2)

    def test_build_dashboard_bundle_counts_ready_capacity_when_owner_has_free_slots(self) -> None:
        state = {
            "updated_at": "2026-04-16T08:50:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "RUN-CODEX",
                    "title": "Running Codex task",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "in_progress",
                    "depends_on": [],
                    "next": "Running",
                    "last_update": "2026-04-16T08:50:00Z",
                },
                {
                    "id": "TODO-CODEX",
                    "title": "Ready Codex task",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "next": "Ready to start",
                    "last_update": "2026-04-16T08:50:00Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z", "focus_mode": "execution"},
            "queue": {"events": {}},
            "workers": {
                "codex-run": {
                    "run_id": "codex-run",
                    "logical_agent_id": "codex",
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "task_id": "RUN-CODEX",
                    "status": "running",
                    "last_event_at": "2026-04-16T08:50:04Z",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                }
            },
            "provider_guardrails": {"dispatch_pauses": {}},
        }
        approval_state = {"pending": [], "history": []}
        config = {
            "ready_dispatcher": {
                "max_tasks_per_agent_by_agent": {"Codex": 2},
                "max_concurrent_per_account": {"codex1": 4},
            }
        }

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["dispatch_policy"]["max_tasks_per_agent"], None)
        self.assertEqual(bundle["dispatch_policy"]["max_tasks_per_agent_by_agent"], {"Codex": 2})
        self.assertEqual(bundle["dispatch_policy"]["max_concurrent_per_account"], {"codex1": 4})
        self.assertEqual(bundle["dispatch_policy"]["max_concurrent_per_quota_group"], {"codex1": 4})

    def test_build_dashboard_bundle_includes_coordination_summary(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/F-042-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/F-042-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        summary = bundle["coordination_summary"]
        self.assertEqual(summary["last_scan_at"], "2026-04-14T02:04:00Z")
        self.assertEqual(summary["counts"]["tracked_features"], 1)
        self.assertEqual(summary["counts"]["lovable_ready"], 1)
        self.assertEqual(summary["counts"]["ui_done_received"], 1)
        self.assertEqual(summary["counts"]["frontend_feedback_received"], 1)
        self.assertEqual(summary["counts"]["waiting_for_lovable"], 0)
        self.assertEqual(summary["features"][0]["stage"], "frontend_feedback_received")
        self.assertTrue(summary["features"][0]["mirrored_to_target_repo"])
        self.assertEqual(summary["features"][0]["paths"]["frontend_feedback"], "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml")

    def test_build_dashboard_bundle_does_not_count_stale_bff_gap_after_frontend_feedback(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "PKT-003-lineage-view": {
                        "feature_id": "PKT-003-lineage-view",
                        "screen": "lineage-view",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                        "lovable_prompt_path": ".coordination/responses/PKT-003-lineage-view-lovable-prompt.md",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "bff-gap": {
                                "type": "bff-gap",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-bff-gap.yaml",
                                "payload": {"type": "bff-gap", "status": "blocked", "summary": "Old gap payload"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-lineage-view-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/PKT-003-lineage-view-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        summary = bundle["coordination_summary"]
        self.assertEqual(summary["counts"]["open_bff_gaps"], 0)
        self.assertEqual(summary["features"][0]["stage"], "frontend_feedback_received")
        self.assertFalse(summary["features"][0]["bff_gap_open"])
        self.assertTrue(summary["features"][0]["has_bff_gap"])

    def test_build_dashboard_bundle_marks_reviewed_frontend_feedback_when_review_packet_exists(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "lovable_task_path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI done"},
                                "updated_at": "2026-04-14T02:02:00Z",
                            },
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(
                ai_status,
                "coordination_review_snapshot",
                return_value={"path": ".coordination/reviews/F-042-review.md", "disposition": "approved"},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "frontend_feedback_reviewed")
        self.assertEqual(feature["review_disposition"], "approved")
        self.assertEqual(feature["paths"]["review"], ".coordination/reviews/F-042-review.md")

    def test_build_dashboard_bundle_prefers_closeout_response_for_loop_complete(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "EW-05-mutation-review": {
                        "feature_id": "EW-05-mutation-review",
                        "screen": "mutation-review",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/EW-05-mutation-review-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/EW-05-mutation-review-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "close",
                                    "can_close": True,
                                    "lovable_ui_task_status": "closed",
                                    "next_action": "none",
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "follow-up-required"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "load_local_coordination_payload", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "loop_complete")

    def test_build_dashboard_bundle_counts_pantheon_frontend_feedback_response_as_feedback_and_runtime_proof(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "KW-01-institutional-memory": {
                        "feature_id": "KW-01-institutional-memory",
                        "screen": "institutional-memory",
                        "summary": "Pantheon closeout proof ready",
                        "current_payload_type": "lovable-ui-task",
                        "source_repo": "ajoe734/pantheon",
                        "source_repo_id": "pantheon",
                        "target_agent": "Gemini",
                        "worker_kind": "runtime-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "ui-done": {
                                "type": "ui-done",
                                "path": "../front-ai-trading-system/.coordination/requests/KW-01-institutional-memory-ui-done.yaml",
                                "payload": {"type": "ui-done", "summary": "UI ready"},
                                "updated_at": "2026-04-14T02:03:30Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/KW-01-institutional-memory-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "close",
                                    "can_close": True,
                                    "runtime_verified_at": "2026-04-14T02:04:30Z",
                                    "verified_runtime_ref": ".coordination/reviews/KW-01-institutional-memory-review.md",
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "coordination_repo_root", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "loop_complete")
        self.assertTrue(feature["has_frontend_feedback"])
        self.assertEqual(feature["paths"]["frontend_feedback"], ".coordination/responses/KW-01-institutional-memory-frontend-feedback.yaml")
        self.assertTrue(feature["state_flags"]["runtime_verified"])
        self.assertEqual(bundle["coordination_summary"]["counts"]["frontend_feedback_received"], 1)
        self.assertEqual(bundle["coordination_summary"]["counts"]["runtime_verified"], 1)

    def test_build_dashboard_bundle_marks_closed_scope_when_followup_response_has_no_active_next_step(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "PKT-003-post-incident-review": {
                        "feature_id": "PKT-003-post-incident-review",
                        "screen": "post-incident-review-console",
                        "summary": "Feedback bundle ready",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                        },
                        "responses_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": ".coordination/responses/PKT-003-post-incident-review-frontend-feedback.yaml",
                                "payload": {
                                    "type": "frontend-feedback",
                                    "disposition": "follow-up-required",
                                    "can_close": False,
                                },
                                "updated_at": "2026-04-14T02:04:30Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/PKT-003-post-incident-review-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "closed"},
                                "updated_at": "2026-04-14T02:01:30Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "load_local_coordination_payload", return_value=None),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        self.assertEqual(feature["stage"], "closed")

    def test_build_dashboard_bundle_exposes_coordination_state_flags(self) -> None:
        state = {
            "updated_at": "2026-04-14T02:00:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T02:05:00Z"},
            "queue": {"events": {}},
            "workers": {},
            "coordination": {
                "last_scan_at": "2026-04-14T02:04:00Z",
                "features": {
                    "F-042": {
                        "feature_id": "F-042",
                        "screen": "promotion-review",
                        "summary": "Contract ready and mirrored",
                        "current_payload_type": "frontend-feedback",
                        "source_repo": "ajoe734/front-ai-trading-system",
                        "source_repo_id": "front_ai_trading_system",
                        "target_repo_id": "pantheon",
                        "target_agent": "Codex",
                        "worker_kind": "front-sync-worker",
                        "last_updated_at": "2026-04-14T02:04:00Z",
                        "last_dispatched_at": "2026-04-14T02:03:00Z",
                        "mirrored_to_target_repo": {"target_repo_id": "front_ai_trading_system"},
                        "requests_by_type": {
                            "frontend-feedback": {
                                "type": "frontend-feedback",
                                "path": "../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml",
                                "payload": {"type": "frontend-feedback", "summary": "Feedback ready"},
                                "updated_at": "2026-04-14T02:04:00Z",
                            },
                            "needs-runtime": {
                                "type": "needs-runtime",
                                "path": ".coordination/requests/F-042-needs-runtime.yaml",
                                "payload": {
                                    "type": "needs-runtime",
                                    "status": "resolved",
                                    "runtime_verified_at": "2026-04-14T02:02:30Z",
                                },
                                "updated_at": "2026-04-14T02:02:30Z",
                            },
                        },
                        "responses_by_type": {
                            "contract-ready": {
                                "type": "contract-ready",
                                "path": ".coordination/responses/F-042-contract-ready.yaml",
                                "payload": {"type": "contract-ready"},
                                "updated_at": "2026-04-14T02:00:00Z",
                            },
                            "lovable-ui-task": {
                                "type": "lovable-ui-task",
                                "path": ".coordination/responses/F-042-lovable-ui-task.yaml",
                                "payload": {"type": "lovable-ui-task", "status": "ready"},
                                "updated_at": "2026-04-14T02:01:00Z",
                            },
                        },
                    }
                },
            },
        }
        approval_state = {"pending": [], "history": []}

        with (
            mock.patch.object(
                ai_status,
                "load_archive_index",
                return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []},
            ),
            mock.patch.object(ai_status, "coordination_local_response_path", return_value=None),
            mock.patch.object(ai_status, "coordination_review_snapshot", return_value=None),
            mock.patch.object(ai_status, "coordination_repo_root", side_effect=lambda repo_id: Path(f"/tmp/{repo_id}")),
            mock.patch.object(
                ai_status,
                "coordination_repo_payload_exists",
                side_effect=lambda _root, rel_path: str(rel_path).endswith("F-042-contract-ready.yaml"),
            ),
            mock.patch.object(
                ai_status,
                "coordination_audit_matches",
                side_effect=lambda _root, _feature_id, marker: marker in {"dispatch-emitted", "received"},
            ),
        ):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        feature = bundle["coordination_summary"]["features"][0]
        flags = feature["state_flags"]
        self.assertEqual(flags["backend_route_live"], True)
        self.assertEqual(flags["pantheon_handoff_published"], True)
        self.assertEqual(flags["mirrored_to_front_default_branch"], True)
        self.assertEqual(flags["dispatch_emitted"], True)
        self.assertEqual(flags["front_receiver_applied"], True)
        self.assertEqual(flags["lovable_consumed"], True)
        self.assertEqual(flags["ui_activated"], True)
        self.assertEqual(flags["runtime_verified"], True)

        counts = bundle["coordination_summary"]["counts"]
        self.assertEqual(counts["backend_route_live"], 1)
        self.assertEqual(counts["pantheon_handoff_published"], 1)
        self.assertEqual(counts["mirrored_to_front_default_branch"], 1)
        self.assertEqual(counts["dispatch_emitted"], 1)
        self.assertEqual(counts["front_receiver_applied"], 1)
        self.assertEqual(counts["lovable_consumed"], 1)
        self.assertEqual(counts["ui_activated"], 1)
        self.assertEqual(counts["runtime_verified"], 1)

    def test_build_dashboard_bundle_treats_dead_suspended_approval_as_approval_wait_not_live_worker(self) -> None:
        state = {
            "updated_at": "2026-04-14T01:42:00Z",
            "agents": [],
            "tasks": [
                {
                    "id": "BG-005",
                    "title": "Define golden replay scenario and acceptance runbook",
                    "summary_zh": "定義 golden replay scenario 與 acceptance runbook。",
                    "phase": "Blueprint Gap P0",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "status": "review_approved",
                    "depends_on": ["BG-000"],
                    "next": "Supervisor resumed BG-005 for finalize after successful dispatch.",
                    "last_update": "2026-04-14T00:42:04Z",
                },
            ],
        }
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {
                "pid": 490443,
                "last_heartbeat_at": "2026-04-14T01:42:22Z",
                "mode_status": "active",
                "mode_occupancy": {
                    "planning": {"running": 0, "pending": 0, "queued": 0},
                    "execution": {"running": 0, "pending": 1, "queued": 0},
                    "coordination": {"running": 0, "pending": 0, "queued": 0},
                },
            },
            "queue": {
                "events": {
                    "evt-1": {
                        "status": "manual_pending",
                        "run_id": "claude-run-1",
                        "processed_at": "2026-04-14T00:42:04Z",
                    }
                }
            },
            "workers": {
                "claude-run-1": {
                    "task_id": "BG-005",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "suspended_approval",
                    "pid": 477808,
                    "last_event_at": "2026-04-14T00:42:46Z",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "BG-005",
                    "worker_run_id": "claude-run-1",
                    "provider": "claude",
                    "created_at": "2026-04-14T00:42:46Z",
                }
            ],
            "history": [],
        }

        with mock.patch.object(ai_status, "pid_is_alive", return_value=False):
            bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 0)
        self.assertEqual(bundle["runtime_summary"]["pending_workers"], 0)
        self.assertEqual(bundle["worker_task_links"], [])
        self.assertFalse(any(item["type"] == "queue_started_without_worker" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_skips_planning_approval_without_task_board_row(self) -> None:
        state = {
            "updated_at": "2026-04-14T05:35:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "active", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T05:35:00Z"},
            "queue": {"events": {}},
            "workers": {
                "claude-run-1": {
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "suspended_approval",
                    "last_event_at": "2026-04-14T05:35:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_readout",
                        "metadata": {"planning": {"mode": "discussion_planning"}},
                    },
                }
            },
        }
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "worker_run_id": "claude-run-1",
                    "provider": "claude",
                    "created_at": "2026-04-14T05:35:00Z",
                }
            ],
            "history": [],
        }

        bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertFalse(any(item["type"] == "approval_missing_task" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_skips_planning_worker_without_task_board_row(self) -> None:
        state = {
            "updated_at": "2026-04-14T05:37:00Z",
            "agents": [],
            "tasks": [],
        }
        planning_state = {"status": "active", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-14T05:37:00Z"},
            "queue": {"events": {}},
            "workers": {
                "claude-run-1": {
                    "task_id": "phase3-2026-04-14-pantheon-console-loop",
                    "queue_event_id": "evt-1",
                    "agent_id": "claude",
                    "provider": "claude",
                    "status": "running",
                    "pid": None,
                    "last_event_at": "2026-04-14T05:37:00Z",
                    "request_snapshot": {
                        "reason": "discussion_planning_round",
                        "metadata": {"planning": {"mode": "discussion_planning"}},
                    },
                }
            },
        }
        approval_state = {"pending": [], "history": []}

        bundle = ai_status.build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)

        self.assertFalse(any(item["type"] == "worker_task_missing" for item in bundle["truth_mismatches"]))


class CanonicalTaskStateAndActivityRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.addCleanup(_teardown_test_isolation, self)
        self.root = self._test_root
        self.status_file = self._test_status_file
        self.log_file = self._test_log_file
        self.log_file.unlink()
        self._task_state_env = mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "",
                ai_status.TASK_STATE_EVENT_LOG_ENV: "",
            },
            clear=False,
        )
        self._task_state_env.start()
        self.addCleanup(self._task_state_env.stop)

        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(self.root), check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(self.root), check=True)
        dummy_file = self.root / "dummy.txt"
        dummy_file.write_text("dummy", encoding="utf-8")
        subprocess.run(["git", "add", "dummy.txt"], cwd=str(self.root), check=True)
        subprocess.run(["git", "commit", "-m", "initial commit", "-q"], cwd=str(self.root), check=True)

    def _fixture_state(self) -> dict[str, object]:
        state = ai_status.default_state()
        state["tasks"] = [
            {
                "id": "LOCK-ONE",
                "title": "First task-state writer",
                "phase": "test",
                "owner": "Codex2",
                "reviewer": "Codex",
                "status": "in_progress",
                "depends_on": [],
                "artifacts": [],
                "acceptance": [],
                "next": "initial-one",
                "last_update": "2026-07-14T00:00:00Z",
            },
            {
                "id": "LOCK-TWO",
                "title": "Second task-state writer",
                "phase": "test",
                "owner": "Codex2",
                "reviewer": "Codex",
                "status": "in_progress",
                "depends_on": [],
                "artifacts": [],
                "acceptance": [],
                "next": "initial-two",
                "last_update": "2026-07-14T00:00:00Z",
            },
        ]
        return state

    def _write_state(self, state: dict[str, object]) -> None:
        self.status_file.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _outbox(events: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema_version": ai_status.STATUS_ACTIVITY_OUTBOX_SCHEMA_VERSION,
            "transaction_id": "ai-status-tx-" + ai_status._canonical_json_sha256(events),
            "events": events,
        }

    def _activity_rows_by_source(self) -> dict[Path, list[dict[str, object]]]:
        sources = sorted(
            (self.root / "archive" / "logs").glob(f"{self.log_file.name}-*.gz")
        )
        if self.log_file.exists():
            sources.append(self.log_file)
        result: dict[Path, list[dict[str, object]]] = {}
        for source in sources:
            if source.suffix == ".gz":
                with gzip.open(source, "rt", encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
            else:
                lines = source.read_text(encoding="utf-8").splitlines()
            result[source] = [json.loads(line) for line in lines if line.strip()]
        return result

    def _run_recovery(self, *, rotate_max_bytes: int, rotate_keep_lines: int) -> None:
        context = multiprocessing.get_context("fork")
        process = context.Process(
            target=_recover_pending_status_outbox,
            args=(
                str(self.status_file),
                str(self.log_file),
                rotate_max_bytes,
                rotate_keep_lines,
            ),
        )
        process.start()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
            self.fail("status outbox recovery process timed out")
        self.assertEqual(process.exitcode, 0)

    def test_legacy_done_archive_replay_and_recovery_are_exact_and_idempotent(
        self,
    ) -> None:
        state = self._fixture_state()
        terminal = state["tasks"][0]
        terminal["status"] = "done"
        terminal.pop("terminal_outcome", None)
        archive_dir = self.root / "ai-task-archive"
        archive_path = archive_dir / "tasks" / "LOCK-ONE.json"
        with (
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(task_archive, "STATUS_ROOT", self.root),
            mock.patch.object(task_archive, "STATUS_FILE", self.status_file),
            mock.patch.object(task_archive, "ARCHIVE_DIR", archive_dir),
            mock.patch.object(
                task_archive,
                "ARCHIVE_TASKS_DIR",
                archive_dir / "tasks",
            ),
            mock.patch.object(
                task_archive,
                "ARCHIVE_INDEX_FILE",
                archive_dir / "index.json",
            ),
        ):
            first = ai_status.archive_terminal_task_from_state(
                state,
                terminal,
                archived_at="2026-07-14T00:03:00Z",
            )
            pending = deepcopy(state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY])
            replay = ai_status.archive_terminal_task_from_state(
                state,
                terminal,
                archived_at="2026-07-14T00:03:00Z",
            )

            self.assertEqual(replay, first)
            self.assertEqual(state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY], pending)
            self.assertEqual(first["terminal_outcome"], "completed")
            self.assertNotIn("terminal_outcome", first["task"])
            self.assertEqual(
                pending["transaction_id"],
                "ai-status-archive-tx-"
                + ai_status._canonical_json_sha256(pending["snapshots"]),
            )

            pending_state = deepcopy(state)
            archive_bytes = None
            for recovery_attempt in range(2):
                with self.subTest(recovery_attempt=recovery_attempt):
                    replay_state = deepcopy(pending_state)
                    self._write_state(replay_state)
                    self.assertTrue(
                        ai_status.recover_status_archive_outbox(replay_state)
                    )
                    self.assertIsNone(
                        replay_state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]
                    )
                    self.assertIsNone(
                        ai_status.get_task(replay_state, "LOCK-ONE")
                    )
                    current_bytes = archive_path.read_bytes()
                    if archive_bytes is None:
                        archive_bytes = current_bytes
                    self.assertEqual(current_bytes, archive_bytes)
                    self.assertEqual(
                        json.loads(current_bytes),
                        first,
                    )

    def test_concurrent_task_mutations_are_serialized_without_lost_update(self) -> None:
        self._write_state(self._fixture_state())
        context = multiprocessing.get_context("fork")
        first_entered = context.Event()
        first_release = context.Event()
        second_entered = context.Event()
        first = context.Process(
            target=_locked_status_update,
            args=(
                str(self.status_file),
                "LOCK-ONE",
                "updated-one",
                first_entered,
                first_release,
            ),
        )
        second = context.Process(
            target=_locked_status_update,
            args=(
                str(self.status_file),
                "LOCK-TWO",
                "updated-two",
                second_entered,
                None,
            ),
        )
        first.start()
        self.assertTrue(first_entered.wait(timeout=5))
        second.start()
        try:
            self.assertFalse(
                second_entered.wait(timeout=0.25),
                "second writer crossed the first writer's stable sidecar lock",
            )
        finally:
            first_release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        for process in (first, second):
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)

        final = json.loads(self.status_file.read_text(encoding="utf-8"))
        by_id = {task["id"]: task for task in final["tasks"]}
        self.assertEqual(by_id["LOCK-ONE"]["next"], "updated-one")
        self.assertEqual(by_id["LOCK-TWO"]["next"], "updated-two")

    def test_partial_outbox_recovery_is_idempotent_and_emits_each_event_once(self) -> None:
        events = [
            {
                "event_id": "status-event-one",
                "ts": "2026-07-14T00:01:00Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-ONE",
                "message": "first event",
            },
            {
                "event_id": "status-event-two",
                "ts": "2026-07-14T00:01:01Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-TWO",
                "message": "second event",
            },
        ]
        pending = self._outbox(events)
        state = self._fixture_state()
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = pending
        self._write_state(state)
        # Represents a process dying after its first append but before clearing
        # the canonical pending outbox.
        self.log_file.write_text(
            json.dumps(events[0], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        self._run_recovery(rotate_max_bytes=1_000_000, rotate_keep_lines=100)
        recovered = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.assertIsNone(recovered[ai_status.STATUS_ACTIVITY_OUTBOX_KEY])

        # Represents another restart from the same pre-clear status image. The
        # replay must recognize both durable events and append neither again.
        recovered[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = pending
        self._write_state(recovered)
        self._run_recovery(rotate_max_bytes=1_000_000, rotate_keep_lines=100)

        rows = [
            row
            for source_rows in self._activity_rows_by_source().values()
            for row in source_rows
            if row.get("event_id") in {"status-event-one", "status-event-two"}
        ]
        event_ids = ("status-event-one", "status-event-two")
        self.assertEqual(
            {
                event_id: sum(row.get("event_id") == event_id for row in rows)
                for event_id in event_ids
            },
            {"status-event-one": 1, "status-event-two": 1},
        )
        final = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.assertIsNone(final[ai_status.STATUS_ACTIVITY_OUTBOX_KEY])

    def test_fresh_outbox_commit_never_scans_historical_archives(self) -> None:
        events = [
            {
                "event_id": "fresh-fast-path",
                "ts": "2026-07-14T00:01:00Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-ONE",
                "message": "fresh event",
            }
        ]
        state = self._fixture_state()
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = self._outbox(events)
        self._write_state(state)

        with (
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(ai_status, "LOG_FILE", self.log_file),
            mock.patch.object(
                ai_status,
                "_activity_event_index_unlocked",
                side_effect=AssertionError("historical scan must not run"),
            ) as historical_scan,
        ):
            ai_status.recover_status_activity_outbox(
                state,
                known_unappended=True,
            )

        historical_scan.assert_not_called()
        self.assertIsNone(state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY])
        rows = [
            json.loads(line)
            for line in self.log_file.read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(rows, events)

    def test_outbox_rejects_noncanonical_event_id_before_append(self) -> None:
        event_id_cases = (
            [" status-event"],
            ["status-event "],
            [" status-event "],
            ["status-event", " status-event "],
        )
        for event_ids in event_id_cases:
            with self.subTest(event_ids=event_ids):
                events = [
                    {
                        "event_id": event_id,
                        "ts": "2026-07-14T00:01:00Z",
                        "agent": "Codex2",
                        "type": "progress",
                        "task_id": "LOCK-ONE",
                        "message": "must not append",
                    }
                    for event_id in event_ids
                ]
                pending = self._outbox(events)
                state = self._fixture_state()
                state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = pending
                self._write_state(state)
                status_bytes = self.status_file.read_bytes()

                with (
                    mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
                    mock.patch.object(ai_status, "LOG_FILE", self.log_file),
                    mock.patch.object(
                        ai_status,
                        "_activity_event_index_unlocked",
                    ) as event_index,
                    mock.patch.object(ai_status, "_append_logs_unlocked") as append,
                    ai_status.canonical_task_state_lock(shared=False),
                ):
                    loaded = ai_status.load_state()
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "status activity outbox contract is invalid",
                    ):
                        ai_status.recover_status_activity_outbox(loaded)

                event_index.assert_not_called()
                append.assert_not_called()
                self.assertFalse(self.log_file.exists())
                self.assertEqual(self.status_file.read_bytes(), status_bytes)

    def test_interrupted_activity_row_is_truncated_then_replayed_once(self) -> None:
        events = [
            {
                "event_id": "tail-event-one",
                "ts": "2026-07-14T00:02:00Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-ONE",
                "message": "first event",
            },
            {
                "event_id": "tail-event-two",
                "ts": "2026-07-14T00:02:01Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-TWO",
                "message": "second event",
            },
        ]
        state = self._fixture_state()
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = self._outbox(events)
        self._write_state(state)
        self.log_file.write_bytes(
            (json.dumps(events[0], ensure_ascii=False) + "\n").encode("utf-8")
            + b'{"event_id":"tail-event-two"'
        )

        self._run_recovery(rotate_max_bytes=1_000_000, rotate_keep_lines=100)

        rows = [
            row
            for source_rows in self._activity_rows_by_source().values()
            for row in source_rows
        ]
        self.assertEqual(
            {
                event_id: sum(row.get("event_id") == event_id for row in rows)
                for event_id in ("tail-event-one", "tail-event-two")
            },
            {"tail-event-one": 1, "tail-event-two": 1},
        )

    def test_duplicate_event_id_across_sources_fails_closed(self) -> None:
        event = {
            "event_id": "duplicate-across-sources",
            "ts": "2026-07-14T00:02:00Z",
            "agent": "Codex2",
            "type": "progress",
            "task_id": "LOCK-ONE",
            "message": "must occur once",
        }
        state = self._fixture_state()
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = self._outbox([event])
        self._write_state(state)
        archive = self.root / "archive" / "logs" / (
            f"{self.log_file.name}-2026-07-16T1130Z.gz"
        )
        archive.parent.mkdir(parents=True)
        with gzip.open(archive, "wt", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        self.log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")

        with (
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(ai_status, "LOG_FILE", self.log_file),
            ai_status.canonical_task_state_lock(shared=False),
        ):
            loaded = ai_status.load_state()
            with self.assertRaisesRegex(RuntimeError, "duplicate across sources"):
                ai_status.recover_status_activity_outbox(loaded)

    def test_archive_outbox_restart_recovery_is_idempotent(self) -> None:
        state = self._fixture_state()
        terminal = state["tasks"][0]
        terminal["status"] = "done"
        terminal["terminal_outcome"] = "completed"
        archive_dir = self.root / "ai-task-archive"
        with (
            mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            mock.patch.object(task_archive, "STATUS_ROOT", self.root),
            mock.patch.object(task_archive, "STATUS_FILE", self.status_file),
            mock.patch.object(task_archive, "ARCHIVE_DIR", archive_dir),
            mock.patch.object(task_archive, "ARCHIVE_TASKS_DIR", archive_dir / "tasks"),
            mock.patch.object(task_archive, "ARCHIVE_INDEX_FILE", archive_dir / "index.json"),
        ):
            ai_status.archive_terminal_task_from_state(
                state,
                terminal,
                archived_at="2026-07-14T00:03:00Z",
            )
        self._write_state(state)
        pending_bytes = self.status_file.read_bytes()
        self.assertIsNotNone(ai_status.get_task(state, "LOCK-ONE"))
        self.assertFalse(
            (self.root / "ai-task-archive/tasks/LOCK-ONE.json").exists()
        )

        context = multiprocessing.get_context("fork")
        for replay in range(2):
            with self.subTest(replay=replay):
                if replay:
                    # Represents a crash after the archive/index became durable
                    # but before the status outbox clear was persisted.
                    self.status_file.write_bytes(pending_bytes)
                process = context.Process(
                    target=_recover_pending_archive_outbox,
                    args=(str(self.status_file), str(self.root)),
                )
                process.start()
                process.join(timeout=10)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                    self.fail("archive outbox recovery process timed out")
                self.assertEqual(process.exitcode, 0)

        final = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.assertIsNone(final[ai_status.STATUS_ARCHIVE_OUTBOX_KEY])
        self.assertIsNone(ai_status.get_task(final, "LOCK-ONE"))
        archived = json.loads(
            (self.root / "ai-task-archive/tasks/LOCK-ONE.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(archived["task_id"], "LOCK-ONE")
        index = json.loads(
            (self.root / "ai-task-archive/index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(index["counts"]["total"], 1)
        self.assertEqual(index["recent_terminal_ids"], ["LOCK-ONE"])

    def test_existing_archive_conflict_or_legacy_shape_preserves_active_task(self) -> None:
        # 1. Version 1 snapshot, no conflict (identical)
        existing_no_conflict = {
            "version": 1,
            "task_id": "LOCK-ONE",
            "archived_at": "2026-07-14T00:03:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                **deepcopy(self._fixture_state()["tasks"][0]),
                "status": "done",
                "terminal_outcome": "completed",
            },
            "handoffs": [],
            "blockers": [],
        }
        state = self._fixture_state()
        terminal = state["tasks"][0]
        terminal["status"] = "done"
        terminal["terminal_outcome"] = "completed"
        before = deepcopy(state)
        with mock.patch.object(
            ai_status,
            "archived_task_snapshot",
            return_value=existing_no_conflict,
        ):
            snapshot = ai_status.archive_terminal_task_from_state(state, terminal, archived_at="2026-07-14T00:03:00Z")
        self.assertEqual(snapshot, existing_no_conflict)
        self.assertEqual(state["tasks"], before["tasks"])
        self.assertEqual(state["handoffs"], before["handoffs"])
        self.assertEqual(state["blockers"], before["blockers"])
        pending = state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]
        self.assertEqual(pending["schema_version"], ai_status.STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION)
        self.assertEqual(pending["snapshots"], [existing_no_conflict])

        # 2. Version 1 snapshot, with conflict (raises RuntimeError)
        existing_conflict = {
            "version": 1,
            "task_id": "LOCK-ONE",
            "archived_at": "2026-07-14T00:03:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                **deepcopy(self._fixture_state()["tasks"][0]),
                "status": "done",
                "terminal_outcome": "completed",
            },
            "handoffs": [],
            "blockers": [],
        }
        state = self._fixture_state()
        terminal = state["tasks"][0]
        terminal["status"] = "done"
        terminal["terminal_outcome"] = "superseded"
        terminal["superseded_by"] = "LOCK-THREE"
        before = deepcopy(state)
        with mock.patch.object(
            ai_status,
            "archived_task_snapshot",
            return_value=existing_conflict,
        ):
            with self.assertRaises(RuntimeError):
                ai_status.archive_terminal_task_from_state(state, terminal)
        self.assertEqual(state, before)

        # 3. Legacy snapshot (raises RuntimeError)
        legacy_existing = {
            "id": "LOCK-ONE",
            "status": "done",
            "terminal_outcome": "completed",
            "archived_at": "2026-07-14T00:03:00Z",
        }
        state = self._fixture_state()
        terminal = state["tasks"][0]
        terminal["status"] = "done"
        terminal["terminal_outcome"] = "superseded"
        terminal["superseded_by"] = "LOCK-THREE"
        before = deepcopy(state)
        with mock.patch.object(
            ai_status,
            "archived_task_snapshot",
            return_value=legacy_existing,
        ):
            with self.assertRaises(RuntimeError):
                ai_status.archive_terminal_task_from_state(state, terminal)
        self.assertEqual(state, before)

    def test_sigkill_at_each_archive_boundary_converges_without_vanishing_task(self) -> None:
        context = multiprocessing.get_context("fork")
        for point in ("pending_status", "snapshot", "index", "rebuild"):
            with self.subTest(point=point):
                root = self.root / point
                root.mkdir()
                status_file = root / "ai-status.json"
                state = self._fixture_state()
                terminal = state["tasks"][0]
                terminal["status"] = "done"
                terminal["terminal_outcome"] = "completed"
                status_file.write_text(
                    json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                process = context.Process(
                    target=_commit_terminal_archive_with_sigkill,
                    args=(str(status_file), str(root), point),
                )
                process.start()
                process.join(timeout=10)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                    self.fail(f"archive child hung at {point}")
                self.assertEqual(process.exitcode, -9)

                pending = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertIsNotNone(ai_status.get_task(pending, "LOCK-ONE"))
                self.assertIsNotNone(
                    pending.get(ai_status.STATUS_ARCHIVE_OUTBOX_KEY)
                )

                recovery = context.Process(
                    target=_recover_pending_archive_outbox,
                    args=(str(status_file), str(root)),
                )
                recovery.start()
                recovery.join(timeout=10)
                if recovery.is_alive():
                    recovery.kill()
                    recovery.join(timeout=5)
                    self.fail(f"archive recovery hung after {point}")
                self.assertEqual(recovery.exitcode, 0)

                final = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertIsNone(ai_status.get_task(final, "LOCK-ONE"))
                self.assertIsNone(final[ai_status.STATUS_ARCHIVE_OUTBOX_KEY])
                archived = json.loads(
                    (root / "ai-task-archive/tasks/LOCK-ONE.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(archived["task_id"], "LOCK-ONE")
                index = json.loads(
                    (root / "ai-task-archive/index.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(index["counts"]["total"], 1)

    def test_rotation_cannot_split_or_duplicate_one_outbox_transaction(self) -> None:
        events = [
            {
                "event_id": "rotation-event-one",
                "ts": "2026-07-14T00:02:00Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-ONE",
                "message": "rotation first",
            },
            {
                "event_id": "rotation-event-two",
                "ts": "2026-07-14T00:02:01Z",
                "agent": "Codex2",
                "type": "progress",
                "task_id": "LOCK-TWO",
                "message": "rotation second",
            },
        ]
        state = self._fixture_state()
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = self._outbox(events)
        self._write_state(state)
        filler = json.dumps(
            {"ts": "2026-07-14T00:00:00Z", "type": "fixture", "message": "x" * 256}
        ) + "\n"
        self.log_file.write_text(filler, encoding="utf-8")

        # The log starts just below the limit. A per-event rotation check would
        # append event one, rotate before event two, and split the transaction.
        self._run_recovery(
            rotate_max_bytes=len(filler.encode("utf-8")) + 1,
            rotate_keep_lines=1,
        )

        rows_by_source = self._activity_rows_by_source()
        transaction_ids = {"rotation-event-one", "rotation-event-two"}
        event_sources: dict[str, list[Path]] = {
            event_id: [] for event_id in transaction_ids
        }
        for source, rows in rows_by_source.items():
            for row in rows:
                event_id = row.get("event_id")
                if event_id in event_sources:
                    event_sources[event_id].append(source)
        self.assertEqual(
            {event_id: len(sources) for event_id, sources in event_sources.items()},
            {"rotation-event-one": 1, "rotation-event-two": 1},
            "rotation duplicated an outbox event across active and archived logs",
        )
        self.assertEqual(
            len({sources[0] for sources in event_sources.values()}),
            1,
            "rotation split one status transaction across audit files",
        )

    def test_integrity_block_persists_pending_markers_on_disk(self) -> None:
        state = self._fixture_state()
        event = {
            "ts": "2026-08-06T05:00:00Z",
            "agent": "Codex2",
            "type": "progress",
            "task_id": "LOCK-ONE",
            "message": "write-back that cannot clear the integrity check",
            "event_id": "ai-status-ev-"
            + "3" * 64,
        }
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = self._outbox([event])
        self._write_state(state)

        blocked = ai_status.ActivityAuditInvariantError(
            "activity content-addressed archives do not match lineage",
            invariant="activity_archive_lineage",
            evidence={"log_path": str(self.log_file)},
        )
        refreshed: list[dict[str, object]] = []
        with (
            mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "1"},
                clear=False,
            ),
            mock.patch.object(
                ai_status, "_activity_event_index_unlocked", return_value={}
            ),
            mock.patch.object(
                ai_status, "_append_logs_unlocked", side_effect=blocked
            ),
            mock.patch.object(
                ai_status,
                "refresh_derived_status_views",
                side_effect=refreshed.append,
            ),
        ):
            loaded = ai_status.load_state()
            with self.assertRaises(ai_status.ActivityAuditInvariantError):
                ai_status.recover_status_activity_outbox(loaded)

        persisted = json.loads(self.status_file.read_text(encoding="utf-8"))
        blocked_task = next(
            task for task in persisted["tasks"] if task["id"] == "LOCK-ONE"
        )
        untouched_task = next(
            task for task in persisted["tasks"] if task["id"] == "LOCK-TWO"
        )
        # The write stays queued for retry, and the board now says so.
        self.assertIsNotNone(persisted[ai_status.STATUS_ACTIVITY_OUTBOX_KEY])
        self.assertTrue(blocked_task["status_write_pending"])
        self.assertEqual(blocked_task["status_write_pending_count"], 1)
        self.assertNotIn("status_write_pending", untouched_task)
        # Derived views are refreshed so the marker is readable while the
        # read-only commands are still failing closed on the pending plane.
        self.assertEqual(len(refreshed), 1)

    def test_integrity_block_leaves_board_untouched_when_flag_is_off(self) -> None:
        state = self._fixture_state()
        event = {
            "ts": "2026-08-06T05:00:00Z",
            "agent": "Codex2",
            "type": "progress",
            "task_id": "LOCK-ONE",
            "message": "write-back that cannot clear the integrity check",
            "event_id": "ai-status-ev-" + "4" * 64,
        }
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = self._outbox([event])
        self._write_state(state)

        blocked = ai_status.ActivityAuditInvariantError(
            "activity content-addressed archives do not match lineage",
            invariant="activity_archive_lineage",
            evidence={"log_path": str(self.log_file)},
        )
        with (
            mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "0"},
                clear=False,
            ),
            mock.patch.object(
                ai_status, "_activity_event_index_unlocked", return_value={}
            ),
            mock.patch.object(
                ai_status, "_append_logs_unlocked", side_effect=blocked
            ),
            mock.patch.object(ai_status, "refresh_derived_status_views"),
        ):
            loaded = ai_status.load_state()
            with self.assertRaises(ai_status.ActivityAuditInvariantError):
                ai_status.recover_status_activity_outbox(loaded)

        persisted = json.loads(self.status_file.read_text(encoding="utf-8"))
        for task in persisted["tasks"]:
            self.assertNotIn("status_write_pending", task)
            self.assertNotIn("status_write_pending_count", task)

    def test_status_write_pending_indicators(self) -> None:
        state = self._fixture_state()
        task = state["tasks"][0]
        self.assertNotIn("status_write_pending", task)
        self.assertNotIn("status_write_pending_count", task)

        # 1. Activity outbox pending with feature flag enabled
        event = {
            "ts": "2026-08-06T05:00:00Z",
            "agent": "Antigravity",
            "type": "progress",
            "task_id": "LOCK-ONE",
            "message": "test progress",
            "event_id": "ai-status-ev-1111111111111111111111111111111111111111111111111111111111111111",
        }
        unbound_event = {
            "ts": "2026-08-06T05:01:00Z",
            "agent": "Antigravity",
            "type": "wave_open",
            "wave_id": "2026-W30",
            "message": "wave open",
            "event_id": "ai-status-ev-2222222222222222222222222222222222222222222222222222222222222222",
        }
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = {
            "schema_version": ai_status.STATUS_ACTIVITY_OUTBOX_SCHEMA_VERSION,
            "transaction_id": "ai-status-tx-" + ai_status._canonical_json_sha256([event, unbound_event]),
            "events": [event, unbound_event],
        }

        with unittest.mock.patch.dict("os.environ", {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "1"}):
            ai_status._update_pending_outbox_indicators(state)
            task_one = ai_status.get_task(state, "LOCK-ONE")
            task_two = ai_status.get_task(state, "LOCK-TWO")
            self.assertTrue(task_one.get("status_write_pending"))
            self.assertEqual(task_one.get("status_write_pending_count"), 1)
            # Unbound events should NOT cause false positive status_write_pending on untouched tasks
            self.assertNotIn("status_write_pending", task_two)
            self.assertNotIn("status_write_pending_count", task_two)

        # 2. Flag off removes indicators
        with unittest.mock.patch.dict("os.environ", {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "0"}):
            ai_status._update_pending_outbox_indicators(state)
            task_one = ai_status.get_task(state, "LOCK-ONE")
            self.assertNotIn("status_write_pending", task_one)

        # 3. Cleared outbox removes indicators even when flag enabled
        state[ai_status.STATUS_ACTIVITY_OUTBOX_KEY] = None
        with unittest.mock.patch.dict("os.environ", {"PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED": "1"}):
            ai_status._update_pending_outbox_indicators(state)
            task_one = ai_status.get_task(state, "LOCK-ONE")
            self.assertNotIn("status_write_pending", task_one)
            self.assertNotIn("status_write_pending_count", task_one)



class ActivityLogRotationTests(unittest.TestCase):
    def _make_log(self, *, size_per_line: int = 200, line_count: int = 100) -> Path:
        tmp = tempfile.TemporaryDirectory(prefix="ai-status-rotate-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        log_path = root / "ai-activity-log.jsonl"
        payload = ("x" * (size_per_line - 1)) + "\n"
        log_path.write_text(payload * line_count, encoding="utf-8")
        return log_path

    def test_does_not_rotate_when_under_threshold(self) -> None:
        log_path = self._make_log(line_count=10)
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1_000_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 5),
        ):
            archive = ai_status.maybe_rotate_activity_log()
        self.assertIsNone(archive)
        self.assertEqual(len(log_path.read_bytes().splitlines()), 10)
        archive_dir = log_path.parent / "archive" / "logs"
        self.assertFalse(archive_dir.exists())

    def test_rotates_and_keeps_tail_when_over_threshold(self) -> None:
        log_path = self._make_log(size_per_line=200, line_count=100)  # ~20 KB
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 5_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 8),
        ):
            archive = ai_status.maybe_rotate_activity_log()
        assert archive is not None
        self.assertTrue(archive.exists())
        self.assertTrue(str(archive).endswith(".gz"))
        # The active log now holds a lineage-head control record plus the tail.
        active_lines = log_path.read_bytes().splitlines()
        self.assertEqual(len(active_lines), 9)
        control = json.loads(active_lines[0])
        self.assertEqual(control["record_type"], "pantheon.activity.lineage_head.v1")
        self.assertEqual(len(active_lines[1:]), 8)
        # The gzip archive and active tail form one disjoint audit corpus.
        import gzip as _gz
        with _gz.open(archive, "rb") as fh:
            archived = fh.read().splitlines()
        self.assertEqual(len(archived), 92)
        self.assertEqual(len(archived) + len(active_lines[1:]), 100)

    def test_rotation_preserves_stable_sidecar_inode(self) -> None:
        log_path = self._make_log(line_count=80)
        lock_path = log_path.parent / ".orchestrator" / "activity-audit.lock"
        with ai_status.activity_audit_lock_file(
            log_path,
            shared=True,
            nonblocking=False,
        ):
            pass
        before_inode = lock_path.stat().st_ino
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 1),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 3),
        ):
            ai_status.maybe_rotate_activity_log()
        after_inode = lock_path.stat().st_ino
        self.assertEqual(before_inode, after_inode)

    def test_append_log_triggers_rotation(self) -> None:
        log_path = self._make_log(size_per_line=200, line_count=50)  # ~10 KB
        with (
            mock.patch.object(ai_status, "LOG_FILE", log_path),
            mock.patch.object(ai_status, "LOG_ROTATE_MAX_BYTES", 5_000),
            mock.patch.object(ai_status, "LOG_ROTATE_KEEP_LINES", 4),
        ):
            ai_status.append_log({"ts": "2026-05-18T00:00:00Z", "msg": "new entry"})
        active_lines = log_path.read_text(encoding="utf-8").splitlines()
        # 1 lineage-head control + 4 kept tail lines + 1 new = 6
        self.assertEqual(len(active_lines), 6)
        self.assertEqual(json.loads(active_lines[0])["record_type"], "pantheon.activity.lineage_head.v1")
        self.assertIn("new entry", active_lines[-1])
        archive_dir = log_path.parent / "archive" / "logs"
        archives = list(archive_dir.glob("*.gz"))
        self.assertEqual(len(archives), 1)

    def test_docs_mirror_uses_locked_snapshot_and_atomic_replace(self) -> None:
        log_path = self._make_log(line_count=5)
        target = log_path.parent / "docs-site" / log_path.name
        tail = b"last-two-lines\n"
        with (
            mock.patch.object(
                ai_status,
                "read_activity_log_tail_bytes",
                return_value=tail,
            ) as read_tail,
            mock.patch.object(ai_status, "durable_write_bytes") as durable_write,
        ):
            ai_status._mirror_log_tail(log_path, target, 2)

        read_tail.assert_called_once_with(log_path, max_lines=2)
        durable_write.assert_called_once_with(target, tail)


class ProgramProofOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        catalog_sha = (
            "8c7610b0e6bbba31c36cb0ecd1ddce4bf843fc6de89dcaecc4a5e3154af8933d"
        )
        self.state = {
            "agents": [],
            "tasks": [
                {
                    "id": "L12-TEACH-001",
                    "program_id": "pantheon-twelve-loop-gap-2026-07-26",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                    "status": "in_progress",
                    "depends_on": ["L12-FLEET-001"],
                    "artifacts": [],
                    "proof_required": ["hosted persona terminal readback"],
                    "artifact_conflict_guard": {
                        "schema_version": 1,
                        "program_id": "pantheon-twelve-loop-gap-2026-07-26",
                        "catalog_sha256": catalog_sha,
                        "task_id": "L12-TEACH-001",
                        "artifact_scope": [],
                        "allowed_overlap_task_ids": [],
                    },
                },
                {
                    "id": "L12-VERIFY-LEARN-001",
                    "status": "todo",
                    "depends_on": ["L12-TEACH-001"],
                    "artifacts": [],
                },
                {
                    "id": "L12-HOSTED-001",
                    "status": "todo",
                    "depends_on": ["L12-VERIFY-LEARN-001"],
                    "artifacts": [],
                },
            ],
            "handoffs": [],
            "blockers": [],
        }
        self.overlay = (
            "docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/"
            "proof-ownership.json"
        )

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_attach_proof_ownership_is_human_ops_only(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "Only Human/Ops"):
                ai_status.command_attach_proof_ownership(
                    self.state,
                    ["L12-TEACH-001", self.overlay, "Delegate hosted proof."],
                )

    def test_attach_proof_ownership_records_exact_forward_delegation(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AI_NAME": "Human/Ops"},
            clear=False,
        ):
            ai_status.command_attach_proof_ownership(
                self.state,
                [
                    "L12-TEACH-001",
                    self.overlay,
                    "Break the activation-proof dependency cycle.",
                ],
            )

        task = ai_status.get_task(self.state, "L12-TEACH-001")
        self.assertIsNotNone(task)
        context = task["proof_ownership"]
        self.assertEqual(context["attached_by"], "Human/Ops")
        self.assertEqual(context["proof_ownership_file"], self.overlay)
        self.assertEqual(len(context["proof_ownership_sha256"]), 64)
        self.assertEqual(
            context["delegations"][0]["owner_task_id"],
            "L12-VERIFY-LEARN-001",
        )
        self.assertEqual(
            context["delegations"][0]["final_witness_task_id"],
            "L12-HOSTED-001",
        )
        self.assertIn("Delegated proof remains required", task["next"])

    def test_attach_proof_ownership_rejects_non_descendant_owner(self) -> None:
        self.state["tasks"][1]["depends_on"] = []
        with mock.patch.dict(
            os.environ,
            {"AI_NAME": "Human/Ops"},
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "not a descendant"):
                ai_status.command_attach_proof_ownership(
                    self.state,
                    ["L12-TEACH-001", self.overlay, "Invalid delegation."],
                )

    def test_quarantined_status_in_schema_constants(self) -> None:
        self.assertIn("quarantined", ai_status.STATUS_LABELS)
        self.assertEqual(ai_status.STATUS_LABELS["quarantined"], "quarantined")
        self.assertIn("quarantined", ai_status.ACTIVE_TASK_STATUSES)


class TestSentinelTimestampOverflow(unittest.TestCase):
    def test_format_display_timestamp_sentinel_max_utc(self) -> None:
        raw_str = "9999-12-31T23:59:59Z"
        result = ai_status.format_display_timestamp(raw_str)
        self.assertEqual(result, "9999-12-31T23:59:59Z")

    def test_format_display_timestamp_datetime_sentinel_max_utc(self) -> None:
        dt_sentinel = ai_status.parse_timestamp("9999-12-31T23:59:59Z")
        self.assertIsNotNone(dt_sentinel)
        result = ai_status.format_display_timestamp(dt_sentinel)
        self.assertEqual(result, "9999-12-31T23:59:59+00:00")

    def test_format_display_timestamp_normal_utc(self) -> None:
        raw_str = "2026-08-09T06:15:00Z"
        result = ai_status.format_display_timestamp(raw_str)
        self.assertEqual(result, "2026-08-09 14:15:00")

    def test_format_display_timestamp_malformed_and_none(self) -> None:
        self.assertEqual(ai_status.format_display_timestamp(None), "-")
        self.assertEqual(ai_status.format_display_timestamp(""), "-")
        self.assertEqual(ai_status.format_display_timestamp("invalid-date"), "invalid-date")

    def test_localize_embedded_timestamps_with_sentinel(self) -> None:
        text = "Task created at 9999-12-31T23:59:59Z and active at 2026-08-09T06:15:00Z"
        expected = "Task created at 9999-12-31T23:59:59Z and active at 2026-08-09 14:15:00"
        result = ai_status.localize_embedded_timestamps(text)
        self.assertEqual(result, expected)

    def test_derived_view_refresh_with_sentinel_activity_log(self) -> None:
        _setup_test_isolation(self)
        try:
            with mock.patch.object(ai_status, "STATUS_FILE", self._test_status_file), \
                 mock.patch.object(ai_status, "LOG_FILE", self._test_log_file), \
                 mock.patch.object(ai_status, "CURRENT_WORK_FILE", self._test_root / "current-work.md"):
                state = {
                    "project": "pantheon",
                    "sprint": "test-sprint",
                    "sprint_started_at": "2026-08-09T00:00:00Z",
                    "objective": "Test objective with sentinel timestamp 9999-12-31T23:59:59Z",
                    "updated_at": "9999-12-31T23:59:59Z",
                    "canonical_files": ["AI_COLLABORATION_GUIDE.md", "ai-status.json"],
                    "tasks": [
                        {
                            "id": "TEST-001",
                            "title": "Sentinel Test Task",
                            "status": "in_progress",
                            "owner": "Antigravity2",
                            "reviewer": "Claude",
                            "phase": "Test Phase",
                            "depends_on": [],
                            "artifacts": [],
                            "last_update": "9999-12-31T23:59:59Z",
                        }
                    ],
                    "handoffs": [],
                    "blockers": [],
                    "agents": [],
                }
                logs = [
                    {
                        "ts": "9999-12-31T23:59:59Z",
                        "agent": "Antigravity2",
                        "action": "progress",
                        "task_id": "TEST-001",
                        "message": "Activity entry containing sentinel timestamp 9999-12-31T23:59:59Z",
                    }
                ]

                # Must not raise OverflowError when rendering derived views
                ai_status.write_current_work(state, logs)
                self.assertTrue((self._test_root / "current-work.md").exists())
                content = (self._test_root / "current-work.md").read_text(encoding="utf-8")
                self.assertIn("9999-12-31T23:59:59Z", content)
        finally:
            self._test_temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
