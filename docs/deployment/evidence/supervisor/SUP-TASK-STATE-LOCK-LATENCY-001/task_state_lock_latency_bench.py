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
      --events 2050 --task-rows 30 --samples 8 \\
      --contention-workers 4 --contention-commands 2 --json report.json

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
import subprocess
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
import supervisor as supervisor_module  # noqa: E402

# Live journal reference points, kept in code so a regression run states what it
# is being compared against.
LIVE_EVENT_COUNT = 2050
LIVE_JOURNAL_BYTES = 157 * 1000 * 1000
LIVE_NOTE_COMMAND_SECONDS = (55.0, 90.0)
LIVE_LOCK_HOLD_SECONDS = (517.0, 771.0)
LIVE_REVIEWER_WAIT_SECONDS = 508.0  # Codex2 BFF reopen: ~22:51:17Z -> 22:59:45Z
TARGET_P95_SECONDS = 2.0


def governed_command_specs(
    *,
    workers: int,
    commands_per_worker: int,
) -> list[dict[str, Any]]:
    """Unique real command invocations that can safely run concurrently."""

    command_names = ("approve", "assign", "note", "reopen")
    specs: list[dict[str, Any]] = []
    for index in range(workers * commands_per_worker):
        command = command_names[index % len(command_names)]
        task_id = (
            f"BENCH-ASSIGN-{index:04d}"
            if command == "assign"
            else f"BENCH-{index:04d}"
        )
        actor = "Human/Ops" if command == "assign" else "Codex2"
        if command == "assign":
            args = [
                task_id,
                "Claude",
                "Codex2",
                f"Governed benchmark assignment {index}",
            ]
        else:
            args = [task_id, f"Governed benchmark {command} {index}"]
        specs.append(
            {
                "index": index,
                "worker_slot": index % workers,
                "command": command,
                "task_id": task_id,
                "actor": actor,
                "args": args,
                "uses_worker_lease": actor != "Human/Ops",
            }
        )
    return specs


def board(
    task_rows: int,
    generation: int,
    *,
    command_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A board shaped like the live one: many tasks, each with long prose.

    Task ids only ever accumulate, so the nonterminal-drop guard stays quiet and
    the fixture exercises the same validation path as production.
    """

    filler = chr(ord("a") + generation % 26)
    state = {
        "project": "pantheon",
        "sprint": "SUP-TASK-STATE-LOCK-LATENCY-001-benchmark",
        "objective": "Measure governed command latency during a full supervisor cycle.",
        "updated_at": "2026-07-27T00:00:00Z",
        "canonical_document_layers": {},
        "canonical_files": [],
        "agents": [],
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
        "handoffs": [],
        "blockers": [],
        "workload": {},
    }
    tasks = {task["id"]: task for task in state["tasks"]}
    for spec in command_specs or []:
        if spec["command"] == "assign":
            continue
        task = tasks.get(spec["task_id"])
        if task is None:
            raise ValueError(
                f"task_rows={task_rows} does not cover {spec['task_id']}"
            )
        # All reviewer commands use a review task so their supervisor-issued
        # lease remains assignment-consistent until the command mutates it.
        task["status"] = "review"
    return state


def build_fixture(
    path: Path,
    *,
    events: int,
    task_rows: int,
    command_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write a valid V2 delta journal and atomic current head."""

    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for sequence in range(1, events + 1):
        state = board(
            task_rows,
            sequence,
            command_specs=command_specs,
        )
        store.append_state_commit(
            path,
            state,
            source="bench-fixture",
            committed_at="2026-07-26T22:00:00Z",
        )
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

    events = store.load_events(path)
    store.validate_state_transition(state, events[-1]["state"] if events else None)
    store.sha256_json(state)
    store.load_events(path)


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
        snapshot = store._snapshot_from_head_and_tail(event_path, repair=True)
        store.validate_state_transition(state, snapshot["state"])
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


def _git_output(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or proc.stdout or f"git {' '.join(args)} failed").strip()
        )
    return proc.stdout.strip()


def command_runtime_binding() -> dict[str, str]:
    """Bind governed commands to the exact committed candidate under test."""

    command_root = REPO_ROOT.resolve()
    source_sha = _git_output(command_root, "rev-parse", "HEAD")
    executable_dirty = _git_output(
        command_root,
        "status",
        "--porcelain",
        "--",
        "scripts/ai-status.sh",
        "scripts/ai_status.py",
        ".orchestrator",
    )
    if executable_dirty:
        raise RuntimeError(
            "benchmark candidate executable paths must be committed: "
            + executable_dirty.splitlines()[0]
        )
    return {
        "command_root": str(command_root),
        "source_sha": source_sha,
        "remote": _git_output(command_root, "remote", "get-url", "origin"),
        # Validate the wrapper against the exact task commit without falsely
        # claiming an unreviewed candidate is already present on origin/dev.
        "base_ref": source_sha,
    }


