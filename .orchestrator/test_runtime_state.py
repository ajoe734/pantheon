#!/usr/bin/env python3
from __future__ import annotations

import base64
import gzip
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
            }
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_load_runtime_state_drops_suspended_worker_without_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "version": 2,
                "workers": {
                    "claude-stale": {
                        "run_id": "claude-stale",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-missing",
                    }
                },
                "queue": {"version": 2, "events": {}},
            },
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["workers"], {})

    def test_load_runtime_state_keeps_suspended_worker_with_live_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "version": 2,
                "workers": {
                    "claude-live": {
                        "run_id": "claude-live",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-live",
                    }
                },
                "queue": {
                    "version": 2,
                    "events": {
                        "evt-live": {
                            "intent": {
                                "event_id": "evt-live",
                                "task_id": "EXEC-FRONT-TW03-001",
                            },
                            "status": "suspended_approval",
                        }
                    },
                },
            },
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertIn("claude-live", state["workers"])

    def test_load_runtime_state_keeps_only_v2_schema_fields(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "version": 2,
                "workers": {},
                "queue": {"version": 2, "events": {}},
                "tasks": {
                    "STALE-CACHE-ROW": {
                        "status": "todo",
                        "owner": "Codex",
                    }
                },
                "unrecognized_control_plane": {"value": 1},
                "supervisor": {
                    "pid": 42,
                    "task_state_projection": {
                        "mode": "authoritative",
                        "ok": True,
                        "caught_up": True,
                    },
                    "last_cycle_metrics": {
                        "cycle_elapsed_seconds": 4.25,
                        "queue_to_start": {"max_seconds": 0.5},
                    },
                    "cycle_elapsed_seconds": 4.25,
                    "queue_to_start_latency_seconds": 0.5,
                    "unrecognized_field": "ignored",
                },
            },
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertNotIn("unrecognized_control_plane", state)
        self.assertNotIn("tasks", state)
        self.assertEqual(state["supervisor"]["pid"], 42)
        self.assertEqual(
            state["supervisor"]["task_state_projection"],
            {
                "mode": "authoritative",
                "ok": True,
                "caught_up": True,
            },
        )
        self.assertEqual(
            state["supervisor"]["last_cycle_metrics"]["cycle_elapsed_seconds"],
            4.25,
        )
        self.assertEqual(state["supervisor"]["cycle_elapsed_seconds"], 4.25)
        self.assertNotIn("queue_to_start", state["supervisor"]["last_cycle_metrics"])
        self.assertNotIn("queue_to_start_latency_seconds", state["supervisor"])
        self.assertNotIn("unrecognized_field", state["supervisor"])

    def test_load_runtime_state_preserves_watchdog_safe_mode(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "version": 2,
                "workers": {},
                "queue": {"version": 2, "events": {}},
                "watchdog": {
                    "safe_mode_until": "2026-05-18T14:30:00Z",
                    "safe_mode_reason": "stale_heartbeat",
                },
            },
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["watchdog"]["safe_mode_until"], "2026-05-18T14:30:00Z")
        self.assertEqual(state["watchdog"]["safe_mode_reason"], "stale_heartbeat")
        self.assertIn("last_safe_mode_observed_until", state["watchdog"])

    def test_runtime_phase_reservation_survives_save_and_reload(self) -> None:
        state = runtime_state.default_state()
        state["supervisor"]["runtime_phase_reservations"] = {
            "process_queue": {
                "token": "phase-process-queue-test",
                "reserved_at": "2026-08-13T03:05:38Z",
            }
        }

        runtime_state.save_runtime_state(self.config, state)
        reloaded = runtime_state.load_runtime_state(self.config)

        self.assertEqual(
            reloaded["supervisor"]["runtime_phase_reservations"],
            state["supervisor"]["runtime_phase_reservations"],
        )

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
                "version": 2,
                "workers": {},
                "queue": {"version": 2, "events": {}},
                "worker_worktree_cleanup": {"last_run": last_run},
            },
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["worker_worktree_cleanup"]["last_run"], last_run)

    def test_ordinary_v2_load_rejects_a_retired_cache_shape(self) -> None:
        self._write_json(
            self.root / "state.json",
            {"version": 1, "workers": [], "queue": {"events": {}}},
        )

        with self.assertRaises(runtime_state.RuntimeStateSchemaError):
            runtime_state.load_runtime_state(self.config)

    def test_ordinary_v2_load_preserves_active_worker_and_queue_lease(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "version": 2,
                "workers": {
                    "run-active": {
                        "run_id": "run-active",
                        "task_id": "TASK-V2",
                        "queue_event_id": "evt-v2",
                        "status": "running",
                    }
                },
                "queue": {
                    "version": 2,
                    "events": {
                        "evt-v2": {
                            "intent": {"event_id": "evt-v2", "task_id": "TASK-V2"},
                            "status": "started",
                        }
                    },
                },
            },
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertIn("run-active", state["workers"])
        self.assertEqual(state["queue"]["events"]["evt-v2"]["status"], "started")

    def test_ordinary_runtime_never_reads_the_retired_queue_file(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "version": 2,
                "workers": {},
                "queue": {
                    "version": 2,
                    "events": {
                        "evt-v2": {
                            "intent": {"event_id": "evt-v2", "task_id": "TASK-V2"},
                            "status": "queued",
                        }
                    },
                },
            },
        )
        retired = self.root / "event-queue.jsonl"
        retired.write_bytes(b"not-jsonl-and-not-runtime-authority\n")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(runtime_state.queue_events(state)[0]["event_id"], "evt-v2")

    def test_projection_reader_does_not_reverse_acquire_runtime_from_task_lock(self) -> None:
        self.config["paths"]["approval_queue"] = str(
            self.root / "approval-queue.json"
        )
        self._write_json(
            self.root / "state.json",
            {"version": 2, "workers": {}, "queue": {"version": 2, "events": {}}},
        )
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
        self.approval_queue_path = self.root / "approval-queue.json"
        self.config = {
            "paths": {
                "state_file": str(self.state_path),
                "approval_queue": str(self.approval_queue_path),
            }
        }

    def _write_valid_sources(
        self,
        *,
        runtime: dict[str, object] | None = None,
        approvals: dict[str, object] | None = None,
    ) -> dict[str, bytes]:
        runtime_payload = runtime or {
            "version": 2,
            "workers": {},
            "queue": {
                "version": 2,
                "events": {
                    "evt-unrelated": {
                        "intent": {
                            "event_id": "evt-unrelated",
                            "task_id": "TASK-UNRELATED",
                        },
                        "status": "completed",
                    },
                }
            },
        }
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
        self.approval_queue_path.write_bytes(
            (
                json.dumps(approval_payload, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
        return {
            "runtime_state": self.state_path.read_bytes(),
            "approval_queue": self.approval_queue_path.read_bytes(),
        }

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

    def test_runtime_source_leaf_symlinks_fail_closed_without_reading_targets(self) -> None:
        source_paths = {
            "runtime_state": self.state_path,
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
            "approval_queue": self.approval_queue_path,
        }
        for source_id, path in source_paths.items():
            with self.subTest(source_id=source_id):
                self._write_valid_sources()
                path.unlink()
                path.mkdir()

                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"canonical {source_id} data leaf must be a regular file",
                ):
                    with runtime_state.runtime_state_lock(
                        self.config,
                        shared=False,
                        nonblocking=True,
                    ):
                        self.fail("runtime lock accepted a nonregular data leaf")
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
                    "approval_queue": str(split_root / "approval-queue.json"),
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
            with runtime_state.runtime_state_lock(
                self.config,
                shared=False,
                nonblocking=True,
            ):
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
