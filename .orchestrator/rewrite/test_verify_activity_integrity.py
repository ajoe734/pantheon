from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_activity_integrity as vai


class VerifyExitCodeTests(unittest.TestCase):
    def test_missing_log_is_operational(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "nope.jsonl"
            self.assertEqual(vai.verify(missing, quiet=True), vai.EXIT_OPERATION)

    def test_malformed_log_is_integrity_failure(self) -> None:
        # A non-JSON line makes the incumbent validator fail closed; the tool
        # must report it (exit 2), never raise.
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("not-json\n{\"a\":1}\n")
            self.assertEqual(vai.verify(bad, quiet=True), vai.EXIT_INTEGRITY)

    def test_verify_never_raises(self) -> None:
        # Whatever the input, verify() returns an int exit code and does not
        # propagate — that is the whole point (never raise inside a cycle/cron).
        with tempfile.TemporaryDirectory() as d:
            weird = Path(d) / "weird.jsonl"
            weird.write_bytes(b"\x00\x01\x02 not text at all\n")
            result = vai.verify(weird, quiet=True)
            self.assertIn(result, {vai.EXIT_INTEGRITY, vai.EXIT_OPERATION})

    def test_cli_requires_a_target(self) -> None:
        with self.assertRaises(SystemExit):
            vai.main([])

    def test_cli_log_override(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("not-json\n")
            self.assertEqual(vai.main(["--log", str(bad), "--quiet"]), vai.EXIT_INTEGRITY)


class VerifyLiveLogTests(unittest.TestCase):
    """Integration check against the real activity log when present."""

    def test_live_log_validates_if_present(self) -> None:
        import json

        import common

        config_path = Path(__file__).resolve().parents[1] / "config.json"
        if not config_path.exists():
            self.skipTest("no config.json")
        config = json.loads(config_path.read_text())
        log_path = common.config_path(config, "activity_log")
        if not log_path.exists():
            self.skipTest("no live activity log")
        self.assertEqual(vai.verify(log_path, quiet=True), vai.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
