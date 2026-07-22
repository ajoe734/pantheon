#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import orchestrator_queue_triage


class OrchestratorQueueTriageWriterGuardTests(unittest.TestCase):
    def test_replay_output_rejects_symlink_alias_of_canonical_event_queue(self) -> None:
        with tempfile.TemporaryDirectory(prefix="queue-triage-guard-") as temp_dir:
            status_root = Path(temp_dir) / "status"
            canonical_queue = status_root / ".orchestrator" / "event-queue.jsonl"
            canonical_queue.parent.mkdir(parents=True)
            canonical_queue.write_text("sentinel\n", encoding="utf-8")
            alias = Path(temp_dir) / "queue-alias.jsonl"
            alias.symlink_to(canonical_queue)
            backup = Path(temp_dir) / "backup.jsonl"
            backup.write_text("", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {"PANTHEON_STATUS_ROOT": str(status_root)},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "cannot mutate PANTHEON_STATUS_ROOT canonical state",
                ):
                    orchestrator_queue_triage.write_replayable_jsonl(
                        {"replay_candidates": []},
                        backup,
                        alias,
                    )

            self.assertEqual(
                canonical_queue.read_text(encoding="utf-8"),
                "sentinel\n",
            )


if __name__ == "__main__":
    unittest.main()
