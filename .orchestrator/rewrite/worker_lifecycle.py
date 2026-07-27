"""Worker lifecycle primitives — Phase 4 (SUPERVISOR_REWRITE_PLAN.md, anti-pattern E).

Two of the plan's Phase-4 fixes are self-contained and land here first, each a
pure/injectable helper the incumbent driver can adopt without touching the
751-line poll_workers loop:

1. **Confirm-kill** (`confirm_kill`). The incumbent `terminate_worker_pid` is
   SIGTERM-and-assume-dead: it returns True after one SIGTERM, so a worker that
   ignores SIGTERM gets marked `failed` while still running (and keeps mutating
   state). This does SIGTERM → wait → SIGKILL → verify and returns True only when
   the process is *confirmed* gone. Every syscall/clock is injected so it is
   deterministic under test.

2. **Observed progress** (`has_work_progress`). The incumbent renews a worker's
   lease off heartbeat freshness, so a live-but-hung runner that keeps
   heartbeating never expires ("hangs but heartbeats"). Real progress is an
   *observed work signal* — a new commit, or more completed tool-calls — not a
   heartbeat; lease renewal should bind to this instead.
"""
from __future__ import annotations

import signal
from typing import Any, Callable


def _wait_until_gone(
    pid: int,
    grace_seconds: float,
    *,
    is_alive: Callable[[int], bool],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    poll_interval: float,
) -> bool:
    deadline = monotonic() + grace_seconds
    while monotonic() < deadline:
        if not is_alive(pid):
            return True
        sleep(poll_interval)
    return not is_alive(pid)


def confirm_kill(
    pid: int | None,
    *,
    is_alive: Callable[[int], bool],
    send_signal: Callable[[int, int], None],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    term_grace_seconds: float = 2.0,
    kill_grace_seconds: float = 1.0,
    poll_interval: float = 0.1,
    term_already_sent: bool = False,
) -> bool:
    """SIGTERM → wait term_grace → SIGKILL → wait kill_grace → verify.

    Returns True iff the process is confirmed gone (so the caller may safely mark
    the worker terminated). Never raises: a signal OSError (already reaped, or not
    ours) is resolved by a final liveness check. Injecting is_alive/send_signal/
    sleep/monotonic keeps it pure and deterministically testable.

    ``term_already_sent`` supports callers that must make the stop decision
    atomically under a state lock but defer this helper's poll/sleep window until
    after releasing it. In that mode the grace period starts immediately without
    sending a duplicate SIGTERM.
    """
    if not pid:
        return False
    if not is_alive(pid):
        return True
    if not term_already_sent:
        try:
            send_signal(pid, signal.SIGTERM)
        except OSError:
            return not is_alive(pid)
    if _wait_until_gone(pid, term_grace_seconds, is_alive=is_alive, sleep=sleep,
                        monotonic=monotonic, poll_interval=poll_interval):
        return True
    try:
        send_signal(pid, signal.SIGKILL)
    except OSError:
        return not is_alive(pid)
    return _wait_until_gone(pid, kill_grace_seconds, is_alive=is_alive, sleep=sleep,
                            monotonic=monotonic, poll_interval=poll_interval)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def has_work_progress(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> bool:
    """True iff observed work advanced between two worker snapshots.

    Progress = a new commit sha, or a higher completed-tool-call count. Heartbeat
    freshness is deliberately NOT progress — that is exactly the hung-but-
    heartbeating signal the incumbent mistook for liveness. A snapshot carries
    ``commit_sha`` and/or ``tool_calls_completed`` observed for the worker's task.
    """
    prev = previous or {}
    curr = current or {}
    curr_sha = str(curr.get("commit_sha") or "").strip()
    prev_sha = str(prev.get("commit_sha") or "").strip()
    if curr_sha and curr_sha != prev_sha:
        return True
    return _as_int(curr.get("tool_calls_completed")) > _as_int(prev.get("tool_calls_completed"))


def lease_progress_is_fresh(
    *,
    last_progress_epoch: float | None,
    now_epoch: float,
    stall_seconds: float,
) -> bool:
    """Whether observed work progress is recent enough to renew a lease.

    `last_progress_epoch` is the most recent moment the worker showed *work*
    (a new commit / tool-call / process-tree activity / provider output) — NOT its
    last heartbeat. Binding lease renewal to this (instead of heartbeat freshness)
    is what makes a hung-but-heartbeating runner's lease finally lapse. With no
    progress signal yet (None) the lease is treated as fresh so a just-started
    worker is never starved before it can produce its first signal.
    """
    if last_progress_epoch is None:
        return True
    return (now_epoch - last_progress_epoch) <= max(0.0, stall_seconds)
