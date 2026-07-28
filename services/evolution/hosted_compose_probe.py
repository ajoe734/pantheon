"""Compose ownership + API acceptance probe for EVOLOOP-001 hosted dev.

This wrapper is intended to run on the dev VM from the managed exact-ref
deploy worktree. It verifies Docker Compose ownership and source identity,
runs the API-only initial probe, restarts only the dispatch worker through
Compose, waits for health recovery and a fresh tick, then performs read-only
restart verification. Its JSON output contains no credentials or environment
values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.evolution.hosted_dispatch_probe import (
    ProbeError,
    run_initial_probe,
    run_verify_probe,
)


_SERVICE = "evolution-dispatch-worker"
_SOURCE_PATH = Path("services/evolution/dispatch_worker.py")
_PROBE_SOURCE_PATHS = (
    Path("services/evolution/hosted_compose_probe.py"),
    Path("services/evolution/hosted_dispatch_probe.py"),
)
_TASK_RUNTIME_SCOPE = (
    Path("docker-compose.yml"),
    Path("services/evolution/Dockerfile"),
    Path("services/evolution/requirements.txt"),
    _SOURCE_PATH,
    *_PROBE_SOURCE_PATHS,
)
_ALLOWED_RUNTIME_DIRTY_PREFIXES = (
    ".orchestrator/metrics/",
    ".orchestrator/task-briefs/",
)
_ALLOWED_RUNTIME_DIRTY_PATHS = {
    ".orchestrator/watchdog-state.json",
    "trade_journey_events.json",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _compose_args(*args: str) -> list[str]:
    return ["docker", "compose", "-p", "pantheon", "-f", "docker-compose.yml", *args]


def _container_source_hash(container_id: str) -> str:
    script = (
        "import hashlib,pathlib; "
        "print(hashlib.sha256(pathlib.Path('/workspace/services/evolution/"
        "dispatch_worker.py').read_bytes()).hexdigest())"
    )
    return _run(["docker", "exec", container_id, "python", "-c", script])


def _status_path(line: str) -> str:
    # _run() strips the command's leading whitespace, so the first porcelain
    # line may arrive as either `` M path`` or ``M path``. Preserve the path's
    # leading dot in both forms.
    if len(line) >= 3 and line[2] == " ":
        path = line[3:]
    elif len(line) >= 2 and line[1] == " ":
        path = line[2:]
    else:
        path = line
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1]
    return path.strip().strip('"')


def _runtime_scope_snapshot() -> dict[str, Any]:
    scope = [str(path) for path in _TASK_RUNTIME_SCOPE]
    scoped_status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *scope]
    )
    if scoped_status:
        raise ProbeError(f"task runtime scope is dirty: {scoped_status.splitlines()}")

    full_status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"]
    )
    dirty_paths = [_status_path(line) for line in full_status.splitlines() if line]
    unexpected = [
        path
        for path in dirty_paths
        if path not in _ALLOWED_RUNTIME_DIRTY_PATHS
        and not any(path.startswith(prefix) for prefix in _ALLOWED_RUNTIME_DIRTY_PREFIXES)
    ]
    if unexpected:
        raise ProbeError(f"managed deploy worktree has unexpected dirty paths: {unexpected}")
    return {
        "task_runtime_scope": scope,
        "task_scope_clean": True,
        "full_worktree_clean": not dirty_paths,
        "allowed_runtime_dirty_paths": dirty_paths,
        "unexpected_dirty_paths": [],
    }


def _probe_source_provenance(*, expected_sha: str) -> list[dict[str, str]]:
    provenance: list[dict[str, str]] = []
    for path in _PROBE_SOURCE_PATHS:
        expected_blob = _run(["git", "rev-parse", f"{expected_sha}:{path}"])
        actual_blob = _run(["git", "hash-object", str(path)])
        if actual_blob != expected_blob:
            raise ProbeError(f"probe source {path} does not match exact-ref git blob")
        provenance.append(
            {
                "path": str(path),
                "git_commit_sha": expected_sha,
                "git_blob_sha": actual_blob,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return provenance


def _bff_source_snapshot(*, url: str, expected_sha: str) -> dict[str, str]:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ProbeError("BFF version endpoint returned a non-object payload")
    actual = payload.get("source_commit_sha")
    if actual != expected_sha:
        raise ProbeError(f"BFF source SHA {actual!r} does not match {expected_sha!r}")
    return {"observed_at": _utc_now(), "url": url, "source_commit_sha": actual}


def _worker_log_events(logs: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in logs.splitlines():
        object_start = line.find("{")
        if object_start < 0:
            continue
        try:
            event = json.loads(line[object_start:])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("tick"), int):
            events.append(event)
    return events


def _decision_event_count(
    events: list[dict[str, Any]],
    *,
    decision_id: str,
    dispositions: set[str],
) -> int:
    count = 0
    for event in events:
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        items = result.get("items")
        if not isinstance(items, list):
            continue
        count += sum(
            1
            for item in items
            if isinstance(item, dict)
            and item.get("decision_id") == decision_id
            and item.get("disposition") in dispositions
        )
    return count


def _validated_restart_events(logs: str) -> tuple[list[dict[str, Any]], list[int]]:
    events = _worker_log_events(logs)
    tick_values = [event["tick"] for event in events]
    fresh_tick_count = sum(tick == 1 for tick in tick_values)
    if not tick_values or tick_values[0] != 1 or fresh_tick_count != 1:
        raise ProbeError(
            f"dispatch worker restart logs do not begin with one exact tick 1: {tick_values}"
        )
    return events, tick_values


def _ownership_snapshot(*, expected_sha: str) -> dict[str, Any]:
    cwd = Path.cwd().resolve()
    checkout_sha = _run(["git", "rev-parse", "HEAD"])
    if checkout_sha != expected_sha:
        raise ProbeError(
            f"managed deploy checkout SHA {checkout_sha!r} does not match {expected_sha!r}"
        )
    worktree = _runtime_scope_snapshot()

    services = _run(_compose_args("config", "--services")).splitlines()
    if _SERVICE not in services:
        raise ProbeError(f"Compose config does not own {_SERVICE}")
    container_id = _run(_compose_args("ps", "-q", _SERVICE))
    if not container_id or "\n" in container_id:
        raise ProbeError(f"Compose returned invalid container id for {_SERVICE}")

    inspect_payload = json.loads(_run(["docker", "inspect", container_id]))
    if not isinstance(inspect_payload, list) or len(inspect_payload) != 1:
        raise ProbeError("docker inspect returned an invalid payload")
    inspect = inspect_payload[0]
    labels = inspect.get("Config", {}).get("Labels", {})
    expected_labels = {
        "com.docker.compose.project": "pantheon",
        "com.docker.compose.service": _SERVICE,
        "com.docker.compose.project.working_dir": str(cwd),
        "com.docker.compose.project.config_files": str(cwd / "docker-compose.yml"),
    }
    for key, expected in expected_labels.items():
        if labels.get(key) != expected:
            raise ProbeError(
                f"container label {key}={labels.get(key)!r}; expected {expected!r}"
            )

    config_hash = labels.get("com.docker.compose.config-hash")
    rendered_hash_parts = _run(
        _compose_args("config", "--hash", _SERVICE)
    ).split()
    if len(rendered_hash_parts) != 2 or rendered_hash_parts[0] != _SERVICE:
        raise ProbeError("Compose returned an invalid service config hash")
    if not config_hash or config_hash != rendered_hash_parts[1]:
        raise ProbeError(
            f"container config hash {config_hash!r} does not match "
            f"rendered Compose hash {rendered_hash_parts[1]!r}"
        )

    host_source_hash = hashlib.sha256(_SOURCE_PATH.read_bytes()).hexdigest()
    container_source_hash = _container_source_hash(container_id)
    if container_source_hash != host_source_hash:
        raise ProbeError(
            "container dispatch_worker.py hash does not match exact-ref checkout"
        )

    state = inspect.get("State", {})
    health = state.get("Health", {})
    return {
        "observed_at": _utc_now(),
        "checkout_sha": checkout_sha,
        "working_dir": str(cwd),
        "compose_config_file": str(cwd / "docker-compose.yml"),
        "container_id": container_id,
        "container_name": str(inspect.get("Name") or "").lstrip("/"),
        "image_id": inspect.get("Image"),
        "container_command": inspect.get("Config", {}).get("Cmd"),
        "container_started_at": state.get("StartedAt"),
        "running": state.get("Running"),
        "health": health.get("Status"),
        "labels": {
            **expected_labels,
            "com.docker.compose.config-hash": config_hash,
        },
        "rendered_service_config_hash": rendered_hash_parts[1],
        "host_source_sha256": host_source_hash,
        "container_source_sha256": container_source_hash,
        "worktree": worktree,
    }


def _wait_for_healthy(*, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() <= deadline:
        container_id = _run(_compose_args("ps", "-q", _SERVICE))
        inspect = json.loads(_run(["docker", "inspect", container_id]))[0]
        last = inspect.get("State", {})
        if last.get("Running") and last.get("Health", {}).get("Status") == "healthy":
            return last
        time.sleep(2)
    raise ProbeError(f"dispatch worker did not recover healthy state: {last}")


def run_compose_probe(
    *,
    expected_sha: str,
    api_url: str,
    prefix: str,
    timeout_seconds: float,
    poll_timeout_seconds: float,
    freeze_observation_seconds: float,
    health_timeout_seconds: float,
    bff_version_url: str,
    requested_ref: str,
    deploy_run_id: str,
    deploy_run_attempt: str,
    deploy_run_url: str,
    invocation: list[str],
) -> dict[str, Any]:
    started_at = _utc_now()
    source_provenance = _probe_source_provenance(expected_sha=expected_sha)
    bff_before = _bff_source_snapshot(url=bff_version_url, expected_sha=expected_sha)
    before = _ownership_snapshot(expected_sha=expected_sha)
    if not before["running"] or before["health"] != "healthy":
        raise ProbeError(f"dispatch worker is not healthy before probe: {before}")

    initial = run_initial_probe(
        api_url=api_url,
        prefix=prefix,
        timeout_seconds=timeout_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        poll_interval_seconds=2,
        freeze_observation_seconds=freeze_observation_seconds,
    )
    initial_logs = _run(
        _compose_args("logs", "--no-color", "--since", started_at, _SERVICE)
    )
    initial_events = _worker_log_events(initial_logs)
    research_id = initial["research"]["decision_id"]
    freeze_id = initial["freeze"]["decision_id"]
    initial_research_dispatch_count = _decision_event_count(
        initial_events,
        decision_id=research_id,
        dispositions={"executed", "already_executed"},
    )
    initial_freeze_skip_count = _decision_event_count(
        initial_events,
        decision_id=freeze_id,
        dispositions={"unsupported"},
    )
    if initial_research_dispatch_count != 1:
        raise ProbeError(
            "worker logs do not contain exactly one initial research dispatch"
        )
    if initial_freeze_skip_count < 1:
        raise ProbeError("worker logs do not contain the active-live freeze skip")

    restart_started_at = _utc_now()
    _run(_compose_args("restart", _SERVICE))
    recovered_state = _wait_for_healthy(timeout_seconds=health_timeout_seconds)
    container_started_at = recovered_state.get("StartedAt")
    if not isinstance(container_started_at, str) or not container_started_at:
        raise ProbeError("restarted worker lacks Docker StartedAt evidence")
    restart_logs = _run(
        _compose_args("logs", "--no-color", "--since", container_started_at, _SERVICE)
    )
    restart_events, tick_values = _validated_restart_events(restart_logs)
    fresh_tick_count = 1
    restart_research_dispatch_count = _decision_event_count(
        restart_events,
        decision_id=research_id,
        dispositions={"executed"},
    )
    restart_freeze_skip_count = _decision_event_count(
        restart_events,
        decision_id=freeze_id,
        dispositions={"unsupported"},
    )
    if restart_research_dispatch_count != 0:
        raise ProbeError("research decision was dispatched again after restart")
    if restart_freeze_skip_count != 0:
        raise ProbeError("dead-lettered freeze was dispatched again after restart")

    restart = run_verify_probe(
        api_url=api_url,
        initial=initial,
        timeout_seconds=timeout_seconds,
    )
    after = _ownership_snapshot(expected_sha=expected_sha)
    bff_after = _bff_source_snapshot(url=bff_version_url, expected_sha=expected_sha)
    if before["container_id"] != after["container_id"]:
        raise ProbeError("Compose restart unexpectedly replaced the container identity")
    for field in (
        "checkout_sha",
        "working_dir",
        "compose_config_file",
        "labels",
        "host_source_sha256",
        "container_source_sha256",
    ):
        if before[field] != after[field]:
            raise ProbeError(f"ownership/source field changed during probe: {field}")

    return {
        "schema_version": "evoloop-001-hosted-compose-probe.v1",
        "started_at": started_at,
        "completed_at": _utc_now(),
        "expected_sha": expected_sha,
        "deployment": {
            "requested_ref": requested_ref,
            "resolved_sha": expected_sha,
            "run_id": deploy_run_id,
            "run_attempt": deploy_run_attempt,
            "run_url": deploy_run_url,
        },
        "probe": {
            "invocation": invocation,
            "source_provenance": source_provenance,
            "exit_code": 0,
        },
        "bff_before": bff_before,
        "ownership_before": before,
        "initial_probe": initial,
        "initial_worker_log_evidence": {
            "tick_values": [event["tick"] for event in initial_events],
            "research_dispatch_count": initial_research_dispatch_count,
            "freeze_skip_count": initial_freeze_skip_count,
        },
        "restart": {
            "started_at": restart_started_at,
            "container_started_at": container_started_at,
            "tick_values": tick_values,
            "fresh_tick_count": fresh_tick_count,
            "research_redispatch_count": restart_research_dispatch_count,
            "freeze_skip_count": restart_freeze_skip_count,
            "running_after_wait": recovered_state.get("Running"),
            "health_after_wait": recovered_state.get("Health", {}).get("Status"),
            "verify_probe": restart,
        },
        "ownership_after": after,
        "bff_after": bff_after,
        "assertion_failures": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:18093")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--poll-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--freeze-observation-seconds", type=float, default=65.0)
    parser.add_argument("--health-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--bff-version-url", default="http://127.0.0.1:18001/bff/version")
    parser.add_argument("--requested-ref", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--deploy-run-attempt", required=True)
    parser.add_argument("--deploy-run-url", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    args = _parser().parse_args(raw_argv)
    invocation = [
        "python3",
        "-m",
        "services.evolution.hosted_compose_probe",
        *raw_argv,
    ]
    output = run_compose_probe(
        expected_sha=args.expected_sha,
        api_url=args.api_url,
        prefix=args.prefix,
        timeout_seconds=args.timeout_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
        freeze_observation_seconds=args.freeze_observation_seconds,
        health_timeout_seconds=args.health_timeout_seconds,
        bff_version_url=args.bff_version_url,
        requested_ref=args.requested_ref,
        deploy_run_id=args.deploy_run_id,
        deploy_run_attempt=args.deploy_run_attempt,
        deploy_run_url=args.deploy_run_url,
        invocation=invocation,
    )
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
