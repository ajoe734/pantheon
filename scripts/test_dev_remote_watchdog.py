"""Isolated unit tests for scripts/dev_remote_watchdog.py startup_timeout vs runtime pause."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts import dev_remote_guarded_exec as sender
from scripts import dev_remote_watchdog as watchdog


ROOT = Path(__file__).resolve().parents[1]
NONCE = "a" * 32


def _wait_until(check, timeout: float = 4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("isolated watchdog condition timed out")


def _process_state(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return None


def _envelope_bytes(script: str, seconds: int = 5) -> bytes:
    return (
        json.dumps(
            {
                "script": base64.b64encode(script.encode("utf-8")).decode("ascii"),
                "nonce": NONCE,
                "deadline_seconds": seconds,
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _launch_watchdog(
    *,
    startup_timeout: float = 30.0,
    pause: float = 0.12,
    silence_kill: float = 2.0,
    poll: float = 0.01,
    terminate_grace: float = 0.05,
) -> subprocess.Popen:
    code = (
        "from scripts.dev_remote_watchdog import WatchdogTimings, run_watchdog; "
        f"raise SystemExit(run_watchdog(timings=WatchdogTimings("
        f"startup_timeout={startup_timeout!r}, "
        f"pause={pause!r}, "
        f"silence_kill={silence_kill!r}, "
        f"poll={poll!r}, "
        f"terminate_grace={terminate_grace!r})))"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _cleanup_process(process: subprocess.Popen) -> None:
    if process.stdin and not process.stdin.closed:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    for stream in (process.stdout, process.stderr):
        if stream and not stream.closed:
            stream.close()


class TestDevRemoteWatchdog(unittest.TestCase):
    def setUp(self) -> None:
        self._processes: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for process in self._processes:
            _cleanup_process(process)

    def _spawn(self, **kwargs) -> subprocess.Popen:
        process = _launch_watchdog(**kwargs)
        self._processes.append(process)
        return process

    def test_watchdog_timings_defaults_separate_startup_timeout_from_pause(self) -> None:
        timings = watchdog.WatchdogTimings()
        self.assertIs(watchdog.Timings, watchdog.WatchdogTimings)
        self.assertEqual(timings.startup_timeout, 30.0)
        self.assertEqual(timings.pause, 3.0)
        self.assertEqual(timings.silence_kill, 120.0)
        self.assertEqual(timings.poll, 0.05)
        self.assertEqual(timings.terminate_grace, 1.0)

        for invalid_startup in (0.0, -1.0, 0.01):
            process = self._spawn(startup_timeout=invalid_startup, pause=0.12, poll=0.01)
            code = process.wait(timeout=4)
            self.assertEqual(code, watchdog.FAILURE)

    def test_large_envelope_exceeding_pause_succeeds_within_startup_timeout(self) -> None:
        # Simulate a ~350KB base64 deploy-dev envelope streaming over WAN SSH slower
        # than runtime pause (0.10s) but well within startup_timeout (1.0s).
        padding = "# " + ("x" * 270000) + "\n"
        script = padding + "printf 'wan-envelope-ok\\n'\n"
        raw_envelope = _envelope_bytes(script, seconds=5)
        self.assertGreaterEqual(len(raw_envelope), 350000)

        process = self._spawn(startup_timeout=1.0, pause=0.10, silence_kill=2.0, poll=0.01)
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        chunk_size = 65536
        for offset in range(0, len(raw_envelope), chunk_size):
            process.stdin.write(raw_envelope[offset : offset + chunk_size])
            process.stdin.flush()
            time.sleep(0.04)  # 6 chunks * 0.04s = ~0.24s > pause (0.10s)
        process.stdin.write(sender._frame(NONCE, 1))
        process.stdin.flush()

        code = process.wait(timeout=4)
        stdout = process.stdout.read().decode("utf-8")
        stderr = process.stderr.read().decode("utf-8")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "wan-envelope-ok\n")
        self.assertEqual(stderr, "")

    def test_envelope_exceeding_startup_timeout_fails_closed_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "must-not-run"
            raw_envelope = _envelope_bytes(f"touch {marker}\n", seconds=5)
            process = self._spawn(startup_timeout=0.20, pause=0.10, silence_kill=2.0, poll=0.01)
            assert process.stdin is not None
            assert process.stderr is not None

            # Write only a partial envelope (no trailing newline) and exceed startup_timeout.
            process.stdin.write(raw_envelope[:-10])
            process.stdin.flush()
            code = process.wait(timeout=4)
            stderr = process.stderr.read().decode("utf-8")
            self.assertEqual(code, watchdog.FAILURE)
            self.assertIn("[artifact-watchdog] guarded execution failed", stderr)
            self.assertFalse(marker.exists())

    def test_runtime_pause_still_triggers_at_pause_after_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            script = f"echo $$ > {pid_file}\nsleep 0.45\n"
            process = self._spawn(startup_timeout=1.5, pause=0.12, silence_kill=2.0, poll=0.01)
            assert process.stdin is not None

            process.stdin.write(_envelope_bytes(script, seconds=5) + sender._frame(NONCE, 1))
            process.stdin.flush()
            _wait_until(pid_file.exists)
            child_pid = int(pid_file.read_text().strip())

            # Child must pause around pause (0.12s), well before startup_timeout (1.5s).
            started = time.monotonic()
            _wait_until(lambda: _process_state(child_pid) == "T", timeout=0.8)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.8)

            process.stdin.write(sender._frame(NONCE, 2))
            process.stdin.flush()
            _wait_until(lambda: _process_state(child_pid) != "T", timeout=1.0)
            for seq in range(3, 25):
                if process.poll() is not None:
                    break
                time.sleep(0.04)
                try:
                    process.stdin.write(sender._frame(NONCE, seq))
                    process.stdin.flush()
                except BrokenPipeError:
                    break
            self.assertEqual(process.wait(timeout=4), 0)


if __name__ == "__main__":
    unittest.main()
