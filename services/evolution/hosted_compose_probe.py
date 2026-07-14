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
import time
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


def _ownership_snapshot(*, expected_sha: str) -> dict[str, Any]:
    cwd = Path.cwd().resolve()
    checkout_sha = _run(["git", "rev-parse", "HEAD"])
    if checkout_sha != expected_sha:
        raise ProbeError(
            f"managed deploy checkout SHA {checkout_sha!r} does not match {expected_sha!r}"
        )

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
        "running": state.get("Running"),
        "health": health.get("Status"),
        "labels": expected_labels,
        "host_source_sha256": host_source_hash,
        "container_source_sha256": container_source_hash,
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
) -> dict[str, Any]:
    started_at = _utc_now()
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

    restart_started_at = _utc_now()
    _run(_compose_args("restart", _SERVICE))
    recovered_state = _wait_for_healthy(timeout_seconds=health_timeout_seconds)
    restart_logs = _run(
        _compose_args("logs", "--no-color", "--since", restart_started_at, _SERVICE)
    )
    fresh_tick_count = restart_logs.count('"tick": 1')
    if fresh_tick_count < 1:
        raise ProbeError("dispatch worker restart logs do not contain a fresh tick 1")

    restart = run_verify_probe(
        api_url=api_url,
        initial=initial,
        timeout_seconds=timeout_seconds,
    )
    after = _ownership_snapshot(expected_sha=expected_sha)
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
        "ownership_before": before,
        "initial_probe": initial,
        "restart": {
            "started_at": restart_started_at,
            "fresh_tick_count": fresh_tick_count,
            "running_after_wait": recovered_state.get("Running"),
            "health_after_wait": recovered_state.get("Health", {}).get("Status"),
            "verify_probe": restart,
        },
        "ownership_after": after,
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = run_compose_probe(
        expected_sha=args.expected_sha,
        api_url=args.api_url,
        prefix=args.prefix,
        timeout_seconds=args.timeout_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
        freeze_observation_seconds=args.freeze_observation_seconds,
        health_timeout_seconds=args.health_timeout_seconds,
    )
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
