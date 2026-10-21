"""TELEMETRY-STORAGE-BOUNDED-20261021 — bounded DLQ spill retention."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from services.telemetry.dead_letter import (
    TAG_RETRY_EXHAUSTED,
    TAG_SCHEMA_VIOLATION,
    TAG_WRITER_ERROR,
    DeadLetterQueue,
)

RETENTION = 3600.0


def _line(event_id: str, tag: str, age_seconds: float) -> str:
    rejected = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return json.dumps(
        {
            "event": {"event_id": event_id},
            "tags": [tag],
            "reason": "test",
            "rejected_at": rejected.isoformat().replace("+00:00", "Z"),
            "retry_count": 0,
            "original_attempt_count": 0,
        }
    )


class DeadLetterRetentionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.spill = Path(self._tmp.name) / "dead_letter.jsonl"

    def _write(self, lines):
        self.spill.write_text("\n".join(lines) + "\n")

    def _spilled_ids(self):
        return [json.loads(l)["event"]["event_id"] for l in self.spill.read_text().splitlines()]

    def test_keeps_replayable_and_drops_expired_validation_failures(self):
        old = RETENTION * 10
        self._write(
            [
                _line("writer-old", TAG_WRITER_ERROR, old),
                _line("retry-old", TAG_RETRY_EXHAUSTED, old),
                _line("schema-old", TAG_SCHEMA_VIOLATION, old),
                _line("schema-new", TAG_SCHEMA_VIOLATION, 60),
                "not json",
            ]
        )
        dlq = DeadLetterQueue(str(self.spill), retention_seconds=RETENTION)
        loaded = dlq.load_from_spill()

        self.assertEqual(self._spilled_ids(), ["writer-old", "retry-old", "schema-new"])
        self.assertEqual(loaded, 3)
        self.assertEqual(
            sorted(e["event_id"] for e in dlq.replay_entries()),
            ["retry-old", "schema-new", "writer-old"],
        )

    def test_custom_replayable_tag_is_kept(self):
        self._write([_line("schema-old", TAG_SCHEMA_VIOLATION, RETENTION * 10)])
        dlq = DeadLetterQueue(
            str(self.spill),
            retention_seconds=RETENTION,
            replayable_tags=(TAG_SCHEMA_VIOLATION,),
        )
        self.assertEqual(dlq.load_from_spill(), 1)

    def test_nothing_expired_leaves_file_untouched(self):
        self._write([_line("a", TAG_SCHEMA_VIOLATION, 10)])
        before = self.spill.stat().st_ino
        dlq = DeadLetterQueue(str(self.spill), retention_seconds=RETENTION)
        self.assertEqual(dlq.compact_spill(), 0)
        self.assertEqual(self.spill.stat().st_ino, before)

    def test_appends_after_compaction_still_land(self):
        self._write([_line("schema-old", TAG_SCHEMA_VIOLATION, RETENTION * 10)])
        dlq = DeadLetterQueue(str(self.spill), retention_seconds=RETENTION)
        dlq.load_from_spill()
        dlq.reject({"event_id": "late"}, [TAG_SCHEMA_VIOLATION], "late")
        self.assertEqual(self._spilled_ids(), ["late"])


if __name__ == "__main__":
    unittest.main()
