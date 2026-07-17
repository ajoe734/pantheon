#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import status_file_guard


def board(updated_at: str, *, agents: int = 2) -> str:
    return json.dumps(
        {
            "updated_at": updated_at,
            "agents": [{"name": f"agent-{i}", "status": "idle"} for i in range(agents)],
        }
    )


def _hold_newer_status_lock(
    status_file: str,
    payload: bytes,
    ready,
    release,
) -> None:
    path = Path(status_file)
    with status_file_guard.canonical_task_state_lock_file(path):
        status_file_guard.restore_canonical_task_state_bytes(path, payload)
        ready.set()
        release.wait(10)


class StatusFileGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "docs-site").mkdir()
        (self.root / ".orchestrator" / "logs").mkdir(parents=True)
        self.log_path = self.root / ".orchestrator" / "logs" / "status-file-guard.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _guard(self, *, dry_run: bool = False) -> int:
        return status_file_guard.guard(
            self.root, dry_run=dry_run, verbose=False, log_path=self.log_path
        )

    def _restore_event(self) -> dict:
        payload = json.loads((self.root / "ai-status.json").read_text())
        outbox = payload["status_activity_outbox"]
        self.assertEqual(outbox["schema_version"], 1)
        self.assertEqual(
            outbox["transaction_id"],
            "ai-status-tx-"
            + status_file_guard._canonical_json_sha256(outbox["events"]),
        )
        return outbox["events"][-1]

    def test_healthy_live_file_is_left_untouched(self) -> None:
        live = self.root / "ai-status.json"
        live.write_text(board("2026-07-12T23:00:00Z"))
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:00:00Z"))

        self.assertEqual(self._guard(), 0)
        self.assertEqual(json.loads(live.read_text())["updated_at"], "2026-07-12T23:00:00Z")

    def test_wiped_live_file_is_restored_from_freshest_snapshot(self) -> None:
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:49:53Z"))
        (self.root / "ai-status.json.bak").write_text(board("2026-07-12T11:55:52Z"))

        self.assertEqual(self._guard(), 1)

        live = self.root / "ai-status.json"
        self.assertTrue(live.exists())
        event = self._restore_event()
        self.assertEqual(event["type"], "status_file_restored")
        self.assertEqual(event["source_updated_at"], "2026-07-12T21:49:53Z")
        self.assertEqual(event["source"], "docs-site/ai-status.json")
        self.assertEqual(live.stat().st_mode & 0o777, status_file_guard.LIVE_MODE)
        self.assertIn("RESTORED", self.log_path.read_text())

    def test_empty_live_file_is_quarantined_then_restored(self) -> None:
        live = self.root / "ai-status.json"
        live.write_text("")
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:49:53Z"))

        self.assertEqual(self._guard(), 1)

        self.assertEqual(
            self._restore_event()["source_updated_at"],
            "2026-07-12T21:49:53Z",
        )
        quarantined = list(self.root.glob("ai-status.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(), "")

    def test_corrupt_json_is_treated_as_unhealthy(self) -> None:
        live = self.root / "ai-status.json"
        live.write_text("{not json")
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:49:53Z"))

        self.assertEqual(self._guard(), 1)
        self.assertEqual(
            self._restore_event()["source_updated_at"],
            "2026-07-12T21:49:53Z",
        )

    def test_snapshot_without_agents_is_not_a_restore_source(self) -> None:
        (self.root / "docs-site" / "ai-status.json").write_text(json.dumps({"updated_at": "2026-07-12T23:00:00Z"}))
        (self.root / "ai-status.json.bak").write_text(board("2026-07-12T11:55:52Z"))

        self.assertEqual(self._guard(), 1)
        event = self._restore_event()
        self.assertEqual(event["source_updated_at"], "2026-07-12T11:55:52Z")
        self.assertEqual(event["source"], "ai-status.json.bak")

    def test_no_healthy_snapshot_reports_failure_without_writing(self) -> None:
        (self.root / "docs-site" / "ai-status.json").write_text("")

        self.assertEqual(self._guard(), 2)
        self.assertFalse((self.root / "ai-status.json").exists())
        self.assertIn("FAILED", self.log_path.read_text())

    def test_dry_run_reports_but_does_not_restore(self) -> None:
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:49:53Z"))

        self.assertEqual(self._guard(dry_run=True), 1)
        self.assertFalse((self.root / "ai-status.json").exists())
        self.assertIn("dry-run", self.log_path.read_text())

    def test_canonical_lock_contention_preserves_newer_live_truth(self) -> None:
        live = self.root / "ai-status.json"
        stale = board("2026-07-12T21:49:53Z")
        newer = board("2026-07-12T23:00:00Z").encode()
        (self.root / "docs-site" / "ai-status.json").write_text(stale)
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        release = context.Event()
        process = context.Process(
            target=_hold_newer_status_lock,
            args=(str(live), newer, ready, release),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            self.assertEqual(self._guard(), 0)
        finally:
            release.set()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(live.read_bytes(), newer)
        self.assertEqual(self._guard(), 0)
        self.assertEqual(live.read_bytes(), newer)

    def test_failed_restore_keeps_live_bytes_and_retry_recovers(self) -> None:
        live = self.root / "ai-status.json"
        corrupt = b"{not json"
        live.write_bytes(corrupt)
        (self.root / "docs-site" / "ai-status.json").write_text(
            board("2026-07-12T21:49:53Z")
        )

        with mock.patch.object(
            status_file_guard,
            "restore_canonical_task_state_bytes",
            side_effect=RuntimeError("injected readback failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected readback failure"):
                self._guard()

        self.assertEqual(live.read_bytes(), corrupt)
        quarantined = list(self.root.glob("ai-status.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), corrupt)
        self.assertIn(hashlib.sha256(corrupt).hexdigest()[:16], quarantined[0].name)
        if self.log_path.exists():
            self.assertNotIn("RESTORED", self.log_path.read_text())

        self.assertEqual(self._guard(), 1)
        self.assertEqual(
            self._restore_event()["prior_sha256"],
            hashlib.sha256(corrupt).hexdigest(),
        )

    def test_live_symlink_is_rejected_without_touching_target(self) -> None:
        outside = self.root / "outside.json"
        outside_bytes = board("2026-07-12T23:00:00Z").encode()
        outside.write_bytes(outside_bytes)
        (self.root / "ai-status.json").symlink_to(outside)
        (self.root / "docs-site" / "ai-status.json").write_text(
            board("2026-07-12T21:49:53Z")
        )

        with self.assertRaisesRegex(RuntimeError, "data file cannot be a symlink"):
            self._guard()

        self.assertEqual(outside.read_bytes(), outside_bytes)

    def test_nonregular_live_leaf_is_rejected(self) -> None:
        live = self.root / "ai-status.json"
        os.mkfifo(live)
        (self.root / "docs-site" / "ai-status.json").write_text(
            board("2026-07-12T21:49:53Z")
        )

        with self.assertRaisesRegex(RuntimeError, "stable regular file"):
            self._guard()

    def test_candidate_symlink_is_ignored(self) -> None:
        outside = self.root / "outside-snapshot.json"
        outside.write_text(board("2026-07-12T23:00:00Z"))
        (self.root / "docs-site" / "ai-status.json").symlink_to(outside)
        (self.root / "ai-status.json.bak").write_text(
            board("2026-07-12T11:55:52Z")
        )

        self.assertEqual(self._guard(), 1)
        self.assertEqual(self._restore_event()["source"], "ai-status.json.bak")

    def test_selected_candidate_bytes_are_bound_before_path_swap(self) -> None:
        source = self.root / "docs-site" / "ai-status.json"
        original = board("2026-07-12T21:49:53Z")
        replacement = board("2026-07-12T23:00:00Z")
        source.write_text(original)
        original_pick = status_file_guard.pick_source

        def pick_then_swap(root: Path):
            selected = original_pick(root)
            source.write_text(replacement)
            return selected

        with mock.patch.object(status_file_guard, "pick_source", side_effect=pick_then_swap):
            self.assertEqual(self._guard(), 1)

        event = self._restore_event()
        self.assertEqual(event["source_updated_at"], "2026-07-12T21:49:53Z")
        self.assertEqual(
            event["source_sha256"],
            hashlib.sha256(original.encode()).hexdigest(),
        )

    def test_existing_activity_outbox_is_preserved(self) -> None:
        pending_event = {
            "event_id": "pending-before-restore",
            "type": "progress",
            "message": "must survive restore",
        }
        source_payload = json.loads(board("2026-07-12T21:49:53Z"))
        source_payload["status_activity_outbox"] = {
            "schema_version": 1,
            "transaction_id": "ai-status-tx-"
            + status_file_guard._canonical_json_sha256([pending_event]),
            "events": [pending_event],
        }
        (self.root / "docs-site" / "ai-status.json").write_text(
            json.dumps(source_payload)
        )

        self.assertEqual(self._guard(), 1)

        restored = json.loads((self.root / "ai-status.json").read_text())
        events = restored["status_activity_outbox"]["events"]
        self.assertEqual(events[0], pending_event)
        self.assertEqual(events[1]["type"], "status_file_restored")
        self.assertEqual(
            restored["status_activity_outbox"]["transaction_id"],
            "ai-status-tx-" + status_file_guard._canonical_json_sha256(events),
        )

    def test_invalid_source_outbox_fails_before_live_or_evidence_write(self) -> None:
        live = self.root / "ai-status.json"
        corrupt = b"{not json"
        live.write_bytes(corrupt)
        source_payload = json.loads(board("2026-07-12T21:49:53Z"))
        source_payload["status_activity_outbox"] = {
            "schema_version": 1,
            "transaction_id": "not-content-bound",
            "events": [{"event_id": "pending-before-restore"}],
        }
        (self.root / "docs-site" / "ai-status.json").write_text(
            json.dumps(source_payload)
        )

        with self.assertRaisesRegex(RuntimeError, "activity outbox is invalid"):
            self._guard()

        self.assertEqual(live.read_bytes(), corrupt)
        self.assertEqual(list(self.root.glob("ai-status.json.corrupt-*")), [])

    def test_noncanonical_source_outbox_fails_before_live_or_evidence_write(self) -> None:
        live = self.root / "ai-status.json"
        corrupt = b"{not json"
        live.write_bytes(corrupt)
        pending_event = {"event_id": " pending-before-restore "}
        source_payload = json.loads(board("2026-07-12T21:49:53Z"))
        source_payload["status_activity_outbox"] = {
            "schema_version": 1,
            "transaction_id": "ai-status-tx-"
            + status_file_guard._canonical_json_sha256([pending_event]),
            "events": [pending_event],
        }
        (self.root / "docs-site" / "ai-status.json").write_text(
            json.dumps(source_payload)
        )

        with self.assertRaisesRegex(RuntimeError, "activity outbox is invalid"):
            self._guard()

        self.assertEqual(live.read_bytes(), corrupt)
        self.assertEqual(list(self.root.glob("ai-status.json.corrupt-*")), [])


if __name__ == "__main__":
    unittest.main()
