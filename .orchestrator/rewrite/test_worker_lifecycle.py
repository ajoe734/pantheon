from __future__ import annotations

import signal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worker_lifecycle as wl


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


class FakeProcess:
    """A process that dies after `dies_after_signals` signals (or never)."""

    def __init__(self, *, alive: bool = True, dies_on: int | None = None) -> None:
        self.alive = alive
        self.dies_on = dies_on  # signal number that kills it, or None = unkillable
        self.signals: list[int] = []

    def is_alive(self, _pid: int) -> bool:
        return self.alive

    def send(self, _pid: int, sig: int) -> None:
        self.signals.append(sig)
        if self.dies_on is not None and sig == self.dies_on:
            self.alive = False


class ConfirmKillTests(unittest.TestCase):
    def _run(self, proc: FakeProcess, **kw):
        clock = FakeClock()
        return wl.confirm_kill(
            4321,
            is_alive=proc.is_alive,
            send_signal=proc.send,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            **kw,
        )

    def test_no_pid(self) -> None:
        self.assertFalse(wl.confirm_kill(0, is_alive=lambda p: True, send_signal=lambda p, s: None,
                                         sleep=lambda d: None, monotonic=lambda: 0.0))

    def test_already_dead_no_signal(self) -> None:
        proc = FakeProcess(alive=False)
        self.assertTrue(self._run(proc))
        self.assertEqual(proc.signals, [])  # never signalled a dead pid

    def test_dies_on_sigterm(self) -> None:
        proc = FakeProcess(dies_on=signal.SIGTERM)
        self.assertTrue(self._run(proc))
        self.assertEqual(proc.signals, [signal.SIGTERM])  # no escalation needed

    def test_pre_sent_sigterm_is_not_sent_twice(self) -> None:
        proc = FakeProcess(dies_on=signal.SIGKILL)
        self.assertTrue(self._run(proc, term_already_sent=True))
        self.assertEqual(proc.signals, [signal.SIGKILL])

    def test_escalates_to_sigkill(self) -> None:
        proc = FakeProcess(dies_on=signal.SIGKILL)
        self.assertTrue(self._run(proc))
        self.assertEqual(proc.signals, [signal.SIGTERM, signal.SIGKILL])

    def test_unkillable_returns_false(self) -> None:
        proc = FakeProcess(dies_on=None)  # ignores every signal
        self.assertFalse(self._run(proc))
        self.assertEqual(proc.signals, [signal.SIGTERM, signal.SIGKILL])

    def test_signal_oserror_resolved_by_liveness(self) -> None:
        def boom(_pid, _sig):
            raise OSError("no such process")

        clock = FakeClock()
        # is_alive True at first check, then the signal errors -> resolved by a
        # final liveness probe (here it reports gone)
        states = iter([True, False, False])
        self.assertTrue(
            wl.confirm_kill(1, is_alive=lambda p: next(states), send_signal=boom,
                            sleep=clock.sleep, monotonic=clock.monotonic)
        )


class HasWorkProgressTests(unittest.TestCase):
    def test_new_commit_is_progress(self) -> None:
        self.assertTrue(wl.has_work_progress({"commit_sha": "a"}, {"commit_sha": "b"}))

    def test_same_commit_no_progress(self) -> None:
        self.assertFalse(wl.has_work_progress({"commit_sha": "a"}, {"commit_sha": "a"}))

    def test_first_commit_is_progress(self) -> None:
        self.assertTrue(wl.has_work_progress({}, {"commit_sha": "a"}))

    def test_more_tool_calls_is_progress(self) -> None:
        self.assertTrue(wl.has_work_progress({"tool_calls_completed": 3}, {"tool_calls_completed": 4}))

    def test_same_tool_calls_no_progress(self) -> None:
        self.assertFalse(wl.has_work_progress({"tool_calls_completed": 4}, {"tool_calls_completed": 4}))

    def test_heartbeat_is_not_progress(self) -> None:
        # only a heartbeat changed — the hung-but-heartbeating case
        self.assertFalse(
            wl.has_work_progress(
                {"commit_sha": "a", "tool_calls_completed": 2, "last_heartbeat_at": "t0"},
                {"commit_sha": "a", "tool_calls_completed": 2, "last_heartbeat_at": "t1"},
            )
        )

    def test_none_snapshots(self) -> None:
        self.assertFalse(wl.has_work_progress(None, None))
        self.assertTrue(wl.has_work_progress(None, {"commit_sha": "x"}))


class LeaseProgressFreshTests(unittest.TestCase):
    def test_no_signal_yet_is_fresh(self) -> None:
        # a just-started worker with no progress signal is not starved
        self.assertTrue(wl.lease_progress_is_fresh(last_progress_epoch=None, now_epoch=1000.0, stall_seconds=300))

    def test_recent_progress_is_fresh(self) -> None:
        self.assertTrue(wl.lease_progress_is_fresh(last_progress_epoch=900.0, now_epoch=1000.0, stall_seconds=300))

    def test_stale_progress_is_not_fresh(self) -> None:
        # hung-but-heartbeating: last real work was long ago -> lease must not renew
        self.assertFalse(wl.lease_progress_is_fresh(last_progress_epoch=500.0, now_epoch=1000.0, stall_seconds=300))

    def test_exactly_at_window_is_fresh(self) -> None:
        self.assertTrue(wl.lease_progress_is_fresh(last_progress_epoch=700.0, now_epoch=1000.0, stall_seconds=300))


if __name__ == "__main__":
    unittest.main()
