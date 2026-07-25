#!/usr/bin/env python3
"""Wait for BFF readiness while a lifecycle projection is recovering.

The ordinary restart budget remains deliberately short.  A bounded extension is
granted only when the BFF proves that all downstream dependencies are healthy,
the exact deployment is being projected, and the lifecycle controller is in
the known recovery state.  Any other degraded reason fails immediately.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


class ReadinessError(RuntimeError):
    """Raised when bounded readiness recovery cannot be accepted."""


@dataclass(frozen=True)
class RecoveryObservation:
    checkpoint: int
    source_high_watermark: int
    backlog: int
    current_generation: int
    controller_generation: int
    last_poll_at: str
    last_successful_publish_at: str

    @property
    def caught_up(self) -> bool:
        return (
            self.checkpoint == self.source_high_watermark
            and self.backlog == 0
        )

Fetch = Callable[[], tuple[int, dict[str, Any] | None]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise ReadinessError(f"lifecycle_projector.{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ReadinessError(
            f"lifecycle_projector.{key} must be an integer"
        ) from exc
    if parsed < 0:
        raise ReadinessError(
            f"lifecycle_projector.{key} must be non-negative"
        )
    return parsed


def classify_readiness(
    status: int,
    payload: dict[str, Any] | None,
    *,
    expected_deployment_sha: str,
) -> tuple[str, RecoveryObservation | None]:
    if payload is None:
        return "unavailable", None

    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ReadinessError("readiness payload has no dependency object")
    projector = dependencies.get("lifecycle_projector")
    if not isinstance(projector, dict):
        raise ReadinessError("readiness payload has no lifecycle projector")

    deployment_sha = str(projector.get("deployment_sha") or "").strip()
    if deployment_sha != expected_deployment_sha:
        raise ReadinessError(
            "lifecycle projector deployment mismatch: "
            f"expected {expected_deployment_sha}, got {deployment_sha or '<empty>'}"
        )
    freshness = projector.get("freshness")
    if (
        not isinstance(freshness, dict)
        or freshness.get("stale") is not False
    ):
        raise ReadinessError("lifecycle projector freshness is stale or absent")

    if status == 200 and payload.get("ready") is True:
        for dependency in ("runtime_manager", "governance", "deployment"):
            dependency_payload = dependencies.get(dependency)
            if (
                not isinstance(dependency_payload, dict)
                or dependency_payload.get("status") != "ok"
            ):
                raise ReadinessError(
                    f"HTTP 200 readiness has unhealthy dependency: {dependency}"
                )
        if (
            projector.get("ready") is not True
            or projector.get("status") != "ok"
            or projector.get("worker_status") != "ready"
            or projector.get("controller_status") != "ready"
            or projector.get("mode") != "live"
            or projector.get("accepted_live") is not True
            or list(projector.get("reasons") or [])
        ):
            raise ReadinessError(
                "HTTP 200 readiness did not contain accepted live projector state"
            )
        checkpoint = _integer(projector, "checkpoint")
        source_high_watermark = _integer(
            projector,
            "source_high_watermark",
        )
        backlog = _integer(projector, "backlog")
        if checkpoint != source_high_watermark or backlog != 0:
            raise ReadinessError(
                "HTTP 200 readiness projector is not caught up"
            )
        return "ready", None

    for dependency in ("runtime_manager", "governance", "deployment"):
        dependency_payload = dependencies.get(dependency)
        if (
            not isinstance(dependency_payload, dict)
            or dependency_payload.get("status") != "ok"
        ):
            raise ReadinessError(
                f"unexpected degraded dependency during recovery: {dependency}"
            )

    reasons = {str(reason) for reason in list(projector.get("reasons") or [])}
    allowed_reasons = {
        "controller_not_ready:recovering",
        "worker_not_ready:recovering",
        "live_truth_not_accepted:recovery:false",
    }
    if (
        status != 503
        or payload.get("ready") is not False
        or projector.get("ready") is not False
        or projector.get("mode") != "recovery"
        or projector.get("accepted_live") is not False
        or projector.get("controller_status") not in {"ready", "recovering"}
        or projector.get("worker_status") not in {"ready", "recovering"}
        or not reasons
        or "live_truth_not_accepted:recovery:false" not in reasons
        or not reasons.issubset(allowed_reasons)
    ):
        raise ReadinessError(
            "unexpected BFF readiness degradation: "
            + ",".join(sorted(reasons or {"<no-reason>"}))
        )

    checkpoint = _integer(projector, "checkpoint")
    source_high_watermark = _integer(projector, "source_high_watermark")
    backlog = _integer(projector, "backlog")
    if checkpoint > source_high_watermark:
        raise ReadinessError(
            "lifecycle projector checkpoint exceeds source high watermark"
        )
    if backlog != source_high_watermark - checkpoint:
        raise ReadinessError(
            "lifecycle projector backlog does not match source minus checkpoint"
        )

    return (
        "recovering",
        RecoveryObservation(
            checkpoint=checkpoint,
            source_high_watermark=source_high_watermark,
            backlog=backlog,
            current_generation=_integer(projector, "current_generation"),
            controller_generation=_integer(
                projector,
                "controller_generation",
            ),
            last_poll_at=str(projector.get("last_poll_at") or ""),
            last_successful_publish_at=str(
                projector.get("last_successful_publish_at") or ""
            ),
        ),
    )


def wait_for_readiness(
    fetch: Fetch,
    *,
    expected_deployment_sha: str,
    initial_timeout_seconds: float,
    recovery_extension_seconds: float,
    stalled_timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Clock = time.monotonic,
    sleep: Sleeper = time.sleep,
) -> None:
    started_at = monotonic()
    initial_deadline = started_at + initial_timeout_seconds
    recovery_deadline = initial_deadline + recovery_extension_seconds
    recovery_seen = False
    last_observation: RecoveryObservation | None = None
    last_progress_at = started_at

    while True:
        now = monotonic()
        status, payload = fetch()
        state, observation = classify_readiness(
            status,
            payload,
            expected_deployment_sha=expected_deployment_sha,
        )
        if state == "ready":
            return
        if state == "recovering":
            assert observation is not None
            recovery_seen = True
            if last_observation is not None and (
                observation.checkpoint < last_observation.checkpoint
                or observation.source_high_watermark
                < last_observation.source_high_watermark
                or observation.backlog > last_observation.backlog
                or observation.current_generation
                < last_observation.current_generation
                or observation.controller_generation
                < last_observation.controller_generation
            ):
                raise ReadinessError(
                    "lifecycle projector recovery regressed"
                )
            progressed = last_observation is None or (
                observation.checkpoint > last_observation.checkpoint
                or observation.backlog < last_observation.backlog
                or observation.current_generation
                > last_observation.current_generation
                or observation.controller_generation
                > last_observation.controller_generation
                or observation.last_successful_publish_at
                != last_observation.last_successful_publish_at
            )
            if progressed:
                last_progress_at = now
            elif (
                not observation.caught_up
                and now - last_progress_at >= stalled_timeout_seconds
            ):
                raise ReadinessError(
                    "lifecycle projector recovery stopped making progress"
            )
            last_observation = observation

        if (
            recovery_seen
            and last_observation is not None
            and not last_observation.caught_up
            and now - last_progress_at >= stalled_timeout_seconds
        ):
            raise ReadinessError(
                "lifecycle projector recovery stopped making progress"
            )
        if now >= initial_deadline and not recovery_seen:
            raise ReadinessError(
                "BFF readiness exceeded the ordinary restart budget without "
                "trusted lifecycle recovery evidence"
            )
        if now >= recovery_deadline:
            raise ReadinessError(
                "BFF lifecycle recovery exceeded the bounded extension"
            )
        sleep(poll_interval_seconds)


def fetch_json(
    url: str,
    *,
    request_timeout_seconds: float,
) -> tuple[int, dict[str, Any] | None]:
    try:
        with urllib.request.urlopen(
            url,
            timeout=request_timeout_seconds,
        ) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (OSError, TimeoutError, urllib.error.URLError):
        return 0, None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None
    if not isinstance(payload, dict):
        return status, None
    return status, payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-deployment-sha", required=True)
    parser.add_argument("--initial-timeout-seconds", type=float, default=120)
    parser.add_argument("--recovery-extension-seconds", type=float, default=120)
    parser.add_argument("--stalled-timeout-seconds", type=float, default=45)
    parser.add_argument("--poll-interval-seconds", type=float, default=2)
    parser.add_argument("--request-timeout-seconds", type=float, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (
        args.initial_timeout_seconds <= 0
        or args.recovery_extension_seconds <= 0
        or args.stalled_timeout_seconds <= 0
        or args.poll_interval_seconds <= 0
        or args.request_timeout_seconds <= 0
    ):
        raise SystemExit("all timeout and interval values must be positive")
    try:
        wait_for_readiness(
            lambda: fetch_json(
                args.url,
                request_timeout_seconds=args.request_timeout_seconds,
            ),
            expected_deployment_sha=args.expected_deployment_sha,
            initial_timeout_seconds=args.initial_timeout_seconds,
            recovery_extension_seconds=args.recovery_extension_seconds,
            stalled_timeout_seconds=args.stalled_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except ReadinessError as exc:
        print(f"BFF lifecycle readiness failed: {exc}")
        return 1
    print(
        "BFF lifecycle readiness accepted for "
        f"{args.expected_deployment_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
