#!/usr/bin/env python3
from __future__ import annotations

import gzip
import base64
import hashlib
import io
import json
import contextlib
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_status
import task_archive
import common
from common import rotate_activity_log_unlocked
from rewrite import task_machine, task_state_store


def _canonical_state_identity_json(status_root: Path, event_log: Path) -> str:
    return json.dumps(
        common.canonical_task_state_identity_for_paths(
            status_root=status_root,
            event_log=event_log,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


class HumanOpsStatusWrapperTests(unittest.TestCase):
    def test_wrapper_selects_explicit_local_human_ops_mode(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = Path(tmpdir) / "scripts"
            scripts_dir.mkdir()
            wrapper = scripts_dir / "human-ops-status.sh"
            shutil.copy2(repo_root / "scripts" / "human-ops-status.sh", wrapper)
            target = scripts_dir / "ai-status.sh"
            target.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s|%s|%s\\n' \"$AI_NAME\" \"$PANTHEON_LOCAL_HUMAN_OPS\" \"$*\"\n",
                encoding="utf-8",
            )
            target.chmod(0o755)

            completed = subprocess.run(
                [str(wrapper), "note", "TASK-1", "explicit maintenance"],
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertEqual(
            completed.stdout.strip(),
            "Human/Ops|1|note TASK-1 explicit maintenance",
        )


class RetiredAgentAliasTests(unittest.TestCase):
    def test_retired_gemini_names_normalize_to_current_antigravity_lanes(self) -> None:
        state = deepcopy(ai_status.default_state())
        task = state["tasks"][0]
        task["owner"] = "Gemini"
        task["reviewer"] = "Gemini2"
        task["waiting_for"] = "Gemini"
        state["agents"].append(
            {
                "name": "Gemini",
                "capability_lane": [],
                "status": "idle",
                "current_task_ids": [],
                "branch": "",
                "next": "",
                "last_update": None,
            }
        )

        ai_status.normalize_state_agents(state)

        self.assertEqual(task["owner"], "Antigravity")
        self.assertEqual(task["reviewer"], "Antigravity2")
        self.assertEqual(task["waiting_for"], "Antigravity")
        self.assertEqual(state["agents"][-1]["name"], "Antigravity")
        ai_status.validate_state(state)


def _rotate_activity_log_for_test(log_path: Path) -> Path | None:
    with ai_status.activity_audit_lock_file(
        log_path,
        shared=False,
        nonblocking=False,
    ):
        return rotate_activity_log_unlocked(
            log_path,
            max_bytes=ai_status.LOG_ROTATE_MAX_BYTES,
            keep_lines=ai_status.LOG_ROTATE_KEEP_LINES,
            archive_dir=log_path.parent / "archive" / "logs",
        )


def _command_approve(state: dict[str, Any], args: list[str]) -> None:
    _execute_external_mutation_command("approve", state, args)


def _command_reopen(state: dict[str, Any], args: list[str]) -> None:
    _execute_external_mutation_command("reopen", state, args)


def _command_done(state: dict[str, Any], args: list[str]) -> None:
    _execute_external_mutation_command("done", state, args)


def _command_reconcile_merged_done(
    state: dict[str, Any], args: list[str]
) -> None:
    _execute_external_mutation_command("reconcile_merged_done", state, args)


def _execute_external_mutation_command(
    command: str, state: dict[str, Any], args: list[str]
) -> None:
    task_id = args[0] if args else ""
    task = ai_status.get_task(state, task_id)
    if task is None:
        if command == "reopen" and ai_status.has_terminal_fact(state, task_id):
            raise SystemExit(
                f"Task {task_id} is terminal and cannot be reopened in place. "
                f"Create a new follow-up task that references {task_id}."
            )
        raise SystemExit(f"Unknown task: {task_id}")
    preflight = ai_status.prepare_external_mutation_preflight(command, task, args)
    functions = {
        "approve": ai_status.command_approve,
        "reopen": ai_status.command_reopen,
        "done": ai_status.command_done,
        "reconcile_merged_done": ai_status.command_reconcile_merged_done,
    }
    with ai_status.bound_external_mutation_preflight(preflight):
        functions[command](state, args)


def audited_reassignment_event(
    *,
    task_id: str = "REG-002",
    old_owner: str = "Codex2",
    new_owner: str = "Antigravity",
    old_reviewer: str = "Claude",
    new_reviewer: str = "Claude",
    timestamp: str = "2026-07-19T23:52:06Z",
    message: str = "canonical owner reassignment",
    actor: str = "Orchestrator",
    old_generation: int = 1,
    new_generation: int = 2,
) -> dict[str, Any]:
    """Build a reassignment fixture through the production event contract.

    Keeping test fixtures on the same builder as both real writers ensures every
    reader test is a writer/reader round trip, rather than a second test-only
    implementation of the digest contract.
    """

    assignment = task_machine.assignment_transition(
        old_owner,
        old_reviewer,
        new_owner,
        new_reviewer,
        actor=actor,
        reason=message,
    )
    return task_machine.build_assignment_activity_event(
        task_id=task_id,
        timestamp=timestamp,
        assignment=assignment,
        old_generation=old_generation,
        new_generation=new_generation,
    )


def legacy_supervisor_reassignment_event(
    *,
    prefix: str = "supervisor-reassign-",
    task_id: str = "REG-002",
    old_owner: str = "Codex2",
    new_owner: str = "Antigravity",
    old_reviewer: str = "Claude",
    new_reviewer: str = "Claude",
    timestamp: str = "2026-07-19T23:52:06Z",
    message: str = "canonical owner reassignment",
) -> dict[str, Any]:
    """Return a pre-generation historical fixture for read compatibility only."""

    payload = (
        f"{task_id}\0{timestamp}\0{old_owner}\0{new_owner}\0"
        f"{old_reviewer}\0{new_reviewer}\0{message}"
    )
    return {
        "event_id": prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
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

from rewrite.task_state_store import load_events


REVIEW_BINDING_ENV_KEYS = (
    "REVIEW_PR",
    "REVIEW_HEAD_SHA",
    "REVIEW_BASE",
    "REVIEW_HEAD_BRANCH",
)

ISOLATED_ENV_KEYS = (
    *REVIEW_BINDING_ENV_KEYS,
    "PANTHEON_STATUS_ROOT",
    "PANTHEON_WORKTREE_ROOT",
    "ORCH_WORKSPACE_PATH",
    "PANTHEON_TASK_STATE_STORE_MODE",
    "PANTHEON_TASK_STATE_EVENT_LOG",
    "PANTHEON_TASK_STATE_SCHEMA_VERSION",
    "TASK_STATE_STORE_MODE",
    "TASK_STATE_EVENT_LOG",
    "ORCH_RUN_ID",
    "ORCH_TASK_ID",
    "ORCH_AGENT_ID",
    "ORCH_PROVIDER",
    "ORCH_SESSION_ID",
)


def _setup_test_isolation(test_case):
    test_case._inherited_isolated_env = {
        key: os.environ.pop(key)
        for key in ISOLATED_ENV_KEYS
        if key in os.environ
    }
    test_case._test_temp_dir = tempfile.TemporaryDirectory(prefix="ai-status-test-")
    test_case._test_root = Path(test_case._test_temp_dir.name)
    test_case._test_status_file = test_case._test_root / "ai-status.json"
    test_case._test_log_file = test_case._test_root / "ai-activity-log.jsonl"

    (test_case._test_root / ".orchestrator").mkdir()
    test_case._test_status_file.write_text("{}\n", encoding="utf-8")
    test_case._test_log_file.write_text("", encoding="utf-8")

    test_case._orig_paths = {
        "STATUS_ROOT": ai_status.STATUS_ROOT,
        "STATUS_FILE": ai_status.STATUS_FILE,
        "LOG_FILE": ai_status.LOG_FILE,
        "CURRENT_WORK_FILE": ai_status.CURRENT_WORK_FILE,
        "DOCS_SITE_DIR": ai_status.DOCS_SITE_DIR,
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
    for key in ISOLATED_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(test_case._inherited_isolated_env)

    paths = test_case._orig_paths
    ai_status.STATUS_ROOT = paths["STATUS_ROOT"]
    ai_status.STATUS_FILE = paths["STATUS_FILE"]
    ai_status.LOG_FILE = paths["LOG_FILE"]
    ai_status.CURRENT_WORK_FILE = paths["CURRENT_WORK_FILE"]
    ai_status.DOCS_SITE_DIR = paths["DOCS_SITE_DIR"]
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
            '"type":"worker_started"}\n',
            '{"event_id":"nested","type":"worker_started",'
            '"metadata":{"role":"a","role":"b"}}\n',
        )
        readers = (("load_logs", ai_status.load_logs),)
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


class TaskStateAuthorityModeTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_save_state_rejects_retired_shadow_mode(self) -> None:
        journal = (
            self._test_root.parent
            / f"{self._test_root.name}-runtime"
            / "task-state-events.jsonl"
        )
        state = {"sprint": "retired-mode", "tasks": [{"id": "STATE-001", "status": "todo"}]}

        with mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "shadow",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(journal),
                "ORCH_RUN_ID": "run-shadow-001",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be authoritative"):
                ai_status.save_state(state)

        self.assertFalse(journal.exists())
        self.assertEqual(json.loads(self._test_status_file.read_text(encoding="utf-8")), {})

    def test_save_state_rejects_retired_off_mode(self) -> None:
        state = {"sprint": "retired-mode", "tasks": [{"id": "STATE-002", "status": "todo"}]}

        with mock.patch.dict(
            os.environ,
            {ai_status.TASK_STATE_STORE_MODE_ENV: "off"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be authoritative"):
                ai_status.save_state(state)

        self.assertEqual(json.loads(self._test_status_file.read_text(encoding="utf-8")), {})

    def test_authoritative_load_ignores_divergent_file_and_save_advances_journal(self) -> None:
        journal = (
            self._test_root.parent
            / f"{self._test_root.name}-runtime"
            / "task-state-events.jsonl"
        )
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
                common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                    self._test_root,
                    journal,
                ),
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
        journal = (
            self._test_root.parent
            / f"{self._test_root.name}-runtime"
            / "task-state-events.jsonl"
        )
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
                common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                    self._test_root,
                    journal,
                ),
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

    def test_authoritative_store_rejects_mismatched_state_domain_identity(self) -> None:
        journal = (
            self._test_root.parent
            / f"{self._test_root.name}-runtime"
            / "task-state-events.jsonl"
        )
        state = {"sprint": "authoritative", "tasks": []}
        ai_status.append_state_commit(journal, state, source="fixture")
        wrong_journal = journal.with_name("other-task-state-events.jsonl")
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_STATUS_ROOT": str(self._test_root),
                ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(journal),
                common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                    self._test_root,
                    wrong_journal,
                ),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "canonical task-state identity mismatch"):
                ai_status.load_state()

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
                            "generation": 1,
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
                    "task_generation": 1,
                    "status": "running",
                    "lease_expires_at": lease_expires_at,
                    "workspace_path": str(self.workspace),
                    "workspace_repository_id": "pantheon",
                    "status_root": str(worker_status_root),
                    "status_command_runtime": self.issued_runtime,
                    "queue_event_id": queue_event_id,
                    "pid": pid,
                    "pid_start_ticks": pid_start_ticks,
                    "process_generation": process_generation,
                    "request_snapshot": {
                        "task_id": worker_task_id,
                        "task_generation": 1,
                        "metadata": {"task_generation": 1},
                    },
                }
            },
            "worker_worktrees": {
                "leases": {
                    worker_task_id: {
                        "task_id": worker_task_id,
                        "path": str(self.workspace),
                        "repository_id": "pantheon",
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

    def test_recalled_assignment_generation_revokes_old_worker_mutations(self) -> None:
        self._validate(
            "note",
            [self.task_id, "stale note"],
            actor="Codex",
            env_task_id=self.task_id,
        )
        recalled_state = {
            "tasks": [
                {
                    "id": self.task_id,
                    "generation": 2,
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "status": "in_progress",
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "task generation mismatch"):
            ai_status.validate_bound_status_command_task_authority(
                recalled_state,
                "note",
                [self.task_id, "stale note"],
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
                "exact active worker lease or explicit local Human/Ops mode",
            ):
                ai_status.validate_active_status_command_lease(
                    "progress", [self.task_id, "missing lease"]
                )

    def test_rejects_human_ops_and_reviewer_spoof_without_run_lease(self) -> None:
        for actor, command in (("Human/Ops", "reopen"), ("Claude", "approve")):
            with (
                self.subTest(actor=actor),
                mock.patch.dict(os.environ, {"AI_NAME": actor}, clear=True),
                mock.patch.object(ai_status, "STATUS_ROOT", self.root),
                mock.patch.object(ai_status, "STATUS_FILE", self.status_file),
            ):
                with self.assertRaisesRegex(RuntimeError, "exact active worker lease"):
                    ai_status.validate_active_status_command_lease(
                        command, [self.task_id, "spoofed mutation"]
                    )

    def test_local_human_ops_cannot_impersonate_reviewer_or_owner_actions(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Claude",
                ai_status.LOCAL_HUMAN_OPS_ENV: "1",
            },
            clear=True,
        ):
            self.assertEqual(ai_status.current_actor(), "Human/Ops")
            for command in ("approve", "done", "handoff", "progress"):
                with self.subTest(command=command), self.assertRaisesRegex(
                    RuntimeError, "local Human/Ops cannot authorize"
                ):
                    ai_status.validate_active_status_command_lease(
                        command, [self.task_id, "attempted role impersonation"]
                    )

    def test_local_human_ops_allows_explicit_board_maintenance_actions(self) -> None:
        with mock.patch.dict(
            os.environ,
            {ai_status.LOCAL_HUMAN_OPS_ENV: "1"},
            clear=True,
        ):
            for command in ai_status.LOCAL_HUMAN_OPS_ACTIONS:
                with self.subTest(command=command):
                    ai_status.validate_active_status_command_lease(
                        command,
                        [self.task_id, "explicit local maintenance"],
                    )
            self.assertEqual(ai_status.current_actor(), "Human/Ops")

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


class TerminalFactRepairTests(unittest.TestCase):
    def test_local_human_ops_records_missing_historical_terminal_dependency(self) -> None:
        state = ai_status.default_state()
        state["tasks"] = []
        with (
            mock.patch.object(ai_status, "current_actor", return_value="Human/Ops"),
            mock.patch.object(ai_status, "append_log") as append_log,
        ):
            ai_status.command_record_terminal_fact(
                state, ["HISTORICAL-DONE", "2", "completed"]
            )

        fact = state[ai_status.TERMINAL_FACTS_KEY]["HISTORICAL-DONE"]
        self.assertEqual(fact["generation"], 2)
        self.assertEqual(fact["terminal_outcome"], "completed")
        self.assertEqual(append_log.call_args.args[0]["type"], "terminal_fact_recorded")

    def test_terminal_fact_rejects_active_task_collision(self) -> None:
        state = ai_status.default_state()
        state["tasks"] = [
            {
                "id": "ACTIVE-TASK",
                "generation": 1,
                "owner": "Codex",
                "reviewer": "Codex2",
                "status": "todo",
            }
        ]
        with mock.patch.object(ai_status, "current_actor", return_value="Human/Ops"):
            with self.assertRaisesRegex(SystemExit, "active task"):
                ai_status.command_record_terminal_fact(
                    state, ["ACTIVE-TASK", "1", "completed"]
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
        self.journal = (
            self._test_root.parent
            / f"{self._test_root.name}-runtime"
            / "task-state-events.jsonl"
        )
        self.bridge_private_key = Ed25519PrivateKey.from_private_bytes(
            hashlib.sha256(b"bridge-test-key-for-ai-status-p0-authority").digest()
        )
        bridge_public_key = self.bridge_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                ai_status.TASK_STATE_EVENT_LOG_ENV: str(self.journal),
                common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                    self._test_root,
                    self.journal,
                ),
                "AI_NAME": "Human/Ops",
                "ORCH_RUN_ID": "",
                "ORCH_TASK_ID": "",
                "ORCH_AGENT_ID": "",
                "ORCH_PROVIDER": "",
                "ORCH_SESSION_ID": "",
                "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": json.dumps(
                    {
                        "assistant-bridge-dev": base64.urlsafe_b64encode(
                            bridge_public_key
                        ).decode().rstrip("=")
                    }
                ),
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

    def _task_spec(
        self,
        task_id: str,
        *,
        owner: str = "Codex",
        reviewer: str = "Antigravity",
        depends_on: list[str] | None = None,
        execution_resources: list[str] | None = None,
    ) -> dict[str, Any]:
        spec = {
            "id": task_id,
            "title": f"Title for {task_id}",
            "owner": owner,
            "reviewer": reviewer,
            "phase": "Canonical Task Materialization",
            "depends_on": list(depends_on or []),
            "artifacts": ["docs/deployment/evidence/" + task_id + "/"],
            "acceptance": ["Do the thing"],
            "summary": f"Summary for {task_id}",
        }
        if execution_resources is not None:
            spec["execution_resources"] = list(execution_resources)
        return spec

    def _task_row(
        self,
        task_id: str,
        *,
        packet_id: str = "pkt-test-20260811T000000Z",
        packet_digest: str | None = None,
        owner: str = "Codex",
        reviewer: str = "Antigravity",
        depends_on: list[str] | None = None,
        execution_resources: list[str] | None = None,
    ) -> dict[str, Any]:
        spec = self._task_spec(
            task_id,
            owner=owner,
            reviewer=reviewer,
            depends_on=depends_on,
            execution_resources=execution_resources,
        )
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

    def _payload_path(
        self,
        rows: list[dict[str, Any]],
        *,
        packet_id: str,
        packet_digest: str,
        work_class: str = "security",
        include_authorization: bool = True,
    ) -> Path:
        now = datetime.now(timezone.utc)
        signed_packet = {
            "version": "pantheon.assistant.dev-task.v1",
            "packet_id": packet_id,
            "intent": "governed_supervisor_architecture_cutover",
            "emitted_at": now.isoformat().replace("+00:00", "Z"),
            "actor": {"id": "management-ai", "roles": ["source"], "capabilities": ["assistant.dev.source"]},
            "work_class": work_class,
            "mode": "kernel_debug",
            "source_conversation_id": "conversation-20260811",
            "source_turn_ids": ["turn-1"],
            "documents": [],
            "tasks": [deepcopy(row["task_metadata"]["dev_bridge"]["task_spec"]) for row in rows],
            "constraints": {"allowed_repos": ["pantheon"], "requires_branch_pr_merge": True, "no_direct_shell_from_web": True},
            "audit_conversation_href": None,
            "signature": None,
        }
        if include_authorization:
            signed_packet["operator_authorization"] = {
                "operator_id": "op-test",
                "control_activation_id": "activation-test",
                "capability": "assistant.canonical.mutate",
                "mfa_verified": True,
                "issued_at": now.isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "nonce": packet_id + "-nonce",
            }
        body = deepcopy(signed_packet)
        body.pop("signature")
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        packet_digest = hashlib.sha256(canonical).hexdigest()
        signed_packet["signature"] = {
            "key_id": "assistant-bridge-dev",
            "algorithm": "Ed25519",
            "value": base64.urlsafe_b64encode(
                self.bridge_private_key.sign(canonical)
            ).decode().rstrip("="),
        }
        for row in rows:
            row["task_metadata"]["dev_bridge"]["packet_digest"] = packet_digest
            row["task_metadata"]["dev_bridge"]["work_class"] = work_class
            row["task_metadata"]["dev_bridge"]["operator_authorization_required"] = (
                work_class not in {"functional", "paper", "read_only", "ci", "reconcile_only"}
            )
        payload = self._test_root / "batch.json"
        payload.write_text(
            json.dumps(
                {
                    "schema_version": ai_status.DEV_BRIDGE_BATCH_SCHEMA_VERSION,
                    "packet_id": packet_id,
                    "packet_digest": packet_digest,
                    "actor": "assistant.dev.source",
                    "signed_packet": signed_packet,
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

    def test_bridge_consume_retires_obsolete_operator_assertion_ledger(self) -> None:
        packet_id = "pkt-ledger-isolation-20260811T000000Z"
        row = self._task_row("LEDGER-ISOLATION", packet_id=packet_id)
        payload = self._payload_path([row], packet_id=packet_id, packet_digest="unused")
        batch = ai_status.load_dev_bridge_materialize_batch(str(payload))
        state = {
            "consumed_canonical_mutation_assertions": {
                "op_keep": {
                    "nonce": "operator-nonce",
                    "consumed_at": "2026-01-01T00:00:00Z",
                }
            }
        }

        ai_status.verify_signed_dev_bridge_packet(batch, state=state)

        self.assertNotIn("consumed_canonical_mutation_assertions", state)
        self.assertEqual(len(state["consumed_dev_bridge_packets"]), 1)

    def test_functional_packet_does_not_require_operator_authorization(self) -> None:
        packet_id = "pkt-functional-without-auth-20260825T000000Z"
        row = self._task_row("FUNCTIONAL-WITHOUT-AUTH", packet_id=packet_id)
        payload = self._payload_path(
            [row],
            packet_id=packet_id,
            packet_digest="unused",
            work_class="functional",
            include_authorization=False,
        )

        self.assertEqual(self._run_main(payload), 0)
        task = ai_status.get_task(ai_status.load_state(), "FUNCTIONAL-WITHOUT-AUTH")
        self.assertEqual(task["dev_bridge"]["work_class"], "functional")
        self.assertFalse(task["dev_bridge"]["operator_authorization_required"])

    def test_security_packet_without_operator_authorization_remains_blocked(self) -> None:
        packet_id = "pkt-security-without-auth-20260825T000000Z"
        row = self._task_row("SECURITY-WITHOUT-AUTH", packet_id=packet_id)
        payload = self._payload_path(
            [row],
            packet_id=packet_id,
            packet_digest="unused",
            work_class="security",
            include_authorization=False,
        )

        with self.assertRaisesRegex(
            SystemExit, "source and operator authorization must be separate"
        ):
            self._run_main(payload)
        self.assertEqual(len(load_events(self.journal)), 1)

    def test_batch_commits_every_task_in_exactly_one_journal_event(self) -> None:
        packet_id = "pkt-batch-ok-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [
            self._task_row("BATCH-ATOMIC-ONE", packet_id=packet_id, packet_digest=digest),
            self._task_row("BATCH-ATOMIC-TWO", packet_id=packet_id, packet_digest=digest),
        ]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)
        bound_digest = ai_status.load_dev_bridge_materialize_batch(str(payload))["packet_digest"]

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
            self.assertEqual(task["dev_bridge"]["packet_digest"], bound_digest)

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

    def test_batch_rejects_dependency_missing_from_canonical_state(self) -> None:
        packet_id = "pkt-batch-missing-dependency-20260815T000000Z"
        row = self._task_row(
            "BATCH-DEPENDENCY-ONE",
            packet_id=packet_id,
            depends_on=["TERMINAL-FACT-NOT-PRESENT"],
        )
        payload = self._payload_path([row], packet_id=packet_id, packet_digest="unused")

        with self.assertRaisesRegex(SystemExit, "unresolved canonical dependencies"):
            self._run_main(payload)

        self.assertEqual(len(load_events(self.journal)), 1)

    def test_batch_accepts_dependency_in_same_atomic_packet(self) -> None:
        packet_id = "pkt-batch-closed-dependency-20260815T000000Z"
        root = self._task_row("BATCH-ROOT", packet_id=packet_id)
        child = self._task_row(
            "BATCH-CHILD",
            packet_id=packet_id,
            depends_on=["BATCH-ROOT"],
        )
        payload = self._payload_path([root, child], packet_id=packet_id, packet_digest="unused")

        self.assertEqual(self._run_main(payload), 0)
        state = ai_status.load_state()
        self.assertIsNotNone(ai_status.get_task(state, "BATCH-ROOT"))
        self.assertIsNotNone(ai_status.get_task(state, "BATCH-CHILD"))

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

        # Operator authority is one-shot even when packet provenance is exact.
        with self.assertRaisesRegex(SystemExit, "already consumed"):
            self._run_main(payload)
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
        with self.assertRaisesRegex(SystemExit, "already consumed"):
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
            self.assertRaisesRegex(RuntimeError, "exact active worker lease"),
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

    def test_projection_crash_after_commit_keeps_one_authoritative_event(self) -> None:
        packet_id = "pkt-batch-postcommit-crash-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [
            self._task_row(
                "BATCH-POSTCOMMIT-ONE",
                packet_id=packet_id,
                packet_digest=digest,
            )
        ]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        with (
            mock.patch.object(
                ai_status,
                "refresh_derived_status_views_if_current",
                side_effect=OSError("injected projection crash"),
            ),
            self.assertRaisesRegex(OSError, "injected projection crash"),
        ):
            self._run_main(payload)

        self.assertEqual(len(load_events(self.journal)), 2)
        state = ai_status.load_state()
        self.assertIsNotNone(
            ai_status.get_task(state, "BATCH-POSTCOMMIT-ONE")
        )
        before_readback = len(load_events(self.journal))
        readback = self._run_readback(payload)
        self.assertEqual(readback["status"], "verified")
        self.assertEqual(len(load_events(self.journal)), before_readback)

    def test_digest_mismatch_between_batch_and_row_fails_closed(self) -> None:
        packet_id = "pkt-batch-mismatch-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        other_digest = hashlib.sha256(b"different-payload").hexdigest()
        rows = [self._task_row("BATCH-MISMATCH-ONE", packet_id=packet_id, packet_digest=other_digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)
        raw = json.loads(payload.read_text(encoding="utf-8"))
        raw["tasks"][0]["task_metadata"]["dev_bridge"]["packet_digest"] = other_digest
        payload.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "packet_digest does not match the batch"):
            ai_status.load_dev_bridge_materialize_batch(str(payload))

    def test_forged_self_consistent_batch_without_matching_signed_packet_fails(self) -> None:
        packet_id = "pkt-forged-batch-20260811T000000Z"
        rows = [self._task_row("BATCH-FORGED-ONE", packet_id=packet_id)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest="0" * 64)
        batch = ai_status.load_dev_bridge_materialize_batch(str(payload))
        batch["tasks"][0]["owner"] = "Claude2"
        batch["tasks"][0]["task_metadata"]["dev_bridge"]["task_spec"]["owner"] = "Claude2"
        with self.assertRaisesRegex(SystemExit, "owner binding failed"):
            ai_status.verify_signed_dev_bridge_packet(batch)

    def test_durable_signed_packet_remains_valid_after_short_admission_expiry(self) -> None:
        packet_id = "pkt-durable-expiry-20260811T000000Z"
        rows = [self._task_row("BATCH-DURABLE-ONE", packet_id=packet_id)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest="0" * 64)
        raw = json.loads(payload.read_text(encoding="utf-8"))
        auth = raw["signed_packet"]["operator_authorization"]
        auth["issued_at"] = "2026-08-11T00:00:00Z"
        auth["expires_at"] = "2026-08-11T00:05:00Z"
        body = deepcopy(raw["signed_packet"])
        body.pop("signature")
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        raw["packet_digest"] = digest
        raw["signed_packet"]["signature"]["value"] = base64.urlsafe_b64encode(
            self.bridge_private_key.sign(canonical)
        ).decode().rstrip("=")
        raw["tasks"][0]["task_metadata"]["dev_bridge"]["packet_digest"] = digest
        payload.write_text(json.dumps(raw), encoding="utf-8")
        ai_status.verify_signed_dev_bridge_packet(
            ai_status.load_dev_bridge_materialize_batch(str(payload))
        )

    def test_owner_reassignment_after_materialization_then_replay_row_is_a_no_op(self) -> None:
        """A later Human/Ops reassignment must not be clobbered by an exact replay."""

        packet_id = "pkt-batch-reassign-20260811T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [self._task_row("BATCH-REASSIGN-ONE", packet_id=packet_id, packet_digest=digest)]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)
        self.assertEqual(self._run_main(payload), 0)

        with mock.patch.object(ai_status, "validate_status_command_runtime_binding"), \
             mock.patch.object(ai_status, "validate_status_root_binding"), \
             self.assertRaisesRegex(RuntimeError, "exact active worker lease"):
            ai_status.main(["ai_status.py", "assign", "BATCH-REASSIGN-ONE", "Claude", "Codex2"])
        reassigned = ai_status.load_state()
        task = ai_status.get_task(reassigned, "BATCH-REASSIGN-ONE")
        self.assertEqual(task["owner"], "Codex")
        self.assertEqual(task["reviewer"], "Antigravity")
        self.assertEqual(task["dev_bridge"]["task_spec"]["owner"], "Codex")

        with self.assertRaisesRegex(SystemExit, "already consumed"):
            self._run_main(payload)
        state = ai_status.load_state()
        task = ai_status.get_task(state, "BATCH-REASSIGN-ONE")
        self.assertEqual(task["owner"], "Codex")
        self.assertEqual(task["reviewer"], "Antigravity")

    def test_human_ops_reassignment_is_not_blocked_by_retired_wave_state(self) -> None:
        state = {
            "agents": [
                {"name": "Codex", "current_task_ids": ["WAVE-RETIRED-ONE"]},
                {"name": "Codex2", "current_task_ids": []},
                {"name": "Claude", "current_task_ids": []},
            ],
            "tasks": [
                {
                    "id": "WAVE-RETIRED-ONE",
                    "title": "Retired wave guard",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "status": "todo",
                    "depends_on": [],
                    "artifacts": [],
                    "last_update": "2026-08-11T00:00:00Z",
                }
            ],
            "handoffs": [],
            "blockers": [],
            "wave_state": {"status": "frozen", "current_wave_id": "legacy"},
        }
        with mock.patch.dict(
            os.environ,
            {"AI_NAME": "Human/Ops", "TASK_ASSIGN_REASON": "operator recall"},
            clear=False,
        ):
            ai_status.command_assign(
                state,
                ["WAVE-RETIRED-ONE", "Codex2", "Claude"],
            )

        task = ai_status.get_task(state, "WAVE-RETIRED-ONE")
        self.assertEqual(task["owner"], "Codex2")
        self.assertEqual(task["reviewer"], "Claude")
        self.assertEqual(task["generation"], 2)

    def test_batch_materializes_and_reads_back_with_execution_resources(self) -> None:
        packet_id = "pkt-exec-res-20260825T000000Z"
        digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        rows = [
            self._task_row(
                "BATCH-RES-ONE",
                packet_id=packet_id,
                packet_digest=digest,
                execution_resources=["pantheon-dev"],
            )
        ]
        payload = self._payload_path(rows, packet_id=packet_id, packet_digest=digest)

        result = self._run_main(payload)
        self.assertEqual(result, 0)

        readback = self._run_readback(payload)
        self.assertEqual(readback["status"], "verified")
        self.assertEqual(readback["packetId"], packet_id)
        self.assertEqual(len(readback["tasks"]), 1)
        self.assertEqual(readback["tasks"][0]["taskId"], "BATCH-RES-ONE")

        events = load_events(self.journal)
        committed_task = events[1]["state"]["tasks"][-1]
        self.assertEqual(committed_task["execution_resources"], ["pantheon-dev"])

    def test_bridge_assignment_rejects_unallowlisted_execution_resources(self) -> None:
        digest = hashlib.sha256(b"dummy-packet").hexdigest()
        spec = {
            "id": "INVALID-RES-TASK",
            "title": "Invalid resource task",
            "owner": "Codex",
            "reviewer": "Claude",
            "execution_resources": ["forbidden-resource"],
            "depends_on": [],
            "artifacts": [],
            "acceptance": [],
        }
        spec_hash = hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        metadata = {
            "dev_bridge": {
                "packet_id": "pkt-invalid-res-20260825",
                "packet_digest": digest,
                "task_spec_hash": spec_hash,
                "task_spec": spec,
                "conversation_id": "conv-1",
                "source_turn_ids": [],
                "documents": [],
            }
        }
        with self.assertRaisesRegex(SystemExit, "contains an unallowlisted resource"):
            ai_status._bridge_assignment_from_metadata(
                metadata,
                task_id="INVALID-RES-TASK",
                owner="Codex",
                reviewer="Claude",
                title="Invalid resource task",
            )


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
                "generation": 1,
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
            "scripts/human-ops-status.sh",
            "scripts/loop_done_guardrail.py",
            ".orchestrator/common.py",
            ".orchestrator/dispatch_policy.py",
            ".orchestrator/runtime_state.py",
            ".orchestrator/task_archive.py",
            ".orchestrator/multi_repo_registry.py",
            ".orchestrator/rewrite/__init__.py",
            ".orchestrator/rewrite/task_machine.py",
            ".orchestrator/rewrite/task_state_store.py",
            ".orchestrator/config.json",
        ):
            source = repo_root / rel
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _commit_all(self, repo: Path, message: str = "install status tooling") -> str:
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=repo, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

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
        self.assertNotIn("event_queue", config["paths"])

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
            task_state_event_log = root / "runtime" / "task-state-events-v2.jsonl"
            ai_status.append_state_commit(
                task_state_event_log,
                json.loads((central / "ai-status.json").read_text(encoding="utf-8")),
                source="test-fixture",
            )
            self._write_status_state(
                worktree,
                owner="Claude2",
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
                                "task_generation": 1,
                                "status": "running",
                                "lease_acquired_at": "2026-07-17T00:00:00Z",
                                "lease_expires_at": "2999-01-01T00:00:00Z",
                                "queue_event_id": "evt-codex-test-run",
                                "pid": worker_pid,
                                "pid_start_ticks": worker_pid_start_ticks,
                                "process_generation": worker_process_generation,
                                "workspace_path": str(worktree),
                                "workspace_repository_id": "pantheon",
                                "status_root": str(central),
                                "status_command_runtime": issued_runtime,
                                "request_snapshot": {
                                    "task_id": "CENTRAL-ROOT-001",
                                    "task_generation": 1,
                                    "metadata": {
                                        "task_generation": 1,
                                        "workspace_task_id": "CENTRAL-ROOT-001",
                                        "workspace_path": str(worktree),
                                        "workspace_repository_id": "pantheon",
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
                                    "repository_id": "pantheon",
                                    "status_root": str(central),
                                    "last_queue_event_id": "evt-codex-test-run",
                                    "last_target_agent": "Codex2",
                                    "last_used_at": "2026-07-17T00:00:00Z",
                                }
                            }
                        },
                        "queue": {
                            "version": 2,
                            "events": {
                                "evt-codex-test-run": {
                                    "status": "running",
                                    "intent": {
                                        "event_id": "evt-codex-test-run",
                                        "task_id": "CENTRAL-ROOT-001",
                                        "task_generation": 1,
                                        "target_agent": "codex2_1",
                                    },
                                }
                            },
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
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
                    "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                    "PANTHEON_TASK_STATE_EVENT_LOG": str(task_state_event_log),
                    common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                        central,
                        task_state_event_log,
                    ),
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

            def bind_runtime_actor(logical_actor: str, agent_id: str) -> None:
                runtime = json.loads(central_state_path.read_text(encoding="utf-8"))
                runtime["workers"]["codex-test-run"]["logical_agent_id"] = logical_actor
                runtime["workers"]["codex-test-run"]["agent_id"] = agent_id
                central_state_path.write_text(
                    json.dumps(runtime, indent=2) + "\n", encoding="utf-8"
                )

            bind_runtime_actor("antigravity", "antigravity_1")
            reopen = run_status(
                ["reopen", "CENTRAL-ROOT-001", "review requested central changes only"],
                actor="Antigravity",
                extra_env={"ORCH_RUN_ID": "codex-test-run"},
            )
            self.assertEqual(reopen.returncode, 0, reopen.stderr + reopen.stdout)
            bind_runtime_actor("codex2", "codex2_1")
            second_handoff = run_status(
                ["handoff", "CENTRAL-ROOT-001", "Antigravity", "central second review only"],
                actor="Codex2",
            )
            self.assertEqual(second_handoff.returncode, 0, second_handoff.stderr + second_handoff.stdout)
            bind_runtime_actor("antigravity", "antigravity_1")
            approve = run_status(
                ["approve", "CENTRAL-ROOT-001", "central approval only"],
                actor="Antigravity",
                extra_env={
                    "REVIEW_NOTES_ZH": "central approve",
                    "ORCH_RUN_ID": "codex-test-run",
                },
            )
            self.assertEqual(approve.returncode, 0, approve.stderr + approve.stdout)
            bind_runtime_actor("codex2", "codex2_1")
            delivery_worktree = root / "registered-delivery-worktree"
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    "task/CENTRAL-ROOT-001",
                    str(delivery_worktree),
                    "HEAD",
                ],
                cwd=central,
                check=True,
                capture_output=True,
                text=True,
            )
            runtime = json.loads(central_state_path.read_text(encoding="utf-8"))
            worker = runtime["workers"]["codex-test-run"]
            worker["workspace_path"] = str(delivery_worktree)
            worker["request_snapshot"]["metadata"]["workspace_path"] = str(
                delivery_worktree
            )
            runtime["worker_worktrees"]["leases"]["CENTRAL-ROOT-001"][
                "path"
            ] = str(delivery_worktree)
            central_state_path.write_text(
                json.dumps(runtime, indent=2) + "\n", encoding="utf-8"
            )
            env["PANTHEON_WORKTREE_ROOT"] = str(delivery_worktree)
            env["ORCH_WORKSPACE_PATH"] = str(delivery_worktree)
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
                expected_delivery_root = (
                    delivery_worktree if event.get("type") == "done" else worktree
                )
                self.assertEqual(
                    event["status_command"]["delivery_root"],
                    str(expected_delivery_root.resolve()),
                )
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
                        "task_generation": 1,
                        "actor": "Codex2",
                        "workspace_repository_id": "pantheon",
                        "workspace_branch": "",
                        "workspace_source_root": "",
                    },
                )
            self.assertEqual(
                archived["task"]["delivery"]["repository_path"],
                str(delivery_worktree.resolve()),
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
                owner="Claude2",
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


class ReviewApprovedWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.state = {
            "agents": [
                {"name": "Codex", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
                {"name": "Claude2", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
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
        task = self.state["tasks"][0]
        contract = ai_status._delivery_contract_payload(task)
        task[ai_status.DELIVERY_BINDING_KEY] = {
            "kind": "artifact_contract",
            **contract,
            "contract_sha256": ai_status._canonical_json_sha256(contract),
        }

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def test_functional_milestone_can_close_without_closing_task(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Codex",
                    "TASK_MILESTONE_EVIDENCE": "e2e/paper.json||run-123",
                },
                clear=False,
            ),
            mock.patch.object(ai_status, "append_log"),
        ):
            ai_status.command_milestone(
                self.state,
                ["REG-002", "functional", "done", "Paper functional proof complete"],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["completion_tracks"]["functional"]["status"], "done")
        self.assertEqual(
            task["completion_tracks"]["functional"]["evidence"],
            ["e2e/paper.json", "run-123"],
        )

    def test_done_milestone_requires_evidence(self) -> None:
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "requires TASK_MILESTONE_EVIDENCE"),
        ):
            ai_status.command_milestone(
                self.state,
                ["REG-002", "functional", "done", "Missing proof"],
            )

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
            _command_approve(self.state, ["REG-002", "Review passed. Owner should finalize."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")
        self.assertNotIn("failure_streak", task)
        self.assertEqual(task["review_notes_zh"], ["審查通過", "交回 owner 收尾"])

        pending = [handoff for handoff in self.state["handoffs"] if handoff["status"] != "done"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Claude")
        self.assertEqual(pending[0]["to"], "Codex")
        self.assertIn("finalize", pending[0]["message"].lower())

    def test_approve_command_has_no_inline_network_fallback(self) -> None:
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "resolve_approval_binding",
                side_effect=AssertionError("network preflight ran under mutation"),
            ),
            self.assertRaisesRegex(RuntimeError, "lock-free external mutation preflight"),
        ):
            ai_status.command_approve(self.state, ["REG-002", "Approved."])

    def test_external_preflight_task_digest_is_a_short_locked_cas(self) -> None:
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(ai_status, "resolve_approval_binding", return_value={}),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
                return_value=None,
            ),
        ):
            task = ai_status.get_task(self.state, "REG-002")
            preflight = ai_status.prepare_external_mutation_preflight(
                "approve", task, ["REG-002", "Approved."]
            )
            task["next"] = "concurrent writer changed this task"
            with (
                ai_status.bound_external_mutation_preflight(preflight),
                self.assertRaisesRegex(SystemExit, "changed after external review evidence"),
            ):
                ai_status.command_approve(self.state, ["REG-002", "Approved."])

        self.assertEqual(task["status"], "review")

    def test_canonical_preflight_releases_task_lock_before_external_io(self) -> None:
        held = {"task": False}

        @contextlib.contextmanager
        def task_lock(*, shared: bool):
            self.assertTrue(shared)
            held["task"] = True
            try:
                yield
            finally:
                held["task"] = False

        def prepare(command, task, args):
            self.assertFalse(held["task"])
            return {
                "command": command,
                "task_id": task["id"],
                "task_digest": ai_status.task_mutation_cas_digest(task),
            }

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude", "ORCH_RUN_ID": ""}, clear=False),
            mock.patch.object(ai_status, "canonical_task_state_lock", side_effect=task_lock),
            mock.patch.object(
                ai_status,
                "authoritative_task_state_transaction",
                return_value=contextlib.nullcontext(),
            ),
            mock.patch.object(ai_status, "load_state", return_value=self.state),
            mock.patch.object(ai_status, "validate_active_status_command_lease"),
            mock.patch.object(
                ai_status,
                "prepare_external_mutation_preflight",
                side_effect=prepare,
            ) as external,
        ):
            result = ai_status.canonical_external_mutation_preflight(
                "approve", ["REG-002", "Approved."]
            )

        self.assertEqual(result["task_id"], "REG-002")
        external.assert_called_once()

    def _approval_events(self) -> list[dict]:
        lines = self._test_log_file.read_text(encoding="utf-8").splitlines()
        return [
            json.loads(line)
            for line in lines
            if line.strip() and json.loads(line).get("type") == "review_approved"
        ]

    def _set_artifact_delivery_binding(self, task: dict[str, Any] | None = None) -> None:
        target = task or self.state["tasks"][0]
        contract = ai_status._delivery_contract_payload(target)
        target[ai_status.DELIVERY_BINDING_KEY] = {
            "kind": "artifact_contract",
            **contract,
            "contract_sha256": ai_status._canonical_json_sha256(contract),
        }

    def _set_pr_delivery_binding(
        self,
        *,
        pr: int,
        head_sha: str,
        task: dict[str, Any] | None = None,
    ) -> None:
        target = task or self.state["tasks"][0]
        target[ai_status.DELIVERY_BINDING_KEY] = {
            "kind": "pull_request",
            "pr": pr,
            "head_sha": head_sha.lower(),
            "head_branch": f"task/{target['id']}",
            "base": "dev",
        }

    def test_approve_records_the_reviewed_pr_head_binding(self) -> None:
        """The merge gate compares these identities; free text is not a binding."""

        self._set_pr_delivery_binding(pr=4218, head_sha="b" * 40)

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
            _command_approve(
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
        self._set_pr_delivery_binding(pr=4269, head_sha="a" * 40)
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
            _command_approve(
                self.state,
                ["REG-002", "Internal approval alone is insufficient."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("review_binding", task)
        self.assertNotIn("github_review_bridge", task)
        self.assertEqual(self._approval_events(), [])

    def test_approve_binding_mismatch_requires_reopen_and_preserves_state(self) -> None:
        self._set_pr_delivery_binding(pr=4269, head_sha="a" * 40)
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                side_effect=ai_status.ReviewBindingMismatchError(
                    "GitHub PR #4269 head differs"
                ),
            ),
            self.assertRaisesRegex(SystemExit, "Reopen the task"),
        ):
            _command_approve(
                self.state,
                ["REG-002", "Do not approve a substituted head."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertNotIn(ai_status.APPROVAL_BINDING_KEY, task)
        self.assertNotIn(ai_status.GITHUB_REVIEW_BRIDGE_KEY, task)
        self.assertEqual(self._approval_events(), [])

    def test_approve_uses_only_the_handoff_pr_binding(self) -> None:
        binding = {
            "pr": 4218,
            "head_sha": "c" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        self.state["tasks"][0][ai_status.DELIVERY_BINDING_KEY] = {
            "kind": "pull_request",
            **binding,
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
            mock.patch.object(
                ai_status, "bridge_github_review_decision", return_value=bridge_evidence
            ) as github_bridge,
        ):
            _command_approve(
                self.state,
                ["REG-002", "Approved against the handoff head."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["review_binding"], binding)
        github_bridge.assert_called_once_with(
            task,
            actor="Claude",
            decision="approve",
            message="Approved against the handoff head.",
            binding=binding,
        )

    def test_approve_refuses_missing_handoff_delivery_binding(self) -> None:
        task = self.state["tasks"][0]
        task.pop(ai_status.DELIVERY_BINDING_KEY)
        task["source_ref"] = {"pr": 4218, "head_sha": "c" * 40}
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            self.assertRaisesRegex(SystemExit, "no handoff delivery binding"),
        ):
            _command_approve(self.state, ["REG-002", "Approved."])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("review_binding", task)

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
                    _command_approve(
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
        self._set_pr_delivery_binding(pr=4218, head_sha="a" * 40)
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
                _command_approve(
                    self.state,
                    ["REG-002", "Approved."],
                )

        self.assertEqual(ai_status.get_task(self.state, "REG-002")["status"], "review")

    def test_approve_refuses_a_head_sha_without_a_pr_number(self) -> None:
        self._set_pr_delivery_binding(pr=4218, head_sha="b" * 40)
        with mock.patch.dict(
            os.environ,
            {"AI_NAME": "Claude", "REVIEW_HEAD_SHA": "b" * 40},
            clear=False,
        ):
            with self.assertRaisesRegex(SystemExit, "REVIEW_PR"):
                _command_approve(
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
            _command_approve(
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
            _command_approve(
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
        self._set_artifact_delivery_binding(task)
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
            _command_approve(
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
        self._set_artifact_delivery_binding(task)
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
            _command_approve(
                self.state,
                ["L12-CLOSE-001", "Manifest outside the trusted roots."],
            )

        blocked = ai_status.get_task(self.state, "L12-CLOSE-001")
        self.assertEqual(blocked["status"], "review")
        self.assertNotIn("protected_closeout_verdict", blocked)

    def test_progress_rejects_todo_without_start(self) -> None:
        self.state["tasks"][0]["status"] = "todo"

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "cannot progress"),
        ):
            ai_status.command_progress(
                self.state, ["REG-002", "Implementation started"]
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "todo")

    def test_progress_rejects_review_approved(self) -> None:
        self.state["tasks"][0]["status"] = "review_approved"

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "cannot progress"),
        ):
            ai_status.command_progress(
                self.state, ["REG-002", "Checking status before finalization"]
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "review_approved")

    def test_commands_reject_illegal_source_states_without_overwrite(self) -> None:
        task = self.state["tasks"][0]
        cases = [
            (
                "review",
                "Codex",
                lambda: ai_status.command_start(
                    self.state, ["REG-002", "Illegal restart"]
                ),
                "cannot start",
            ),
            (
                "todo",
                "Codex",
                lambda: ai_status.command_handoff(
                    self.state, ["REG-002", "Claude", "Illegal handoff"]
                ),
                "cannot handoff",
            ),
            (
                "done",
                "Human/Ops",
                lambda: _command_reopen(
                    self.state, ["REG-002", "Illegal terminal reopen"]
                ),
                "cannot reopen",
            ),
            (
                "in_progress",
                "Claude",
                lambda: _command_approve(
                    self.state, ["REG-002", "Illegal early approval"]
                ),
                "cannot approve",
            ),
        ]
        for status, actor, command, error in cases:
            with self.subTest(status=status, actor=actor):
                task["status"] = status
                with (
                    mock.patch.dict(os.environ, {"AI_NAME": actor}, clear=False),
                    self.assertRaisesRegex(SystemExit, error),
                ):
                    command()
                self.assertEqual(task["status"], status)

    def test_done_requires_owner_and_review_approved(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit):
                _command_done(self.state, ["REG-002", "Attempted direct completion"])

        self.state["tasks"][0]["status"] = "review_approved"

        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                _command_done(self.state, ["REG-002", "Reviewer cannot finalize"])

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "collect_done_delivery_metadata", return_value={}),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_done(self.state, ["REG-002", "Owner finalized approved task"])

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
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_done(
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
            _command_done(self.state, ["REG-002", "Owner adds evidence post-approval"])

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
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_done(self.state, ["REG-002", "Owner binds the already-reviewed manifest"])

        archive_task = self.state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]["snapshots"][0]["task"]
        self.assertEqual(archive_task["status"], "done")
        self.assertEqual(archive_task["review_file"], review_file)

    def test_approve_refuses_a_review_file_not_present_at_the_reviewed_head(self) -> None:
        self._set_pr_delivery_binding(pr=4218, head_sha="b" * 40)
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
            _command_approve(self.state, ["REG-002", "Claiming evidence that is not really there"])

        self.assertEqual(ai_status.get_task(self.state, "REG-002")["status"], "review")

    def test_approve_accepts_a_review_file_present_at_the_reviewed_head(self) -> None:
        self._set_pr_delivery_binding(pr=4218, head_sha="b" * 40)
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
            _command_approve(self.state, ["REG-002", "Evidence verified present at the head"])

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
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_done(
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
            _command_done(
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
                _command_reconcile_merged_done(
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
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_reconcile_merged_done(
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
        self.state["tasks"][0]["reviewer"] = "Claude2"
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "Only Human/Ops or the task's current reviewer"):
                _command_reconcile_merged_done(
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
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_reconcile_merged_done(
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
            _command_reconcile_merged_done(
                self.state,
                ["REG-002", "Attempt protected recovery."],
            )

        self.assertEqual(task["status"], "review_approved")
        self.assertNotIn("terminal_outcome", task)

    def test_reconcile_merged_done_accepts_human_ops_tooling_delivery(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "blocked"
        task["task_class"] = "development_tooling"
        delivery = {
            "reconciled_from_tooling_delivery": True,
            "delivery_class": "development_tooling",
            "commit": "a" * 40,
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Human/Ops",
                    "RECONCILE_DELIVERY_CLASS": "development_tooling",
                },
                clear=False,
            ),
            mock.patch.object(
                ai_status,
                "validate_merged_tooling_done",
                return_value=delivery,
            ),
            mock.patch.object(
                ai_status,
                "validate_protected_closeout_transition",
            ) as protected,
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            _command_reconcile_merged_done(
                self.state,
                ["REG-002", "Operator-authorized tooling delivery reconciled."],
            )

        terminal = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(terminal["status"], "done")
        self.assertEqual(terminal["terminal_outcome"], "completed")
        self.assertTrue(terminal["delivery"]["reconciled_from_tooling_delivery"])
        protected.assert_not_called()

    def test_reconcile_merged_done_tooling_mode_rejects_product_task(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "blocked"
        task["task_class"] = "product"
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Human/Ops",
                    "RECONCILE_DELIVERY_CLASS": "development_tooling",
                },
                clear=False,
            ),
            self.assertRaisesRegex(SystemExit, "task_class=development_tooling"),
        ):
            _command_reconcile_merged_done(
                self.state,
                ["REG-002", "Invalid product tooling reconcile."],
            )

    def test_reconcile_merged_done_tooling_mode_rejects_reviewer(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "blocked"
        task["task_class"] = "development_tooling"
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AI_NAME": "Claude",
                    "RECONCILE_DELIVERY_CLASS": "development_tooling",
                },
                clear=False,
            ),
            self.assertRaisesRegex(SystemExit, "Only Human/Ops"),
        ):
            _command_reconcile_merged_done(
                self.state,
                ["REG-002", "Reviewer cannot use direct tooling reconcile."],
            )

    def test_validate_merged_tooling_done_binds_task_id(self) -> None:
        task = {
            "id": "REG-002",
            "task_class": "development_tooling",
        }
        delivery = {
            "repository_id": "pantheon",
            "repository_slug": "ajoe734/pantheon",
            "repository_path": "/tmp/pantheon",
            "commit": "a" * 40,
            "merge_target_ref": "origin/dev",
            "merge_target_sha": "b" * 40,
            "head_merged_to_target": True,
        }
        with (
            mock.patch.object(
                ai_status,
                "_validated_reconcile_delivery",
                return_value=delivery,
            ),
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="Merge delivery without matching task",
            ),
            self.assertRaisesRegex(SystemExit, "does not bind the task id"),
        ):
            ai_status.validate_merged_tooling_done(task)

        with (
            mock.patch.object(
                ai_status,
                "_validated_reconcile_delivery",
                return_value=delivery,
            ),
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="REG-002: merged tooling delivery",
            ),
        ):
            result = ai_status.validate_merged_tooling_done(task)

        self.assertTrue(result["reconciled_from_tooling_delivery"])
        self.assertEqual(result["commit"], "a" * 40)

    def test_validate_merged_tooling_done_rejects_task_id_substring(self) -> None:
        task = {
            "id": "REG-002",
            "task_class": "development_tooling",
        }
        delivery = {
            "repository_id": "pantheon",
            "repository_slug": "ajoe734/pantheon",
            "repository_path": "/tmp/pantheon",
            "commit": "a" * 40,
            "merge_target_ref": "origin/dev",
            "merge_target_sha": "b" * 40,
            "head_merged_to_target": True,
        }
        with (
            mock.patch.object(
                ai_status,
                "_validated_reconcile_delivery",
                return_value=delivery,
            ),
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="REG-002-UNRELATED: another task",
            ),
            self.assertRaisesRegex(SystemExit, "does not bind the task id"),
        ):
            ai_status.validate_merged_tooling_done(task)

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
                        "- Reviewer: Claude2\n\n"
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
                archive = _rotate_activity_log_for_test(log_file)
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
                new_owner="Claude2",
                timestamp="2026-07-19T23:00:00Z",
                message="hop 1",
            ),
            audited_reassignment_event(
                task_id="BROKEN-001",
                # Disconnected: the chain left off at Claude2, not Codex.
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
        tampered["new_owner"] = "Claude2"
        self._test_log_file.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "no exact task_reassigned audit event chain"):
            ai_status._verified_owner_reassignment(
                {"id": "TAMPER-001", "owner": "Claude2", "reviewer": "Claude"},
                evidence_owner="Codex2",
                current_owner="Claude2",
            )

    def test_handoff_must_go_from_owner_to_reviewer(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Claude", "Wrong actor"])

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            with self.assertRaises(SystemExit):
                ai_status.command_handoff(self.state, ["REG-002", "Claude2", "Wrong reviewer"])

        self.state["tasks"][0]["failure_streak"] = 2
        self.state["tasks"][0]["status"] = "in_progress"
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_handoff(self.state, ["REG-002", "Claude", "Ready for review"])

        self.assertEqual(self.state["tasks"][0]["status"], "review")
        self.assertNotIn("failure_streak", self.state["tasks"][0])
        binding = self.state["tasks"][0][ai_status.DELIVERY_BINDING_KEY]
        self.assertEqual(binding["kind"], "artifact_contract")
        self.assertEqual(binding["task_id"], "REG-002")
        self.assertTrue(binding["contract_sha256"])

    def test_artifact_contract_approval_survives_reassignment_generation_bump(self) -> None:
        self.state["tasks"][0]["status"] = "in_progress"
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_handoff(self.state, ["REG-002", "Claude", "Ready for review"])

        task = self.state["tasks"][0]
        binding = task[ai_status.DELIVERY_BINDING_KEY]

        # A mid-review reassignment (worker_reassignment recovery, or a
        # manual `assign`) bumps the task's assignment generation without
        # touching the reviewed artifacts/acceptance. That must not
        # invalidate the frozen delivery binding.
        task["generation"] = task.get("generation", 1) + 1

        ai_status.validate_delivery_binding_for_approval(task, binding)

    def test_pr_handoff_freezes_exact_head_and_rejects_late_head_substitution(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task["required_artifacts"] = ["pull request", "exact-head review"]
        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Codex",
                "REVIEW_PR": "4820",
                "REVIEW_HEAD_SHA": "a" * 40,
            },
            clear=False,
        ), mock.patch.object(
            ai_status,
            "validate_handoff_pr_delivery_binding",
            side_effect=lambda _task, _config, binding: dict(binding),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Review the frozen delivery head."],
            )

        delivery = task[ai_status.DELIVERY_BINDING_KEY]
        self.assertEqual(
            delivery,
            {
                "kind": "pull_request",
                "pr": 4820,
                "head_sha": "a" * 40,
                "head_branch": "task/REG-002",
                "base": "dev",
            },
        )
        with self.assertRaisesRegex(SystemExit, "does not match the exact PR head frozen at handoff"):
            ai_status.validate_delivery_binding_for_approval(
                task,
                {
                    "pr": 4820,
                    "head_sha": "b" * 40,
                    "head_branch": "task/REG-002",
                    "base": "dev",
                },
            )

    def test_handoff_never_reuses_historical_source_ref_as_delivery_binding(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task.pop(ai_status.DELIVERY_BINDING_KEY)
        task["required_artifacts"] = ["pull request"]
        task["source_ref"] = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "requires a PR delivery binding"),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Do not freeze historical provenance."],
            )
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn(ai_status.DELIVERY_BINDING_KEY, task)

    def test_handoff_auto_discovers_open_pr_when_no_explicit_binding_or_requirement(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status,
                "_discover_open_pull_request_for_branch",
                return_value={"pr": 4820, "head_sha": "a" * 40},
            ) as discover,
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Opened the PR before handing off."],
            )
        discover.assert_called_once_with(
            repository=mock.ANY, head_branch="task/REG-002", base="dev"
        )
        self.assertEqual(
            task[ai_status.DELIVERY_BINDING_KEY],
            {
                "kind": "pull_request",
                "pr": 4820,
                "head_sha": "a" * 40,
                "head_branch": "task/REG-002",
                "base": "dev",
            },
        )

    def test_handoff_falls_back_to_artifact_contract_when_no_open_pr_discovered(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status, "_discover_open_pull_request_for_branch", return_value=None
            ),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "No PR exists for this delivery."],
            )
        self.assertEqual(
            task[ai_status.DELIVERY_BINDING_KEY]["kind"], "artifact_contract"
        )

    def test_handoff_verifies_explicit_review_pr_without_discovery(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        with (
            mock.patch.dict(
                os.environ,
                {"AI_NAME": "Codex", "REVIEW_PR": "9999", "REVIEW_HEAD_SHA": "b" * 40},
                clear=False,
            ),
            mock.patch.object(
                ai_status,
                "_discover_open_pull_request_for_branch",
                return_value={"pr": 4820, "head_sha": "a" * 40},
            ) as discover,
            mock.patch.object(
                ai_status,
                "validate_handoff_pr_delivery_binding",
                side_effect=lambda _task, _config, binding: dict(binding),
            ) as validate,
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Explicit binding wins."],
            )
        discover.assert_not_called()
        validate.assert_called_once()
        self.assertEqual(task[ai_status.DELIVERY_BINDING_KEY]["pr"], 9999)

    def test_handoff_rejects_explicit_binding_that_github_does_not_confirm(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        original_binding = deepcopy(task[ai_status.DELIVERY_BINDING_KEY])
        with (
            mock.patch.dict(
                os.environ,
                {"AI_NAME": "Codex", "REVIEW_PR": "9999", "REVIEW_HEAD_SHA": "b" * 40},
                clear=False,
            ),
            mock.patch.object(
                ai_status,
                "validate_handoff_pr_delivery_binding",
                side_effect=SystemExit("GitHub head mismatch"),
            ),
            self.assertRaisesRegex(SystemExit, "GitHub head mismatch"),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Do not persist an invented head."],
            )

        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task[ai_status.DELIVERY_BINDING_KEY], original_binding)

    def test_fe_sidecar_task_resolves_execute_plans_pr_627_without_pantheon_lookup(self) -> None:
        task = {
            "id": "AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24",
            "owner": "Codex",
            "reviewer": "Claude",
            "target_repo": "execute-plans",
            "artifacts": ["support/sidecars/AG-FE-DB-002/evidence.json"],
        }
        mock_bridge = mock.MagicMock()
        mock_bridge.validate_review_binding.return_value = mock.MagicMock(
            pr=627,
            head_sha="a" * 40,
            head_branch="task/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24",
            base="dev",
        )
        with mock.patch.object(ai_status, "_github_review_bridge_module", return_value=mock_bridge):
            binding = ai_status.validate_handoff_pr_delivery_binding(
                task, {}, {"pr": "627", "head_sha": "a" * 40}
            )

        mock_bridge.validate_review_binding.assert_called_once()
        called_repo = mock_bridge.validate_review_binding.call_args[1]["repository"]
        self.assertEqual(called_repo, "ajoe734/execute-plans")
        self.assertNotEqual(called_repo, "ajoe734/pantheon")
        self.assertEqual(binding["pr"], 627)

    def test_validate_handoff_pr_delivery_binding_rejects_conflicting_or_ambiguous_repo(self) -> None:
        conflicting_task = {
            "id": "CONFLICT-TASK",
            "target_repo": "pantheon",
            "artifacts": ["execute-plans/src/App.tsx"],
        }
        with self.assertRaisesRegex(SystemExit, "conflicting repository scope"):
            ai_status.validate_handoff_pr_delivery_binding(
                conflicting_task, {}, {"pr": "627", "head_sha": "a" * 40}
            )

        ambiguous_task = {
            "id": "AMBIGUOUS-TASK",
            "target_repo": "pantheon+execute-plans",
            "artifacts": ["src/App.tsx"],
        }
        with self.assertRaisesRegex(SystemExit, "ambiguous multi-repository target_repo"):
            ai_status.validate_handoff_pr_delivery_binding(
                ambiguous_task, {}, {"pr": "627", "head_sha": "a" * 40}
            )

    def test_validate_handoff_pr_delivery_binding_rejects_unrecognized_repo(self) -> None:
        unknown_task = {
            "id": "UNKNOWN-TASK",
            "target_repo": "invalid_unknown_repo",
            "artifacts": ["src/App.tsx"],
        }
        with self.assertRaisesRegex(SystemExit, "unrecognized target_repo"):
            ai_status.validate_handoff_pr_delivery_binding(
                unknown_task, {}, {"pr": "627", "head_sha": "a" * 40}
            )

    def test_resolve_handoff_delivery_binding_rejects_conflicting_ambiguous_and_unknown_target_repo(self) -> None:
        conflicting_task = {
            "id": "CONFLICT-TASK",
            "target_repo": "pantheon",
            "artifacts": ["execute-plans/src/App.tsx"],
        }
        with self.assertRaisesRegex(SystemExit, "conflicting repository scope"):
            ai_status.resolve_handoff_delivery_binding(conflicting_task, {})

        ambiguous_task = {
            "id": "AMBIGUOUS-TASK",
            "target_repo": "pantheon+execute-plans",
            "artifacts": ["src/App.tsx"],
        }
        with self.assertRaisesRegex(SystemExit, "ambiguous multi-repository target_repo"):
            ai_status.resolve_handoff_delivery_binding(ambiguous_task, {})

        unknown_task = {
            "id": "UNKNOWN-TASK",
            "target_repo": "invalid_unknown_repo",
            "artifacts": ["src/App.tsx"],
        }
        with self.assertRaisesRegex(SystemExit, "unrecognized target_repo"):
            ai_status.resolve_handoff_delivery_binding(unknown_task, {})

    def test_resolve_handoff_delivery_binding_artifact_contract_fallback_success(self) -> None:
        pantheon_task = {
            "id": "DOCS-001",
            "artifacts": ["docs/review.md"],
            "acceptance": ["Document updated"],
        }
        binding = ai_status.resolve_handoff_delivery_binding(pantheon_task, {})
        self.assertEqual(binding["kind"], "artifact_contract")
        self.assertEqual(binding["artifacts"], ["docs/review.md"])
        self.assertEqual(binding["acceptance"], ["Document updated"])
        self.assertIn("contract_sha256", binding)

        fe_sidecar_task = {
            "id": "FE-DOCS-001",
            "target_repo": "execute-plans",
            "artifacts": ["support/sidecars/evidence.json"],
            "acceptance": ["Sidecar evidence verified"],
        }
        binding_fe = ai_status.resolve_handoff_delivery_binding(fe_sidecar_task, {})
        self.assertEqual(binding_fe["kind"], "artifact_contract")
        self.assertEqual(binding_fe["artifacts"], ["support/sidecars/evidence.json"])

    def test_command_handoff_rejects_conflicting_ambiguous_and_unknown_target_repo(self) -> None:
        self.state["tasks"][0]["status"] = "in_progress"
        self.state["tasks"][0]["owner"] = "Codex"
        self.state["tasks"][0]["reviewer"] = "Claude"

        # Conflicting scope
        self.state["tasks"][0]["target_repo"] = "pantheon"
        self.state["tasks"][0]["artifacts"] = ["execute-plans/src/App.tsx"]
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "conflicting repository scope"),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Conflicting repo handoff should fail"],
            )

        # Ambiguous scope
        self.state["tasks"][0]["target_repo"] = "pantheon+execute-plans"
        self.state["tasks"][0]["artifacts"] = ["src/App.tsx"]
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "ambiguous multi-repository target_repo"),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Ambiguous repo handoff should fail"],
            )

        # Unknown scope
        self.state["tasks"][0]["target_repo"] = "bogus_unknown_repo"
        self.state["tasks"][0]["artifacts"] = ["src/App.tsx"]
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "unrecognized target_repo"),
        ):
            ai_status.command_handoff(
                self.state,
                ["REG-002", "Claude", "Unknown repo handoff should fail"],
            )

    def test_reviewer_reopen_creates_handoff_back_to_owner(self) -> None:
        self.state["tasks"][0]["status"] = "review"
        self.state["tasks"][0]["failure_streak"] = 2
        with mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False):
            _command_reopen(self.state, ["REG-002", "Please address the requested changes"])

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn("failure_streak", task)
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
        self._set_pr_delivery_binding(pr=4269, head_sha="a" * 40)
        self.state["tasks"][0]["review_binding"] = binding
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                return_value=bridge_evidence,
            ) as github_bridge,
        ):
            _command_reopen(
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

    def test_reviewer_reopen_recovers_definitive_binding_mismatch_once(self) -> None:
        binding = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "head_branch": "task/REG-002",
            "base": "dev",
        }
        task = self.state["tasks"][0]
        task["status"] = "review"
        self._set_pr_delivery_binding(pr=4269, head_sha="a" * 40)
        task[ai_status.APPROVAL_BINDING_KEY] = dict(binding)
        task[ai_status.GITHUB_REVIEW_BRIDGE_KEY] = {"decision": "approve"}
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                side_effect=ai_status.ReviewBindingMismatchError(
                    "GitHub PR #4269 head differs"
                ),
            ),
        ):
            _command_reopen(
                self.state,
                ["REG-002", "Binding is stale; return the task to its owner."],
            )

        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn(ai_status.DELIVERY_BINDING_KEY, task)
        self.assertNotIn(ai_status.APPROVAL_BINDING_KEY, task)
        self.assertNotIn(ai_status.GITHUB_REVIEW_BRIDGE_KEY, task)

    def test_reviewer_reopen_keeps_state_on_transient_github_failure(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "review"
        self._set_pr_delivery_binding(pr=4269, head_sha="a" * 40)
        frozen = deepcopy(task[ai_status.DELIVERY_BINDING_KEY])
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
            mock.patch.object(
                ai_status,
                "bridge_github_review_decision",
                side_effect=SystemExit("GitHub review bridge timed out"),
            ),
            self.assertRaisesRegex(SystemExit, "timed out"),
        ):
            _command_reopen(self.state, ["REG-002", "Try again later."])

        self.assertEqual(task["status"], "review")
        self.assertEqual(task[ai_status.DELIVERY_BINDING_KEY], frozen)

    def test_reviewer_reopen_ignores_historical_pr_provenance(self) -> None:
        self.state["tasks"][0]["source_ref"] = {
            "pr": 4269,
            "head_sha": "a" * 40,
            "base": "dev",
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Claude"}, clear=False),
        ):
            _command_reopen(
                self.state,
                ["REG-002", "Changes are required."],
            )

        task = ai_status.get_task(self.state, "REG-002")
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn("github_review_bridge", task)

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
            _command_reopen(
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

    def test_task_without_dependencies_keeps_plain_human_blocker(self) -> None:
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_blocker(
                self.state,
                ["REG-002", "Need an operator judgement", "Claude"],
            )

        blocker = self.state["blockers"][-1]
        self.assertNotIn("check_kind", blocker)
        self.assertEqual(blocker["task_id"], "REG-002")

    def test_declared_dependency_requires_explicit_blocker_classification(self) -> None:
        task = self.state["tasks"][0]
        task["depends_on"] = ["DEP-001"]
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "must classify blockers"),
        ):
            ai_status.command_blocker(
                self.state,
                ["REG-002", "Unsure what is blocking", "Claude"],
            )

        self.assertEqual(task["status"], "review")
        self.assertEqual(self.state["blockers"], [])

    def test_completed_declared_dependency_cannot_block_task(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task["depends_on"] = ["DEP-001"]
        self.state["terminal_facts"] = {
            "DEP-001": {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 2,
                "recorded_at": "2026-08-21T00:00:00Z",
            }
        }
        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            self.assertRaisesRegex(SystemExit, "already canonically satisfied"),
        ):
            ai_status.command_blocker(
                self.state,
                [
                    "REG-002",
                    "Waiting for stale context",
                    "Claude",
                    "task_dependency",
                    "DEP-001",
                ],
            )

        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(self.state["blockers"], [])

    def test_unresolved_declared_dependency_records_normal_blocker_only(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task["depends_on"] = ["DEP-001"]
        self.state["tasks"].append(
            {
                "id": "DEP-001",
                "owner": "Claude2",
                "reviewer": "Copilot",
                "status": "todo",
                "depends_on": [],
            }
        )
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_blocker(
                self.state,
                [
                    "REG-002",
                    "Waiting for declared dependency",
                    "Claude",
                    "task_dependency",
                    "DEP-001",
                ],
            )

        blocker = self.state["blockers"][-1]
        self.assertEqual(task["status"], "blocked")
        self.assertNotIn("check_kind", blocker)
        self.assertNotIn("check_params", blocker)

    def test_external_blocker_remains_available_for_task_with_dependencies(self) -> None:
        task = self.state["tasks"][0]
        task["status"] = "in_progress"
        task["depends_on"] = ["DEP-001"]
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            ai_status.command_blocker(
                self.state,
                [
                    "REG-002",
                    "Dev endpoint is unavailable",
                    "Claude",
                    "external",
                ],
            )

        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.state["blockers"][-1]["message"], "Dev endpoint is unavailable")

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
        self.state["tasks"][0]["waiting_for"] = "Claude2"
        self.state["blockers"] = [
            {
                "task_id": "REG-002",
                "owner": "Codex",
                "waiting_for": "Claude2",
                "message": "Legacy lane replaced by newer execution slice",
                "status": "open",
                "created_at": "2026-04-06T15:05:00Z",
            }
        ]

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
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


