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
import urllib.parse
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
            self.checkpoint >= self.source_high_watermark
            and self.backlog == 0
        )

Fetch = Callable[[], tuple[int, dict[str, Any] | None]]
ExactTargetProbe = Callable[[], str]
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
        return "unavailable", None
    projector = dependencies.get("lifecycle_projector")
    if not isinstance(projector, dict):
        return "deployment_pending", None

    deployment_sha = str(projector.get("deployment_sha") or "").strip()
    if deployment_sha != expected_deployment_sha:
        return "deployment_pending", None
    freshness = projector.get("freshness")
    if (
        not isinstance(freshness, dict)
        or freshness.get("stale") is not False
    ):
        if status != 200:
            return "unavailable", None
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
        expected_backlog = max(0, source_high_watermark - checkpoint)
        if backlog != expected_backlog or checkpoint < source_high_watermark:
            # ``telemetry_events`` is a retained source window.  A durable
            # checkpoint may safely exceed its latest retained row, but a
            # checkpoint behind the observed window must report matching
            # backlog and cannot advertise ready truth.
            return "snapshot_inconsistent", None
        return "ready", None

    # A non-200 response is ordinary while the BFF and its projector are
    # restarting.  It can be retried inside the base window, but it must never
    # grant the recovery extension unless every condition below proves the
    # exact deployment's trusted recovery state.
    if status == 200:
        return "unavailable", None

    for dependency in ("runtime_manager", "governance", "deployment"):
        dependency_payload = dependencies.get(dependency)
        if (
            not isinstance(dependency_payload, dict)
            or dependency_payload.get("status") != "ok"
        ):
            return "unavailable", None

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
        return "unavailable", None

    try:
        checkpoint = _integer(projector, "checkpoint")
        source_high_watermark = _integer(projector, "source_high_watermark")
        backlog = _integer(projector, "backlog")
        current_generation = _integer(projector, "current_generation")
        controller_generation = _integer(
            projector,
            "controller_generation",
        )
    except ReadinessError:
        return "unavailable", None
    if backlog != max(0, source_high_watermark - checkpoint):
        return "unavailable", None

    return (
        "recovering",
        RecoveryObservation(
            checkpoint=checkpoint,
            source_high_watermark=source_high_watermark,
            backlog=backlog,
            current_generation=current_generation,
            controller_generation=controller_generation,
            last_poll_at=str(projector.get("last_poll_at") or ""),
            last_successful_publish_at=str(
                projector.get("last_successful_publish_at") or ""
            ),
        ),
    )


def exact_deployment_evidence(
    payload: dict[str, Any] | None,
    *,
    expected_deployment_sha: str,
) -> str:
    """Classify whether a readiness sample proves or contradicts the target.

    A temporarily unavailable response can retain a recent exact observation,
    but an explicit old deployment or stale projector state invalidates it
    immediately.
    """
    if payload is None:
        return "absent"
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        return "absent"
    projector = dependencies.get("lifecycle_projector")
    if not isinstance(projector, dict):
        return "absent"
    deployment_sha = str(projector.get("deployment_sha") or "").strip()
    freshness = projector.get("freshness")
    if deployment_sha and deployment_sha != expected_deployment_sha:
        return "contradicted"
    if isinstance(freshness, dict) and freshness.get("stale") is True:
        return "contradicted"
    if (
        deployment_sha == expected_deployment_sha
        and isinstance(freshness, dict)
        and freshness.get("stale") is False
    ):
        return "exact"
    return "absent"


