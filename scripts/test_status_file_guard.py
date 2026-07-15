#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import status_file_guard


def board(updated_at: str, *, agents: int = 2) -> str:
    return json.dumps(
        {
            "updated_at": updated_at,
            "agents": [{"name": f"agent-{i}", "status": "idle"} for i in range(agents)],
        }
    )


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
        self.assertEqual(json.loads(live.read_text())["updated_at"], "2026-07-12T21:49:53Z")
        self.assertEqual(live.stat().st_mode & 0o777, status_file_guard.LIVE_MODE)
        self.assertIn("RESTORED", self.log_path.read_text())

    def test_empty_live_file_is_quarantined_then_restored(self) -> None:
        live = self.root / "ai-status.json"
        live.write_text("")
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:49:53Z"))

        self.assertEqual(self._guard(), 1)

        self.assertEqual(json.loads(live.read_text())["updated_at"], "2026-07-12T21:49:53Z")
        quarantined = list(self.root.glob("ai-status.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(), "")

    def test_corrupt_json_is_treated_as_unhealthy(self) -> None:
        live = self.root / "ai-status.json"
        live.write_text("{not json")
        (self.root / "docs-site" / "ai-status.json").write_text(board("2026-07-12T21:49:53Z"))

        self.assertEqual(self._guard(), 1)
        self.assertEqual(json.loads(live.read_text())["updated_at"], "2026-07-12T21:49:53Z")

    def test_snapshot_without_agents_is_not_a_restore_source(self) -> None:
        (self.root / "docs-site" / "ai-status.json").write_text(json.dumps({"updated_at": "2026-07-12T23:00:00Z"}))
        (self.root / "ai-status.json.bak").write_text(board("2026-07-12T11:55:52Z"))

        self.assertEqual(self._guard(), 1)
        live = self.root / "ai-status.json"
        self.assertEqual(json.loads(live.read_text())["updated_at"], "2026-07-12T11:55:52Z")

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


if __name__ == "__main__":
    unittest.main()
