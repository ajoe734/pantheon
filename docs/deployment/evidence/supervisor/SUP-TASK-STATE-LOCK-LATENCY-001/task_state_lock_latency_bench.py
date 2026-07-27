#!/usr/bin/env python3
"""Reproduce and bound the SUP-TASK-STATE-LOCK-LATENCY-001 lock stall.

The live incident, preserved here as the fixture's shape:

* Supervisor PID 901543 stamped a tick heartbeat at 22:35:04Z and completed it
  at 22:47:55Z (771s) while reviewer and status processes queued on
  runtime-admission inode 807896; the task-state reviewer reopen waited about
  nine minutes.
* The next exclusive hold ran from at least 22:50:27Z to 22:59:04Z (~517s).
  Codex2 BFF reopen PID 480495 waited from about 22:51:17Z and committed only
  at 22:59:45Z.
* Even lock-free Human/Ops note commands over the ~157MB / 2050-event journal
  each took roughly 55-90s.

None of that was lock-bypass, config drift, or a stuck worker. It was replay
cost: a status command replayed and revalidated the entire journal four times
(``load_events`` + ``project_latest_state`` to read, then two more full passes
inside ``append_state_commit``), and the supervisor's reconciliation phase did
the same four times per cycle while holding the exclusive canonical lock.

This harness measures both shapes against one fixture:

  legacy   -- the read/commit pattern as it stood before the fix
  current  -- the snapshot pattern shipped by this task

Run the full fixture for evidence::

    PYTHONPATH=.orchestrator python3 \\
      docs/deployment/evidence/supervisor/SUP-TASK-STATE-LOCK-LATENCY-001/task_state_lock_latency_bench.py \\
      --events 2050 --task-rows 60 --samples 12 --json report.json

Nothing here mutates canonical state: the fixture is built in a scratch
directory and removed unless ``--keep`` is passed.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
ORCHESTRATOR = REPO_ROOT / ".orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

from common import canonical_task_state_lock_file  # noqa: E402
from rewrite import task_state_store as store  # noqa: E402

# Live journal reference points, kept in code so a regression run states what it
# is being compared against.
LIVE_EVENT_COUNT = 2050
LIVE_JOURNAL_BYTES = 157 * 1000 * 1000
LIVE_NOTE_COMMAND_SECONDS = (55.0, 90.0)
LIVE_LOCK_HOLD_SECONDS = (517.0, 771.0)
LIVE_REVIEWER_WAIT_SECONDS = 508.0  # Codex2 BFF reopen: ~22:51:17Z -> 22:59:45Z
TARGET_P95_SECONDS = 2.0


def board(task_rows: int, generation: int) -> dict[str, Any]:
    """A board shaped like the live one: many tasks, each with long prose.

    Task ids only ever accumulate, so the nonterminal-drop guard stays quiet and
    the fixture exercises the same validation path as production.
    """

    filler = chr(ord("a") + generation % 26)
    return {
        "generation": generation,
        "tasks": [
            {
                "id": f"BENCH-{index:04d}",
                "status": "done" if index % 4 == 0 else "in_progress",
                "owner": "Claude",
                "reviewer": "Codex2",
                "phase": "Supervisor Runtime Repair",
                "next": filler * 900,
                "summary_zh": filler * 300,
                "acceptance": [filler * 90 for _ in range(8)],
                "artifacts": [f".orchestrator/bench/{index}/{filler}" for _ in range(6)],
            }
            for index in range(task_rows)
        ],
    }


def build_fixture(path: Path, *, events: int, task_rows: int) -> dict[str, Any]:
    """Write a valid hash-chained journal in one pass.

    Appending through ``append_state_commit`` would re-read the growing file
    once per commit, making fixture construction quadratic; the chain is built
    directly here using the store's own digest rules.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    previous_sha256: str | None = None
    with path.open("wb") as handle:
        for sequence in range(1, events + 1):
            state = board(task_rows, sequence)
            event: dict[str, Any] = {
                "version": store.EVENT_VERSION,
                "type": store.EVENT_TYPE_STATE_COMMITTED,
                "sequence": sequence,
                "committed_at": "2026-07-26T22:00:00Z",
                "source": "bench-fixture",
                "previous_event_sha256": previous_sha256,
                "state_sha256": store.sha256_json(state),
                "state": state,
            }
            event_sha256 = store.sha256_json(event)
            event["event_sha256"] = event_sha256
            event["event_id"] = f"task-state-{event_sha256}"
            handle.write(store.canonical_json_bytes(event) + b"\n")
            previous_sha256 = event_sha256
    return {
        "events": events,
        "task_rows": task_rows,
        "bytes": path.stat().st_size,
        "build_seconds": round(time.monotonic() - started, 3),
    }


