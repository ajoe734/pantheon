"""Phase 2 (§3.2) — the activity-log write hot path must never raise.

A recovery/integrity fault on write used to propagate and (pre-Phase-0) crash the
whole cycle. write_activity_log now degrades that fault to a warning and a forced
append so the entry is never lost and the cycle keeps running; integrity is owned
by the offline verifier. These tests pin that contract by forcing the validated
append to fault.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common


class ResilientWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = common.append_activity_log_entries_unlocked
        # A genuine lineage-integrity drift fault — the exact class (missing
        # archive) that caused the 4h outage, which §3.2 makes non-fatal.
        self._drift_message = "activity lineage archive is missing"

        def _boom(*_args, **_kwargs):
            raise RuntimeError(self._drift_message)

        common.append_activity_log_entries_unlocked = _boom  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(common, "append_activity_log_entries_unlocked", self._orig))

    def _config(self, tmp: str, *, strict: bool = False) -> dict:
        cfg = {"paths": {"activity_log": str(Path(tmp) / "ai-activity-log.jsonl")}}
        if strict:
            cfg["activity_log_strict_hot_path"] = True
        return cfg

    def test_fault_does_not_raise_and_entry_lands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            # Must not raise despite the validated append faulting.
            common.write_activity_log(cfg, {"event": "unit-test", "marker": "abc123"})
            log = Path(tmp) / "ai-activity-log.jsonl"
            self.assertTrue(log.exists())
            self.assertIn("abc123", log.read_text())

    def test_strict_config_still_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp, strict=True)
            with self.assertRaises(RuntimeError):
                common.write_activity_log(cfg, {"event": "unit-test"})

    def test_strict_env_override_still_raises(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            os.environ["PANTHEON_ACTIVITY_LOG_STRICT"] = "1"
            self.addCleanup(lambda: os.environ.pop("PANTHEON_ACTIVITY_LOG_STRICT", None))
            with self.assertRaises(RuntimeError):
                common.write_activity_log(cfg, {"event": "unit-test"})

    def test_non_drift_fault_still_raises(self) -> None:
        # Security/correctness faults (symlink, "recovery is pending", injected
        # markers) must keep failing closed even in resilient mode.
        common.append_activity_log_entries_unlocked = (  # type: ignore[assignment]
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("path contains a symlink"))
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            with self.assertRaises(RuntimeError):
                common.write_activity_log(cfg, {"event": "unit-test"})

    def test_repeated_faulting_writes_all_land(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config(tmp)
            for i in range(5):
                common.write_activity_log(cfg, {"event": "loop", "i": i})
            lines = [l for l in (Path(tmp) / "ai-activity-log.jsonl").read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 5)


if __name__ == "__main__":
    unittest.main()