class DiscoverOpenPullRequestForBranchTests(unittest.TestCase):
    @staticmethod
    def _completed(*, returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["gh"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_returns_single_open_match(self) -> None:
        payload = json.dumps([{"number": 4820, "head": {"sha": "A" * 40}}])
        with mock.patch.object(
            ai_status.subprocess, "run", return_value=self._completed(stdout=payload)
        ):
            result = ai_status._discover_open_pull_request_for_branch(
                repository="ajoe734/pantheon", head_branch="task/REG-002", base="dev"
            )
        self.assertEqual(result, {"pr": 4820, "head_sha": "a" * 40})

    def test_returns_none_when_no_open_pr(self) -> None:
        with mock.patch.object(
            ai_status.subprocess, "run", return_value=self._completed(stdout="[]")
        ):
            result = ai_status._discover_open_pull_request_for_branch(
                repository="ajoe734/pantheon", head_branch="task/REG-002", base="dev"
            )
        self.assertIsNone(result)

    def test_returns_none_on_ambiguous_matches(self) -> None:
        payload = json.dumps(
            [
                {"number": 1, "head": {"sha": "a" * 40}},
                {"number": 2, "head": {"sha": "b" * 40}},
            ]
        )
        with mock.patch.object(
            ai_status.subprocess, "run", return_value=self._completed(stdout=payload)
        ):
            result = ai_status._discover_open_pull_request_for_branch(
                repository="ajoe734/pantheon", head_branch="task/REG-002", base="dev"
            )
        self.assertIsNone(result)

    def test_returns_none_on_gh_failure(self) -> None:
        with mock.patch.object(
            ai_status.subprocess, "run", return_value=self._completed(returncode=1)
        ):
            result = ai_status._discover_open_pull_request_for_branch(
                repository="ajoe734/pantheon", head_branch="task/REG-002", base="dev"
            )
        self.assertIsNone(result)

    def test_returns_none_on_malformed_json(self) -> None:
        with mock.patch.object(
            ai_status.subprocess, "run", return_value=self._completed(stdout="not json")
        ):
            result = ai_status._discover_open_pull_request_for_branch(
                repository="ajoe734/pantheon", head_branch="task/REG-002", base="dev"
            )
        self.assertIsNone(result)

    def test_returns_none_without_owner_slash_repo(self) -> None:
        result = ai_status._discover_open_pull_request_for_branch(
            repository="pantheon", head_branch="task/REG-002", base="dev"
        )
        self.assertIsNone(result)


class SupervisorReassignmentEventIdCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)

    def tearDown(self) -> None:
        _teardown_test_isolation(self)

    def _audited_events(
        self,
        *events: dict[str, Any],
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
    def _with_prefix(event: dict[str, Any], prefix: str) -> dict[str, Any]:
        event = dict(event)
        digest = str(event["event_id"])[-64:]
        event["event_id"] = f"{prefix}{digest}"
        return event

    def test_accepts_both_canonical_event_id_prefixes(self) -> None:
        for prefix in ("supervisor-reassign-", "supervisor-task-reassigned-"):
            with self.subTest(prefix=prefix):
                event = legacy_supervisor_reassignment_event(prefix=prefix)
                audited = self._audited_events(event)

                self.assertEqual(len(audited), 1)
                self.assertEqual(audited[0][1]["event_id"], event["event_id"])
                validated = task_machine.validate_assignment_activity_event(event)
                self.assertIsNotNone(validated)
                self.assertTrue(validated.legacy_ungenerated)

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
        validated = task_machine.validate_assignment_activity_event(event)
        self.assertIsNotNone(validated)
        self.assertTrue(validated.legacy_ungenerated)
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

    def test_historical_pfg_human_ops_event_remains_accepted_at_closeout(self) -> None:
        event = {
            "event_id": (
                "human-ops-task-reassigned-"
                "82b15e319a021e5f49627e46a884e8867a44b7a6a5b18c3199d3ff7e8de2caa2"
            ),
            "ts": "2026-08-20T13:40:29Z",
            "agent": "Human/Ops",
            "type": "task_reassigned",
            "task_id": "PFG-PAPER-STATE-20260820",
            "old_owner": "Antigravity",
            "new_owner": "Antigravity",
            "old_reviewer": "Codex2",
            "new_reviewer": "Antigravity2",
            "old_generation": 1,
            "generation": 2,
            "message": (
                "Capacity rebalance: move independent exact-head review off the "
                "saturated Codex2 account."
            ),
        }

        audited = self._audited_events(event, task_id=event["task_id"])

        self.assertEqual(len(audited), 1)
        validated = task_machine.validate_assignment_activity_event(event)
        self.assertIsNotNone(validated)
        self.assertFalse(validated.legacy_ungenerated)
        result = ai_status._verified_done_reviewer_reassignment(
            {
                "id": event["task_id"],
                "owner": event["new_owner"],
                "reviewer": event["new_reviewer"],
            },
            commit_reviewer=event["old_reviewer"],
            current_reviewer=event["new_reviewer"],
            commit_timestamp="2026-08-20T13:33:53+00:00",
        )
        self.assertEqual(result["event_id"], event["event_id"])

    def test_human_ops_command_reassignment_round_trips_through_closeout_reader(self) -> None:
        state = {
            "agents": [
                {"name": "Antigravity", "current_task_ids": ["REG-002"]},
                {"name": "Codex2", "current_task_ids": []},
                {"name": "Antigravity2", "current_task_ids": []},
            ],
            "tasks": [
                {
                    "id": "REG-002",
                    "title": "Human/Ops closeout round trip",
                    "owner": "Antigravity",
                    "reviewer": "Codex2",
                    "status": "review_approved",
                    "generation": 4,
                    "depends_on": [],
                    "artifacts": [],
                    "acceptance": [],
                }
            ],
            "handoffs": [],
            "blockers": [],
        }
        reason = "Capacity rebalance after reviewer authentication failure"
        with (
            mock.patch.dict(
                os.environ,
                {"AI_NAME": "Human/Ops", "TASK_ASSIGN_REASON": reason},
                clear=False,
            ),
            mock.patch.object(
                ai_status, "iso_now", return_value="2026-08-20T13:40:29Z"
            ),
        ):
            ai_status.command_assign(
                state,
                ["REG-002", "Antigravity", "Antigravity2"],
            )

        task = ai_status.get_task(state, "REG-002")
        self.assertEqual(task["generation"], 5)
        events = [
            json.loads(line)
            for line in self._test_log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        reassignment = next(
            event for event in events if event.get("type") == "task_reassigned"
        )
        validated = task_machine.validate_assignment_activity_event(reassignment)
        self.assertIsNotNone(validated)
        self.assertEqual(validated.actor, "Human/Ops")
        self.assertEqual(validated.old_generation, 4)
        self.assertEqual(validated.generation, 5)

        def fake_run_git_command(args: list[str], **_kwargs: object) -> str:
            responses = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "task/REG-002",
                ("rev-parse", "HEAD"): "a" * 40,
                ("show", "-s", "--format=%s", "HEAD"): "REG-002: finish delivery",
                ("show", "-s", "--format=%b", "HEAD"): (
                    "LLM-Agent: Antigravity\n"
                    "Task-ID: REG-002\n"
                    "Reviewer: Codex2\n"
                ),
                ("show", "-s", "--format=%an", "HEAD"): "Antigravity",
                ("show", "-s", "--format=%ae", "HEAD"): "agent@example.com",
                ("show", "-s", "--format=%cI", "HEAD"): (
                    "2026-08-20T13:33:53+00:00"
                ),
                ("status", "--porcelain"): "",
                ("remote",): "",
            }
            return responses[tuple(args)]

        with (
            mock.patch.dict(
                os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False
            ),
            mock.patch.object(
                ai_status, "run_git_command", side_effect=fake_run_git_command
            ),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Antigravity")

        self.assertEqual(
            delivery["commit_reviewer_reassignment"]["event_id"],
            reassignment["event_id"],
        )

    def test_canonical_validator_rejects_each_mutated_bound_field(self) -> None:
        event = audited_reassignment_event(
            old_reviewer="Claude",
            new_reviewer="Codex2",
        )
        mutations = {
            "event_id": "supervisor-task-reassigned-" + "0" * 64,
            "ts": "2026-07-19T23:52:07Z",
            "agent": "Human/Ops",
            "type": "note",
            "task_id": "OTHER-001",
            "old_owner": "Claude2",
            "new_owner": "Codex",
            "old_reviewer": "Antigravity",
            "new_reviewer": "Antigravity2",
            "old_generation": 2,
            "generation": 3,
            "message": "mutated reason",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                mutated = {**event, field: value}
                self.assertIsNone(
                    task_machine.validate_assignment_activity_event(mutated)
                )

    def test_rejects_human_ops_event_with_wrong_digest(self) -> None:
        event = audited_reassignment_event(actor="Human/Ops")
        event["event_id"] = "human-ops-task-reassigned-" + "0" * 64

        self.assertEqual(self._audited_events(event), [])

    def test_rejects_actor_prefix_authority_mismatch(self) -> None:
        base_event = audited_reassignment_event()
        cases = (
            ("Human/Ops", "supervisor-task-reassigned-"),
            ("Orchestrator", "human-ops-task-reassigned-"),
            ("Codex", "human-ops-task-reassigned-"),
        )
        for actor, prefix in cases:
            with self.subTest(actor=actor, prefix=prefix):
                event = dict(base_event)
                event["agent"] = actor
                event = self._with_prefix(event, prefix)
                self.assertEqual(self._audited_events(event), [])

    def test_rejects_ungenerated_human_ops_event_even_with_matching_prefix(self) -> None:
        event = legacy_supervisor_reassignment_event()
        event["agent"] = "Human/Ops"
        event = self._with_prefix(event, "human-ops-task-reassigned-")

        self.assertIsNone(task_machine.validate_assignment_activity_event(event))
        self.assertEqual(self._audited_events(event), [])

    def test_rejects_wrong_digest_and_wrong_prefix(self) -> None:
        base_event = audited_reassignment_event()
        digest = str(base_event["event_id"])[-64:]
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


class DeliveryWorkspaceAuthorityTests(unittest.TestCase):
    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def _repository_fixture(
        self, root: Path
    ) -> tuple[dict[str, object], Path, Path, Path, dict[str, object]]:
        status_root = root / "pantheon-status"
        source_root = root / "execute-plans"
        workspace_root = root / "worker" / "agora-candidate"
        status_root.mkdir()
        source_root.mkdir()
        self._git(source_root, "init", "-b", "dev")
        self._git(source_root, "config", "user.name", "Test")
        self._git(source_root, "config", "user.email", "test@example.com")
        (source_root / "README.md").write_text("execute plans\n", encoding="utf-8")
        self._git(source_root, "add", "README.md")
        self._git(source_root, "commit", "-m", "initial")
        self._git(
            source_root,
            "remote",
            "add",
            "origin",
            "https://github.com/ajoe734/execute-plans.git",
        )
        workspace_root.parent.mkdir()
        self._git(
            source_root,
            "worktree",
            "add",
            "-b",
            "task/AGORA-FE-CANDIDATE-20260813",
            str(workspace_root),
        )
        config: dict[str, object] = {
            "paths": {"status_file": str(status_root / "ai-status.json")},
            "coordination": {
                "repositories": {
                    "pantheon": {"repo": "ajoe734/pantheon"},
                    "execute_plans": {"local_path": str(source_root)}
                }
            },
        }
        task: dict[str, object] = {
            "id": "AGORA-FE-CANDIDATE-20260813",
            "artifacts": [
                "execute-plans/src/agora/components/CandidateReviewDrawer.tsx"
            ],
        }
        return config, status_root, source_root, workspace_root, task

    def test_explicit_workspace_uses_registered_execute_plans_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, status_root, _source, workspace, task = self._repository_fixture(
                Path(directory)
            )
            with (
                mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                mock.patch.dict(
                    os.environ,
                    {
                        "PANTHEON_WORKTREE_ROOT": str(workspace),
                        "ORCH_WORKSPACE_PATH": str(workspace),
                    },
                    clear=True,
                ),
            ):
                selected, metadata = ai_status._done_delivery_repository_root(
                    config, task, "execute_plans"
                )

        self.assertEqual(selected, workspace.resolve())
        self.assertEqual(metadata["repository_path_source"], "explicit_workspace_env")
        self.assertTrue(metadata["workspace_env_match"])
        self.assertFalse(metadata["workspace_lease_validated"])

    def test_workspace_binding_requires_both_environment_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, status_root, _source, workspace, task = self._repository_fixture(
                Path(directory)
            )
            with (
                mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                mock.patch.dict(
                    os.environ,
                    {"PANTHEON_WORKTREE_ROOT": str(workspace)},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    SystemExit, "requires both PANTHEON_WORKTREE_ROOT"
                ),
            ):
                ai_status._done_delivery_repository_root(
                    config, task, "execute_plans"
                )

    def test_worker_workspace_requires_matching_lease_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, status_root, _source, workspace, task = self._repository_fixture(
                Path(directory)
            )
            ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                "task_id": task["id"],
                "workspace_repository_id": "pantheon",
            }
            try:
                with (
                    mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                    mock.patch.dict(
                        os.environ,
                        {
                            "ORCH_RUN_ID": "run-agora",
                            "PANTHEON_WORKTREE_ROOT": str(workspace),
                            "ORCH_WORKSPACE_PATH": str(workspace),
                        },
                        clear=True,
                    ),
                    self.assertRaisesRegex(
                        SystemExit, "worker lease repository does not match"
                    ),
                ):
                    ai_status._done_delivery_repository_root(
                        config, task, "execute_plans"
                    )
            finally:
                ai_status._clear_status_command_lease_binding()

    def test_registry_fallback_remains_for_commands_without_workspace_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, status_root, source, _workspace, task = self._repository_fixture(
                Path(directory)
            )
            with (
                mock.patch.object(ai_status, "STATUS_ROOT", status_root),
                mock.patch.dict(os.environ, {}, clear=True),
            ):
                selected, metadata = ai_status._done_delivery_repository_root(
                    config, task, "execute_plans"
                )

        self.assertEqual(selected, source.resolve())
        self.assertEqual(metadata["repository_path_source"], "repository_registry")


class DeliveryMetadataValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        _setup_test_isolation(self)
        self.addCleanup(_teardown_test_isolation, self)

    @staticmethod
    def _owner_reassignment_event(
        *,
        task_id: str = "REG-002",
        old_owner: str = "Codex2",
        new_owner: str = "Codex",
        reviewer: str = "Antigravity",
        new_reviewer: str | None = None,
        timestamp: str = "2026-07-31T16:22:48Z",
        message: str = "Supervisor reassigned finalization owner.",
    ) -> dict[str, Any]:
        return audited_reassignment_event(
            task_id=task_id,
            old_owner=old_owner,
            new_owner=new_owner,
            old_reviewer=reviewer,
            new_reviewer=new_reviewer or reviewer,
            timestamp=timestamp,
            message=message,
        )

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
                archive = _rotate_activity_log_for_test(log_file)
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
                self.assertRaisesRegex(SystemExit, "exact canonical audited task_reassigned"),
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

    def test_prior_owner_reassignment_ignores_pre_delivery_cycle(self) -> None:
        """Only the audited chain after the delivered commit is relevant.

        A task can be reassigned away and back before its final delivery.  The
        post-delivery handoff must be walked from the commit owner without
        letting that earlier cycle poison the closeout chain.
        """
        pre_cycle_one = self._owner_reassignment_event(
            old_owner="Codex", new_owner="Codex2", reviewer="Claude",
            timestamp="2026-08-24T15:29:43Z",
        )
        pre_cycle_two = self._owner_reassignment_event(
            old_owner="Codex2", new_owner="Codex", reviewer="Claude",
            timestamp="2026-08-24T15:37:14Z",
        )
        post_delivery = self._owner_reassignment_event(
            old_owner="Codex", new_owner="Antigravity", reviewer="Claude",
            new_reviewer="Antigravity2", timestamp="2026-08-24T22:44:05Z",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(
                "\n".join(json.dumps(event) for event in (pre_cycle_one, pre_cycle_two, post_delivery))
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ai_status, "LOG_FILE", log_file):
                result = ai_status._verified_done_owner_reassignment(
                    {"id": "REG-002", "owner": "Antigravity", "reviewer": "Antigravity2"},
                    commit_owner="Codex",
                    current_owner="Antigravity",
                    commit_timestamp="2026-08-24T15:46:58+00:00",
                )

        self.assertEqual(result["event_id"], post_delivery["event_id"])
        self.assertEqual(result["hops"], 1)

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
            new_reviewer="Claude",
            timestamp="2026-07-31T16:23:00Z",
            message="Supervisor reassigned reviewer continuity.",
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
                llm_agent="Claude2",
                reviewer="Claude",
                commit_timestamp="2026-08-05T11:00:00+00:00",
            )

    def test_done_reviewer_reassignment_rejects_unaudited_event(self) -> None:
        forged = audited_reassignment_event(
            task_id="REG-002", new_owner="Codex", new_reviewer="Antigravity"
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
                    {"id": "REG-002", "owner": "Codex", "reviewer": "Antigravity"},
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

    def test_done_reviewer_reassignment_ignores_pre_delivery_cycle(self) -> None:
        pre_cycle_one = audited_reassignment_event(
            task_id="REG-002", old_owner="Antigravity", new_owner="Antigravity",
            old_reviewer="Claude", new_reviewer="Codex2",
            timestamp="2026-08-24T15:29:43Z",
        )
        pre_cycle_two = audited_reassignment_event(
            task_id="REG-002", old_owner="Antigravity", new_owner="Antigravity",
            old_reviewer="Codex2", new_reviewer="Claude",
            timestamp="2026-08-24T15:37:14Z",
        )
        post_delivery = audited_reassignment_event(
            task_id="REG-002", old_owner="Antigravity", new_owner="Antigravity",
            old_reviewer="Claude", new_reviewer="Antigravity2",
            timestamp="2026-08-24T22:44:05Z",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(
                "\n".join(json.dumps(event) for event in (pre_cycle_one, pre_cycle_two, post_delivery))
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(ai_status, "LOG_FILE", log_file):
                result = ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Antigravity", "reviewer": "Antigravity2"},
                    commit_reviewer="Claude",
                    current_reviewer="Antigravity2",
                    commit_timestamp="2026-08-24T15:46:58+00:00",
                )

        self.assertEqual(result["event_id"], post_delivery["event_id"])
        self.assertEqual(result["hops"], 1)

    @staticmethod
    def _first_assignment_event(
        *,
        task_id: str = "REG-002",
        agent: str = "Human/Ops",
        new_reviewer: str = "Antigravity2",
        timestamp: str = "2026-08-17T16:39:04Z",
    ) -> dict[str, Any]:
        event = {
            "ts": timestamp,
            "agent": agent,
            "type": "assign",
            "task_id": task_id,
            "old_owner": "",
            "new_owner": "Claude",
            "old_reviewer": "",
            "new_reviewer": new_reviewer,
            "message": f"Assigned {task_id} to Claude with reviewer {new_reviewer}",
        }
        event["event_id"] = "ai-status-event-" + ai_status._canonical_json_sha256(event)
        return event

    def test_done_reviewer_reassignment_accepts_audited_first_assignment(self) -> None:
        for agent in ("Orchestrator", "Human/Ops"):
            with self.subTest(agent=agent):
                event = self._first_assignment_event(agent=agent)
                with tempfile.TemporaryDirectory() as tmpdir:
                    log_file = Path(tmpdir) / "ai-activity-log.jsonl"
                    log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
                    with mock.patch.object(ai_status, "LOG_FILE", log_file):
                        result = ai_status._verified_done_reviewer_reassignment(
                            {"id": "REG-002", "owner": "Claude", "reviewer": "Antigravity2"},
                            commit_reviewer="pending",
                            current_reviewer="Antigravity2",
                            commit_timestamp="2026-08-17T16:37:40+00:00",
                        )
                self.assertEqual(result["event_id"], event["event_id"])
                self.assertEqual(result["old_reviewer"], "")
                self.assertEqual(result["new_reviewer"], "Antigravity2")

    @staticmethod
    def _legacy_first_assignment_event(
        *,
        task_id: str = "REG-002",
        agent: str = "Human/Ops",
        owner: str = "Claude",
        new_reviewer: str = "Antigravity2",
        timestamp: str = "2026-08-18T13:20:05Z",
    ) -> dict[str, Any]:
        """An assign event shaped exactly like one predating #5006
        (OPS-DONE-REVIEWER-ASSIGN-AUDIT-GAP-20260818): no structured
        old_reviewer/new_reviewer fields, only the free-text message.
        """
        event = {
            "ts": timestamp,
            "agent": agent,
            "type": "assign",
            "task_id": task_id,
            "message": f"Assigned {task_id} to {owner} with reviewer {new_reviewer}",
            "operator_mode": "local_human_ops",
        }
        event["event_id"] = "ai-status-event-" + ai_status._canonical_json_sha256(event)
        return event

    def test_done_reviewer_reassignment_accepts_legacy_first_assignment_message(self) -> None:
        """A pre-#5006 assign event with no structured new_reviewer field is
        still recognized by parsing its one known, unchanging message shape.
        """
        event = self._legacy_first_assignment_event()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with mock.patch.object(ai_status, "LOG_FILE", log_file):
                result = ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Claude", "reviewer": "Antigravity2"},
                    commit_reviewer="pending",
                    current_reviewer="Antigravity2",
                    commit_timestamp="2026-08-18T13:19:30+00:00",
                )
        self.assertEqual(result["event_id"], event["event_id"])
        self.assertEqual(result["new_reviewer"], "Antigravity2")

    def test_done_reviewer_reassignment_rejects_legacy_message_for_wrong_task(self) -> None:
        event = self._legacy_first_assignment_event(task_id="OTHER-TASK")
        event["task_id"] = "REG-002"  # event field matches, but message body does not
        event["event_id"] = "ai-status-event-" + ai_status._canonical_json_sha256(
            {k: v for k, v in event.items() if k != "event_id"}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(
                    SystemExit, "no audited first reviewer assignment explains"
                ),
            ):
                ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Claude", "reviewer": "Antigravity2"},
                    commit_reviewer="pending",
                    current_reviewer="Antigravity2",
                    commit_timestamp="2026-08-18T13:19:30+00:00",
                )

    def test_done_reviewer_reassignment_rejects_forged_first_assignment_actor(self) -> None:
        event = self._first_assignment_event(agent="Claude")
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(
                    SystemExit, "no audited first reviewer assignment explains"
                ),
            ):
                ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Claude", "reviewer": "Antigravity2"},
                    commit_reviewer="pending",
                    current_reviewer="Antigravity2",
                    commit_timestamp="2026-08-17T16:37:40+00:00",
                )

    def test_done_reviewer_reassignment_rejects_first_assignment_before_commit(self) -> None:
        event = self._first_assignment_event(timestamp="2026-08-17T16:00:00Z")
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(
                    SystemExit, "no audited first reviewer assignment explains"
                ),
            ):
                ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Claude", "reviewer": "Antigravity2"},
                    commit_reviewer="pending",
                    current_reviewer="Antigravity2",
                    commit_timestamp="2026-08-17T16:37:40+00:00",
                )

    def test_done_reviewer_reassignment_rejects_tampered_first_assignment_digest(self) -> None:
        event = self._first_assignment_event()
        event["new_reviewer"] = "Codex"  # mutated after the digest was stamped
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                self.assertRaisesRegex(
                    SystemExit, "no audited first reviewer assignment explains"
                ),
            ):
                ai_status._verified_done_reviewer_reassignment(
                    {"id": "REG-002", "owner": "Claude", "reviewer": "Codex"},
                    commit_reviewer="pending",
                    current_reviewer="Codex",
                    commit_timestamp="2026-08-17T16:37:40+00:00",
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
        execute_plans_root = Path("/tmp/execute-plans-delivery-test")
        with (
            mock.patch.dict(os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False),
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(
                ai_status,
                "_done_delivery_repository_root",
                return_value=(
                    execute_plans_root,
                    {
                        "repository_path_source": "repository_registry",
                        "workspace_env_names": [],
                        "workspace_env_match": False,
                        "workspace_lease_validated": False,
                    },
                ),
            ),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex2")

        self.assertEqual(delivery["repository_id"], "execute_plans")
        self.assertEqual(delivery["repository_path"], str(execute_plans_root))
        self.assertEqual(delivery["repository_slug"], "ajoe734/execute-plans")
        self.assertEqual(delivery["branch"], "bff-luv-fe-006-dev-deploy")
        self.assertTrue(calls)
        self.assertTrue(all(cwd == execute_plans_root for _, cwd in calls))

    def test_collect_done_delivery_metadata_rejects_missing_mixed_repo(self) -> None:
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
                mock.patch.object(ai_status, "repository_local_path", side_effect=fake_repository_local_path),
                self.assertRaisesRegex(
                    SystemExit, "registered delivery repository does not exist"
                ),
            ):
                ai_status.collect_done_delivery_metadata(task, "Codex2")

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

    def test_collect_done_uses_exact_approved_head_after_workspace_fast_forward(self) -> None:
        approved_head = "a" * 40
        workspace_head = "d" * 40
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": [],
            ai_status.APPROVAL_BINDING_KEY: {
                "pr": 152,
                "head_sha": approved_head,
                "head_branch": "task/REG-002",
                "base": "dev",
            },
            ai_status.GITHUB_REVIEW_BRIDGE_KEY: {
                "decision": "approve",
                "mode": "required_commit_status",
                "pr": 152,
                "head_sha": approved_head,
                "head_branch": "task/REG-002",
                "base": "dev",
                "status_id": 99,
                "status_context": ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT,
                "status_state": "success",
            },
        }

        def fake_run_git_command(args: list[str], **kwargs: object) -> str:
            responses = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "task/REG-002",
                ("rev-parse", "HEAD"): workspace_head,
                ("rev-parse", approved_head): approved_head,
                ("show", "-s", "--format=%s", approved_head): "REG-002: deliver reviewed fix",
                ("show", "-s", "--format=%b", approved_head): (
                    "LLM-Agent: Codex\nTask-ID: REG-002\nReviewer: Claude\n"
                ),
                ("show", "-s", "--format=%an", approved_head): "Codex",
                ("show", "-s", "--format=%ae", approved_head): "codex@example.com",
                ("status", "--porcelain"): "",
                ("remote",): "origin",
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "",
                ("fetch", "origin", "dev"): "",
                ("rev-parse", "--verify", "origin/dev"): workspace_head,
            }
            try:
                return responses[tuple(args)]
            except KeyError as exc:
                raise AssertionError(f"unexpected git command: {args}") from exc

        succeeded_calls: list[list[str]] = []

        def fake_git_command_succeeds(args: list[str], **kwargs: object) -> bool:
            succeeded_calls.append(args)
            if args == ["cat-file", "-e", f"{approved_head}^{{commit}}"]:
                return True
            if args == ["merge-base", "--is-ancestor", approved_head, "origin/dev"]:
                return True
            raise AssertionError(f"unexpected git predicate: {args}")

        with (
            mock.patch.object(ai_status, "run_git_command", side_effect=fake_run_git_command),
            mock.patch.object(
                ai_status,
                "git_command_succeeds",
                side_effect=fake_git_command_succeeds,
            ),
        ):
            delivery = ai_status.collect_done_delivery_metadata(task, "Codex")

        self.assertEqual(delivery["commit"], approved_head)
        self.assertEqual(delivery["workspace_head"], workspace_head)
        self.assertEqual(delivery["commit_source"], "canonical_approved_head")
        self.assertEqual(delivery["merge_test_commit"], approved_head)
        self.assertIn(
            ["merge-base", "--is-ancestor", approved_head, "origin/dev"],
            succeeded_calls,
        )

    def test_collect_done_does_not_trust_unverified_approved_head_binding(self) -> None:
        approved_head = "a" * 40
        task = {
            "id": "REG-002",
            "owner": "Codex",
            "reviewer": "Claude",
            "status": "review_approved",
            "artifacts": [],
            ai_status.APPROVAL_BINDING_KEY: {
                "pr": 152,
                "head_sha": approved_head,
                "head_branch": "task/REG-002",
                "base": "dev",
            },
            ai_status.GITHUB_REVIEW_BRIDGE_KEY: {
                "decision": "approve",
                "mode": "required_commit_status",
                "pr": 152,
                "head_sha": approved_head,
                "head_branch": "task/REG-002",
                "base": "dev",
                "status_id": 99,
                "status_context": ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT,
                "status_state": "failure",
            },
        }

        responses = iter(
            [
                "task/REG-002",
                "d" * 40,
                "Merge pull request #999 from ajoe734/task/OTHER-TASK",
                "OTHER-TASK: unrelated later merge\n",
                "GitHub",
                "noreply@github.com",
            ]
        )
        with (
            mock.patch.dict(
                os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False
            ),
            mock.patch.object(
                ai_status,
                "run_git_command",
                side_effect=lambda *args, **kwargs: next(responses),
            ),
            self.assertRaisesRegex(
                SystemExit,
                "latest commit subject must include task id REG-002",
            ),
        ):
            ai_status.collect_done_delivery_metadata(task, "Codex")

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

    def test_collect_done_squash_merge_reviewer_trailer_uses_reviewed_head_timestamp(self) -> None:
        """Reproduces the real 2026-08-19 false rejection.

        A worker wrote a commit with a correct `Reviewer: Claude` trailer at
        15:30, hours before the reviewer was reassigned to Antigravity2 at
        00:06 the next day. GitHub then squash-merged the PR, producing a
        brand-new HEAD commit with the *same* trailer text but a fresh
        06:48 timestamp. Reading HEAD's own timestamp made the reassignment
        (00:06) look like it preceded the "delivered commit" (06:48), which
        is backwards -- the real content was authored at 15:30, well before
        the reassignment. The fix reads the timestamp of the exact reviewed
        head (recorded at approval time) instead of HEAD's post-squash one.
        """

        reviewed_head = "1" * 40
        squashed_head = "6" * 40
        event = audited_reassignment_event(
            task_id="REG-002",
            old_owner="Antigravity",
            new_owner="Antigravity",
            old_reviewer="Claude",
            new_reviewer="Antigravity2",
            timestamp="2026-08-19T00:06:49Z",
            message="Recovery reassigned reviewer from Claude after durable terminal_auth:claude.",
        )
        task = {
            "id": "REG-002",
            "owner": "Antigravity",
            "reviewer": "Antigravity2",
            "status": "review_approved",
            ai_status.APPROVAL_BINDING_KEY: {
                "pr": 5020,
                "head_sha": reviewed_head,
                "head_branch": "task/REG-002",
                "base": "dev",
            },
        }

        def fake_run_git_command(args: list[str], **_kwargs: object) -> str:
            responses = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "task/REG-002",
                ("rev-parse", "HEAD"): squashed_head,
                ("show", "-s", "--format=%s", "HEAD"): "REG-002: finish delivery (#5020)",
                ("show", "-s", "--format=%b", "HEAD"): (
                    "LLM-Agent: Antigravity\nTask-ID: REG-002\nReviewer: Claude\n"
                ),
                ("show", "-s", "--format=%an", "HEAD"): "Antigravity",
                ("show", "-s", "--format=%ae", "HEAD"): "worker@example.com",
                # HEAD's own (post-squash) timestamp -- must NOT be what
                # decides the ordering check, or this reproduces the bug.
                ("show", "-s", "--format=%cI", "HEAD"): "2026-08-19T06:48:00+00:00",
                # The exact reviewed head's true authoring timestamp.
                ("show", "-s", "--format=%cI", reviewed_head): "2026-08-18T15:30:12+00:00",
                ("status", "--porcelain"): "",
                ("remote",): "",
            }
            return responses[tuple(args)]

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "ai-activity-log.jsonl"
            log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with (
                mock.patch.dict(
                    os.environ, {"TASK_REQUIRE_MERGED_PR": "false"}, clear=False
                ),
                mock.patch.object(ai_status, "LOG_FILE", log_file),
                mock.patch.object(
                    ai_status, "run_git_command", side_effect=fake_run_git_command
                ),
                mock.patch.object(
                    ai_status, "git_command_succeeds", return_value=True
                ) as succeeds,
            ):
                delivery = ai_status.collect_done_delivery_metadata(task, "Antigravity")

        succeeds.assert_called_once_with(
            ["cat-file", "-e", reviewed_head], cwd=mock.ANY
        )
        proof = delivery["commit_reviewer_reassignment"]
        self.assertEqual(proof["old_reviewer"], "Claude")
        self.assertEqual(proof["new_reviewer"], "Antigravity2")
        self.assertEqual(proof["commit_timestamp"], "2026-08-18T15:30:12+00:00")


