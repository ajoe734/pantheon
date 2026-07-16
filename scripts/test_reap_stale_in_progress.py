#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reap_stale_in_progress


class ReapStaleInProgressSequencingGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix="reap-stale-in-progress-guard-"
        )
        self.root = Path(self._temp_dir.name)
        self.orchestrator_dir = self.root / ".orchestrator"
        self.orchestrator_dir.mkdir(parents=True)
        self.status_file = self.root / "ai-status.json"
        self.state_file = self.orchestrator_dir / "reap-in-progress-state.json"
        self.lock_file = self.orchestrator_dir / "reap-in-progress.lock"

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _write_status(self, payload: dict) -> bytes:
        self.status_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.status_file.read_bytes()

    def _run(self, *, dry_run: bool = False) -> tuple[int, str]:
        output = io.StringIO()
        with (
            mock.patch.multiple(
                reap_stale_in_progress,
                STATUS_FILE=self.status_file,
                STATE_FILE=self.state_file,
                LOCK_FILE=self.lock_file,
                DRY_RUN=dry_run,
            ),
            mock.patch.object(
                reap_stale_in_progress,
                "_running_task_ids",
                return_value=set(),
            ),
            mock.patch.object(reap_stale_in_progress.time, "time", return_value=2_000_000_000),
            redirect_stdout(output),
        ):
            result = reap_stale_in_progress.main()
        return result, output.getvalue()

    def test_non_null_program_activity_outbox_blocks_reap_write(self) -> None:
        before = self._write_status(
            {
                "program_activity_outbox": {},
                "tasks": [
                    {
                        "id": "GENERIC-STALE-001",
                        "status": "in_progress",
                        "owner": "Codex",
                        "last_update": "2000-01-01T00:00:00Z",
                    }
                ],
            }
        )

        result, output = self._run()

        self.assertEqual(result, 0)
        self.assertIn("program_activity_outbox", output)
        self.assertEqual(self.status_file.read_bytes(), before)
        self.assertFalse(self.state_file.exists())

    def test_sequencing_parked_task_is_not_reaped(self) -> None:
        self._write_status(
            {
                "program_activity_outbox": None,
                "tasks": [
                    {
                        "id": "LOOP-PROD-AUTH-001",
                        "status": "in_progress",
                        "owner": "Codex",
                        "last_update": "2000-01-01T00:00:00Z",
                    },
                    {
                        "id": "GENERIC-STALE-001",
                        "status": "in_progress",
                        "owner": "Codex",
                        "last_update": "2000-01-01T00:00:00Z",
                    },
                ],
            }
        )

        result, _output = self._run()

        self.assertEqual(result, 0)
        status = json.loads(self.status_file.read_text(encoding="utf-8"))
        tasks = {task["id"]: task for task in status["tasks"]}
        self.assertEqual(tasks["LOOP-PROD-AUTH-001"]["status"], "in_progress")
        self.assertEqual(tasks["GENERIC-STALE-001"]["status"], "todo")
        seen = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertNotIn("LOOP-PROD-AUTH-001", seen)
        self.assertEqual(seen["GENERIC-STALE-001"]["reaps"], 1)

    def test_pending_outbox_dry_run_remains_read_only(self) -> None:
        before = self._write_status(
            {
                "program_activity_outbox": {"transaction": "pending"},
                "tasks": [
                    {
                        "id": "GENERIC-STALE-001",
                        "status": "in_progress",
                        "owner": "Codex",
                        "last_update": "2000-01-01T00:00:00Z",
                    }
                ],
            }
        )

        result, output = self._run(dry_run=True)

        self.assertEqual(result, 0)
        self.assertIn("WOULD reap GENERIC-STALE-001", output)
        self.assertEqual(self.status_file.read_bytes(), before)
        self.assertFalse(self.state_file.exists())


if __name__ == "__main__":
    unittest.main()