def prepare_full_supervisor_fixture(
    workspace: Path,
    journal: Path,
    status_file: Path,
    specs: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a scratch coordination root used by real supervisor/status code."""

    subprocess.run(
        ["git", "init", "-q", str(workspace)],
        capture_output=True,
        text=True,
        check=True,
    )
    runtime_dir = workspace / ".orchestrator"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "status_file": str(status_file),
        "activity_log": str(workspace / "ai-activity-log.jsonl"),
        "current_work": str(workspace / "current-work.md"),
        "dashboard": str(workspace / "docs-site" / "index.html"),
        "state_file": str(runtime_dir / "state.json"),
        "event_queue": str(runtime_dir / "event-queue.jsonl"),
        "approval_queue": str(runtime_dir / "approval-queue.json"),
        "provider_capabilities": str(runtime_dir / "provider-capabilities.json"),
    }
    Path(paths["activity_log"]).write_text("", encoding="utf-8")
    Path(paths["event_queue"]).write_text("", encoding="utf-8")
    Path(paths["approval_queue"]).write_text(
        json.dumps({"pending": [], "history": []}) + "\n",
        encoding="utf-8",
    )
    Path(paths["provider_capabilities"]).write_text(
        json.dumps({"providers": {}, "agent_adapters": {}}) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "planning-state.json").write_text("{}\n", encoding="utf-8")

    config = json.loads(
        (ORCHESTRATOR / "config.json").read_text(encoding="utf-8")
    )
    config["paths"] = paths
    config["task_state_store"] = {
        "mode": "authoritative",
        "event_log": str(journal),
    }
    config["coordination"] = {"enabled": False}
    config["assistant_dev_bridge"] = {"enabled": False}
    config["github_bus"] = {"enabled": False}
    config["chair_review"] = {"enabled": False}
    config["auto_commit_archive"] = {"enabled": False}
    config["worker_worktrees"] = {"enabled": False}
    config["worker_worktree_cleanup"] = {"enabled": False}
    config["worker_worktree_housekeeping"] = {"enabled": False}
    config["ready_dispatcher"] = {
        "enabled": False,
        "active_worker_statuses": [],
        "ownerless_in_progress": {"enabled": False},
    }
    config["supervisor"] = {
        **(config.get("supervisor") or {}),
        "auto_refresh_provider_capabilities": False,
    }

    command_binding = command_runtime_binding()
    queue_events: list[dict[str, Any]] = []
    runtime_state = supervisor_module.load_runtime_state(config)
    for spec in specs:
        if not spec["uses_worker_lease"]:
            continue
        run_id = f"bench-command-{spec['index']:04d}"
        workspace_path = workspace / "worker-leases" / run_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        runtime_state.setdefault("workers", {})[run_id] = {
            "run_id": run_id,
            "task_id": spec["task_id"],
            "logical_agent_id": "codex2",
            "agent_id": "codex2",
            "provider": "codex2",
            # ``fallback`` is a valid active command lease that the full cycle
            # deliberately leaves alone without inventing a live PID or queue
            # delivery. That keeps the benchmark identity truthful.
            "status": "fallback",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "status_root": str(workspace),
            "workspace_path": str(workspace_path),
            "status_command_runtime": command_binding,
            "request_snapshot": {
                "reason": "review_ready_dispatch",
                "metadata": {
                    "workspace_task_id": spec["task_id"],
                    "status_command_runtime": command_binding,
                },
            },
        }
        runtime_state.setdefault("worker_worktrees", {}).setdefault(
            "leases", {}
        )[spec["task_id"]] = {
            "task_id": spec["task_id"],
            "run_id": run_id,
            "path": str(workspace_path),
            "status_root": str(workspace),
        }
        spec["run_id"] = run_id
        spec["workspace_path"] = str(workspace_path)

    supervisor_module.replace_event_queue(config, queue_events)
    supervisor_module.save_runtime_state(config, runtime_state)
    return config, command_binding


def _full_supervisor_loop(
    config: dict[str, Any],
    planning_state_file: str,
    stop: Any,
    ready: Any,
    results: Any,
    max_seconds: float,
) -> None:
    intervals: list[dict[str, Any]] = []
    deadline = time.monotonic() + max_seconds
    supervisor_module.PLANNING_STATE_FILE = Path(planning_state_file)
    ready.set()
    error = None
    while not stop.is_set() and time.monotonic() < deadline:
        started = time.monotonic()
        try:
            supervisor_module.run_once(
                config,
                watch=False,
                quiet=True,
                once=True,
            )
        except BaseException as exc:  # pragma: no cover - reported to parent
            error = f"{type(exc).__name__}: {exc}"
            break
        intervals.append(
            {
                "started": started,
                "finished": time.monotonic(),
            }
        )
    results.put({"intervals": intervals, "error": error})


def _governed_command(
    workspace: str,
    journal: str,
    spec: dict[str, Any],
    command_binding: dict[str, str],
    results: Any,
) -> None:
    env = dict(os.environ)
    for key in (
        "ORCH_RUN_ID",
        "ORCH_TASK_ID",
        "PANTHEON_WORKTREE_ROOT",
        "ORCH_WORKSPACE_PATH",
        "ORCH_RUNNER_STATUS_PATH",
        "ORCH_HEARTBEAT_PATH",
        "PANTHEON_COMMAND_ROOT",
        "PANTHEON_COMMAND_RUNTIME_SHA",
        "PANTHEON_COMMAND_REMOTE",
        "PANTHEON_COMMAND_BASE_REF",
        "PANTHEON_STATUS_COMMAND_ROOT",
        "PANTHEON_STATUS_COMMAND_SHA",
        "PANTHEON_STATUS_COMMAND_REMOTE",
        "PANTHEON_STATUS_COMMAND_BASE_REF",
    ):
        env.pop(key, None)
    env.update(
        {
            "AI_NAME": spec["actor"],
            "PANTHEON_STATUS_ROOT": workspace,
            "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
            "PANTHEON_TASK_STATE_EVENT_LOG": journal,
        }
    )
    command_root = command_binding["command_root"]
    if spec["uses_worker_lease"]:
        env.update(
            {
                "ORCH_RUN_ID": spec["run_id"],
                "ORCH_TASK_ID": spec["task_id"],
                "PANTHEON_WORKTREE_ROOT": spec["workspace_path"],
                "ORCH_WORKSPACE_PATH": spec["workspace_path"],
                "PANTHEON_COMMAND_ROOT": command_root,
                "PANTHEON_COMMAND_RUNTIME_SHA": command_binding["source_sha"],
                "PANTHEON_COMMAND_REMOTE": command_binding["remote"],
                "PANTHEON_COMMAND_BASE_REF": command_binding["base_ref"],
            }
        )

    started = time.monotonic()
    proc = subprocess.run(
        [
            str(Path(command_root) / "scripts" / "ai-status.sh"),
            spec["command"],
            *spec["args"],
        ],
        cwd=command_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    results.put(
        {
            "index": spec["index"],
            "command": spec["command"],
            "actor": spec["actor"],
            "uses_worker_lease": spec["uses_worker_lease"],
            "started": started,
            "finished": time.monotonic(),
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip()[-500:],
            "stdout": (proc.stdout or "").strip()[-500:],
        }
    )


def _governed_command_loop(
    workspace: str,
    journal: str,
    specs: list[dict[str, Any]],
    command_binding: dict[str, str],
    results: Any,
) -> None:
    for spec in specs:
        _governed_command(
            workspace,
            journal,
            spec,
            command_binding,
            results,
        )


def measure_governed_contention(
    workspace: Path,
    journal: Path,
    status_file: Path,
    *,
    specs: list[dict[str, Any]],
    cycle_seconds: float,
) -> dict[str, Any]:
    """Run real governed commands against the full ``run_once`` cycle."""

    config, command_binding = prepare_full_supervisor_fixture(
        workspace,
        journal,
        status_file,
        specs,
    )
    context = multiprocessing.get_context("fork")
    stop = context.Event()
    ready = context.Event()
    supervisor_results: Any = context.Queue()
    command_results: Any = context.Queue()
    supervisor = context.Process(
        target=_full_supervisor_loop,
        args=(
            config,
            str(workspace / ".orchestrator" / "planning-state.json"),
            stop,
            ready,
            supervisor_results,
            cycle_seconds,
        ),
    )
    supervisor.start()
    if not ready.wait(10):
        supervisor.terminate()
        supervisor.join(timeout=5)
        raise RuntimeError("full supervisor cycle did not start")
    time.sleep(0.1)

    worker_slots = sorted({int(spec["worker_slot"]) for spec in specs})
    commands = [
        context.Process(
            target=_governed_command_loop,
            args=(
                str(workspace),
                str(journal),
                [
                    spec
                    for spec in specs
                    if int(spec["worker_slot"]) == worker_slot
                ],
                command_binding,
                command_results,
            ),
        )
        for worker_slot in worker_slots
    ]
    for process in commands:
        process.start()
    for process in commands:
        process.join(timeout=cycle_seconds + 120)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            raise RuntimeError("governed command exceeded benchmark timeout")
    stop.set()
    supervisor.join(timeout=cycle_seconds + 30)
    if supervisor.is_alive():
        supervisor.terminate()
        supervisor.join(timeout=5)
        raise RuntimeError("full supervisor cycle exceeded benchmark timeout")

    command_rows = sorted(
        (command_results.get() for _ in specs),
        key=lambda item: item["index"],
    )
    failed = [row for row in command_rows if row["returncode"] != 0]
    if failed:
        raise RuntimeError(f"governed command failed: {failed[0]}")
    supervisor_row = supervisor_results.get()
    if supervisor_row.get("error"):
        raise RuntimeError(
            f"full supervisor cycle failed: {supervisor_row['error']}"
        )
    intervals = supervisor_row["intervals"]
    if not intervals:
        raise RuntimeError("full supervisor cycle completed no run_once call")

    durations = sorted(
        row["finished"] - row["started"] for row in command_rows
    )
    index = max(
        0,
        min(len(durations) - 1, int(round(0.95 * (len(durations) - 1)))),
    )
    overlap = any(
        command["started"] < interval["finished"]
        and command["finished"] > interval["started"]
        for command in command_rows
        for interval in intervals
    )
    final_snapshot = store.load_snapshot(journal)
    final_projection = json.loads(status_file.read_text(encoding="utf-8"))
    exact_projection = (
        store.sha256_json(final_projection)
        == final_snapshot["state_sha256"]
    )
    return {
        "label": "current",
        "harness": "real_governed_commands_full_run_once",
        "concurrent_workers": len(worker_slots),
        "commands": len(durations),
        "command_mix": {
            command: sum(
                1 for row in command_rows if row["command"] == command
            )
            for command in ("approve", "assign", "note", "reopen")
        },
        "worker_lease_bound_commands": sum(
            1 for row in command_rows if row["uses_worker_lease"]
        ),
        "candidate_runtime": {
            "source_sha": command_binding["source_sha"],
            "base_ref": command_binding["base_ref"],
            "executable_paths_clean": True,
        },
        "full_run_once_cycles": len(intervals),
        "full_run_once_seconds": [
            round(interval["finished"] - interval["started"], 3)
            for interval in intervals
        ],
        "supervisor_active_during_commands": overlap,
        "p50_seconds": round(statistics.median(durations), 3),
        "p95_seconds": round(durations[index], 3),
        "max_seconds": round(durations[-1], 3),
        "command_latencies": [
            {
                "index": row["index"],
                "command": row["command"],
                "seconds": round(row["finished"] - row["started"], 3),
            }
            for row in command_rows
        ],
        "final_event_count": final_snapshot["event_count"],
        "exact_projection": exact_projection,
        "all_commands_succeeded": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace) if args.workspace else Path(
        tempfile.mkdtemp(prefix="task-state-lock-latency-")
    )
    journal = workspace / "runtime" / "task-state-events.jsonl"
    try:
        specs = governed_command_specs(
            workers=args.contention_workers,
            commands_per_worker=args.contention_commands,
        )
        fixture = build_fixture(
            journal,
            events=args.events,
            task_rows=args.task_rows,
            command_specs=specs,
        )

        # Cold read: no checkpoint exists yet, so this is a full validated replay.
        started = time.monotonic()
        cold = store.load_snapshot(journal)
        cold_seconds = round(time.monotonic() - started, 3)

        legacy = measure("legacy", journal, samples=args.samples, legacy=True)
        current = measure("current", journal, samples=args.samples, legacy=False)

        status_file = workspace / "ai-status.json"
        status_file.write_text(
            json.dumps(
                board(
                    args.task_rows,
                    args.events,
                    command_specs=specs,
                )
            ),
            encoding="utf-8",
        )
        contention = {
            "legacy": measure_contention(
                journal,
                status_file,
                legacy=True,
                workers=args.contention_workers,
                commands_per_worker=args.contention_commands,
                cycle_seconds=args.contention_seconds,
            ),
            "current": measure_governed_contention(
                workspace,
                journal,
                status_file,
                specs=specs,
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
                and contention["current"]["supervisor_active_during_commands"]
                and contention["current"]["exact_projection"]
                and contention["current"]["all_commands_succeeded"]
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
    parser.add_argument("--task-rows", type=int, default=30)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--contention-workers", type=int, default=4)
    parser.add_argument("--contention-commands", type=int, default=2)
    parser.add_argument("--contention-seconds", type=float, default=45.0)
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
