from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.wait_for_bff_lifecycle_readiness import (
    ReadinessError,
    classify_readiness,
    confirm_exact_target,
    endpoint_url,
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
                "backlog": max(0, source - checkpoint),
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


def test_requires_two_consecutive_consistent_ready_samples() -> None:
    fake = FakeTime()
    inconsistent = payload(ready=True, checkpoint=9, source=10)
    inconsistent["dependencies"]["lifecycle_projector"]["backlog"] = 0
    _, fetch = sequence_fetch(
        [
            (200, payload(ready=True)),
            (200, inconsistent),
            (200, payload(ready=True, checkpoint=12, source=12)),
            (200, payload(ready=True, checkpoint=13, source=13)),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=5,
        recovery_extension_seconds=4,
        stalled_timeout_seconds=2,
        poll_interval_seconds=1,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 3


def test_http_200_retained_snapshot_is_accepted_after_two_samples() -> None:
    fake = FakeTime()
    retained = payload(ready=True, checkpoint=6_099_223, source=0)
    _, fetch = sequence_fetch(
        [
            (200, retained),
            (200, retained),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=3,
        recovery_extension_seconds=10,
        stalled_timeout_seconds=2,
        poll_interval_seconds=1,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 1


def test_http_200_checkpoint_behind_retained_high_is_not_ready() -> None:
    inconsistent = payload(ready=True, checkpoint=9, source=10)
    inconsistent["dependencies"]["lifecycle_projector"]["backlog"] = 0
    state, observation = classify_readiness(
        200,
        inconsistent,
        expected_deployment_sha=SHA,
    )
    assert state == "snapshot_inconsistent"
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
    assert fake.value == 5


def test_recent_exact_unavailable_state_can_use_identity_bound_extension() -> None:
    fake = FakeTime()
    exact_but_unavailable = payload(ready=True)
    _, fetch = sequence_fetch(
        [
            (503, exact_but_unavailable),
            (0, None),
            (503, exact_but_unavailable),
            (200, payload(ready=True)),
            (200, payload(ready=True, checkpoint=11, source=11)),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=2,
        recovery_extension_seconds=4,
        stalled_timeout_seconds=1,
        poll_interval_seconds=1,
        exact_evidence_max_age_seconds=2,
        confirm_exact_target=lambda: "live",
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 4


def test_recent_exact_unavailable_state_still_requires_live_identity() -> None:
    fake = FakeTime()
    exact_but_unavailable = payload(ready=True)
    _, fetch = sequence_fetch([(503, exact_but_unavailable)])
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=4,
            stalled_timeout_seconds=1,
            poll_interval_seconds=1,
            exact_evidence_max_age_seconds=2,
            confirm_exact_target=lambda: "unavailable",
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


def test_liveness_alone_without_recent_exact_target_cannot_extend() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch([(0, None)])
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=4,
            stalled_timeout_seconds=1,
            poll_interval_seconds=1,
            exact_evidence_max_age_seconds=2,
            confirm_exact_target=lambda: "live",
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


def test_exact_version_can_supply_identity_bound_extension() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [
            (0, None),
            (0, None),
            (0, None),
            (200, payload(ready=True)),
            (200, payload(ready=True, checkpoint=11, source=11)),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=2,
        recovery_extension_seconds=4,
        stalled_timeout_seconds=1,
        poll_interval_seconds=1,
        exact_evidence_max_age_seconds=2,
        confirm_exact_target=lambda: "version_exact",
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 4


def test_expired_exact_evidence_cannot_use_identity_bound_extension() -> None:
    fake = FakeTime()
    exact_but_unavailable = payload(ready=True)
    _, fetch = sequence_fetch(
        [
            (503, exact_but_unavailable),
            (0, None),
            (0, None),
        ]
    )
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=4,
            stalled_timeout_seconds=1,
            poll_interval_seconds=1,
            exact_evidence_max_age_seconds=1,
            confirm_exact_target=lambda: "live",
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


def test_exact_target_probe_rejects_explicit_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "http://bff/bff/version": (200, {"source_commit_sha": "b" * 40}),
        "http://bff/livez": (200, {"live": True}),
    }
    monkeypatch.setattr(
        "scripts.wait_for_bff_lifecycle_readiness.fetch_json",
        lambda url, request_timeout_seconds: responses[url],
    )
    assert confirm_exact_target(
        liveness_url="http://bff/livez",
        version_url="http://bff/bff/version",
        expected_deployment_sha=SHA,
        request_timeout_seconds=1,
    ) == "contradicted"


def test_exact_target_probe_can_fall_back_to_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "http://bff/bff/version": (0, None),
        "http://bff/livez": (200, {"live": True}),
    }
    monkeypatch.setattr(
        "scripts.wait_for_bff_lifecycle_readiness.fetch_json",
        lambda url, request_timeout_seconds: responses[url],
    )
    assert confirm_exact_target(
        liveness_url="http://bff/livez",
        version_url="http://bff/bff/version",
        expected_deployment_sha=SHA,
        request_timeout_seconds=1,
    ) == "live"
    assert endpoint_url("http://bff/readyz?ignored=1", "/livez") == (
        "http://bff/livez"
    )


@pytest.mark.parametrize("contradiction", ["old", "stale"])
def test_old_or_stale_sample_invalidates_recent_exact_evidence(
    contradiction: str,
) -> None:
    fake = FakeTime()
    exact_but_unavailable = payload(ready=True)
    contradicted = payload(ready=True)
    if contradiction == "old":
        contradicted["dependencies"]["lifecycle_projector"][
            "deployment_sha"
        ] = "b" * 40
    else:
        contradicted["dependencies"]["lifecycle_projector"]["freshness"][
            "stale"
        ] = True
    _, fetch = sequence_fetch(
        [
            (503, exact_but_unavailable),
            (503, contradicted),
            (503, contradicted),
        ]
    )
    with pytest.raises(ReadinessError, match="ordinary restart budget"):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=4,
            stalled_timeout_seconds=1,
            poll_interval_seconds=1,
            exact_evidence_max_age_seconds=2,
            confirm_exact_target=lambda: "version_exact",
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


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
            initial_timeout_seconds=3,
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


def test_nonprogressing_recovery_cannot_enter_extension() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [(503, payload(ready=False, checkpoint=8, source=10))]
    )
    with pytest.raises(
        ReadinessError,
        match="without monotonic lifecycle recovery convergence",
    ):
        wait_for_readiness(
            fetch,
            expected_deployment_sha=SHA,
            initial_timeout_seconds=2,
            recovery_extension_seconds=10,
            stalled_timeout_seconds=5,
            poll_interval_seconds=1,
            monotonic=fake.monotonic,
            sleep=fake.sleep,
        )
    assert fake.value == 2


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


def test_recovery_accepts_retained_checkpoint_ahead_of_current_window() -> None:
    state, observation = classify_readiness(
        503,
        payload(ready=False, checkpoint=6_099_223, source=0),
        expected_deployment_sha=SHA,
    )
    assert state == "recovering"
    assert observation is not None
    assert observation.caught_up is True


def test_wrong_deployment_can_converge_to_exact_ready_inside_base_window() -> None:
    fake = FakeTime()
    _, fetch = sequence_fetch(
        [
            (503, payload(ready=False, deployment_sha="b" * 40)),
            (200, payload(ready=True, deployment_sha="b" * 40)),
            (200, payload(ready=True)),
            (200, payload(ready=True, checkpoint=11, source=11)),
        ]
    )
    wait_for_readiness(
        fetch,
        expected_deployment_sha=SHA,
        initial_timeout_seconds=4,
        recovery_extension_seconds=4,
        stalled_timeout_seconds=2,
        poll_interval_seconds=1,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
    )
    assert fake.value == 3


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
    assert "--exact-evidence-max-age-seconds 30" in script


def _component_block(script: str, component: str, next_component: str) -> str:
    return script.split(f"\n  {component})", 1)[1].split(
        f"\n  {next_component})",
        1,
    )[0]


def test_root_initial_readiness_uses_exact_waiter_before_residual_smoke() -> None:
    script = Path("scripts/deploy_nonprod_vm.sh").read_text()
    root = _component_block(script, "root", "bff")
    compose_up = (
        "docker compose -p pantheon -f docker-compose.yml up -d --build"
    )
    helper_call = (
        "wait_for_exact_bff_lifecycle_readiness \\\n"
        "      http://127.0.0.1:18001/readyz"
    )
    assert helper_call in root
    assert "curl_with_retry http://127.0.0.1:18001/readyz" not in root
    assert root.index(compose_up) < root.index(helper_call)
    assert root.index(helper_call) < root.index(
        "bash scripts/verify_trade_journey_residual_dev.sh"
    )
    assert root.index(helper_call) < root.index("assert_bff_source_sha")
    assert root.index(helper_call) < root.index("assert_bff_auth_gate")
    assert root.index(helper_call) < root.index(
        "assert_ppl_alloc_009_dev_proof_gate"
    )
    assert root.index(helper_call) < root.index("ensure_dev_caddy_ingress")


def test_root_exact_waiter_is_revision_bound_and_bounded() -> None:
    script = Path("scripts/deploy_nonprod_vm.sh").read_text()
    helper = script.split(
        "\nwait_for_exact_bff_lifecycle_readiness() {",
        1,
    )[1].split("\n}", 1)[0]
    assert "scripts/wait_for_bff_lifecycle_readiness.py" in helper
    assert '--expected-deployment-sha "${PANTHEON_DEPLOY_SHA}"' in helper
    assert "--initial-timeout-seconds 600" in helper
    assert "--recovery-extension-seconds 180" in helper
    assert "--stalled-timeout-seconds 45" in helper
    assert "--exact-evidence-max-age-seconds 30" in helper


def test_operator_bff_compose_health_is_liveness_not_projector_readiness() -> None:
    compose = Path("docker-compose.yml").read_text()
    operator_bff = compose.split("\n  operator-bff:", 1)[1].split(
        "\n  loop-run-projector-scheduler:",
        1,
    )[0]
    healthcheck = operator_bff.split("\n    healthcheck:", 1)[1]
    assert "http://127.0.0.1:8001/livez" in healthcheck
    assert "http://127.0.0.1:8001/readyz" not in healthcheck
    assert "interval: 10s" in healthcheck
    assert "retries: 10" in healthcheck


def test_bff_only_readiness_also_uses_exact_waiter() -> None:
    script = Path("scripts/deploy_nonprod_vm.sh").read_text()
    bff = _component_block(script, "bff", "exec")
    compose_up = (
        "docker compose -p pantheon -f docker-compose.yml "
        "up -d --build --no-deps operator-bff loop-run-projector-scheduler"
    )
    helper_call = (
        "wait_for_exact_bff_lifecycle_readiness \\\n"
        "      http://127.0.0.1:18001/readyz"
    )
    assert helper_call in bff
    assert "curl_with_retry http://127.0.0.1:18001/readyz" not in bff
    assert bff.index(compose_up) < bff.index(helper_call)
    assert bff.index(helper_call) < bff.index("assert_bff_source_sha")
    assert bff.index(helper_call) < bff.index("assert_bff_auth_gate")
    assert bff.index(helper_call) < bff.index(
        "assert_ppl_alloc_009_dev_proof_gate"
    )
    assert bff.index(helper_call) < bff.index("ensure_dev_caddy_ingress")


def test_non_bff_components_do_not_use_lifecycle_waiter() -> None:
    script = Path("scripts/deploy_nonprod_vm.sh").read_text()
    exec_block = _component_block(script, "exec", "control")
    control_block = script.split("\n  control)", 1)[1].split(
        "\n  *)",
        1,
    )[0]
    assert "wait_for_exact_bff_lifecycle_readiness" not in exec_block
    assert "wait_for_exact_bff_lifecycle_readiness" not in control_block


def test_agora_restart_persistence_uses_exact_waiter_before_verify() -> None:
    workflow = Path(".github/workflows/nonprod-deploy.yml").read_text()
    agora = workflow.split(
        "\n      - name: Dev Agora restart persistence smoke under lease",
        1,
    )[1].split(
        "\n      - name: Stop heartbeat and release only after complete success",
        1,
    )[0]
    waiter = (
        'python3 "${GITHUB_WORKSPACE}/.target/scripts/'
        'wait_for_bff_lifecycle_readiness.py"'
    )
    restart = "docker compose -p pantheon -f docker-compose.yml restart operator-bff"
    verify = (
        "agora_workshop_restart_persistence_smoke.py verify "
        "--workshop-id ${workshop_id_q}"
    )

    assert "PANTHEON_DEPLOY_SHA: ${{ steps.target.outputs.sha }}" in agora
    assert waiter in agora
    assert '--expected-deployment-sha "${PANTHEON_DEPLOY_SHA}"' in agora
    assert "--initial-timeout-seconds 120" in agora
    assert "--recovery-extension-seconds 120" in agora
    assert "--stalled-timeout-seconds 45" in agora
    assert "--exact-evidence-max-age-seconds 30" in agora
    assert "for _ in $(seq 1 30)" not in agora
    assert 'curl -fsS "${DEV_BFF_URL}/readyz"' not in agora
    assert agora.index(restart) < agora.index(waiter) < agora.index(verify)