def legacy_load_state(path: Path) -> dict[str, Any]:
    """The pre-fix read: full replay, then a second full replay to project."""

    events = store.load_events(path)
    return store.project_latest_state(events)


def legacy_commit(path: Path, state: dict[str, Any]) -> None:
    """The pre-fix commit: full replay for the head, then another to read back.

    Only the replay cost is reproduced; the fixture is left unchanged so every
    sample measures the same journal.
    """

    event_path = store._prepare_parent(path)
    with store._store_lock(event_path, shared=False):
        events = store._load_events_unlocked(event_path)
        store.validate_state_transition(state, events[-1]["state"] if events else None)
        store.sha256_json(state)
        store._load_events_unlocked(event_path)


def current_load_state(path: Path) -> dict[str, Any]:
    return store.load_snapshot(path)["state"]


def current_commit(path: Path, state: dict[str, Any]) -> None:
    """The shipped commit path's read cost, with the append itself withheld.

    Matching ``legacy_commit`` keeps the comparison like for like: both measure
    what a commit pays to establish the journal head under the exclusive store
    lock, over an identical fixture.
    """

    event_path = store._prepare_parent(path)
    with store._store_lock(event_path, shared=False):
        snapshot = store._load_snapshot_unlocked(event_path)
        store.validate_state_transition(state, snapshot["last_event"]["state"])
        store.sha256_json(state)


def measure(label: str, path: Path, *, samples: int, legacy: bool) -> dict[str, Any]:
    """Time the full shape of one status command: read the board, then commit."""

    if legacy:
        os.environ[store.FULL_REPLAY_ENV] = "1"
    else:
        os.environ.pop(store.FULL_REPLAY_ENV, None)
    load = legacy_load_state if legacy else current_load_state
    commit = legacy_commit if legacy else current_commit

    durations: list[float] = []
    for _ in range(samples):
        started = time.monotonic()
        state = load(path)
        state["tasks"][0]["next"] = "reviewer approve"
        commit(path, state)
        durations.append(time.monotonic() - started)
    os.environ.pop(store.FULL_REPLAY_ENV, None)

    ordered = sorted(durations)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "label": label,
        "samples": samples,
        "p50_seconds": round(statistics.median(ordered), 3),
        "p95_seconds": round(ordered[index], 3),
        "max_seconds": round(ordered[-1], 3),
        "min_seconds": round(ordered[0], 3),
    }


def _reconcile_once(journal: Path, status_file: Path, *, legacy: bool) -> None:
    """One supervisor reconciliation, holding the canonical lock as the cycle does."""

    with canonical_task_state_lock_file(status_file, shared=False):
        if legacy:
            # Four full replays: load, project, then verify_projection loading
            # and projecting the journal all over again.
            events = store._load_events_unlocked(store._prepare_parent(journal))
            state = store.project_latest_state(events)
            replay = store._load_events_unlocked(store._prepare_parent(journal))
            store.sha256_json(store.project_latest_state(replay))
            store.sha256_json(state)
        else:
            snapshot = store.load_snapshot(journal)
            store.verify_snapshot(snapshot, snapshot["state"])


def _supervisor_loop(journal: str, status_file: str, seconds: float, legacy: bool) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _reconcile_once(Path(journal), Path(status_file), legacy=legacy)


def _status_command_loop(
    journal: str,
    status_file: str,
    count: int,
    legacy: bool,
    results: Any,
) -> None:
    """A reviewer approve / assign / note command: lock, read the board, commit."""

    for _ in range(count):
        started = time.monotonic()
        with canonical_task_state_lock_file(Path(status_file), shared=False):
            if legacy:
                state = legacy_load_state(Path(journal))
                state["tasks"][0]["next"] = "reviewer approve"
                legacy_commit(Path(journal), state)
            else:
                state = current_load_state(Path(journal))
                state["tasks"][0]["next"] = "reviewer approve"
                current_commit(Path(journal), state)
        results.put(time.monotonic() - started)


