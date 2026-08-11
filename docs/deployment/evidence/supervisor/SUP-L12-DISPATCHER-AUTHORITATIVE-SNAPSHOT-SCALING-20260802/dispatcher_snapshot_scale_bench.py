#!/usr/bin/env python3
"""Benchmark guarded L12 admission against a scratch clone of the live-scale journal.

The source journal is held under its shared store lock only while a copy-on-write
clone and checkpoint copy are captured. Reflink is preferred and a physical
copy is used when unsupported. All validation and dispatcher work then runs
against scratch paths; neither source file is opened for writing.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[5]
DISPATCHER_PATH = REPO_ROOT / "scripts" / "dispatch_twelve_loop_gap_2026_07_26.py"
CATALOG_PATH = (
    REPO_ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-31-l12-current-gap-supervisor-dispatch"
    / "guarded-remediation-tasks.json"
)
IMPLEMENTATION_PATHS = [
    "scripts/dispatch_twelve_loop_gap_2026_07_26.py",
    "scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py",
    ".orchestrator/rewrite/task_state_store.py",
    ".orchestrator/rewrite/test_task_state_store.py",
]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _file_surface(path: Path, *, include_sha256: bool = True) -> dict[str, Any]:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return {"exists": False}
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"benchmark authority path is not regular: {path}")
    return {
        "exists": True,
        "inode": int(info.st_ino),
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
        "sha256": _sha256(path) if include_sha256 else None,
    }


def _read_only_surface(event_log: Path) -> dict[str, Any]:
    checkpoint = event_log.with_name(f"{event_log.name}.checkpoint.json")
    lock = event_log.with_name(f"{event_log.name}.lock")
    return {
        # The journal is already integrity-hashed by load_snapshot; hashing it
        # twice more here would distort the phase timing. Size/inode/stat plus
        # the validated snapshot digest identify the same immutable generation.
        "journal": _file_surface(event_log, include_sha256=False),
        "checkpoint": _file_surface(checkpoint),
        "lock": _file_surface(lock),
        "directory_entries": sorted(path.name for path in event_log.parent.iterdir()),
        "checkpoint_temp_files": sorted(
            path.name
            for path in checkpoint.parent.glob(f"{checkpoint.name}.*.tmp")
        ),
    }


@contextmanager
def _shared_store_lock(event_log: Path):
    lock_path = event_log.with_name(f"{event_log.name}.lock")
    descriptor = os.open(
        lock_path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _clone_generation(source: Path, scratch: Path) -> dict[str, Any]:
    checkpoint = source.with_name(f"{source.name}.checkpoint.json")
    lock = source.with_name(f"{source.name}.lock")
    clone = scratch / source.name
    clone_checkpoint = clone.with_name(f"{clone.name}.checkpoint.json")
    clone_lock = clone.with_name(f"{clone.name}.lock")
    started = time.monotonic()
    with _shared_store_lock(source):
        source_stat = source.stat()
        lock_stat = lock.stat(follow_symlinks=False)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise RuntimeError(f"source task-state lock is not regular: {lock}")
        checkpoint_bytes = checkpoint.read_bytes()
        checkpoint_sha256 = hashlib.sha256(checkpoint_bytes).hexdigest()
        reflink = subprocess.run(
            ["cp", "--reflink=always", "--sparse=auto", str(source), str(clone)],
            capture_output=True,
            text=True,
        )
        if reflink.returncode == 0:
            clone_strategy = "copy_on_write_reflink"
            reflink_rejection = None
        else:
            clone.unlink(missing_ok=True)
            fallback = subprocess.run(
                ["cp", "--reflink=auto", "--sparse=auto", str(source), str(clone)],
                check=True,
                capture_output=True,
                text=True,
            )
            clone_strategy = "reflink_auto_physical_fallback"
            reflink_rejection = reflink.stderr.strip()
        clone_checkpoint.write_bytes(checkpoint_bytes)
        shutil.copy2(lock, clone_lock, follow_symlinks=False)
        if source.stat().st_size != source_stat.st_size:
            raise RuntimeError("source journal changed while its shared lock was held")
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != checkpoint_sha256:
            raise RuntimeError("source checkpoint changed while its shared lock was held")
    checkpoint_payload = _json(clone_checkpoint)
    return {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "clone_strategy": clone_strategy,
        "reflink_always_rejection": reflink_rejection,
        "source_path": str(source),
        "source_device": int(source_stat.st_dev),
        "source_inode": int(source_stat.st_ino),
        "journal_bytes": int(source_stat.st_size),
        "checkpoint_sha256": checkpoint_sha256,
        "checkpoint_prefix_bytes": checkpoint_payload.get("prefix_bytes"),
        "checkpoint_prefix_sha256": checkpoint_payload.get("prefix_sha256"),
        "checkpoint_event_count": checkpoint_payload.get("event_count"),
        "checkpoint_last_event_id": (
            (checkpoint_payload.get("last_event") or {}).get("event_id")
        ),
        "checkpoint_last_event_sha256": (
            (checkpoint_payload.get("last_event") or {}).get("event_sha256")
        ),
        "checkpoint_state_sha256": (
            (checkpoint_payload.get("last_event") or {}).get("state_sha256")
        ),
        "clone_path": str(clone),
        "clone_bytes": clone.stat().st_size,
        "clone_checkpoint_sha256": _sha256(clone_checkpoint),
        "clone_lock_sha256": _sha256(clone_lock),
        "clone_lock_mode": oct(stat.S_IMODE(clone_lock.stat().st_mode)),
    }


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("snapshot_scale_dispatcher", DISPATCHER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import dispatcher: {DISPATCHER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(
    *,
    python: Path,
    live_config: Path,
    command_root: Path,
    status_root: Path,
    readiness_config: Path,
    runtime_state: Path,
    provider_capabilities: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    command = [
        str(python),
        str(DISPATCHER_PATH),
        "--current",
        "--dry-run",
        "--live-config",
        str(live_config),
        "--command-root",
        str(command_root),
        "--readiness-config",
        str(readiness_config),
        "--runtime-state",
        str(runtime_state),
        "--provider-capabilities",
        str(provider_capabilities),
    ]
    environment = {
        **os.environ,
        "PANTHEON_STATUS_ROOT": str(status_root),
    }
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "completed": False,
            "timed_out": True,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout_seconds,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    elapsed = round(time.monotonic() - started, 3)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    output = json.loads(stdout) if completed.returncode == 0 else None
    return {
        "completed": True,
        "timed_out": False,
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout_seconds,
        "returncode": completed.returncode,
        "verdict": "dry_run" if completed.returncode == 0 else "failed_closed",
        "output": output,
        "stderr": stderr,
        "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    candidate_sha = _git("rev-parse", "HEAD")
    subprocess.run(
        ["git", "diff", "--quiet", candidate_sha, "--", *IMPLEMENTATION_PATHS],
        cwd=REPO_ROOT,
        check=True,
    )
    live_config = _json(args.live_config)
    status_file = Path(live_config["paths"]["status_file"]).resolve()
    status_root = status_file.parent
    source_event_log = Path(live_config["task_state_store"]["event_log"]).resolve()
    command_root = args.command_root.resolve()
    readiness_config = (args.readiness_config or command_root / ".orchestrator/config.json").resolve()
    runtime_state = (args.runtime_state or status_root / ".orchestrator/state.json").resolve()
    provider_capabilities = (
        args.provider_capabilities
        or status_root / ".orchestrator/provider_capabilities.json"
    ).resolve()

    with tempfile.TemporaryDirectory(prefix="pantheon-l12-snapshot-scale-") as raw_scratch:
        scratch = Path(raw_scratch)
        input_identity = _clone_generation(source_event_log, scratch)
        clone = Path(input_identity["clone_path"])
        scratch_config = scratch / "live-config.json"
        _write_json(
            scratch_config,
            {
                "paths": {"status_file": str(status_file)},
                "task_state_store": {
                    "mode": "authoritative",
                    "event_log": str(clone),
                },
            },
        )

        dispatcher = _load_dispatcher()
        read_only_surface_before = _read_only_surface(clone)
        snapshot_started = time.monotonic()
        snapshot = dispatcher.load_authoritative_task_snapshot(
            {"mode": "authoritative", "event_log": clone},
            observational=True,
        )
        snapshot_seconds = round(time.monotonic() - snapshot_started, 3)
        snapshot_evidence = dispatcher.authoritative_snapshot_evidence(snapshot)
        snapshot_peak_rss_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)

        catalog = dispatcher.load_json_object(CATALOG_PATH)
        tasks = dispatcher.validate_catalog(catalog)
        dispatcher.validate_catalog_file_binding(CATALOG_PATH, catalog)
        readiness = dispatcher.load_current_readiness_snapshot(
            config_path=readiness_config,
            runtime_state_path=runtime_state,
            provider_capabilities_path=provider_capabilities,
        )
        plan_started = time.monotonic()
        try:
            plan = dispatcher.plan_materialization(
                catalog,
                tasks,
                status_root=status_root,
                state=snapshot["state"],
                readiness=readiness,
            )
            plan_verdict = {
                "verdict": "planned",
                "create_count": len(plan["create"]),
                "exact_count": len(plan["exact"]),
                "deferred_count": len(plan["deferred"]),
            }
        except dispatcher.DispatchError as exc:
            plan_verdict = {"verdict": "failed_closed", "message": str(exc)}
        plan_seconds = round(time.monotonic() - plan_started, 3)

        cli = _run_cli(
            python=args.python.resolve(),
            live_config=scratch_config,
            command_root=command_root,
            status_root=status_root,
            readiness_config=readiness_config,
            runtime_state=runtime_state,
            provider_capabilities=provider_capabilities,
            timeout_seconds=args.target_seconds,
        )
        read_only_surface_after = _read_only_surface(clone)

    cli_output = cli.get("output") or {}
    cli_snapshot = ((cli_output.get("task_state_store") or {}).get("snapshot") or {})
    input_ok = (
        input_identity["journal_bytes"] >= args.minimum_bytes
        and snapshot_evidence["event_count"] >= args.minimum_events
        and input_identity["journal_bytes"] == input_identity["clone_bytes"]
        and input_identity["checkpoint_sha256"]
        == input_identity["clone_checkpoint_sha256"]
    )
    cli_reached_verdict = (
        cli.get("completed") is True and cli.get("returncode") in {0, 2}
    )
    read_only_surface_preserved = (
        read_only_surface_before == read_only_surface_after
    )
    meets_target = bool(
        input_ok
        and snapshot_evidence["checkpoint_used"]
        and cli_reached_verdict
        and read_only_surface_preserved
        and float(cli["elapsed_seconds"]) < args.target_seconds
        and (
            cli.get("returncode") == 2
            or cli_snapshot.get("state_sha256") == snapshot_evidence["state_sha256"]
        )
    )
    return {
        "schema_version": "l12_dispatcher_snapshot_scale_benchmark.v1",
        "candidate": {
            "source_sha": candidate_sha,
            "branch": _git("branch", "--show-current"),
            "repository": "ajoe734/pantheon",
            "dispatcher_sha256": _sha256(DISPATCHER_PATH),
            "catalog_file_sha256": _sha256(CATALOG_PATH),
            "implementation_paths_clean_at_candidate": True,
        },
        "input": input_identity,
        "baseline": {
            "observed_at": "2026-08-02T09:13:00Z",
            "journal_bytes": 2_174_900_966,
            "event_count": 8_632,
            "checkpoint_prefix_sha256": "903d83718159c9b401cc9bdc682abfe213793396c008cb227581804d8902f656",
            "timeout_seconds": 30.0,
            "elapsed_seconds": 30.52,
            "peak_rss_kib": 5_604_472,
            "result": "timed_out_before_catalog_admission",
            "implementation": "load_events_then_project_latest_state",
        },
        "phases": {
            "clone_under_shared_lock_seconds": input_identity["elapsed_seconds"],
            "validated_snapshot_seconds": snapshot_seconds,
            "admission_plan_seconds": plan_seconds,
            "guarded_dry_run_seconds": cli["elapsed_seconds"],
        },
        "snapshot": {
            **snapshot_evidence,
            "peak_rss_kib": snapshot_peak_rss_kib,
        },
        "admission_plan": plan_verdict,
        "guarded_dry_run": cli,
        "target": {
            "minimum_bytes": args.minimum_bytes,
            "minimum_events": args.minimum_events,
            "maximum_guarded_dry_run_seconds": args.target_seconds,
            "requires_checkpoint": True,
            "requires_catalog_admission_verdict": True,
        },
        "meets_target": meets_target,
        "non_interference": {
            "source_event_log_opened_for_write": False,
            "source_checkpoint_opened_for_write": False,
            "source_lock_opened_for_write": False,
            "benchmark_journal": "scratch clone removed after run",
            "scratch_read_only_surface_before": read_only_surface_before,
            "scratch_read_only_surface_after": read_only_surface_after,
            "scratch_read_only_surface_preserved": read_only_surface_preserved,
            "catalog_materialization": "none; dry-run only",
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-config", type=Path, required=True)
    parser.add_argument("--command-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--readiness-config", type=Path)
    parser.add_argument("--runtime-state", type=Path)
    parser.add_argument("--provider-capabilities", type=Path)
    parser.add_argument("--minimum-bytes", type=int, default=2_030_000_000)
    parser.add_argument("--minimum-events", type=int, default=8_400)
    parser.add_argument("--target-seconds", type=float, default=30.0)
    parser.add_argument("--json", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(args)
    _write_json(args.json, report)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report["meets_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
