#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sidecar_cleanup


class SidecarCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.sidecars_root = self.root / "support" / "sidecars"
        self.archive_tasks_dir = self.root / "ai-task-archive" / "tasks"
        self.status_path = self.root / "ai-status.json"
        self.now = datetime(2026, 5, 16, tzinfo=timezone.utc)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")

    def _write_sidecar(self, task_id: str) -> Path:
        packet_dir = self.sidecars_root / task_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        (packet_dir / f"{task_id}-SIDECAR-ACCEPTANCE.md").write_text(
            f"# {task_id}\n",
            encoding="utf-8",
        )
        return packet_dir

    def _write_archive_snapshot(self, task_id: str, done_at: str) -> None:
        self.archive_tasks_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "version": 1,
            "task_id": task_id,
            "archived_at": done_at,
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": task_id,
                "status": "done",
                "last_update": done_at,
            },
        }
        (self.archive_tasks_dir / f"{task_id}.json").write_text(
            json.dumps(snapshot, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_scan_classifies_fresh_archive_and_delete_ages(self) -> None:
        self._write_sidecar("FRESH-001")
        self._write_sidecar("STALE-001")
        self._write_sidecar("OLD-001")
        self._write_archive_snapshot("FRESH-001", "2026-05-10T00:00:00Z")
        self._write_archive_snapshot("STALE-001", "2026-04-20T00:00:00Z")
        self._write_archive_snapshot("OLD-001", "2026-03-01T00:00:00Z")

        plan = sidecar_cleanup.scan(
            sidecars_root=self.sidecars_root,
            archive_tasks_dir=self.archive_tasks_dir,
            status_path=self.status_path,
            now=self.now,
        )

        actions = {item.task_id: item.action for item in plan.items}
        self.assertEqual(actions["FRESH-001"], sidecar_cleanup.ACTION_KEEP)
        self.assertEqual(actions["STALE-001"], sidecar_cleanup.ACTION_ARCHIVE)
        self.assertEqual(actions["OLD-001"], sidecar_cleanup.ACTION_DELETE)

        dry_run = sidecar_cleanup.execute(plan, dry_run=True)
        self.assertEqual(dry_run, plan)
        self.assertTrue((self.sidecars_root / "STALE-001").exists())
        self.assertTrue((self.sidecars_root / "OLD-001").exists())

        result = sidecar_cleanup.execute(plan, dry_run=False)

        self.assertIsInstance(result, sidecar_cleanup.ExecutionResult)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue((self.sidecars_root / "FRESH-001").exists())
        self.assertFalse((self.sidecars_root / "STALE-001").exists())
        self.assertTrue((self.sidecars_root / "archived" / "STALE-001").exists())
        self.assertFalse((self.sidecars_root / "OLD-001").exists())

    def test_classify_keeps_packets_without_terminal_parent(self) -> None:
        packet_dir = self._write_sidecar("ACTIVE-001")

        item = sidecar_cleanup.classify(
            packet_dir,
            sidecars_root=self.sidecars_root,
            archive_tasks_dir=self.archive_tasks_dir,
            status_path=self.status_path,
            now=self.now,
        )

        self.assertEqual(item.action, sidecar_cleanup.ACTION_KEEP)
        self.assertIn("not archived as done", item.reason)

    def test_cli_dry_run_exits_zero(self) -> None:
        self._write_sidecar("STALE-001")
        self._write_archive_snapshot("STALE-001", "2026-04-20T00:00:00Z")
        stdout = io.StringIO()

        exit_code = sidecar_cleanup.main(
            [
                "--sidecars-root",
                str(self.sidecars_root),
                "--archive-tasks-dir",
                str(self.archive_tasks_dir),
                "--status-path",
                str(self.status_path),
                "--now",
                "2026-05-16T00:00:00Z",
            ],
            stdout=stdout,
        )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["plan"]["counts"][sidecar_cleanup.ACTION_ARCHIVE], 1)
        self.assertTrue((self.sidecars_root / "STALE-001").exists())


if __name__ == "__main__":
    unittest.main()