def measure_contention(
    journal: Path,
    status_file: Path,
    *,
    legacy: bool,
    workers: int,
    commands_per_worker: int,
    cycle_seconds: float,
) -> dict[str, Any]:
    """Run status commands against a supervisor that is actively cycling.

    This is the live shape: the supervisor owns the canonical lock for the
    length of its reconciliation, and every reviewer or worker status command
    queues behind it. The number reported is what those commands actually
    waited, end to end.
    """

    if legacy:
        os.environ[store.FULL_REPLAY_ENV] = "1"
    else:
        os.environ.pop(store.FULL_REPLAY_ENV, None)

    results: Any = multiprocessing.Queue()
    supervisor = multiprocessing.Process(
        target=_supervisor_loop,
        args=(str(journal), str(status_file), cycle_seconds, legacy),
    )
    supervisor.start()
    time.sleep(0.25)  # let the cycle take the lock first, as it does live

    commands = [
        multiprocessing.Process(
            target=_status_command_loop,
            args=(str(journal), str(status_file), commands_per_worker, legacy, results),
        )
        for _ in range(workers)
    ]
    for process in commands:
        process.start()
    for process in commands:
        process.join()
    supervisor.join()
    os.environ.pop(store.FULL_REPLAY_ENV, None)

    durations = sorted(results.get() for _ in range(workers * commands_per_worker))
    index = max(0, min(len(durations) - 1, int(round(0.95 * (len(durations) - 1)))))
    return {
        "label": "legacy" if legacy else "current",
        "concurrent_workers": workers,
        "commands": len(durations),
        "supervisor_cycle_seconds": cycle_seconds,
        "p50_seconds": round(statistics.median(durations), 3),
        "p95_seconds": round(durations[index], 3),
        "max_seconds": round(durations[-1], 3),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace) if args.workspace else Path(
        tempfile.mkdtemp(prefix="task-state-lock-latency-")
    )
    journal = workspace / "runtime" / "task-state-events.jsonl"
    try:
        fixture = build_fixture(journal, events=args.events, task_rows=args.task_rows)

        # Cold read: no checkpoint exists yet, so this is a full validated replay.
        started = time.monotonic()
        cold = store.load_snapshot(journal)
        cold_seconds = round(time.monotonic() - started, 3)

        legacy = measure("legacy", journal, samples=args.samples, legacy=True)
        current = measure("current", journal, samples=args.samples, legacy=False)

        status_file = workspace / "ai-status.json"
        status_file.write_text(json.dumps(board(args.task_rows, args.events)), encoding="utf-8")
        contention = {
            "legacy": measure_contention(
                journal,
                status_file,
                legacy=True,
                workers=args.contention_workers,
                commands_per_worker=args.contention_commands,
                cycle_seconds=args.contention_seconds,
            ),
            "current": measure_contention(
                journal,
                status_file,
                legacy=False,
                workers=args.contention_workers,
                commands_per_worker=args.contention_commands,
                cycle_seconds=args.contention_seconds,
            ),
        }

        report = {
            "fixture": fixture,
            "live_reference": {
                "event_count": LIVE_EVENT_COUNT,
                "journal_bytes": LIVE_JOURNAL_BYTES,
                "note_command_seconds": list(LIVE_NOTE_COMMAND_SECONDS),
                "exclusive_lock_hold_seconds": list(LIVE_LOCK_HOLD_SECONDS),
                "reviewer_reopen_wait_seconds": LIVE_REVIEWER_WAIT_SECONDS,
                "supervisor_pid": 901543,
                "runtime_admission_inode": 807896,
            },
            "cold_first_read": {
                "seconds": cold_seconds,
                "event_count": cold["event_count"],
                "resumed_from_checkpoint": cold["resumed_from_checkpoint"],
            },
            "uncontended": {"legacy": legacy, "current": current},
            "under_active_supervisor_cycle": contention,
            "target_p95_seconds": TARGET_P95_SECONDS,
            "meets_target": (
                current["p95_seconds"] < TARGET_P95_SECONDS
                and contention["current"]["p95_seconds"] < TARGET_P95_SECONDS
            ),
            "speedup_p95": {
                "uncontended": round(
                    legacy["p95_seconds"] / max(current["p95_seconds"], 1e-6), 1
                ),
                "under_active_supervisor_cycle": round(
                    contention["legacy"]["p95_seconds"]
                    / max(contention["current"]["p95_seconds"], 1e-6),
                    1,
                ),
            },
        }
        return report
    finally:
        if not args.keep and not args.workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=LIVE_EVENT_COUNT)
    parser.add_argument("--task-rows", type=int, default=60)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--contention-workers", type=int, default=4)
    parser.add_argument("--contention-commands", type=int, default=3)
    parser.add_argument("--contention-seconds", type=float, default=30.0)
    parser.add_argument("--workspace", default=None, help="Reuse a fixture directory.")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--json", default=None, help="Write the report to this path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(args)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["meets_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