class DeliveredCommitTimestampTests(unittest.TestCase):
    def test_prefers_reviewed_head_timestamp_when_locally_present(self) -> None:
        reviewed_head = "a" * 40
        task = {ai_status.APPROVAL_BINDING_KEY: {"head_sha": reviewed_head}}
        with (
            mock.patch.object(ai_status, "git_command_succeeds", return_value=True),
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="2026-08-18T15:30:12+00:00",
            ) as run_git,
        ):
            result = ai_status._delivered_commit_timestamp(Path("/repo"), task)
        self.assertEqual(result, "2026-08-18T15:30:12+00:00")
        run_git.assert_called_once_with(
            ["show", "-s", "--format=%cI", reviewed_head],
            cwd=Path("/repo"),
            failure_message=mock.ANY,
        )

    def test_fetches_reviewed_head_when_not_locally_present(self) -> None:
        reviewed_head = "b" * 40
        task = {ai_status.APPROVAL_BINDING_KEY: {"head_sha": reviewed_head}}
        with (
            mock.patch.object(
                ai_status, "git_command_succeeds", side_effect=[False, True]
            ) as succeeds,
            mock.patch.object(ai_status.subprocess, "run") as fetch,
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="2026-08-18T15:30:12+00:00",
            ),
        ):
            result = ai_status._delivered_commit_timestamp(Path("/repo"), task)
        self.assertEqual(result, "2026-08-18T15:30:12+00:00")
        self.assertEqual(succeeds.call_count, 2)
        fetch.assert_called_once()
        self.assertIn(reviewed_head, fetch.call_args.args[0])

    def test_falls_back_to_head_when_reviewed_head_unresolvable_after_fetch(self) -> None:
        reviewed_head = "c" * 40
        task = {ai_status.APPROVAL_BINDING_KEY: {"head_sha": reviewed_head}}
        with (
            mock.patch.object(ai_status, "git_command_succeeds", return_value=False),
            mock.patch.object(ai_status.subprocess, "run"),
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="2026-08-19T06:48:00+00:00",
            ) as run_git,
        ):
            result = ai_status._delivered_commit_timestamp(Path("/repo"), task)
        self.assertEqual(result, "2026-08-19T06:48:00+00:00")
        run_git.assert_called_once_with(
            ["show", "-s", "--format=%cI", "HEAD"],
            cwd=Path("/repo"),
            failure_message=mock.ANY,
        )

    def test_falls_back_to_head_when_no_approval_binding_recorded(self) -> None:
        with (
            mock.patch.object(ai_status, "git_command_succeeds") as succeeds,
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="2026-08-19T06:48:00+00:00",
            ) as run_git,
        ):
            result = ai_status._delivered_commit_timestamp(Path("/repo"), {})
        self.assertEqual(result, "2026-08-19T06:48:00+00:00")
        succeeds.assert_not_called()
        run_git.assert_called_once_with(
            ["show", "-s", "--format=%cI", "HEAD"],
            cwd=Path("/repo"),
            failure_message=mock.ANY,
        )

    def test_falls_back_to_head_when_binding_head_sha_is_malformed(self) -> None:
        task = {ai_status.APPROVAL_BINDING_KEY: {"head_sha": "not-a-sha"}}
        with (
            mock.patch.object(ai_status, "git_command_succeeds") as succeeds,
            mock.patch.object(
                ai_status,
                "run_git_command",
                return_value="2026-08-19T06:48:00+00:00",
            ),
        ):
            result = ai_status._delivered_commit_timestamp(Path("/repo"), task)
        self.assertEqual(result, "2026-08-19T06:48:00+00:00")
        succeeds.assert_not_called()


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

    def test_terminal_facts_satisfy_dependencies_without_archive_lookup(self) -> None:
        terminal = deepcopy(self.state["tasks"][0])
        terminal.update({"status": "done", "terminal_outcome": "completed", "generation": 3})
        self.state["tasks"] = [self.state["tasks"][1]]
        ai_status.record_terminal_fact(
            self.state,
            terminal,
            recorded_at="2026-04-14T02:01:00Z",
        )

        with mock.patch.object(
            ai_status.task_archive_module,
            "load_archived_snapshot",
            side_effect=AssertionError("scheduler must not read the archive"),
        ):
            resolver = ai_status.task_resolver(self.state)
            resolved = resolver.get("REG-100")

        self.assertEqual(resolver.source("REG-100"), "terminal_fact")
        self.assertEqual(resolved["status"], "done")
        self.assertTrue(resolver.dependency_satisfied("REG-100"))

    def test_terminal_facts_preserve_compact_completion_track_status(self) -> None:
        terminal = deepcopy(self.state["tasks"][0])
        terminal.update(
            {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 3,
                "completion_tracks": {
                    "functional": {
                        "status": "done",
                        "evidence": ["paper-proof.json"],
                    },
                    "hosted": {
                        "status": "external_wait",
                        "message": "awaiting governed hosted run",
                    },
                },
            }
        )
        self.state["tasks"] = [self.state["tasks"][1]]

        ai_status.record_terminal_fact(
            self.state,
            terminal,
            recorded_at="2026-04-14T02:01:00Z",
        )

        fact = self.state[ai_status.TERMINAL_FACTS_KEY]["REG-100"]
        self.assertEqual(
            fact["completion_tracks"],
            {
                "functional": {"status": "done"},
                "hosted": {"status": "external_wait"},
            },
        )
        resolver = ai_status.task_resolver(self.state)
        resolved = resolver.get("REG-100")
        self.assertEqual(
            resolved["completion_tracks"]["hosted"]["status"],
            "external_wait",
        )

    def test_reopen_rejects_archived_task(self) -> None:
        self.state["tasks"] = []
        self.state[ai_status.TERMINAL_FACTS_KEY] = {
            "REG-100": {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 1,
                "recorded_at": "2026-04-14T02:00:00Z",
            }
        }
        with mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False):
            with self.assertRaises(SystemExit) as exc_info:
                _command_reopen(self.state, ["REG-100", "Resume work"])

        self.assertIn("terminal", str(exc_info.exception))
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
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=snapshot),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ai_status.command_show(self.state, ["REG-100"])

        rendered = stdout.getvalue()
        self.assertIn('"source": "archive"', rendered)
        self.assertIn('"task_id": "REG-100"', rendered)
        self.assertIn("ai-task-archive/tasks", rendered)

    def test_show_retains_terminal_fact_when_rich_archive_is_missing(self) -> None:
        self.state["tasks"] = []
        self.state[ai_status.TERMINAL_FACTS_KEY] = {
            "REG-100": {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 3,
                "recorded_at": "2026-04-14T02:00:00Z",
            }
        }
        with (
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ai_status.command_show(self.state, ["REG-100"])

        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["source"], "terminal_fact")
        self.assertTrue(rendered["archive_missing"])
        self.assertEqual(rendered["task"]["generation"], 3)

    def test_terminal_projection_reports_missing_archive_without_losing_fact(self) -> None:
        self.state["tasks"] = []
        self.state[ai_status.TERMINAL_FACTS_KEY] = {
            "REG-100": {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 3,
                "recorded_at": "2026-04-14T02:00:00Z",
            }
        }
        with mock.patch.object(ai_status, "load_archived_snapshot", return_value=None):
            projection = ai_status.terminal_archive_projection(self.state)

        self.assertEqual(projection[0]["task_id"], "REG-100")
        self.assertTrue(projection[0]["archive_missing"])
        self.assertFalse(projection[0]["archive_receipt_valid"])

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
            event_log = Path(tmpdir).parent / f"{Path(tmpdir).name}-runtime" / "task-state-events.jsonl"
            ai_status.append_state_commit(event_log, state, source="test-fixture")
            before = status_file.read_bytes()
            env = os.environ.copy()
            for name in (
                "ORCH_RUN_ID",
                "PANTHEON_WORKTREE_ROOT",
                "ORCH_WORKSPACE_PATH",
                "ORCH_RUNNER_STATUS_PATH",
                "ORCH_HEARTBEAT_PATH",
                "PANTHEON_COMMAND_ROOT",
                "PANTHEON_COMMAND_RUNTIME_SHA",
            ):
                env.pop(name, None)
            env.update(
                {
                    "AI_NAME": "Codex",
                    "PANTHEON_STATUS_ROOT": str(status_root),
                    ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                    ai_status.TASK_STATE_EVENT_LOG_ENV: str(event_log),
                    common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                        status_root,
                        event_log,
                    ),
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
            event_log = Path(tmpdir).parent / f"{Path(tmpdir).name}-runtime" / "task-state-events.jsonl"
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
                "PANTHEON_COMMAND_ROOT",
                "PANTHEON_COMMAND_RUNTIME_SHA",
            ):
                env.pop(name, None)
            env.update(
                {
                    "AI_NAME": "Codex",
                    "PANTHEON_STATUS_ROOT": str(status_root),
                    ai_status.TASK_STATE_STORE_MODE_ENV: "authoritative",
                    ai_status.TASK_STATE_EVENT_LOG_ENV: str(event_log),
                    common.CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_state_identity_json(
                        status_root,
                        event_log,
                    ),
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
                {"name": "Claude2", "capability_lane": [], "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": None},
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
            ai_status.command_assign(self.state, ["APP-001-SIDECAR-BFF-HANDOFF", "Claude2", "Copilot"])

        task = ai_status.get_task(self.state, "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertIsNotNone(task)
        self.assertEqual(task["task_class"], "sidecar")
        self.assertTrue(task["auto_generated"])
        self.assertEqual(task["helper_parent"], "APP-001")
        self.assertEqual(task["helper_kind"], "bff_handoff_packet")
        self.assertFalse(task["mutates_canonical"])
        self.assertEqual(task["auto_created_by"], "supervisor-underutilization")
        self.assertEqual(task["depends_on"], ["PER-001"])

    def test_assign_supports_allowlisted_execution_resources_from_env(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy to pantheon-dev",
            "TASK_EXECUTION_RESOURCES": "pantheon-dev",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_assign(self.state, ["HOSTED-DEV-001", "Codex", "Claude"])

        task = ai_status.get_task(self.state, "HOSTED-DEV-001")
        self.assertIsNotNone(task)
        self.assertEqual(task["execution_resources"], ["pantheon-dev"])

    def test_assign_supports_execution_resources_json_from_env(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy to pantheon-dev JSON",
            "TASK_EXECUTION_RESOURCES_JSON": json.dumps(["pantheon-dev"]),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_assign(self.state, ["HOSTED-DEV-002", "Codex", "Claude"])

        task = ai_status.get_task(self.state, "HOSTED-DEV-002")
        self.assertIsNotNone(task)
        self.assertEqual(task["execution_resources"], ["pantheon-dev"])

    def test_assign_execution_resources_absent_defaults_empty(self) -> None:
        # ABSENT []: When neither TASK_EXECUTION_RESOURCES nor TASK_EXECUTION_RESOURCES_JSON is set
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with absent execution resources",
        }
        filtered_env = {
            k: v for k, v in os.environ.items()
            if k not in {"TASK_EXECUTION_RESOURCES", "TASK_EXECUTION_RESOURCES_JSON"}
        }
        filtered_env.update(env)
        with mock.patch.dict(os.environ, filtered_env, clear=True):
            ai_status.command_assign(self.state, ["HOSTED-DEV-ABSENT", "Codex", "Claude"])

        task = ai_status.get_task(self.state, "HOSTED-DEV-ABSENT")
        self.assertIsNotNone(task)
        self.assertEqual(task["execution_resources"], [])

    def test_assign_rejects_explicit_empty_json_string(self) -> None:
        # JSON_EMPTY: TASK_EXECUTION_RESOURCES_JSON='' must be rejected, not treated as omitted => []
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with empty JSON execution resources",
            "TASK_EXECUTION_RESOURCES_JSON": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "TASK_EXECUTION_RESOURCES_JSON must be a valid JSON list"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-JSON-EMPTY", "Codex", "Claude"])

    def test_assign_rejects_explicit_empty_csv_string(self) -> None:
        # CSV_EMPTY: TASK_EXECUTION_RESOURCES='' must be rejected, not treated as omitted => []
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with empty CSV execution resources",
            "TASK_EXECUTION_RESOURCES": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "cannot be empty"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-CSV-EMPTY", "Codex", "Claude"])

    def test_assign_rejects_unallowlisted_execution_resources(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with forbidden resource",
            "TASK_EXECUTION_RESOURCES": "forbidden-cluster",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "unallowlisted resource"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-003", "Codex", "Claude"])

    def test_assign_rejects_invalid_execution_resources_json(self) -> None:
        env = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with invalid resource json",
            "TASK_EXECUTION_RESOURCES_JSON": json.dumps({"not": "a list"}),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "must be a list"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-004", "Codex", "Claude"])

        # JSON null
        env_null = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with null resource json",
            "TASK_EXECUTION_RESOURCES_JSON": "null",
        }
        with mock.patch.dict(os.environ, env_null, clear=False):
            with self.assertRaisesRegex(SystemExit, "must be a list, got null"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-004-NULL", "Codex", "Claude"])

    def test_assign_rejects_duplicate_execution_resources(self) -> None:
        # Duplicate in CSV
        env_csv = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with duplicate resource csv",
            "TASK_EXECUTION_RESOURCES": "pantheon-dev,pantheon-dev",
        }
        with mock.patch.dict(os.environ, env_csv, clear=False):
            with self.assertRaisesRegex(SystemExit, "duplicate resource"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-DUP-1", "Codex", "Claude"])

        # Duplicate in JSON
        env_json = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with duplicate resource json",
            "TASK_EXECUTION_RESOURCES_JSON": json.dumps(["pantheon-dev", "pantheon-dev"]),
        }
        with mock.patch.dict(os.environ, env_json, clear=False):
            with self.assertRaisesRegex(SystemExit, "duplicate resource"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-DUP-2", "Codex", "Claude"])

    def test_assign_rejects_empty_string_execution_resources(self) -> None:
        # Empty string element in CSV
        env_csv = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with empty resource csv",
            "TASK_EXECUTION_RESOURCES": "pantheon-dev,",
        }
        with mock.patch.dict(os.environ, env_csv, clear=False):
            with self.assertRaisesRegex(SystemExit, "cannot be empty"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-EMPTY-1", "Codex", "Claude"])

        # Empty string element in JSON
        env_json = {
            "AI_NAME": "Codex",
            "TASK_TITLE": "Deploy with empty resource json",
            "TASK_EXECUTION_RESOURCES_JSON": json.dumps(["pantheon-dev", ""]),
        }
        with mock.patch.dict(os.environ, env_json, clear=False):
            with self.assertRaisesRegex(SystemExit, "cannot be empty"):
                ai_status.command_assign(self.state, ["HOSTED-DEV-EMPTY-2", "Codex", "Claude"])

    def test_bridge_metadata_validation_rejects_duplicate_execution_resources(self) -> None:
        task_spec = {
            "id": "TASK-DUP-RES",
            "owner": "Codex",
            "reviewer": "Claude",
            "title": "Bridge Task with duplicate resources",
            "depends_on": [],
            "artifacts": [],
            "acceptance": [],
            "execution_resources": ["pantheon-dev", "pantheon-dev"],
        }
        encoded = json.dumps(task_spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        task_spec_hash = hashlib.sha256(encoded).hexdigest()
        metadata = {
            "dev_bridge": {
                "packet_id": "pkt-test-dup",
                "packet_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "conversation_id": "conv123",
                "source_turn_ids": ["turn1"],
                "documents": [{"path": "docs/test.md"}],
                "task_spec_hash": task_spec_hash,
                "task_spec": task_spec,
            }
        }
        with self.assertRaisesRegex(SystemExit, "duplicate resource"):
            ai_status._bridge_assignment_from_metadata(
                metadata,
                task_id="TASK-DUP-RES",
                owner="Codex",
                reviewer="Claude",
                title="Bridge Task with duplicate resources",
            )


    def test_execution_resource_command_adds_and_removes_resource_on_todo_task(self) -> None:
        task = {
            "id": "TASK-RES-001",
            "title": "Hosted resource test",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "execution_resources": [],
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)

        env = {"AI_NAME": "Human/Ops"}
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_execution_resource(
                self.state,
                ["TASK-RES-001", "add", "pantheon-dev", "Admit shared dev environment"],
            )

        updated = ai_status.get_task(self.state, "TASK-RES-001")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["execution_resources"], ["pantheon-dev"])
        self.assertEqual(updated["next"], "Admit shared dev environment")
        self.assertEqual(
            updated["contract_revision"],
            {
                "kind": "execution_resource",
                "action": "add",
                "resource": "pantheon-dev",
                "previous": [],
                "current": ["pantheon-dev"],
                "reason": "Admit shared dev environment",
                "updated_at": updated["last_update"],
                "updated_by": "Human/Ops",
            },
        )

        lines = self._test_log_file.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
        rev_events = [e for e in events if e.get("type") == "execution_resource_revised"]
        self.assertEqual(len(rev_events), 1)
        self.assertEqual(rev_events[0]["task_id"], "TASK-RES-001")
        self.assertEqual(rev_events[0]["action"], "add")
        self.assertEqual(rev_events[0]["resource"], "pantheon-dev")

        # Now remove resource
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_execution_resource(
                self.state,
                ["TASK-RES-001", "remove", "pantheon-dev", "Release shared dev environment claim"],
            )

        removed = ai_status.get_task(self.state, "TASK-RES-001")
        self.assertIsNotNone(removed)
        self.assertEqual(removed["execution_resources"], [])
        self.assertEqual(removed["contract_revision"]["action"], "remove")
        self.assertEqual(removed["contract_revision"]["previous"], ["pantheon-dev"])
        self.assertEqual(removed["contract_revision"]["current"], [])

    def test_execution_resource_command_works_on_blocked_task(self) -> None:
        task = {
            "id": "TASK-RES-BLOCKED",
            "title": "Blocked task resource revision",
            "status": "blocked",
            "owner": "Codex",
            "reviewer": "Claude",
            "waiting_for": "External",
            "execution_resources": [],
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)

        env = {"AI_NAME": "Human/Ops"}
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_execution_resource(
                self.state,
                ["TASK-RES-BLOCKED", "add", "pantheon-dev", "Admit dev resource while blocked"],
            )

        updated = ai_status.get_task(self.state, "TASK-RES-BLOCKED")
        self.assertEqual(updated["execution_resources"], ["pantheon-dev"])

    def test_execution_resource_command_rejects_non_human_ops(self) -> None:
        task = {
            "id": "TASK-RES-NON-HUMAN",
            "title": "Non human actor test",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)

        env = {"AI_NAME": "Codex"}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "Only Human/Ops can revise execution resources"):
                ai_status.command_execution_resource(
                    self.state,
                    ["TASK-RES-NON-HUMAN", "add", "pantheon-dev", "Codex attempt"],
                )

    def test_execution_resource_command_rejects_active_task_lifecycle(self) -> None:
        for active_status in ("in_progress", "review", "review_approved"):
            task_id = f"TASK-RES-ACTIVE-{active_status.upper()}"
            task = {
                "id": task_id,
                "title": f"Active task in {active_status}",
                "status": active_status,
                "owner": "Codex",
                "reviewer": "Claude",
                "last_update": "2026-08-25T10:00:00Z",
            }
            self.state["tasks"].append(task)
            env = {"AI_NAME": "Human/Ops"}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(SystemExit, "is active in lifecycle state"):
                    ai_status.command_execution_resource(
                        self.state,
                        [task_id, "add", "pantheon-dev", "Active task revision attempt"],
                    )

    def test_execution_resource_command_rejects_terminal_task_lifecycle(self) -> None:
        for term_status in ("done", "superseded"):
            task_id = f"TASK-RES-TERM-{term_status.upper()}"
            task = {
                "id": task_id,
                "title": f"Terminal task in {term_status}",
                "status": term_status,
                "owner": "Codex",
                "reviewer": "Claude",
                "last_update": "2026-08-25T10:00:00Z",
            }
            self.state["tasks"].append(task)
            env = {"AI_NAME": "Human/Ops"}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(SystemExit, "is terminal in lifecycle state"):
                    ai_status.command_execution_resource(
                        self.state,
                        [task_id, "add", "pantheon-dev", "Terminal task revision attempt"],
                    )

    def test_execution_resource_command_rejects_non_pre_dispatch_unknown_lifecycle_states(self) -> None:
        for unknown_status in ("draft", "paused", "unknown", ""):
            task_id = f"TASK-RES-UNK-{unknown_status or 'EMPTY'}"
            task = {
                "id": task_id,
                "title": f"Unknown status task in {unknown_status!r}",
                "status": unknown_status,
                "owner": "Codex",
                "reviewer": "Claude",
                "last_update": "2026-08-25T10:00:00Z",
            }
            self.state["tasks"].append(task)
            env = {"AI_NAME": "Human/Ops"}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(SystemExit, "non-pre-dispatch lifecycle state"):
                    ai_status.command_execution_resource(
                        self.state,
                        [task_id, "add", "pantheon-dev", "Unknown state revision attempt"],
                    )


    def test_execution_resource_command_rejects_unallowlisted_resource(self) -> None:
        task = {
            "id": "TASK-RES-UNALLOWLISTED",
            "title": "Unallowlisted resource test",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)
        env = {"AI_NAME": "Human/Ops"}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "allowlisted execution resources"):
                ai_status.command_execution_resource(
                    self.state,
                    ["TASK-RES-UNALLOWLISTED", "add", "unallowlisted-vm", "Invalid resource"],
                )

    def test_execution_resource_command_rejects_invalid_action_and_unknown_task(self) -> None:
        task = {
            "id": "TASK-RES-INVALID",
            "title": "Invalid action test",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)
        env = {"AI_NAME": "Human/Ops"}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(SystemExit, "Action must be add or remove"):
                ai_status.command_execution_resource(
                    self.state,
                    ["TASK-RES-INVALID", "modify", "pantheon-dev", "Invalid action"],
                )
            with self.assertRaisesRegex(SystemExit, "Unknown task: UNKNOWN-TASK-ID"):
                ai_status.command_execution_resource(
                    self.state,
                    ["UNKNOWN-TASK-ID", "add", "pantheon-dev", "Unknown task"],
                )
            with self.assertRaisesRegex(SystemExit, "Usage: execution-resource"):
                ai_status.command_execution_resource(
                    self.state,
                    ["TASK-RES-INVALID", "add"],
                )

    def test_execution_resource_command_rejects_malformed_existing_execution_resources(self) -> None:
        malformed_cases = [
            ("TASK-MAL-NULL", None, "must be a list, got null"),
            ("TASK-MAL-STR", "pantheon-dev", "must be a list"),
            ("TASK-MAL-NON-STR", [123], "elements must be strings"),
            ("TASK-MAL-EMPTY-STR", [""], "cannot be empty"),
            ("TASK-MAL-UNALLOW", ["bad-resource"], "unallowlisted resource"),
            ("TASK-MAL-DUP", ["pantheon-dev", "pantheon-dev"], "duplicate resource"),
            ("TASK-MAL-DICT", {"pantheon-dev": 1}, "must be a list"),
        ]
        for task_id, malformed_val, error_regex in malformed_cases:
            task = {
                "id": task_id,
                "title": f"Malformed test for {task_id}",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "execution_resources": malformed_val,
                "last_update": "2026-08-25T10:00:00Z",
            }
            self.state["tasks"].append(task)
            env = {"AI_NAME": "Human/Ops"}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(SystemExit, error_regex):
                    ai_status.command_execution_resource(
                        self.state,
                        [task_id, "add", "pantheon-dev", "Migration attempt on malformed task"],
                    )

    def test_execution_resource_command_success_when_field_absent(self) -> None:
        task = {
            "id": "TASK-RES-ABSENT",
            "title": "Absent execution_resources test",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)
        self.assertNotIn("execution_resources", task)

        env = {"AI_NAME": "Human/Ops"}
        with mock.patch.dict(os.environ, env, clear=False):
            ai_status.command_execution_resource(
                self.state,
                ["TASK-RES-ABSENT", "add", "pantheon-dev", "Add resource when field absent"],
            )

        updated = ai_status.get_task(self.state, "TASK-RES-ABSENT")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["execution_resources"], ["pantheon-dev"])

    def test_execution_resource_command_alias_removed(self) -> None:
        # Confirm that execution_resource (with underscore) is no longer a recognized command
        with (
            mock.patch.object(ai_status, "validate_status_command_runtime_binding"),
            mock.patch.object(ai_status, "validate_status_root_binding"),
            mock.patch.object(ai_status, "load_state", return_value=self.state),
            self.assertRaisesRegex(SystemExit, "Unknown command: execution_resource"),
        ):
            ai_status.main(["ai_status.py", "execution_resource", "TASK-001", "add", "pantheon-dev", "reason"])

    def test_command_show_reads_back_execution_resources(self) -> None:
        task = {
            "id": "TASK-SHOW-RES",
            "title": "Show execution resource test",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "execution_resources": ["pantheon-dev"],
            "last_update": "2026-08-25T10:00:00Z",
        }
        self.state["tasks"].append(task)
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            ai_status.command_show(self.state, ["TASK-SHOW-RES"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["source"], "active")
        self.assertEqual(payload["task"]["execution_resources"], ["pantheon-dev"])

    def test_assign_preserves_antigravity_runtime_agent_names(self) -> None:
        ai_status.command_assign(self.state, ["APP-002-SIDECAR-REVIEW", "Antigravity2", "Claude"])

        task = ai_status.get_task(self.state, "APP-002-SIDECAR-REVIEW")
        self.assertIsNotNone(task)
        self.assertEqual(task["owner"], "Antigravity2")
        self.assertEqual(task["reviewer"], "Claude")

    def test_first_assign_logs_structured_old_and_new_roles(self) -> None:
        ai_status.command_assign(self.state, ["APP-003-SIDECAR-REVIEW", "Claude", "Antigravity2"])

        lines = self._test_log_file.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
        assign_events = [e for e in events if e.get("task_id") == "APP-003-SIDECAR-REVIEW"]
        self.assertEqual(len(assign_events), 1)
        event = assign_events[0]
        self.assertEqual(event["type"], "assign")
        self.assertEqual(event["old_owner"], "")
        self.assertEqual(event["new_owner"], "Claude")
        self.assertEqual(event["old_reviewer"], "")
        self.assertEqual(event["new_reviewer"], "Antigravity2")

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
                    ["APP-003", "Claude2", "Copilot", "Conflicting contract"],
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

    def test_human_ops_runtime_recall_does_not_rerun_source_scope_admission(self) -> None:
        guard = self._artifact_guard(
            "CATALOG-BFF-RECALL",
            "services/control-plane/bff/main.py",
            [],
        )
        self.state["tasks"].extend(
            [
                {
                    "id": "CATALOG-BFF-RECALL",
                    "status": "in_progress",
                    "owner": "Codex2",
                    "reviewer": "Claude",
                    "title": "Protected source task",
                    "artifacts": ["services/control-plane/bff/main.py"],
                    "target_repo": "pantheon",
                    "artifact_conflict_guard": guard,
                },
                {
                    "id": "LATER-BFF-OVERLAP",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "title": "Later overlapping task",
                    "artifacts": ["services/control-plane/bff"],
                },
            ]
        )

        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Human/Ops",
                "TASK_ASSIGN_REASON": "Recall the current runtime assignment.",
                "TASK_ASSIGN_EXPECTED_OWNER": "Codex2",
                "TASK_ASSIGN_EXPECTED_REVIEWER": "Claude",
            },
            clear=False,
        ):
            ai_status.command_assign(
                self.state,
                ["CATALOG-BFF-RECALL", "Human/Ops", "Claude"],
            )

        task = ai_status.get_task(self.state, "CATALOG-BFF-RECALL")
        self.assertEqual(task["owner"], "Human/Ops")
        self.assertEqual(task["reviewer"], "Claude")
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["artifact_conflict_guard"], guard)

    def test_human_ops_recall_cannot_change_admitted_contract_metadata(self) -> None:
        self.state["tasks"].append(
            {
                "id": "SOURCE-CONTRACT-RECALL",
                "status": "todo",
                "owner": "Codex2",
                "reviewer": "Claude",
                "title": "Immutable source contract",
                "artifacts": ["scripts/source.py"],
                "acceptance": ["tests pass"],
                "target_repo": "pantheon",
            }
        )

        with mock.patch.dict(
            os.environ,
            {
                "AI_NAME": "Human/Ops",
                "TASK_ASSIGN_REASON": "operator recall",
                "TASK_METADATA_JSON": json.dumps(
                    {"target_repo": "execute-plans"}
                ),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                SystemExit, "contract metadata is immutable"
            ):
                ai_status.command_assign(
                    self.state,
                    ["SOURCE-CONTRACT-RECALL", "Human/Ops", "Claude"],
                )

        task = ai_status.get_task(self.state, "SOURCE-CONTRACT-RECALL")
        self.assertEqual(task["owner"], "Codex2")
        self.assertEqual(task["target_repo"], "pantheon")

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
    def test_structured_quarantine_is_a_valid_blocked_state_without_waiting_for(self) -> None:
        state = {
            "agents": [
                {"name": "Codex", "status": "idle"},
                {"name": "Antigravity", "status": "idle"},
            ],
            "tasks": [
                {
                    "id": "QUARANTINED-001",
                    "owner": "Codex",
                    "reviewer": "Antigravity",
                    "status": "blocked",
                    "block_reason": {
                        "kind": "legacy_task_quarantine",
                        "required_action": "operator_reopen_after_diagnosis",
                    },
                }
            ],
            "handoffs": [],
            "blockers": [],
        }

        ai_status.validate_state(state)

    def test_unstructured_blocked_state_still_requires_waiting_for(self) -> None:
        state = {
            "agents": [
                {"name": "Codex", "status": "idle"},
                {"name": "Antigravity", "status": "idle"},
            ],
            "tasks": [
                {
                    "id": "INVALID-BLOCKED-001",
                    "owner": "Codex",
                    "reviewer": "Antigravity",
                    "status": "blocked",
                }
            ],
            "handoffs": [],
            "blockers": [],
        }

        with self.assertRaisesRegex(SystemExit, "missing waiting_for or a structured block_reason"):
            ai_status.validate_state(state)

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
                    "reviewer": "Codex2",
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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertNotIn("focus_mode", bundle)
        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertNotIn("dispatch_targets", bundle["runtime_summary"])
        self.assertEqual(bundle["execution_summary"]["ready_now"], 0)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["in_review"], 1)
        self.assertNotIn("planning_summary", bundle)
        self.assertNotIn("bridge_summary", bundle)
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
                {"name": "Claude2", "status": "idle", "current_task_ids": [], "branch": "", "next": "", "last_update": "2026-04-15T15:32:45Z"},
            ],
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "title": "Lock the deployable service baseline and single-VM topology",
                    "summary_zh": "主線 baseline 定義。",
                    "owner": "Codex",
                    "reviewer": "Claude2",
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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        mismatch_ids = {item["id"] for item in bundle["truth_mismatches"]}
        self.assertNotIn("active-task-without-worker:BP5-LUV-007", mismatch_ids)

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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

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
        config = {"paths": {"state_file": ".orchestrator/state.json"}}
        planning_state = {"status": "accepted", "runtime_mode": "supervisor_managed_execution", "proposed_execution_tasks": []}
        orchestrator_state = {"supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-11T13:08:22Z"}, "queue": {"events": {}}, "workers": {}}
        approval_state = {"pending": [], "history": []}

        with tempfile.TemporaryDirectory(prefix="ai-status-dashboard-bundle-") as temp_dir:
            output_path = Path(temp_dir) / "dashboard-bundle.json"
            with mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", output_path):
                with mock.patch.object(ai_status, "load_config", return_value=config):
                    with mock.patch.object(ai_status, "load_runtime_state", return_value=orchestrator_state) as load_runtime_state:
                        with mock.patch.object(ai_status, "load_json_file", return_value=approval_state) as load_json_file:
                            ai_status.write_dashboard_bundle(state)

            bundle = json.loads(output_path.read_text(encoding="utf-8"))

        load_runtime_state.assert_called_once_with(config)
        load_json_file.assert_called_once_with(ai_status.APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
        self.assertEqual(bundle["runtime_summary"]["supervisor_pid"], 1)
        self.assertEqual(bundle["execution_summary"]["in_progress"], 1)
        self.assertNotIn("focus_mode", bundle)
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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["execution_summary"]["done"], 2)
        self.assertEqual(bundle["execution_summary"]["superseded"], 1)
        self.assertEqual(bundle["archive_summary"]["recent_terminal_ids"], ["REG-100"])
        self.assertEqual(bundle["archive_summary"]["recent_terminal_tasks"][0]["task_id"], "REG-100")

    def test_build_dashboard_bundle_reports_lifecycle_ready_without_policy_inference(self) -> None:
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
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z"},
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
        }
        approval_state = {"pending": [], "history": []}
        config = {"agents": {"claude": {"display_name": "Claude", "max_parallel": 1}}}

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 2)
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
            "supervisor": {"pid": 1, "last_heartbeat_at": "2026-04-16T08:50:05Z"},
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
        }
        approval_state = {"pending": [], "history": []}
        config = {
            "agents": {"codex": {"display_name": "Codex", "max_parallel": 2}},
            "ready_dispatcher": {
                "max_concurrent_per_account": {"codex1": 4},
            }
        }

        with (
            mock.patch.object(ai_status, "load_config", return_value=config),
            mock.patch.object(ai_status, "load_archive_index", return_value={"updated_at": None, "counts": {"total": 0, "completed": 0, "superseded": 0}, "recent_terminal_ids": []}),
            mock.patch.object(ai_status, "pid_is_alive", return_value=True),
        ):
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 1)
        self.assertEqual(bundle["execution_summary"]["ready_now"], 1)
        self.assertEqual(bundle["execution_summary"]["dependency_ready"], 1)
        self.assertEqual(bundle["dispatch_policy"]["max_parallel_by_agent"], {"codex": 2})
        self.assertEqual(bundle["dispatch_policy"]["max_concurrent_per_account"], {"codex1": 4})

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
            },
            "queue": {
                "events": {
                    "evt-1": {
                        "status": "waiting_approval",
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
            bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertEqual(bundle["runtime_summary"]["running_workers"], 0)
        self.assertEqual(bundle["runtime_summary"]["pending_workers"], 0)
        self.assertEqual(bundle["worker_task_links"], [])
        self.assertFalse(any(item["type"] == "queue_started_without_worker" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_flags_legacy_planning_approval_without_task_board_row(self) -> None:
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

        bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertTrue(any(item["type"] == "approval_missing_task" for item in bundle["truth_mismatches"]))

    def test_build_dashboard_bundle_flags_legacy_planning_worker_without_task_board_row(self) -> None:
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

        bundle = ai_status.build_dashboard_bundle(state, orchestrator_state, approval_state)

        self.assertTrue(any(item["type"] == "worker_task_missing" for item in bundle["truth_mismatches"]))


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
                task_archive.status_archive_outbox_payload(
                    pending["snapshots"],
                    archive_root=str(archive_dir.resolve()),
                )["transaction_id"],
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
        self.assertEqual(
            final[ai_status.TERMINAL_FACTS_KEY]["LOCK-ONE"],
            {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 1,
                "recorded_at": "2026-07-14T00:03:00Z",
            },
        )
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
        receipt = final[ai_status.ARCHIVE_RECEIPTS_KEY]["LOCK-ONE"]
        self.assertEqual(receipt["archive_root"], str((self.root / "ai-task-archive").resolve()))
        self.assertEqual(
            receipt["snapshot_sha256"],
            ai_status._canonical_json_sha256(archived),
        )
        self.assertEqual(
            receipt["index_sha256"],
            ai_status._canonical_json_sha256(index),
        )

    def test_archive_reconcile_imports_only_matching_terminal_fact(self) -> None:
        state = self._fixture_state()
        terminal = deepcopy(state["tasks"][0])
        terminal.update({"status": "done", "terminal_outcome": "completed", "generation": 2})
        state["tasks"] = []
        ai_status.record_terminal_fact(
            state,
            terminal,
            recorded_at="2026-07-14T00:03:00Z",
        )
        former_tasks = self.root / "former-root" / "ai-task-archive" / "tasks"
        former_tasks.mkdir(parents=True)
        snapshot = {
            "version": 1,
            "task_id": "LOCK-ONE",
            "archived_at": "2026-07-14T00:03:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": terminal,
            "handoffs": [],
            "blockers": [],
        }
        (former_tasks / "LOCK-ONE.json").write_text(
            json.dumps(snapshot, indent=2) + "\n",
            encoding="utf-8",
        )
        # An unreferenced legacy record must not be copied merely because it
        # shares the former archive directory.
        (former_tasks / "UNOWNED-LEGACY.json").write_text(
            json.dumps({**snapshot, "task_id": "UNOWNED-LEGACY", "task": {**terminal, "id": "UNOWNED-LEGACY"}}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False):
            ai_status.command_archive_reconcile(state, [str(former_tasks)])
        pending = state[ai_status.STATUS_ARCHIVE_OUTBOX_KEY]
        self.assertEqual([item["task_id"] for item in pending["snapshots"]], ["LOCK-ONE"])
        self._write_state(state)
        self.assertTrue(ai_status.recover_status_archive_outbox(state))

        archived = json.loads(
            (self.root / "ai-task-archive" / "tasks" / "LOCK-ONE.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(archived, snapshot)
        self.assertFalse(
            (self.root / "ai-task-archive" / "tasks" / "UNOWNED-LEGACY.json").exists()
        )
        self.assertIn("LOCK-ONE", state[ai_status.ARCHIVE_RECEIPTS_KEY])

    def test_retire_archive_collision_preserves_archives_and_links_replacement(
        self,
    ) -> None:
        state = self._fixture_state()
        active = state["tasks"][0]
        active.update(
            {
                "generation": 2,
                "status": "blocked",
                "waiting_for": "Human/Ops",
                "delivery_binding": {
                    "kind": "pull_request",
                    "pr": 5028,
                    "head_sha": "b" * 40,
                },
            }
        )
        original_terminal = {
            **deepcopy(active),
            "status": "done",
            "terminal_outcome": "completed",
        }
        original_terminal.pop("waiting_for", None)
        replacement_terminal = {
            **deepcopy(state["tasks"][1]),
            "status": "done",
            "terminal_outcome": "completed",
        }
        state["tasks"] = [active]
        state["handoffs"] = [{"task_id": "LOCK-ONE", "status": "pending"}]
        state["blockers"] = [{"task_id": "LOCK-ONE", "status": "open"}]
        ai_status.record_terminal_fact(
            state,
            replacement_terminal,
            recorded_at="2026-07-14T00:04:00Z",
        )
        original_snapshot = {
            "version": 1,
            "task_id": "LOCK-ONE",
            "archived_at": "2026-07-14T00:03:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": original_terminal,
            "handoffs": [],
            "blockers": [],
        }
        replacement_snapshot = {
            "version": 1,
            "task_id": "LOCK-TWO",
            "archived_at": "2026-07-14T00:04:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": replacement_terminal,
            "handoffs": [],
            "blockers": [],
        }
        original_path = task_archive.ARCHIVE_TASKS_DIR / "LOCK-ONE.json"
        replacement_path = task_archive.ARCHIVE_TASKS_DIR / "LOCK-TWO.json"
        original_path.write_text(
            json.dumps(original_snapshot, indent=2) + "\n",
            encoding="utf-8",
        )
        replacement_path.write_text(
            json.dumps(replacement_snapshot, indent=2) + "\n",
            encoding="utf-8",
        )
        original_bytes = original_path.read_bytes()
        replacement_bytes = replacement_path.read_bytes()
        before = deepcopy(state)

        with (
            mock.patch.dict(os.environ, {"AI_NAME": "Human/Ops"}, clear=False),
            ai_status.buffer_activity_events() as events,
        ):
            ai_status.command_retire_archive_collision(
                state,
                ["LOCK-ONE", "LOCK-TWO", "Retire duplicate active row."],
            )

        task_state_store.validate_state_transition(state, before)
        self.assertIsNone(ai_status.get_task(state, "LOCK-ONE"))
        self.assertEqual(state["handoffs"], [])
        self.assertEqual(state["blockers"], [])
        self.assertEqual(
            state[ai_status.TERMINAL_FACTS_KEY]["LOCK-ONE"],
            {
                "status": "done",
                "terminal_outcome": "completed",
                "generation": 2,
                "recorded_at": "2026-07-14T00:03:00Z",
            },
        )
        self.assertIn("LOCK-ONE", state[ai_status.ARCHIVE_RECEIPTS_KEY])
        self.assertEqual(
            state[task_state_store.DRAIN_MARKER_KEY],
            {
                "reason": "Retire duplicate active row.",
                "actor": "Human/Ops",
                "approved_at": events[0]["ts"],
                "task_ids": ["LOCK-ONE"],
            },
        )
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(replacement_path.read_bytes(), replacement_bytes)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "active_archive_collision_retired")
        self.assertEqual(events[0]["replacement_task_id"], "LOCK-TWO")
        self.assertEqual(events[0]["removed_handoff_count"], 1)
        self.assertEqual(events[0]["removed_blocker_count"], 1)

    def test_retire_archive_collision_rejects_unfinished_replacement(self) -> None:
        state = self._fixture_state()
        state["tasks"][0].update({"generation": 2, "status": "blocked"})
        before = deepcopy(state)

        with mock.patch.dict(
            os.environ,
            {"AI_NAME": "Human/Ops"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                SystemExit,
                "Replacement task is still active",
            ):
                ai_status.command_retire_archive_collision(
                    state,
                    ["LOCK-ONE", "LOCK-TWO", "Must fail closed."],
                )

        self.assertEqual(state, before)

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
            "load_archived_snapshot",
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
            "load_archived_snapshot",
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
            "load_archived_snapshot",
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
            archive = _rotate_activity_log_for_test(log_path)
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
            archive = _rotate_activity_log_for_test(log_path)
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
            _rotate_activity_log_for_test(log_path)
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
