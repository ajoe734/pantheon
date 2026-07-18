#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMPLEMENTATION_MERGE = "c9560db5cba9583bd2dff70894e583cdca5d2a20"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_digest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": None, "size": 0, "lines": 0}
    data = path.read_bytes()
    return {
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "lines": len(path.read_text(encoding="utf-8").splitlines()),
    }


def git_head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def build_fixture_root() -> tuple[Path, Path, dict[str, Any]]:
    root = Path(tempfile.mkdtemp(prefix="pantheon-watchdog-postmerge-"))
    orch = root / ".orchestrator"
    orch.mkdir(parents=True, exist_ok=True)
    state_file = orch / "state.json"
    activity_log = root / "activity-log.jsonl"
    watchdog_state = orch / "watchdog-state.json"
    metrics = orch / "metrics" / "supervisor-watchdog.jsonl"
    contention = orch / "metrics" / "supervisor-watchdog-contention.jsonl"
    config = {
        "paths": {
            "state_file": str(state_file),
            "activity_log": str(activity_log),
        },
        "watchdog": {
            "state_file": str(watchdog_state),
            "metrics_file": str(metrics),
            "contention_metrics_file": str(contention),
            "heartbeat_stale_seconds": 900,
            "restart_budget_window_seconds": 900,
            "max_restarts_per_window": 2,
            "max_restarts_per_hour": 4,
            "backoff_schedule_seconds": [0, 0, 0],
            "circuit_cooldown_seconds": 1800,
            "safe_mode_seconds": 120,
            "min_disk_free_gb": 2.0,
            "max_disk_used_percent": 95.0,
            "min_memory_available_mb": 512,
            "max_load_1m": 128.0,
            "max_active_workers": 512,
        },
    }
    config_path = orch / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    activity_log.write_text("", encoding="utf-8")
    now = iso_now()
    state_file.write_text(
        json.dumps(
            {
                "supervisor": {
                    "pid": os.getpid(),
                    "last_heartbeat_at": now,
                    "lifecycle": "running",
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (orch / "supervisor.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    return root, config_path, {
        "watchdog_state": watchdog_state,
        "metrics": metrics,
        "contention": contention,
        "runtime_lock": orch / "runtime-admission.lock",
        "supervisor_lock": orch / "supervisor.lock",
        "metric_lock": contention.with_suffix(".lock"),
        "activity_log": activity_log,
    }


def run_batch(
    *,
    repo: Path,
    config_path: Path,
    paths: dict[str, Path],
    label: str,
    hold_metric_lock: bool,
    count: int,
    timeout: float,
) -> dict[str, Any]:
    for key in ("watchdog_state", "metrics", "contention"):
        try:
            paths[key].unlink()
        except FileNotFoundError:
            pass

    runtime_handle = paths["runtime_lock"].open("w", encoding="utf-8")
    fcntl.flock(runtime_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    metric_handle = None
    if hold_metric_lock:
        paths["metric_lock"].parent.mkdir(parents=True, exist_ok=True)
        metric_handle = paths["metric_lock"].open("w", encoding="utf-8")
        fcntl.flock(metric_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    before = {key: file_digest(paths[key]) for key in ("watchdog_state", "metrics", "contention")}
    started = time.monotonic()
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(repo / ".orchestrator" / "supervisor_watchdog.py"),
                "--config",
                str(config_path),
                "--restart",
                "--json",
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(count)
    ]
    # Wait for all processes to finish within the absolute batch deadline
    batch_timeout = timeout
    deadline = started + batch_timeout
    while time.monotonic() < deadline:
        if all(p.poll() is not None for p in processes):
            break
        time.sleep(0.05)

    outputs: list[dict[str, Any]] = []
    for i, process in enumerate(processes):
        if process.poll() is None:
            raise TimeoutError(f"Process {i} did not exit within the absolute batch deadline of {batch_timeout} seconds.")
        stdout, stderr = process.communicate()
        outputs.append(
            {
                "returncode": process.returncode,
                "stdout": json.loads(stdout.decode()) if stdout else None,
                "stderr": stderr.decode(),
            }
        )
    elapsed = time.monotonic() - started

    if metric_handle is not None:
        fcntl.flock(metric_handle.fileno(), fcntl.LOCK_UN)
        metric_handle.close()
    fcntl.flock(runtime_handle.fileno(), fcntl.LOCK_UN)
    runtime_handle.close()

    after = {key: file_digest(paths[key]) for key in ("watchdog_state", "metrics", "contention")}
    decisions: dict[str, int] = {}
    reasons: dict[str, int] = {}
    drops = 0
    lock_held_values: set[str] = set()
    heartbeat_values: list[float] = []
    for output in outputs:
        data = output["stdout"] or {}
        decisions[str(data.get("decision"))] = decisions.get(str(data.get("decision")), 0) + 1
        reasons[str(data.get("reason"))] = reasons.get(str(data.get("reason")), 0) + 1
        if "watchdog contention metric write dropped due to lock contention" in output["stderr"]:
            drops += 1
        lock_held_values.add(str(data.get("lock_held")))
        heartbeat = data.get("heartbeat_age_seconds")
        if isinstance(heartbeat, int | float):
            heartbeat_values.append(float(heartbeat))
        # Assert restart counters are null
        assert data.get("restart_count_window") is None, f"Expected null restart_count_window, got {data.get('restart_count_window')}"
        assert data.get("restart_count_hour") is None, f"Expected null restart_count_hour, got {data.get('restart_count_hour')}"

    summary = {
        "label": label,
        "probe_count": count,
        "elapsed_seconds": round(elapsed, 6),
        "all_returncode_zero": all(output["returncode"] == 0 for output in outputs),
        "terminal_processes": sum(1 for process in processes if process.poll() is not None),
        "decisions": decisions,
        "reasons": reasons,
        "stderr_drop_count": drops,
        "contention_metric_lines": after["contention"]["lines"],
        "metric_events_written_plus_dropped": after["contention"]["lines"] + drops,
        "lock_held_values": sorted(lock_held_values),
        "heartbeat_age_min": min(heartbeat_values) if heartbeat_values else None,
        "heartbeat_age_max": max(heartbeat_values) if heartbeat_values else None,
        "before_hashes": before,
        "after_hashes": after,
        "sample_stdout": outputs[0]["stdout"] if outputs else None,
        "sample_stderr": outputs[0]["stderr"] if outputs else "",
    }
    assert summary["all_returncode_zero"], summary
    assert summary["terminal_processes"] == count, summary
    assert summary["decisions"] == {"skip": count}, summary
    assert summary["reasons"] == {"lock_contention": count}, summary
    assert summary["metric_events_written_plus_dropped"] == count, summary
    assert not after["watchdog_state"]["exists"], summary
    assert not after["metrics"]["exists"], summary
    return summary


def run_post_release_probe(repo: Path, root: Path, config_path: Path, paths: dict[str, Path]) -> dict[str, Any]:
    started = time.monotonic()
    probe = subprocess.run(
        [
            sys.executable,
            str(repo / ".orchestrator" / "supervisor_watchdog.py"),
            "--config",
            str(config_path),
            "--restart",
            "--json",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=5.0,
        check=False,
    )
    health = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "supervisor_runtime_health.py"),
            "--repo",
            str(root),
            "--config",
            str(config_path),
            "--require-watchdog",
            "--json",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=5.0,
        check=False,
    )
    probe_json = json.loads(probe.stdout)
    health_json = json.loads(health.stdout)
    assert probe.returncode == 0, probe.stderr
    assert probe_json["decision"] == "observe_only", probe_json
    assert probe_json["reason"] == "supervisor_healthy", probe_json
    assert health.returncode == 0, health.stderr
    assert health_json["healthy"] is True, health_json

    watchdog_state_hash = file_digest(paths["watchdog_state"])
    metrics_hash = file_digest(paths["metrics"])
    assert watchdog_state_hash["exists"] is True, "Expected watchdog-state.json to exist"
    assert metrics_hash["exists"] is True, "Expected metrics.jsonl to exist"
    assert metrics_hash["lines"] == 1, f"Expected exactly 1 line in metrics.jsonl, got {metrics_hash['lines']}"

    return {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "probe": {
            "returncode": probe.returncode,
            "stdout": probe_json,
            "stderr": probe.stderr,
        },
        "health": {
            "returncode": health.returncode,
            "stdout": health_json,
            "stderr": health.stderr,
        },
        "watchdog_state_hash": watchdog_state_hash,
        "metrics_hash": metrics_hash,
        "activity_log_hash": file_digest(paths["activity_log"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run post-merge watchdog lock-contention fixture in an isolated runtime root.")
    parser.add_argument("--repo", default=".", help="Pantheon repository root containing .orchestrator/supervisor_watchdog.py.")
    parser.add_argument("--count", type=int, default=12, help="Concurrent probe count per contention batch.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-probe communicate timeout in seconds.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    repo_head_sha = git_head(repo)
    intended_sha = os.environ.get("INTENDED_SHA", IMPLEMENTATION_MERGE)
    assert repo_head_sha == intended_sha, f"Expected repo HEAD to be {intended_sha}, got {repo_head_sha}"

    root, config_path, paths = build_fixture_root()
    supervisor_handle = paths["supervisor_lock"].open("w", encoding="utf-8")
    fcntl.flock(supervisor_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        primary = run_batch(
            repo=repo,
            config_path=config_path,
            paths=paths,
            label="primary-runtime-admission-lock-held",
            hold_metric_lock=False,
            count=args.count,
            timeout=args.timeout,
        )
        secondary = run_batch(
            repo=repo,
            config_path=config_path,
            paths=paths,
            label="primary-and-contention-metric-lock-held",
            hold_metric_lock=True,
            count=args.count,
            timeout=args.timeout,
        )
        post_release = run_post_release_probe(repo, root, config_path, paths)
    finally:
        fcntl.flock(supervisor_handle.fileno(), fcntl.LOCK_UN)
        supervisor_handle.close()

    print(
        json.dumps(
            {
                "generated_at": iso_now(),
                "fixture_root": str(root),
                "repo_head": git_head(repo),
                "implementation_merge": IMPLEMENTATION_MERGE,
                "config_path": str(config_path),
                "primary_batch": primary,
                "secondary_metric_lock_batch": secondary,
                "post_release_probe": post_release,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