def wait_for_readiness(
    fetch: Fetch,
    *,
    expected_deployment_sha: str,
    initial_timeout_seconds: float,
    recovery_extension_seconds: float,
    stalled_timeout_seconds: float,
    poll_interval_seconds: float,
    exact_evidence_max_age_seconds: float = 30,
    confirm_exact_target: ExactTargetProbe | None = None,
    monotonic: Clock = time.monotonic,
    sleep: Sleeper = time.sleep,
) -> None:
    started_at = monotonic()
    initial_deadline = started_at + initial_timeout_seconds
    recovery_deadline = initial_deadline + recovery_extension_seconds
    recovery_seen = False
    recovery_progress_seen = False
    last_observation: RecoveryObservation | None = None
    last_progress_at = started_at
    consecutive_ready_samples = 0
    last_exact_evidence_at: float | None = None

    while True:
        now = monotonic()
        status, payload = fetch()
        evidence = exact_deployment_evidence(
            payload,
            expected_deployment_sha=expected_deployment_sha,
        )
        if evidence == "exact":
            last_exact_evidence_at = now
        elif evidence == "contradicted":
            last_exact_evidence_at = None
        state, observation = classify_readiness(
            status,
            payload,
            expected_deployment_sha=expected_deployment_sha,
        )
        if state == "ready":
            consecutive_ready_samples += 1
            if consecutive_ready_samples >= 2:
                return
        else:
            consecutive_ready_samples = 0
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
            progressed = last_observation is not None and (
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
                recovery_progress_seen = True
                last_progress_at = now
            elif last_observation is None:
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
        if now >= initial_deadline:
            recovery_extension_eligible = (
                recovery_seen
                and last_observation is not None
                and (
                    last_observation.caught_up
                    or recovery_progress_seen
                )
            )
            target_probe = (
                confirm_exact_target()
                if (
                    state in {"ready", "unavailable"}
                    and confirm_exact_target is not None
                )
                else "unavailable"
            )
            if target_probe == "contradicted":
                last_exact_evidence_at = None
            identity_bound_extension_eligible = (
                state in {"ready", "unavailable"}
                and evidence != "contradicted"
                and (
                    target_probe == "version_exact"
                    or (
                        target_probe == "live"
                        and last_exact_evidence_at is not None
                        and now - last_exact_evidence_at
                        <= exact_evidence_max_age_seconds
                    )
                )
            )
            if not (
                (state in {"ready", "recovering"} and recovery_extension_eligible)
                or identity_bound_extension_eligible
            ):
                if state == "recovering":
                    if not recovery_seen:
                        raise ReadinessError(
                            "BFF readiness exceeded the ordinary restart budget "
                            "without trusted lifecycle recovery evidence"
                        )
                    if (
                        last_observation is None
                        or (
                            not last_observation.caught_up
                            and not recovery_progress_seen
                        )
                    ):
                        raise ReadinessError(
                            "BFF readiness exceeded the ordinary restart budget "
                            "without monotonic lifecycle recovery convergence"
                        )
                raise ReadinessError(
                    "BFF readiness exceeded the ordinary restart budget "
                    "without current exact-deployment trusted lifecycle "
                    "recovery evidence"
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


def endpoint_url(url: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", "")
    )


def confirm_exact_target(
    *,
    liveness_url: str,
    version_url: str,
    expected_deployment_sha: str,
    request_timeout_seconds: float,
) -> str:
    version_status, version_payload = fetch_json(
        version_url,
        request_timeout_seconds=request_timeout_seconds,
    )
    if version_status == 200 and isinstance(version_payload, dict):
        # An explicit version response is authoritative. Never mask a wrong or
        # missing source identity with a healthy liveness response.
        return (
            "version_exact"
            if str(version_payload.get("source_commit_sha") or "").strip()
            == expected_deployment_sha
            else "contradicted"
        )
    liveness_status, liveness_payload = fetch_json(
        liveness_url,
        request_timeout_seconds=request_timeout_seconds,
    )
    return (
        "live"
        if liveness_status == 200
        and isinstance(liveness_payload, dict)
        and liveness_payload.get("live") is True
        else "unavailable"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-deployment-sha", required=True)
    parser.add_argument("--initial-timeout-seconds", type=float, default=120)
    parser.add_argument("--recovery-extension-seconds", type=float, default=120)
    parser.add_argument("--stalled-timeout-seconds", type=float, default=45)
    parser.add_argument("--poll-interval-seconds", type=float, default=2)
    parser.add_argument("--request-timeout-seconds", type=float, default=2)
    parser.add_argument("--exact-evidence-max-age-seconds", type=float, default=30)
    parser.add_argument("--liveness-url", default="")
    parser.add_argument("--version-url", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (
        args.initial_timeout_seconds <= 0
        or args.recovery_extension_seconds <= 0
        or args.stalled_timeout_seconds <= 0
        or args.poll_interval_seconds <= 0
        or args.request_timeout_seconds <= 0
        or args.exact_evidence_max_age_seconds <= 0
    ):
        raise SystemExit("all timeout and interval values must be positive")
    liveness_url = args.liveness_url or endpoint_url(args.url, "/livez")
    version_url = args.version_url or endpoint_url(args.url, "/bff/version")
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
            exact_evidence_max_age_seconds=args.exact_evidence_max_age_seconds,
            confirm_exact_target=lambda: confirm_exact_target(
                liveness_url=liveness_url,
                version_url=version_url,
                expected_deployment_sha=args.expected_deployment_sha,
                request_timeout_seconds=args.request_timeout_seconds,
            ),
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
