#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import tempfile
import unittest
from pathlib import Path

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

    def test_load_runtime_state_adds_chair_rotation_defaults(self) -> None:
        self._write_json(self.root / "state.json", {"workers": {}, "queue": {"events": {}}})
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["chair_rotation"]["current_index"], 0)
        self.assertIsNone(state["chair_rotation"]["last_chair_agent"])
        self.assertIn("chair_review", state["supervisor"]["mode_occupancy"])

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
