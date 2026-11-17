"""Atomic state-file helper: deterministic temp, cleanup, owner-scoped sweep."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from services.foundation.reliable_delivery import (
    atomic_temp_path,
    atomic_write_text,
    sweep_stale_temp_siblings,
)


class AtomicStateFileTest(unittest.TestCase):
    def test_write_leaves_no_temp_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.json"
            for i in range(3):
                atomic_write_text(target, str(i))
            self.assertEqual(target.read_text(), "2")
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["state.json"])

    def test_failed_write_cleans_temp_on_every_exit_path(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.json"
            atomic_write_text(target, "old")
            with mock.patch("os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    atomic_write_text(target, "new")
            self.assertEqual(target.read_text(), "old")
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["state.json"])

    def test_kill_leaves_one_temp_per_target_and_sweep_removes_only_own(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "b-1.json"
            other = root / "b-2.json"
            target.write_text("T")
            other.write_text("O")
            atomic_temp_path(target).write_text("partial")  # killed mid-write
            legacy = [
                root / f"b-1.json.{uuid.uuid4().hex}.tmp",
                root / f".b-1.json.{uuid.uuid4().hex}.tmp",
                root / f".b-1.json.4242.{uuid.uuid4().hex}.tmp",
            ]
            for p in legacy:
                p.write_text("x")
            keep = [
                root / f"b-2.json.{uuid.uuid4().hex}.tmp",
                root / "b-1.json.lifecycle-outbox.json.tmp",
                root / "b-1.json.lifecycle-outbox.json",
            ]
            for p in keep:
                p.write_text("k")
            removed = sweep_stale_temp_siblings(target)
            self.assertEqual(len(removed), 4)
            self.assertEqual(target.read_text(), "T")
            self.assertEqual(other.read_text(), "O")
            for p in keep:
                self.assertTrue(p.exists(), p)


if __name__ == "__main__":
    unittest.main()
