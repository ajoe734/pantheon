from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.wait_for_bff_lifecycle_readiness import (
    ReadinessError,
    classify_readiness,
    wait_for_readiness,
)


SHA = "a" * 40


def payload(
    *,
    ready: bool,
    checkpoint: int = 10,
    source: int = 10,
    reasons: list[str] | None = None,
    deployment_sha: str = SHA,
) -> dict[str, Any]:
    recovering = not ready
    return {
        "ready": ready,
        "dependencies": {
            "runtime_manager": {"status": "ok"},
            "governance": {"status": "ok"},
            "deployment": {"status": "ok"},
            "lifecycle_projector": {
                "ready": ready,
                "status": "ok" if ready else "degraded",
                "worker_status": "ready",
                "controller_status": "ready" if ready else "recovering",
                "mode": "live" if ready else "recovery",
                "accepted_live": ready,
                "deployment_sha": deployment_sha,
                "checkpoint": checkpoint,
                "source_high_watermark": source,
                "backlog": source - checkpoint,
                "current_generation": 12,
                "controller_generation": 12,
                "last_poll_at": f"poll-{checkpoint}",
                "last_successful_publish_at": f"publish-{checkpoint}",
                "freshness": {"stale": False},
                "reasons": reasons
                if reasons is not None
                else (
                    []
                    if ready
                    else [
                        "controller_not_ready:recovering",
                        "live_truth_not_accepted:recovery:false",
                    ]
                ),
            },
        },
    }


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def sequence_fetch(
    values: list[tuple[int, dict[str, Any] | None]],
) -> tuple[Iterator[tuple[int, dict[str, Any] | None]], Any]:
    iterator = iter(values)
    last = values[-1]

    def fetch() -> tuple[int, dict[str, Any] | None]:
        nonlocal iterator
        try:
            return next(iterator)
        except StopIteration:
            return last

    return iterator, fetch


def test_accepts_exact_ready_payload() -> None:
    state, observation = classify_readiness(
        200,
        payload(ready=True),
        expected_deployment_sha=SHA,
    )
    assert state == "ready"
    assert observation is None


def test_uses_bounded_extension_for_trusted_recovery() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [
            (0, None),
            (503, payload(ready=False, checkpoint=8, source=10)),
            (503, payload(ready=False, checkpoint=10, source=10)),
            (503, payload(ready=False, checkpoint=10, source=10)),
            (200, payload(ready=True)),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=2,
        recovery_extension_seconds=4,
        stalled_timeout_seconds=1,
        poll_interval_seconds=1,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 4


def test_unexpected_degraded_reason_does_not_grant_extension() -> None:
    state, observation = classify_readiness(
        503,
        payload(
            ready=False,
            reasons=["dependency_unavailable:postgres"],
        ),
        expected_deployment_sha=SHA,
    )
    assert state == "unavailable"
    assert observation is None


def test_wrong_deployment_is_pending_during_base_window() -> None:
    state, observation = classify_readiness(
        503,
        payload(ready=False, deployment_sha="b" * 40),
        expected_deployment_sha=SHA,
    )
    assert state == "deployment_pending"
    assert observation is None


def test_missing_deployment_is_pending_during_base_window() -> None:
    pending = payload(ready=False)
    pending["dependencies"]["lifecycle_projector"]["deployment_sha"] = ""
    state, observation = classify_readiness(
        503,
        pending,
        expected_deployment_sha=SHA,
    )
    assert state == "deployment_pending"
    assert observation is None


def test_unhealthy_dependency_does_not_grant_extension() -> None:
    degraded = payload(ready=False)
    degraded["dependencies"]["governance"]["status"] = "unavailable"
    state, observation = classify_readiness(
        503,
        degraded,
        expected_deployment_sha=SHA,
    )
    assert state == "unavailable"
    assert observation is None


def test_stale_projector_state_does_not_grant_extension() -> None:
    stale = payload(ready=False)
    stale["dependencies"]["lifecycle_projector"]["freshness"]["stale"] = True
    state, observation = classify_readiness(
        503,
        stale,
        expected_deployment_sha=SHA,
    )
    assert state == "unavailable"
    assert observation is None


def test_fails_when_nonzero_backlog_stalls() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [(503, payload(ready=False, checkpoint=8, source=10))]
    )
    with pytest.raises(ReadinessError, match="stopped making progress"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=1,
            recovery_extension_seconds=10,
            stalled_timeout_seconds=2,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )


def test_fails_fast_when_recovery_regresses() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [
            (503, payload(ready=False, checkpoint=9, source=10)),
            (503, payload(ready=False, checkpoint=8, source=10)),
        ]
    )
    with pytest.raises(ReadinessError, match="recovery regressed"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=3,
            recovery_extension_seconds=3,
            stalled_timeout_seconds=2,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )


def test_fails_without_recovery_evidence_at_initial_deadline() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch([(0, None)])
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=10,
            stalled_timeout_seconds=2,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )


def test_caught_up_recovery_is_still_bounded() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [(503, payload(ready=False, checkpoint=10, source=10))]
    )
    with pytest.raises(ReadinessError, match="bounded extension"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=3,
            stalled_timeout_seconds=1,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )


def test_rejects_inconsistent_backlog() -> None:
    inconsistent = payload(ready=False, checkpoint=8, source=10)
    inconsistent["dependencies"]["lifecycle_projector"]["backlog"] = 1
    state, observation = classify_readiness(
        503,
        inconsistent,
        expected_deployment_sha=SHA,
    )
    assert state == "unavailable"
    assert observation is None


def test_wrong_deployment_can_converge_to_exact_ready_inside_base_window() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [
            (503, payload(ready=False, deployment_sha="b" * 40)),
            (200, payload(ready=True, deployment_sha="b" * 40)),
            (200, payload(ready=True)),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=3,
        recovery_extension_seconds=4,
        stalled_timeout_seconds=2,
        poll_interval_seconds=1,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 2


def test_persistent_wrong_deployment_fails_at_base_cap_without_extension() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [(503, payload(ready=False, deployment_sha="b" * 40))]
    )
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=10,
            stalled_timeout_seconds=2,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


def test_http_200_wrong_deployment_is_never_success() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [(200, payload(ready=True, deployment_sha="b" * 40))]
    )
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=10,
            stalled_timeout_seconds=2,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


def test_residual_smoke_uses_bounded_recovery_waiter() -> None:
    script = Path("scripts/verify_trade_journey_residual_dev.sh").read_text()
    assert "scripts/wait_for_bff_lifecycle_readiness.py" in script
    assert "--initial-timeout-seconds 120" in script
    assert "--recovery-extension-seconds 120" in script
    assert "--stalled-timeout-seconds 45" in script
