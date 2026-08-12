#!/usr/bin/env python3
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import multiprocessing
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import common
import runtime_state


def _hold_runtime_lock(config: dict[str, object], connection: object) -> None:
    """Hold the real sidecar in a child until the parent asks for release."""

    try:
        with runtime_state.runtime_state_lock(
            config,
            shared=False,
            nonblocking=False,
        ):
            connection.send(("locked", os.getpid()))
            if connection.recv() != "release":
                raise RuntimeError("unexpected lock-holder command")
        connection.send(("released", os.getpid()))
    finally:
        connection.close()


def _hold_protocol_lock(
    plane: str,
    config: dict[str, object],
    status_file: str,
    activity_file: str,
    connection: object,
    shared: bool = False,
) -> None:
    if plane == "runtime_admission":
        guard = runtime_state.runtime_state_lock(
            config,
            shared=shared,
            nonblocking=False,
        )
    elif plane == "task_state":
        guard = runtime_state.canonical_task_state_lock_file(
            status_file,
            shared=shared,
            nonblocking=False,
        )
    elif plane == "activity_audit":
        guard = runtime_state.activity_audit_lock_file(
            activity_file,
            shared=shared,
            nonblocking=False,
        )
    else:
        raise ValueError(f"unknown test lock plane: {plane}")
    mode = "shared" if shared else "exclusive"
    try:
        with guard:
            connection.send(("locked", plane, mode, os.getpid()))
            if connection.recv() != "release":
                raise RuntimeError("unexpected lock-holder command")
        connection.send(("released", plane, mode, os.getpid()))
    finally:
        connection.close()


def _hold_audit_lock_and_rotate(activity_file: str, connection: object) -> None:
    log_path = Path(activity_file)
    try:
        with runtime_state.activity_audit_lock_file(
            log_path,
            shared=False,
            nonblocking=False,
        ):
            connection.send(("locked", os.getpid()))
            if connection.recv() != "rotate":
                raise RuntimeError("unexpected audit rotation command")
            archive_path = common.rotate_activity_log_unlocked(
                log_path,
                max_bytes=1,
                keep_lines=0,
            )
            connection.send(("rotated", str(archive_path)))
            if connection.recv() != "release":
                raise RuntimeError("unexpected audit rotation release command")
        connection.send(("released", os.getpid()))
    finally:
        connection.close()


def _write_rotating_audit_entry(
    activity_file: str,
    trace_file: str,
    connection: object,
) -> None:
    os.environ["PANTHEON_RUNTIME_LOCK_TRACE"] = trace_file
    try:
        connection.send(("started", os.getpid()))
        common.write_activity_log(
            {
                "paths": {
                    "activity_log": activity_file,
                    "activity_log_rotate_bytes": 1,
                }
            },
            {
                "event_id": "waiting-writer",
                "type": "cross_process_rotation_test",
            },
        )
        connection.send(("written", os.getpid()))
    finally:
        connection.close()


class LoadRuntimeStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            }
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_load_runtime_state_drops_suspended_worker_without_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-stale": {
                        "run_id": "claude-stale",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-missing",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["workers"], {})

    def test_load_runtime_state_keeps_suspended_worker_with_live_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-live": {
                        "run_id": "claude-live",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-live",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text(
            json.dumps({"event_id": "evt-live", "task_id": "EXEC-FRONT-TW03-001"}) + "\n",
            encoding="utf-8",
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertIn("claude-live", state["workers"])

    def test_load_runtime_state_drops_retired_control_plane_buckets(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "chair_rotation": {"current_index": 3},
                "underutilization": {"last_sidecar_wave_at": "2026-01-01T00:00:00Z"},
                "coordination": {"features": {"F-1": {}}},
                "provider_guardrails": {"task_failure_streaks": {"TASK-1": {}}},
                "supervisor": {
                    "mode_occupancy": {
                        "execution": {"running": 1},
                        "chair_review": {"running": 2},
                    }
                },
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertNotIn("chair_rotation", state)
        self.assertNotIn("underutilization", state)
        self.assertNotIn("coordination", state)
        self.assertNotIn("provider_guardrails", state)
        self.assertNotIn("mode_occupancy", state["supervisor"])

    def test_load_runtime_state_preserves_watchdog_safe_mode(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "watchdog": {
                    "safe_mode_until": "2026-05-18T14:30:00Z",
                    "safe_mode_reason": "stale_heartbeat",
                },
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["watchdog"]["safe_mode_until"], "2026-05-18T14:30:00Z")
        self.assertEqual(state["watchdog"]["safe_mode_reason"], "stale_heartbeat")
        self.assertIn("last_safe_mode_observed_until", state["watchdog"])

    def test_load_runtime_state_preserves_worker_worktree_cleanup_summary(self) -> None:
        last_run = {
            "at": "2026-06-20T06:59:40Z",
            "source": "worker_lifecycle",
            "checked": 25,
            "removed": 25,
            "archived": 4,
            "failed": 0,
        }
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "worker_worktree_cleanup": {"last_run": last_run},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["worker_worktree_cleanup"]["last_run"], last_run)

    def test_projection_reader_does_not_reverse_acquire_runtime_from_task_lock(self) -> None:
        self.config["paths"]["approval_queue"] = str(
            self.root / "approval-queue.json"
        )
        self._write_json(
            self.root / "state.json",
            {"version": 2, "workers": {}, "queue": {"events": {}}},
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self._write_json(
            self.root / "approval-queue.json",
            {"version": 2, "pending": [], "history": []},
        )
        with runtime_state.canonical_task_state_lock_file(
            self.root / "ai-status.json",
            shared=False,
            nonblocking=True,
        ):
            snapshot = runtime_state.load_runtime_state_snapshot(self.config)
        self.assertEqual(snapshot["workers"], {})


class RuntimeAdmissionProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.state_path = self.root / "state.json"
        self.event_queue_path = self.root / "event-queue.jsonl"
        self.approval_queue_path = self.root / "approval-queue.json"
        self.config = {
            "paths": {
                "state_file": str(self.state_path),
                "event_queue": str(self.event_queue_path),
                "approval_queue": str(self.approval_queue_path),
            }
        }

    def _write_valid_sources(
        self,
        *,
        runtime: dict[str, object] | None = None,
        events: list[dict[str, object]] | None = None,
        approvals: dict[str, object] | None = None,
    ) -> dict[str, bytes]:
        runtime_payload = runtime or {
            "version": 2,
            "workers": {},
            "queue": {
                "events": {
                    "evt-unrelated": {"status": "completed"},
                }
            },
        }
        event_payload = events or [
            {
                "event_id": "evt-unrelated",
                "task_id": "TASK-UNRELATED",
                "status": "completed",
            }
        ]
        approval_payload = approvals or {
            "version": 2,
            "pending": [],
            "history": [],
        }
        self.state_path.write_bytes(
            (
                json.dumps(runtime_payload, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
        self.event_queue_path.write_bytes(
            b"".join(
                (
                    json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")
                for event in event_payload
            )
        )
        self.approval_queue_path.write_bytes(
            (
                json.dumps(approval_payload, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
        return {
            "runtime_state": self.state_path.read_bytes(),
            "event_queue": self.event_queue_path.read_bytes(),
            "approval_queue": self.approval_queue_path.read_bytes(),
        }

    @staticmethod
    def _canonical_sha256(value: object) -> str:
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    def _start_lock_holder(self) -> tuple[multiprocessing.Process, object]:
        context = multiprocessing.get_context("fork")
        parent_connection, child_connection = context.Pipe()
        process = context.Process(
            target=_hold_runtime_lock,
            args=(self.config, child_connection),
        )
        process.start()
        child_connection.close()

        def cleanup() -> None:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            parent_connection.close()

        self.addCleanup(cleanup)
        self.assertTrue(
            parent_connection.poll(5),
            "child did not report runtime lock acquisition",
        )
        self.assertEqual(parent_connection.recv()[0], "locked")
        return process, parent_connection

    def _protocol_guard(
        self,
        plane: str,
        *,
        shared: bool,
        nonblocking: bool,
    ):
        if plane == "runtime_admission":
            return runtime_state.runtime_state_lock(
                self.config,
                shared=shared,
                nonblocking=nonblocking,
            )
        if plane == "task_state":
            return runtime_state.canonical_task_state_lock_file(
                self.root / "ai-status.json",
                shared=shared,
                nonblocking=nonblocking,
            )
        if plane == "activity_audit":
            return runtime_state.activity_audit_lock_file(
                self.root / "ai-activity-log.jsonl",
                shared=shared,
                nonblocking=nonblocking,
            )
        raise ValueError(f"unknown test lock plane: {plane}")

    def _start_protocol_lock_holder(
        self,
        plane: str,
        *,
        shared: bool,
    ) -> tuple[multiprocessing.Process, object]:
        context = multiprocessing.get_context("fork")
        parent_connection, child_connection = context.Pipe()
        process = context.Process(
            target=_hold_protocol_lock,
            args=(
                plane,
                self.config,
                str(self.root / "ai-status.json"),
                str(self.root / "ai-activity-log.jsonl"),
                child_connection,
                shared,
            ),
        )
        process.start()
        child_connection.close()
        try:
            self.assertTrue(
                parent_connection.poll(5),
                f"child did not acquire the {plane} lock",
            )
            self.assertEqual(
                parent_connection.recv()[:3],
                ("locked", plane, "shared" if shared else "exclusive"),
            )
        except BaseException:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            parent_connection.close()
            raise
        return process, parent_connection

    def test_admission_decision_has_exact_keys_source_order_and_digests(self) -> None:
        bodies = self._write_valid_sources()
        source_sha256 = {
            source_id: hashlib.sha256(bodies[source_id]).hexdigest()
            for source_id in (
                "runtime_state",
                "event_queue",
                "approval_queue",
            )
        }

        with runtime_state.tasks_runtime_admission_guard(
            self.config,
            ["TASK-B", "TASK-A"],
            strict=True,
            shared=False,
            nonblocking=True,
        ) as decision:
            self.assertEqual(
                list(decision),
                [
                    "schema_version",
                    "protocol_id",
                    "strict",
                    "lock_mode",
                    "task_ids",
                    "source_sha256",
                    "conflicts",
                    "allowed",
                    "reason_id",
                    "snapshot_sha256",
                ],
            )
            self.assertEqual(
                decision,
                {
                    "schema_version": 1,
                    "protocol_id": "pantheon-runtime-task-audit-lock-v1",
                    "strict": True,
                    "lock_mode": "exclusive",
                    "task_ids": ["TASK-B", "TASK-A"],
                    "source_sha256": source_sha256,
                    "conflicts": [],
                    "allowed": True,
                    "reason_id": "clear",
                    "snapshot_sha256": self._canonical_sha256(source_sha256),
                },
            )
            self.assertEqual(
                list(decision["source_sha256"]),
                ["runtime_state", "event_queue", "approval_queue"],
            )

    def test_queued_event_joins_runtime_queue_record_for_conflict_status(self) -> None:
        self._write_valid_sources(
            runtime={
                "version": 2,
                "workers": {},
                "queue": {
                    "events": {
                        "evt-target": {"status": "queued"},
                    }
                },
            },
            events=[
                {
                    "event_id": "evt-target",
                    "task_id": "TASK-A",
                }
            ],
        )

        with runtime_state.tasks_runtime_admission_guard(
            self.config,
            ["TASK-A"],
            strict=True,
            shared=True,
            nonblocking=True,
        ) as decision:
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["reason_id"], "target_has_runtime_admission")
            self.assertEqual(
                decision["conflicts"],
                [
                    {
                        "source_id": "event_queue",
                        "task_id": "TASK-A",
                        "status": "queued",
                        "record_id": "evt-target",
                    }
                ],
            )

    def test_invalid_runtime_sources_fail_closed(self) -> None:
        def missing_source() -> None:
            self.state_path.unlink()

        def empty_source() -> None:
            self.event_queue_path.write_bytes(b"")

        def malformed_source() -> None:
            self.approval_queue_path.write_bytes(b'{"version":2,"pending":[')

        def duplicate_key_source() -> None:
            self.state_path.write_bytes(
                b'{"version":2,"workers":{},"workers":{},"queue":{"events":{}}}\n'
            )

        cases = {
            "missing": missing_source,
            "empty": empty_source,
            "malformed": malformed_source,
            "duplicate-key": duplicate_key_source,
        }
        for label, mutate in cases.items():
            with self.subTest(source=label):
                self._write_valid_sources()
                mutate()
                before = {
                    path: path.read_bytes() if path.exists() else None
                    for path in (
                        self.state_path,
                        self.event_queue_path,
                        self.approval_queue_path,
                    )
                }
                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    ["TASK-A"],
                    strict=True,
                    shared=False,
                    nonblocking=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["reason_id"], "runtime_source_invalid")
                    self.assertEqual(decision["conflicts"], [])
                    self.assertEqual(
                        list(decision["source_sha256"]),
                        ["runtime_state", "event_queue", "approval_queue"],
                    )
                self.assertEqual(
                    {
                        path: path.read_bytes() if path.exists() else None
                        for path in before
                    },
                    before,
                    "failed admission mutated a canonical runtime source",
                )

    def test_runtime_source_schema_and_unreadable_matrix_fails_closed(self) -> None:
        mutations = {
            "runtime-older-version": lambda: self._write_valid_sources(
                runtime={"version": 1, "workers": {}, "queue": {"events": {}}}
            ),
            "runtime-newer-version": lambda: self._write_valid_sources(
                runtime={"version": 3, "workers": {}, "queue": {"events": {}}}
            ),
            "approval-older-version": lambda: self._write_valid_sources(
                approvals={"version": 1, "pending": [], "history": []}
            ),
            "approval-newer-version": lambda: self._write_valid_sources(
                approvals={"version": 3, "pending": [], "history": []}
            ),
            "duplicate-event-id": lambda: self._write_valid_sources(
                events=[
                    {"event_id": "evt-duplicate", "task_id": "TASK-A"},
                    {"event_id": "evt-duplicate", "task_id": "TASK-B"},
                ]
            ),
            "blank-event-task": lambda: self._write_valid_sources(
                events=[{"event_id": "evt-blank", "task_id": ""}]
            ),
            "blank-worker-task": lambda: self._write_valid_sources(
                runtime={
                    "version": 2,
                    "workers": {"run-blank": {"task_id": "", "status": "running"}},
                    "queue": {"events": {}},
                }
            ),
            "blank-approval-task": lambda: self._write_valid_sources(
                approvals={
                    "version": 2,
                    "pending": [{"approval_id": "approval-blank", "task_id": ""}],
                    "history": [],
                }
            ),
        }
        for label, prepare in mutations.items():
            with self.subTest(source=label):
                prepare()
                before = {
                    path: path.read_bytes()
                    for path in (
                        self.state_path,
                        self.event_queue_path,
                        self.approval_queue_path,
                    )
                }
                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    ["TASK-A"],
                    strict=True,
                    shared=False,
                    nonblocking=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["reason_id"], "runtime_source_invalid")
                    self.assertEqual(decision["conflicts"], [])
                self.assertEqual(
                    {path: path.read_bytes() for path in before},
                    before,
                )

        self._write_valid_sources()
        real_read = runtime_state._read_canonical_runtime_source

        def unreadable(path: Path, *, source_id: str) -> bytes:
            if source_id == "event_queue":
                raise PermissionError("injected unreadable source")
            return real_read(path, source_id=source_id)

        with mock.patch.object(
            runtime_state,
            "_read_canonical_runtime_source",
            side_effect=unreadable,
        ):
            with runtime_state.tasks_runtime_admission_guard(
                self.config,
                ["TASK-A"],
                strict=True,
                shared=False,
                nonblocking=True,
            ) as decision:
                self.assertFalse(decision["allowed"])
                self.assertEqual(decision["reason_id"], "runtime_source_invalid")
                self.assertEqual(
                    decision["source_sha256"]["event_queue"],
                    hashlib.sha256(b"").hexdigest(),
                )

    def test_every_runtime_conflict_status_blocks_with_exact_reason(self) -> None:
        for status in sorted(runtime_state.RUNTIME_ADMISSION_CONFLICT_STATUSES):
            with self.subTest(status=status):
                self._write_valid_sources(
                    runtime={
                        "version": 2,
                        "workers": {
                            "run-target": {
                                "run_id": "run-target",
                                "task_id": "TASK-A",
                                "status": status,
                            }
                        },
                        "queue": {"events": {}},
                    }
                )
                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    ["TASK-A"],
                    strict=True,
                    shared=True,
                    nonblocking=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(
                        decision["reason_id"],
                        "target_has_runtime_admission",
                    )
                    self.assertEqual(
                        decision["conflicts"],
                        [
                            {
                                "source_id": "runtime_state",
                                "task_id": "TASK-A",
                                "status": status,
                                "record_id": "run-target",
                            }
                        ],
                    )

    def test_runtime_source_leaf_symlinks_fail_closed_without_reading_targets(self) -> None:
        source_paths = {
            "runtime_state": self.state_path,
            "event_queue": self.event_queue_path,
            "approval_queue": self.approval_queue_path,
        }
        for source_id, path in source_paths.items():
            with self.subTest(source_id=source_id):
                self._write_valid_sources()
                external = self.root / f"external-{source_id}.data"
                external_body = (
                    f"must-not-be-read-or-written:{source_id}\n".encode()
                )
                external.write_bytes(external_body)
                path.unlink()
                path.symlink_to(external)

                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    ["TASK-A"],
                    strict=True,
                    shared=False,
                    nonblocking=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["reason_id"], "runtime_source_invalid")
                    self.assertEqual(decision["conflicts"], [])
                    self.assertEqual(
                        decision["source_sha256"][source_id],
                        hashlib.sha256(b"").hexdigest(),
                    )

                self.assertEqual(external.read_bytes(), external_body)
                self.assertTrue(path.is_symlink())
                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"canonical {source_id} data leaf cannot be a symlink",
                ):
                    with runtime_state.runtime_state_lock(
                        self.config,
                        shared=False,
                        nonblocking=True,
                    ):
                        self.fail(
                            "ordinary runtime writer followed a data-leaf symlink"
                        )
                path.unlink()

    def test_nonregular_runtime_source_leaves_fail_closed(self) -> None:
        source_paths = {
            "runtime_state": self.state_path,
            "event_queue": self.event_queue_path,
            "approval_queue": self.approval_queue_path,
        }
        for source_id, path in source_paths.items():
            with self.subTest(source_id=source_id):
                self._write_valid_sources()
                path.unlink()
                path.mkdir()

                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    ["TASK-A"],
                    strict=True,
                    shared=False,
                    nonblocking=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["reason_id"], "runtime_source_invalid")
                    self.assertEqual(decision["conflicts"], [])

                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"canonical {source_id} data leaf must be a regular file",
                ):
                    runtime_state.runtime_admission_lock_path(self.config)
                path.rmdir()

    def test_runtime_sources_and_all_lock_planes_share_canonical_status_root(
        self,
    ) -> None:
        status_root = self.root / "canonical-status"
        runtime_root = status_root / ".orchestrator"
        runtime_root.mkdir(parents=True)
        status_path = status_root / "ai-status.json"
        activity_path = status_root / "ai-activity-log.jsonl"
        config = {
            "paths": {
                "status_file": str(status_path),
                "activity_log": str(activity_path),
                "state_file": str(runtime_root / "state.json"),
                "event_queue": str(runtime_root / "event-queue.jsonl"),
                "approval_queue": str(runtime_root / "approval-queue.json"),
            }
        }

        self.assertEqual(
            runtime_state.runtime_admission_lock_path(config),
            runtime_root / "runtime-admission.lock",
        )
        self.assertEqual(
            common.canonical_task_state_lock_path(status_path),
            runtime_root / "task-state.lock",
        )
        self.assertEqual(
            common.activity_audit_lock_path(activity_path),
            runtime_root / "activity-audit.lock",
        )

    def test_split_runtime_or_status_roots_are_rejected_before_lock_creation(
        self,
    ) -> None:
        self._write_valid_sources()
        canonical_status_root = self.root / "canonical-status"
        canonical_status_root.mkdir()
        split_root = self.root / "split-runtime"
        split_root.mkdir()
        cases = {
            "runtime-sources": {
                "paths": {
                    **self.config["paths"],
                    "event_queue": str(split_root / "event-queue.jsonl"),
                }
            },
            "status-vs-runtime": {
                "paths": {
                    **self.config["paths"],
                    "status_file": str(canonical_status_root / "ai-status.json"),
                }
            },
            "activity-vs-status": {
                "paths": {
                    **self.config["paths"],
                    "status_file": str(self.root / "ai-status.json"),
                    "activity_log": str(split_root / "ai-activity-log.jsonl"),
                }
            },
        }
        for label, config in cases.items():
            with self.subTest(layout=label):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "split roots|does not belong to the status root|does not match the status root",
                ):
                    with runtime_state.runtime_state_lock(
                        config,
                        shared=False,
                        nonblocking=True,
                    ):
                        self.fail("split-root runtime configuration acquired a lock")
        self.assertFalse(
            (
                canonical_status_root
                / ".orchestrator"
                / "runtime-admission.lock"
            ).exists()
        )
        self.assertFalse((split_root / ".orchestrator").exists())

    def test_runtime_lock_root_symlink_is_rejected_without_following_target(
        self,
    ) -> None:
        self._write_valid_sources()
        external = self.root / "external-lock-root"
        external.mkdir()
        lock_root = self.root / ".orchestrator"
        lock_root.symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(
            RuntimeError,
            "canonical runtime lock root cannot be a symlink",
        ):
            with runtime_state.runtime_state_lock(
                self.config,
                shared=False,
                nonblocking=True,
            ):
                self.fail("runtime lock followed an external lock-root symlink")

        self.assertEqual(list(external.iterdir()), [])

    def test_runtime_whole_file_writers_fail_on_readback_mismatch(self) -> None:
        cases = (
            (
                "runtime_state",
                lambda: runtime_state.save_runtime_state(
                    self.config,
                    {"version": 2, "workers": {}, "queue": {"events": {}}},
                ),
            ),
            (
                "event_queue",
                lambda: runtime_state.replace_event_queue(self.config, []),
            ),
            (
                "approval_queue",
                lambda: runtime_state.save_approval_state(
                    self.config,
                    {"version": 2, "pending": [], "history": []},
                ),
            ),
        )
        for source_id, writer in cases:
            with self.subTest(source_id=source_id), mock.patch.object(
                runtime_state,
                "_read_canonical_runtime_source",
                return_value=b"corrupt",
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"canonical {source_id} readback mismatch",
                ):
                    writer()

    def test_input_and_strict_reason_ids_are_stable(self) -> None:
        self._write_valid_sources()
        cases = (
            ([], True, "task_ids_empty"),
            (["TASK-A", "TASK-A"], True, "task_ids_duplicate"),
            ([""], True, "task_ids_invalid"),
            (["TASK-A"], False, "strict_required"),
        )
        for task_ids, strict, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                with runtime_state.tasks_runtime_admission_guard(
                    self.config,
                    task_ids,
                    strict=strict,
                    shared=False,
                    nonblocking=True,
                ) as decision:
                    self.assertFalse(decision["allowed"])
                    self.assertEqual(decision["reason_id"], expected_reason)

    def test_runtime_sidecar_inode_survives_canonical_replace(self) -> None:
        self._write_valid_sources()
        original_state_inode = self.state_path.stat().st_ino
        lock_path = runtime_state.runtime_admission_lock_path(self.config)

        with runtime_state.runtime_state_lock(
            self.config,
            shared=False,
            nonblocking=True,
        ) as lock_handle:
            original_lock_inode = lock_path.stat().st_ino
            runtime_state.write_json(
                self.state_path,
                {
                    "version": 2,
                    "workers": {},
                    "queue": {"events": {}},
                    "replacement": True,
                },
            )

            self.assertNotEqual(self.state_path.stat().st_ino, original_state_inode)
            self.assertEqual(lock_path.stat().st_ino, original_lock_inode)
            self.assertEqual(os.fstat(lock_handle.fileno()).st_ino, original_lock_inode)

        self.assertEqual(lock_path.stat().st_ino, original_lock_inode)

    def test_nonblocking_multiprocess_contention_and_normal_release(self) -> None:
        process, connection = self._start_lock_holder()

        with self.assertRaises(BlockingIOError):
            with runtime_state.runtime_state_lock(
                self.config,
                shared=False,
                nonblocking=True,
            ):
                self.fail("contender acquired a sidecar held by another process")

        connection.send("release")
        self.assertTrue(connection.poll(5), "child did not report normal release")
        self.assertEqual(connection.recv()[0], "released")
        process.join(timeout=5)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)
        with runtime_state.runtime_state_lock(
            self.config,
            shared=False,
            nonblocking=True,
        ):
            pass

    def test_sigkill_releases_runtime_sidecar(self) -> None:
        process, _connection = self._start_lock_holder()

        os.kill(process.pid, signal.SIGKILL)
        process.join(timeout=5)

        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        with runtime_state.runtime_state_lock(
            self.config,
            shared=False,
            nonblocking=True,
        ):
            pass

    def test_shared_readers_are_compatible_across_processes_on_all_planes(self) -> None:
        for plane in ("runtime_admission", "task_state", "activity_audit"):
            with self.subTest(plane=plane):
                process, connection = self._start_protocol_lock_holder(
                    plane,
                    shared=True,
                )
                try:
                    with self._protocol_guard(
                        plane,
                        shared=True,
                        nonblocking=True,
                    ):
                        pass
                    connection.send("release")
                    self.assertTrue(connection.poll(5))
                    self.assertEqual(
                        connection.recv()[:3],
                        ("released", plane, "shared"),
                    )
                    process.join(timeout=5)
                    self.assertFalse(process.is_alive())
                    self.assertEqual(process.exitcode, 0)
                finally:
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    connection.close()

    def test_shared_and_exclusive_contend_across_processes_on_all_planes(self) -> None:
        modes = (
            (True, False, "shared_holder_exclusive_contender"),
            (False, True, "exclusive_holder_shared_contender"),
        )
        for plane in ("runtime_admission", "task_state", "activity_audit"):
            for holder_shared, contender_shared, direction in modes:
                with self.subTest(plane=plane, direction=direction):
                    process, connection = self._start_protocol_lock_holder(
                        plane,
                        shared=holder_shared,
                    )
                    try:
                        with self.assertRaises(BlockingIOError):
                            with self._protocol_guard(
                                plane,
                                shared=contender_shared,
                                nonblocking=True,
                            ):
                                self.fail(
                                    f"{direction} bypassed the {plane} sidecar"
                                )
                        connection.send("release")
                        self.assertTrue(connection.poll(5))
                        self.assertEqual(connection.recv()[0], "released")
                        process.join(timeout=5)
                        self.assertFalse(process.is_alive())
                        self.assertEqual(process.exitcode, 0)
                        with self._protocol_guard(
                            plane,
                            shared=contender_shared,
                            nonblocking=True,
                        ):
                            pass
                    finally:
                        if process.is_alive():
                            process.kill()
                            process.join(timeout=5)
                        connection.close()

    def test_sigkill_releases_task_and_audit_sidecars(self) -> None:
        for plane in ("task_state", "activity_audit"):
            with self.subTest(plane=plane):
                process, connection = self._start_protocol_lock_holder(
                    plane,
                    shared=False,
                )
                try:
                    os.kill(process.pid, signal.SIGKILL)
                    process.join(timeout=5)

                    self.assertFalse(process.is_alive())
                    self.assertEqual(process.exitcode, -signal.SIGKILL)
                    with self._protocol_guard(
                        plane,
                        shared=False,
                        nonblocking=True,
                    ):
                        pass
                finally:
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    connection.close()

    def test_audit_rotation_serializes_a_waiting_writer_across_processes(self) -> None:
        context = multiprocessing.get_context("fork")
        activity_file = self.root / "ai-activity-log.jsonl"
        trace_file = self.root / "waiting-writer-lock-trace.txt"
        original = [
            {"event_id": f"original-{index}", "payload": "x" * 128}
            for index in range(3)
        ]
        activity_file.write_text(
            "".join(json.dumps(row) + "\n" for row in original),
            encoding="utf-8",
        )
        holder_parent, holder_child = context.Pipe()
        holder = context.Process(
            target=_hold_audit_lock_and_rotate,
            args=(str(activity_file), holder_child),
        )
        writer_parent, writer_child = context.Pipe()
        writer = context.Process(
            target=_write_rotating_audit_entry,
            args=(str(activity_file), str(trace_file), writer_child),
        )
        holder.start()
        holder_child.close()
        try:
            self.assertTrue(holder_parent.poll(5))
            self.assertEqual(holder_parent.recv()[0], "locked")

            writer.start()
            writer_child.close()
            self.assertTrue(writer_parent.poll(5))
            started = writer_parent.recv()
            self.assertEqual(started[0], "started")
            writer_pid = started[1]
            request_line = f"request:activity_audit:{writer_pid}:"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                trace = (
                    trace_file.read_text(encoding="utf-8")
                    if trace_file.exists()
                    else ""
                )
                if request_line in trace:
                    break
                time.sleep(0.01)
            else:
                self.fail("waiting writer did not request the audit sidecar")
            self.assertFalse(
                writer_parent.poll(0.2),
                "audit writer completed while another process held audit EX",
            )

            holder_parent.send("rotate")
            self.assertTrue(holder_parent.poll(5))
            rotated = holder_parent.recv()
            self.assertEqual(rotated[0], "rotated")
            self.assertTrue(Path(rotated[1]).is_file())
            self.assertFalse(
                writer_parent.poll(0.2),
                "audit writer crossed the lock during rotation",
            )

            holder_parent.send("release")
            self.assertTrue(holder_parent.poll(5))
            self.assertEqual(holder_parent.recv()[0], "released")
            holder.join(timeout=5)
            self.assertFalse(holder.is_alive())
            self.assertEqual(holder.exitcode, 0)

            self.assertTrue(writer_parent.poll(5))
            self.assertEqual(writer_parent.recv()[0], "written")
            writer.join(timeout=5)
            self.assertFalse(writer.is_alive())
            self.assertEqual(writer.exitcode, 0)

            rows: list[dict[str, object]] = []
            with runtime_state.activity_audit_lock_file(
                activity_file,
                shared=True,
                nonblocking=True,
            ):
                for source in common.activity_audit_source_paths_unlocked(
                    activity_file
                ):
                    if source.suffix == ".gz":
                        with gzip.open(source, "rt", encoding="utf-8") as handle:
                            text = handle.read()
                    else:
                        text = source.read_text(encoding="utf-8")
                    rows.extend(
                        json.loads(line) for line in text.splitlines() if line
                    )
            counts: dict[str, int] = {}
            for row in rows:
                if "record_type" in row:
                    continue
                event_id = str(row.get("event_id") or "")
                counts[event_id] = counts.get(event_id, 0) + 1
            self.assertEqual(
                counts,
                {
                    "original-0": 1,
                    "original-1": 1,
                    "original-2": 1,
                    "waiting-writer": 1,
                },
            )
            self.assertFalse(
                common.activity_rotation_intent_path(activity_file).exists()
            )
        finally:
            if holder.is_alive():
                holder.kill()
                holder.join(timeout=5)
            if writer.is_alive():
                writer.kill()
                writer.join(timeout=5)
            holder_parent.close()
            writer_parent.close()

    def test_task_and_audit_sidecars_contend_across_processes(self) -> None:
        context = multiprocessing.get_context("fork")
        status_file = str(self.root / "ai-status.json")
        activity_file = str(self.root / "ai-activity-log.jsonl")
        for plane in ("task_state", "activity_audit"):
            with self.subTest(plane=plane):
                parent, child = context.Pipe()
                process = context.Process(
                    target=_hold_protocol_lock,
                    args=(plane, self.config, status_file, activity_file, child),
                )
                process.start()
                child.close()
                try:
                    self.assertTrue(parent.poll(5))
                    self.assertEqual(parent.recv()[:2], ("locked", plane))
                    guard = (
                        runtime_state.canonical_task_state_lock_file(
                            status_file,
                            shared=False,
                            nonblocking=True,
                        )
                        if plane == "task_state"
                        else runtime_state.activity_audit_lock_file(
                            activity_file,
                            shared=False,
                            nonblocking=True,
                        )
                    )
                    with self.assertRaises(BlockingIOError):
                        with guard:
                            self.fail("contender crossed a held stable sidecar")
                    parent.send("release")
                    self.assertTrue(parent.poll(5))
                    self.assertEqual(parent.recv()[:2], ("released", plane))
                    process.join(timeout=5)
                    self.assertEqual(process.exitcode, 0)
                finally:
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    parent.close()

    def test_trace_records_global_three_plane_acquisition_order(self) -> None:
        self._write_valid_sources()
        trace_path = self.root / "lock-trace.txt"
        with mock.patch.dict(
            os.environ,
            {"PANTHEON_RUNTIME_LOCK_TRACE": str(trace_path)},
        ):
            with runtime_state.tasks_runtime_admission_guard(
                self.config,
                ["TASK-A"],
                strict=True,
                shared=False,
                nonblocking=True,
            ) as decision:
                self.assertTrue(decision["allowed"])
                with runtime_state.canonical_task_state_lock_file(
                    self.root / "ai-status.json",
                    shared=False,
                    nonblocking=True,
                ):
                    with runtime_state.activity_audit_lock_file(
                        self.root / "ai-activity-log.jsonl",
                        shared=False,
                        nonblocking=True,
                    ):
                        pass
        acquired = [
            line.split(":", 3)[1]
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("acquire:")
        ]
        self.assertEqual(
            acquired,
            ["runtime_admission", "task_state", "activity_audit"],
        )

    def test_reverse_lock_order_is_rejected_before_kernel_acquisition(self) -> None:
        status_path = self.root / "ai-status.json"
        with runtime_state.canonical_task_state_lock_file(
            status_path,
            shared=False,
            nonblocking=True,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"canonical lock order violation: cannot acquire "
                r"runtime_admission after task_state",
            ):
                with runtime_state.runtime_state_lock(
                    self.config,
                    shared=False,
                    nonblocking=True,
                ):
                    self.fail("reverse-order runtime lock was acquired")


class RuntimeCapabilityVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name) / "repo"
        self.root.mkdir()

    @staticmethod
    def _write_json(path: Path, payload: object) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        path.write_bytes(body)
        return body

    def _signed_fixture(self) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object], str]:
        writers: dict[str, str] = {}
        for relative in runtime_state.RUNTIME_LOCK_REQUIRED_WRITER_PATHS:
            body = f"fixture writer: {relative}\n".encode("utf-8")
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            writers[relative] = hashlib.sha256(body).hexdigest()
        registry = {
            "schema_version": 1,
            "protocol_id": runtime_state.RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
            "transaction_scope": "complete_read_validate_mutate_replace",
            "direct_canonical_writes_forbidden": True,
            "writers": writers,
        }
        registry_path = self.root / ".orchestrator/runtime-task-audit-writer-registry.json"
        registry_body = self._write_json(registry_path, registry)
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        evidence: dict[str, object] = {
            "schema_version": 1,
            "task_id": "LOOP-PROD-RUNTIME-BOOT-001",
            "task_contract_sha256": "04f382e320292e11df3b4668ec4383819b9c9abadcc48f3b9150a7abcb65141e",
            "conclusion": "passed",
            "worker_runtime_identity": "Codex2",
            "reviewer_runtime_identity": "Codex",
            "checks_sha256": "1" * 64,
            "verdict_id": "runtime-lock-bootstrap-review-1",
            "verifier_capability_sha256": writers[".orchestrator/runtime_state.py"],
            "signature_algorithm": "ed25519",
            "key_id": "ops-runtime-lock-review-1",
            "policy_version": "runtime-lock-policy-1",
            "signature": "",
            "revocation_checked_at": "2026-07-14T00:00:00Z",
            "ledger_entry_id": "runtime-lock-ledger-1",
        }
        evidence_path = self.root / "docs/deployment/evidence/loop-product-level/LOOP-PROD-RUNTIME-BOOT-001/bootstrap-completion.json"
        manifest: dict[str, object] = {
            "schema_version": 1,
            "protocol_id": runtime_state.RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
            "module_path": ".orchestrator/runtime_state.py",
            "lock_order": ["runtime_admission", "task_state", "activity_audit"],
            "stable_lock_paths": [
                ".orchestrator/runtime-admission.lock",
                ".orchestrator/task-state.lock",
                ".orchestrator/activity-audit.lock",
            ],
            "shared_read_supported": True,
            "api": list(runtime_state.RUNTIME_LOCK_REQUIRED_API),
            "writers": writers,
            "writer_registry_path": str(registry_path.relative_to(self.root)),
            "writer_registry_sha256": hashlib.sha256(registry_body).hexdigest(),
            "bootstrap_task_id": "LOOP-PROD-RUNTIME-BOOT-001",
            "bootstrap_task_contract_sha256": evidence["task_contract_sha256"],
            "bootstrap_completion_evidence_path": str(evidence_path.relative_to(self.root)),
            "bootstrap_completion_evidence_sha256": "0" * 64,
            "merged_commit_sha": "2" * 40,
        }
        evidence["signature"] = base64.b64encode(
            private_key.sign(
                runtime_state.runtime_capability_signature_payload(
                    manifest,
                    evidence,
                )
            )
        ).decode("ascii")
        evidence_body = self._write_json(evidence_path, evidence)
        manifest["bootstrap_completion_evidence_sha256"] = hashlib.sha256(
            evidence_body
        ).hexdigest()
        manifest_sha256 = "3" * 64
        policy = {
            "schema_version": 1,
            "protocol_id": runtime_state.RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID,
            "policy_version": evidence["policy_version"],
            "key_id": evidence["key_id"],
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "revoked_key_ids": [],
            "ledger_entries": [
                {
                    "ledger_entry_id": evidence["ledger_entry_id"],
                    "verdict_id": evidence["verdict_id"],
                    "task_id": evidence["task_id"],
                    "reviewer_runtime_identity": evidence["reviewer_runtime_identity"],
                    "merged_commit_sha": manifest["merged_commit_sha"],
                    "manifest_sha256": manifest_sha256,
                    "writer_registry_sha256": manifest["writer_registry_sha256"],
                    "completion_evidence_sha256": manifest["bootstrap_completion_evidence_sha256"],
                    "revocation_checked_at": evidence["revocation_checked_at"],
                    "status": "accepted",
                }
            ],
        }
        return manifest, registry, evidence, policy, manifest_sha256

    def test_valid_signature_and_protected_ledger_are_required(self) -> None:
        manifest, registry, evidence, policy, manifest_sha256 = self._signed_fixture()
        with (
            mock.patch.object(
                runtime_state,
                "_protected_verifier_policy",
                return_value=policy,
            ),
            mock.patch.object(
                runtime_state,
                "_protected_checks_evidence",
                return_value={},
            ),
        ):
            decision = runtime_state.verify_runtime_lock_capability(
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                writer_registry=registry,
                completion_evidence=evidence,
                repository_root=self.root,
            )
            self.assertTrue(decision["allowed"])
            self.assertEqual(decision["reason_id"], "verified")

            evidence["signature"] = base64.b64encode(b"invalid").decode("ascii")
            rejected = runtime_state.verify_runtime_lock_capability(
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                writer_registry=registry,
                completion_evidence=evidence,
                repository_root=self.root,
            )
            self.assertFalse(rejected["allowed"])
            self.assertEqual(rejected["reason_id"], "protected_evidence_invalid")

    def test_worker_owned_policy_is_not_a_protected_trust_anchor(self) -> None:
        policy_path = Path(self.tmpdir.name) / "worker-policy.json"
        self._write_json(policy_path, {})
        with mock.patch.dict(
            os.environ,
            {"PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY": str(policy_path)},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "protected verifier policy parent is unsafe|permissions are unsafe",
            ):
                runtime_state._protected_verifier_policy(self.root)


class RuntimeWriterInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / ".orchestrator").mkdir()
        (self.root / "scripts").mkdir()

    def test_seeded_unregistered_direct_writer_is_rejected(self) -> None:
        unsafe = self.root / "scripts/unsafe_writer.py"
        unsafe.write_text(
            "from pathlib import Path\n"
            "STATUS = Path(__file__).parent / 'ai-status.json'\n"
            "def mutate():\n"
            "    STATUS.write_text('{}\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        inventory = runtime_state.runtime_lock_source_inventory(self.root)
        self.assertEqual(
            inventory["unregistered_direct_writers"],
            [
                {
                    "path": "scripts/unsafe_writer.py",
                    "line": 4,
                    "sink": "path.write_text",
                    "reason_id": "unregistered_direct_canonical_write",
                }
            ],
        )

    def test_seeded_os_replace_module_call_direct_writer_is_rejected(self) -> None:
        unsafe = self.root / "scripts/unsafe_os_replace.py"
        unsafe.write_text(
            "import os\n"
            "import tempfile\n"
            "from pathlib import Path\n"
            "STATUS_FILE = Path(__file__).parent / 'ai-status.json'\n"
            "def mutate():\n"
            "    with tempfile.NamedTemporaryFile('w', delete=False) as handle:\n"
            "        handle.write('{}\\n')\n"
            "        temp_path = Path(handle.name)\n"
            "    os.replace(temp_path, STATUS_FILE)\n",
            encoding="utf-8",
        )
        inventory = runtime_state.runtime_lock_source_inventory(self.root)
        self.assertEqual(
            inventory["unregistered_direct_writers"],
            [
                {
                    "path": "scripts/unsafe_os_replace.py",
                    "line": 9,
                    "sink": "replace",
                    "reason_id": "unregistered_direct_canonical_write",
                }
            ],
        )

    def test_writer_inside_canonical_task_state_lock_block_is_not_flagged(self) -> None:
        locked = self.root / "scripts/locked_writer.py"
        locked.write_text(
            "import os\n"
            "import tempfile\n"
            "from pathlib import Path\n"
            "from runtime_state import canonical_task_state_lock_file\n"
            "STATUS_FILE = Path(__file__).parent / 'ai-status.json'\n"
            "def mutate():\n"
            "    with canonical_task_state_lock_file(STATUS_FILE):\n"
            "        with tempfile.NamedTemporaryFile('w', delete=False) as handle:\n"
            "            handle.write('{}\\n')\n"
            "            temp_path = Path(handle.name)\n"
            "        os.replace(temp_path, STATUS_FILE)\n",
            encoding="utf-8",
        )
        inventory = runtime_state.runtime_lock_source_inventory(self.root)
        self.assertEqual(inventory["unregistered_direct_writers"], [])

    def test_guarded_isolated_fixture_writer_is_not_canonical(self) -> None:
        guarded = self.root / "scripts/guarded_writer.py"
        guarded.write_text(
            "from pathlib import Path\n"
            "from canonical_writer_guard import assert_isolated_legacy_write_target\n"
            "STATUS = Path(__file__).parent / 'ai-status.json'\n"
            "def mutate():\n"
            "    assert_isolated_legacy_write_target(STATUS, tool='fixture')\n"
            "    STATUS.write_text('{}\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        inventory = runtime_state.runtime_lock_source_inventory(self.root)
        self.assertEqual(inventory["unregistered_direct_writers"], [])

    def test_real_repository_has_no_unregistered_canonical_writer(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        inventory = runtime_state.runtime_lock_source_inventory(repository_root)
        self.assertGreater(len(inventory["files"]), 100)
        self.assertEqual(inventory["unregistered_direct_writers"], [])
