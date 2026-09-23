from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scripts import bootstrap_dev_paper_baseline as bootstrap


DEV_ENV = {
    "PANTHEON_ENV": "dev",
    "PANTHEON_BFF_AUTH_MODE": "strict",
    "PANTHEON_BFF_AUTH_STUB": "false",
    "PANTHEON_LIVE_BROKER_ENABLED": "false",
    "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
    "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID": "operator-a-id",
    "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": "operator-a-secret",
    "PANTHEON_BFF_OIDC_CLIENT_ID": "operator-id",
    "PANTHEON_BFF_OIDC_CLIENT_SECRET": "operator-secret",
}

PERMISSIVE_DEV_ENV = {
    **DEV_ENV,
    "PANTHEON_BFF_AUTH_MODE": "permissive",
    "PANTHEON_BFF_AUTH_STUB": "true",
}

# The governed source provisioning prerequisite requires this bearer token
# (bootstrap.source_ingest_controller_token) whenever a source_ingest_url is
# passed; every test that exercises the market-snapshot wait path needs it.
SOURCE_INGEST_DEV_ENV = {
    **DEV_ENV,
    "SOURCE_INGEST_CONTROLLER_TOKEN": "controller-token-test",
}

RECONCILE_OK_RESPONSE = (
    200,
    {"summary": {"mutated": 1, "satisfied": 0, "unsupported": 0, "conflicts": 0}},
)


def _run(**overrides):
    params = {
        "base_url": "http://127.0.0.1:8001",
        "name": bootstrap.DEFAULT_NAME,
        "idempotency_key": bootstrap.DEFAULT_IDEMPOTENCY_KEY,
        "timeout_seconds": 30,
        "poll_seconds": 0.1,
        "request_timeout_seconds": 5,
        "monotonic": lambda: 0,
        "sleep": lambda _seconds: None,
    }
    params.update(overrides)
    return bootstrap.ensure_paper_baseline(**params)


def test_baseline_reservation_version_tracks_operator_a_semantics() -> None:
    assert bootstrap.DEFAULT_NAME == "Pantheon Dev Paper Baseline 3"
    assert bootstrap.DEFAULT_IDEMPOTENCY_KEY == "dev-paper-bootstrap-20260720-operator-a-v3"


def test_login_credentials_prefer_dedicated_mfa_operator() -> None:
    with patch.dict(os.environ, DEV_ENV, clear=True):
        assert bootstrap._login_credential_pair() == (
            "operator-a-id",
            "operator-a-secret",
            "operator_a",
        )


def test_login_credentials_reject_incomplete_dedicated_pair() -> None:
    env = {**DEV_ENV, "PANTHEON_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": ""}
    with patch.dict(os.environ, env, clear=True), pytest.raises(
        bootstrap.BootstrapError, match="credential pair.*incomplete"
    ):
        bootstrap._login_credential_pair()


def test_replays_one_idempotent_request_until_authoritative_readback() -> None:
    responses = [
        (
            200,
            {"access_token": "short-lived", "meta": {"identity": "operator_a"}},
        ),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                "meta": {
                    "lifecycle_state": "paper_running",
                    "status": "ok",
                    "degraded_dependencies": [],
                },
            },
        ),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "succeeded",
                    "provisioning_step": "authoritative_readback_complete",
                    "runtime_id": "rt-1",
                    "runtime_binding_id": "rb-1",
                    "deployment_plan_id": "plan-1",
                    "live_capital_side_effects": False,
                },
            },
        ),
    ]

    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ) as post:
        result = _run()

    assert result == {
        "status": "ok",
        "attempts": 2,
        "persona_id": "persona-1",
        "state": "paper_running",
        "provisioning_state": "succeeded",
        "provisioning_step": "authoritative_readback_complete",
        "runtime_id": "rt-1",
        "runtime_binding_id": "rb-1",
        "deployment_plan_id": "plan-1",
        "capital_mode": "paper",
        "live_capital_side_effects": False,
    }
    assert post.call_count == 4
    login = post.call_args_list[0]
    assert login.args[1]["client_id"] == "operator-a-id"
    assert login.args[1]["client_secret"] == "operator-a-secret"

    first_create = post.call_args_list[1]
    assert first_create.args[0] == "http://127.0.0.1:8001/bff/management/personas/create-paper-bundle"
    assert first_create.kwargs["headers"]["Idempotency-Key"] == bootstrap.DEFAULT_IDEMPOTENCY_KEY
    assert first_create.kwargs["headers"]["Authorization"] == "Bearer short-lived"

    reconcile = post.call_args_list[2]
    assert reconcile.args[0] == "http://127.0.0.1:8001/bff/personas/persona-1/provisioning/reconcile"
    assert reconcile.kwargs["headers"]["Authorization"] == "Bearer short-lived"

    second_create = post.call_args_list[3]
    assert second_create.args[0] == "http://127.0.0.1:8001/bff/management/personas/create-paper-bundle"
    assert second_create.kwargs["headers"]["Idempotency-Key"] == bootstrap.DEFAULT_IDEMPOTENCY_KEY
    assert second_create.kwargs["headers"]["Authorization"] == "Bearer short-lived"


def test_market_snapshot_wait_runs_after_persona_creation_never_provisioned_first_run() -> None:
    """On a fresh host the dev synthetic connector (dev-paper-us-equity-
    simulation) is only provisioned once a Persona has declared it under
    required_data_sources, which happens as part of create-paper-bundle.
    A snapshot wait that ran before that Persona existed would wait forever
    on a producer that can never be provisioned
    (DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001). This proves the wait now
    runs only after persona creation, and that the first-run 404-then-appears
    cycle -- with an active run-scheduled nudge -- still converges."""

    call_order: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    valid_snapshot = {
        "schema_version": 1,
        "snapshot_id": "snap-1",
        "symbol": "SPY",
        "event_time": now_iso,
        "observed_at": now_iso,
        "closes": [500.0, 501.5],
    }

    post_queue = {
        "/bff/auth/dev-login": [
            (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        ],
        "create-paper-bundle": [
            (
                201,
                {
                    "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                    "meta": {
                        "provisioning_state": "provisioning",
                        "provisioning_step": "schedule_registered",
                        "live_capital_side_effects": False,
                    },
                },
            ),
            (
                201,
                {
                    "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                    "meta": {
                        "provisioning_state": "succeeded",
                        "provisioning_step": "authoritative_readback_complete",
                        "runtime_id": "rt-1",
                        "runtime_binding_id": "rb-1",
                        "deployment_plan_id": "plan-1",
                        "live_capital_side_effects": False,
                    },
                },
            ),
        ],
        "/bff/personas/persona-1/provisioning/reconcile": [
            (
                200,
                {
                    "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                    "meta": {"lifecycle_state": "paper_running", "status": "ok"},
                },
            ),
        ],
        "persona-source-provisioning/reconcile": [RECONCILE_OK_RESPONSE],
        "run-scheduled": [
            (200, {"status": "ok"}),
        ],
    }

    def fake_post_json(url, payload=None, **kwargs):
        call_order.append(f"post:{url}")
        for key, responses in post_queue.items():
            if key in url:
                return responses.pop(0)
        raise AssertionError(f"unexpected POST {url}")

    get_queue = [
        (404, {"detail": {"code": "market_snapshot_not_found", "symbol": "SPY"}}),
        (200, valid_snapshot),
    ]

    def fake_get_json(url, **kwargs):
        call_order.append(f"get:{url}")
        return get_queue.pop(0)

    with patch.dict(os.environ, SOURCE_INGEST_DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=fake_post_json
    ), patch.object(bootstrap, "_get_json", side_effect=fake_get_json):
        result = _run(source_ingest_url="http://mock-source:8097")

    assert result["status"] == "ok"
    assert result["persona_id"] == "persona-1"

    # The Persona create call is the second call overall (after login) --
    # the source provisioning prerequisite and market snapshot poll never
    # run before required_data_sources exists on a Persona.
    create_index = next(
        i for i, c in enumerate(call_order) if "create-paper-bundle" in c
    )
    provisioning_index = next(
        i for i, c in enumerate(call_order) if "persona-source-provisioning/reconcile" in c
    )
    first_get_index = next(i for i, c in enumerate(call_order) if c.startswith("get:"))
    assert create_index < provisioning_index < first_get_index, (
        "governed source provisioning must run after persona creation and "
        f"before the market snapshot wait (call_order={call_order})"
    )

    # The 404 branch actively nudges the reconciler via run-scheduled instead
    # of only passively polling.
    assert any("run-scheduled" in c for c in call_order)


def test_market_snapshot_already_fresh_steady_state_does_not_delay_reconcile() -> None:
    """Once the connector is steady-state (schedule already configured, the
    latest snapshot already fresh), the wait must resolve on the very first
    poll and never call run-scheduled, so an already-healthy host is not
    slowed down by the ordering fix."""

    call_order: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    valid_snapshot = {
        "schema_version": 1,
        "snapshot_id": "snap-steady",
        "symbol": "SPY",
        "event_time": now_iso,
        "observed_at": now_iso,
        "closes": [500.0, 501.5],
    }

    post_queue = {
        "/bff/auth/dev-login": [
            (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        ],
        "create-paper-bundle": [
            (
                201,
                {
                    "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                    "meta": {
                        "provisioning_state": "provisioning",
                        "provisioning_step": "schedule_registered",
                        "live_capital_side_effects": False,
                    },
                },
            ),
            (
                201,
                {
                    "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                    "meta": {
                        "provisioning_state": "succeeded",
                        "provisioning_step": "authoritative_readback_complete",
                        "runtime_id": "rt-1",
                        "runtime_binding_id": "rb-1",
                        "deployment_plan_id": "plan-1",
                        "live_capital_side_effects": False,
                    },
                },
            ),
        ],
        "/bff/personas/persona-1/provisioning/reconcile": [
            (
                200,
                {
                    "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                    "meta": {"lifecycle_state": "paper_running", "status": "ok"},
                },
            ),
        ],
        "persona-source-provisioning/reconcile": [RECONCILE_OK_RESPONSE],
    }

    def fake_post_json(url, payload=None, **kwargs):
        call_order.append(f"post:{url}")
        for key, responses in post_queue.items():
            if key in url:
                return responses.pop(0)
        raise AssertionError(f"unexpected POST {url}")

    def fake_get_json(url, **kwargs):
        call_order.append(f"get:{url}")
        return (200, valid_snapshot)

    with patch.dict(os.environ, SOURCE_INGEST_DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=fake_post_json
    ), patch.object(bootstrap, "_get_json", side_effect=fake_get_json):
        result = _run(source_ingest_url="http://mock-source:8097")

    assert result["status"] == "ok"
    get_calls = [c for c in call_order if c.startswith("get:")]
    # The snapshot is already fresh, so the wait resolves on its very first
    # poll -- no run-scheduled nudge is needed on the steady-state path.
    assert len(get_calls) == 1
    assert not any("run-scheduled" in c for c in call_order)
    # It still only polls after the Persona (and its required_data_sources
    # declaration) already exists.
    create_index = next(
        i for i, c in enumerate(call_order) if "create-paper-bundle" in c
    )
    assert create_index < call_order.index(get_calls[0])


def test_reconcile_mismatched_persona_id_raises() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-mismatched", "state": "paper_running", "capitalMode": "paper"},
                "meta": {"lifecycle_state": "paper_running", "status": "ok"},
            },
        ),
    ]

    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(bootstrap.BootstrapError, match="mismatched Persona ID"):
        _run()


def test_reconcile_terminal_failure_raises() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-1", "state": "provisioning_failed", "capitalMode": "paper"},
                "meta": {
                    "lifecycle_state": "provisioning_failed",
                    "status": "ok",
                },
            },
        ),
    ]

    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(bootstrap.BootstrapError, match="terminal failure during reconcile"):
        _run()


def test_reconcile_degraded_dependencies_raises() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "lifecycle_state": "provisioning",
                    "status": "degraded",
                    "degraded_dependencies": ["paper_runtime_manager"],
                },
            },
        ),
    ]

    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(bootstrap.BootstrapError, match="degraded during reconcile"):
        _run()


def test_reconcile_timeout_raises() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
    ]

    # A low server-side timeout keeps the effective deadline (server + margin)
    # below the fake clock's jump, so the indefinite-stall path is still
    # exercised without waiting on the production 600s default.
    env = {**DEV_ENV, "PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS": "5"}
    times = [0, 100]  # monotonic calls: start, then already past deadline (5 + 30 margin = 35)
    with patch.dict(os.environ, env, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ) as post, pytest.raises(bootstrap.BootstrapError, match="timed out"):
        _run(monotonic=lambda: times.pop(0) if times else 999)

    assert post.call_count == 2  # login + create, then timeout before reconcile poll


def test_server_authoritative_timeout_seconds_defaults_and_parses() -> None:
    assert bootstrap.server_authoritative_timeout_seconds({}) == 600.0
    assert bootstrap.server_authoritative_timeout_seconds(
        {"PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS": "900"}
    ) == 900.0
    # Malformed or non-positive values fail back to the BFF's own default
    # instead of producing a zero/negative poll deadline.
    assert bootstrap.server_authoritative_timeout_seconds(
        {"PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS": "not-a-number"}
    ) == 600.0
    assert bootstrap.server_authoritative_timeout_seconds(
        {"PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS": "-10"}
    ) == 600.0


def test_effective_poll_timeout_widens_a_too_short_client_budget() -> None:
    # The production CI invocation passes --timeout-seconds 420, which is
    # shorter than the BFF's own 600s authoritative provisioning timeout.
    # The effective deadline must never be shorter than that server timeout
    # plus the safety margin, regardless of the caller-requested value.
    assert bootstrap.effective_poll_timeout_seconds(420, environ={}) == 630.0
    # A generous caller-requested timeout is never shortened.
    assert bootstrap.effective_poll_timeout_seconds(900, environ={}) == 900


def test_reconcile_keeps_polling_past_a_too_short_client_timeout_until_terminal() -> None:
    """The client's requested 420s budget alone would abort before the BFF's
    600s authoritative timeout is ever reached. With the widened effective
    deadline, polling continues until the BFF itself reports a real terminal
    provisioning_failed reason instead of a generic client-side timeout."""

    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {"lifecycle_state": "provisioning", "status": "ok"},
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-1", "state": "provisioning_failed", "capitalMode": "paper"},
                "meta": {
                    "lifecycle_state": "provisioning_failed",
                    "status": "ok",
                    "provisioning_failure_reason": "runtime_binding_failed_or_mismatched",
                },
            },
        ),
    ]

    # monotonic(): start(0), top-of-loop check (421), bottom-of-loop check
    # (440), second top-of-loop check (450) -- all past the naive 420s
    # client budget but still inside the widened 630s effective deadline
    # (600s server timeout + 30s margin), so polling reaches the second
    # reconcile call and observes the terminal failure.
    times = [0, 421, 440, 450]
    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(
        bootstrap.BootstrapError, match="terminal failure during reconcile"
    ) as exc_info:
        _run(
            timeout_seconds=420,
            monotonic=lambda: times.pop(0) if times else 999,
        )

    # The real, named failure reason surfaces -- not a generic timeout.
    assert "runtime_binding_failed_or_mismatched" in str(exc_info.value)


def test_timeout_error_surfaces_last_known_reconcile_reason() -> None:
    """If the stall genuinely outlasts even the widened effective deadline,
    the timeout error still carries whatever real diagnostic the last
    reconcile poll observed, instead of a content-free message."""

    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "provisioning",
                    "provisioning_step": "schedule_registered",
                    "live_capital_side_effects": False,
                },
            },
        ),
        (
            200,
            {
                "data": {"id": "persona-1", "state": "provisioning", "capitalMode": "paper"},
                "meta": {"lifecycle_state": "provisioning", "status": "ok"},
            },
        ),
    ]

    env = {**DEV_ENV, "PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS": "5"}
    times = [0, 10, 999]
    with patch.dict(os.environ, env, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(bootstrap.BootstrapError, match="timed out") as exc_info:
        _run(monotonic=lambda: times.pop(0) if times else 9999)

    message = str(exc_info.value)
    assert '"last_reconcile_lifecycle_state": "provisioning"' in message


def test_no_reconcile_after_terminal_create_failure() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "provisioning_failed", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "failed",
                    "provisioning_step": "deployment_failed",
                    "live_capital_side_effects": False,
                },
            },
        ),
    ]

    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ) as post, pytest.raises(bootstrap.BootstrapError, match="unexpected non-success state"):
        _run()

    # Reconcile must NEVER be called after terminal creation state
    assert post.call_count == 2  # login and create only


def test_allows_permissive_stub_for_dev_paper_functional_closure() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                "meta": {
                    "provisioning_state": "succeeded",
                    "provisioning_step": "authoritative_readback_complete",
                    "runtime_id": "rt-1",
                    "runtime_binding_id": "rb-1",
                    "deployment_plan_id": "plan-1",
                    "live_capital_side_effects": False,
                },
            },
        ),
    ]

    with patch.dict(os.environ, PERMISSIVE_DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ):
        result = _run()

    assert result["status"] == "ok"
    assert result["capital_mode"] == "paper"


@pytest.mark.parametrize(
    ("env_update", "message"),
    [
        ({"PANTHEON_ENV": "staging-live"}, "PANTHEON_ENV=dev"),
        ({"PANTHEON_BFF_AUTH_MODE": "disabled"}, "supported BFF auth mode"),
        ({"PANTHEON_LIVE_BROKER_ENABLED": "true"}, "live broker enabled"),
        (
            {"PANTHEON_CANARY_EXECUTION_ENABLED": "true"},
            "canary execution enabled",
        ),
    ],
)
def test_refuses_to_leave_strict_dev_paper_boundary(env_update, message) -> None:
    env = {**DEV_ENV, **env_update}
    with patch.dict(os.environ, env, clear=True), pytest.raises(
        bootstrap.BootstrapError, match=message
    ):
        _run()


def test_surfaces_sanitized_terminal_provisioning_error() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            502,
            {
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": "Persona provisioning failed",
                    "details": {
                        "precondition_failed": "schedule_registration",
                        "reason": "device pairing required",
                    },
                }
            },
        ),
    ]
    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(bootstrap.BootstrapError) as exc_info:
        _run()

    message = str(exc_info.value)
    assert "schedule_registration" in message
    assert "device pairing required" in message
    assert "operator-a-secret" not in message
    assert "operator-secret" not in message
    assert "short-lived" not in message


def test_refuses_non_paper_or_live_side_effect_response() -> None:
    responses = [
        (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        (
            201,
            {
                "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "live"},
                "meta": {
                    "provisioning_state": "succeeded",
                    "runtime_id": "rt-1",
                    "runtime_binding_id": "rb-1",
                    "live_capital_side_effects": True,
                },
            },
        ),
    ]
    with patch.dict(os.environ, DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ), pytest.raises(bootstrap.BootstrapError, match="paper-only boundary"):
        _run()


def _replay_post_queue(reconcile_response=None) -> dict[str, list]:
    """An idempotent successful replay: create-paper-bundle already reports
    paper_running/succeeded on the very first call, with no reconcile-loop
    poll involved at all."""

    return {
        "/bff/auth/dev-login": [
            (200, {"access_token": "short-lived", "meta": {"identity": "operator_a"}}),
        ],
        "create-paper-bundle": [
            (
                201,
                {
                    "data": {"id": "persona-1", "state": "paper_running", "capitalMode": "paper"},
                    "meta": {
                        "provisioning_state": "succeeded",
                        "provisioning_step": "authoritative_readback_complete",
                        "runtime_id": "rt-1",
                        "runtime_binding_id": "rb-1",
                        "deployment_plan_id": "plan-1",
                        "live_capital_side_effects": False,
                    },
                },
            ),
        ],
        "persona-source-provisioning/reconcile": [reconcile_response or RECONCILE_OK_RESPONSE],
    }


def test_replay_with_stale_snapshot_still_raises_market_input_stale() -> None:
    """AC4 regression: an idempotent successful replay (create-paper-bundle
    already returns paper_running/succeeded on the first call) must not
    bypass the post-create freshness gate. Reproduces the independent
    base/head finding for DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001: with
    a stale SPY snapshot and a terminal successful create response, this must
    still raise market_input_stale rather than returning ok."""

    stale_snapshot = {
        "schema_version": 1,
        "snapshot_id": "snap-stale",
        "symbol": "SPY",
        "event_time": "2020-01-01T00:00:00Z",
        "observed_at": "2020-01-01T00:00:00Z",
        "closes": [500.0, 501.5],
    }
    post_queue = _replay_post_queue()
    call_order: list[str] = []

    def fake_post_json(url, payload=None, **kwargs):
        call_order.append(f"post:{url}")
        for key, responses in post_queue.items():
            if key in url:
                return responses.pop(0)
        raise AssertionError(f"unexpected POST {url}")

    def fake_get_json(url, **kwargs):
        call_order.append(f"get:{url}")
        return (200, stale_snapshot)

    times = [0, 100]  # start, then past a 5s market-input deadline
    with patch.dict(os.environ, SOURCE_INGEST_DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=fake_post_json
    ), patch.object(bootstrap, "_get_json", side_effect=fake_get_json), pytest.raises(
        bootstrap.BootstrapError, match="market_input_stale"
    ):
        _run(
            source_ingest_url="http://mock-source:8097",
            market_input_timeout_seconds=5.0,
            monotonic=lambda: times.pop(0) if times else 999,
        )

    # Exactly one snapshot GET: the replay must not skip the freshness check,
    # but also must not re-poll past the very first stale observation before
    # the bounded deadline is (already) exceeded.
    assert len([c for c in call_order if c.startswith("get:")]) == 1


def test_replay_with_missing_snapshot_still_raises_market_snapshot_not_found() -> None:
    """AC4 regression: an idempotent successful replay must still surface a
    named, bounded failure when the connector has never produced a snapshot
    at all, rather than returning ok without ever checking."""

    post_queue = _replay_post_queue()
    call_order: list[str] = []

    def fake_post_json(url, payload=None, **kwargs):
        call_order.append(f"post:{url}")
        for key, responses in post_queue.items():
            if key in url:
                return responses.pop(0)
        return (200, {"status": "ok"})  # run-scheduled nudge on 404

    def fake_get_json(url, **kwargs):
        call_order.append(f"get:{url}")
        return (404, {"detail": {"code": "market_snapshot_not_found", "symbol": "SPY"}})

    times = [0, 100]
    with patch.dict(os.environ, SOURCE_INGEST_DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=fake_post_json
    ), patch.object(bootstrap, "_get_json", side_effect=fake_get_json), pytest.raises(
        bootstrap.BootstrapError, match="market_snapshot_not_found"
    ):
        _run(
            source_ingest_url="http://mock-source:8097",
            market_input_timeout_seconds=5.0,
            monotonic=lambda: times.pop(0) if times else 999,
        )


def test_replay_with_fresh_snapshot_returns_ok_after_one_get() -> None:
    """AC4 regression: the already-fresh steady-state replay path still
    converges immediately (the freshness gate is not merely present but also
    not a regression in the common case)."""

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fresh_snapshot = {
        "schema_version": 1,
        "snapshot_id": "snap-fresh",
        "symbol": "SPY",
        "event_time": now_iso,
        "observed_at": now_iso,
        "closes": [500.0, 501.5],
    }
    post_queue = _replay_post_queue()
    call_order: list[str] = []

    def fake_post_json(url, payload=None, **kwargs):
        call_order.append(f"post:{url}")
        for key, responses in post_queue.items():
            if key in url:
                return responses.pop(0)
        raise AssertionError(f"unexpected POST {url}")

    def fake_get_json(url, **kwargs):
        call_order.append(f"get:{url}")
        return (200, fresh_snapshot)

    with patch.dict(os.environ, SOURCE_INGEST_DEV_ENV, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=fake_post_json
    ), patch.object(bootstrap, "_get_json", side_effect=fake_get_json):
        result = _run(source_ingest_url="http://mock-source:8097")

    assert result["status"] == "ok"
    assert result["persona_id"] == "persona-1"
    assert len([c for c in call_order if c.startswith("get:")]) == 1


def test_source_provisioning_skipped_without_required_data_sources() -> None:
    result = bootstrap.ensure_source_provisioning(
        source_ingest_url="http://mock-source:8097",
        persona_id="persona-1",
        required_data_sources=[],
        controller_token="",
    )
    assert result == {"status": "skipped", "reason": "no_required_data_sources"}


def test_source_provisioning_requires_controller_token() -> None:
    with pytest.raises(bootstrap.BootstrapError, match="controller token"):
        bootstrap.ensure_source_provisioning(
            source_ingest_url="http://mock-source:8097",
            persona_id="persona-1",
            required_data_sources=list(bootstrap.DEV_US_REQUIRED_DATA_SOURCES),
            controller_token="",
        )


def test_source_provisioning_sends_authoritative_reconcile_request() -> None:
    captured = {}

    def fake_post_json(url, payload=None, **kwargs):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = kwargs.get("headers")
        return RECONCILE_OK_RESPONSE

    with patch.object(bootstrap, "_post_json", side_effect=fake_post_json):
        result = bootstrap.ensure_source_provisioning(
            source_ingest_url="http://mock-source:8097",
            persona_id="persona-1",
            required_data_sources=list(bootstrap.DEV_US_REQUIRED_DATA_SOURCES),
            controller_token="controller-token-test",
        )

    assert result == RECONCILE_OK_RESPONSE[1]
    assert captured["url"] == "http://mock-source:8097/api/source-ingest/persona-source-provisioning/reconcile"
    assert captured["payload"]["dry_run"] is False
    assert captured["payload"]["persona"]["persona_id"] == "persona-1"
    assert captured["payload"]["persona"]["required_data_sources"] == list(
        bootstrap.DEV_US_REQUIRED_DATA_SOURCES
    )
    assert captured["headers"]["Authorization"] == "Bearer controller-token-test"


def test_source_provisioning_raises_on_unsupported_requirement() -> None:
    with patch.object(
        bootstrap,
        "_post_json",
        return_value=(200, {"summary": {"mutated": 0, "unsupported": 1, "conflicts": 0}}),
    ), pytest.raises(bootstrap.BootstrapError, match="unsupported"):
        bootstrap.ensure_source_provisioning(
            source_ingest_url="http://mock-source:8097",
            persona_id="persona-1",
            required_data_sources=list(bootstrap.DEV_US_REQUIRED_DATA_SOURCES),
            controller_token="controller-token-test",
        )


def test_source_provisioning_raises_on_non_200() -> None:
    with patch.object(
        bootstrap, "_post_json", return_value=(500, {"error": "boom"})
    ), pytest.raises(bootstrap.BootstrapError, match="prerequisite failed"):
        bootstrap.ensure_source_provisioning(
            source_ingest_url="http://mock-source:8097",
            persona_id="persona-1",
            required_data_sources=list(bootstrap.DEV_US_REQUIRED_DATA_SOURCES),
            controller_token="controller-token-test",
        )


def test_controller_token_prefers_explicit_env_over_file(tmp_path) -> None:
    token_file = tmp_path / "controller_token"
    token_file.write_text("file-token\n", encoding="utf-8")
    env = {
        "SOURCE_INGEST_CONTROLLER_TOKEN": "explicit-token",
        "SOURCE_INGEST_CONTROLLER_TOKEN_FILE": str(token_file),
    }
    assert bootstrap.source_ingest_controller_token(env) == "explicit-token"


def test_controller_token_reads_from_file_when_unset(tmp_path) -> None:
    token_file = tmp_path / "controller_token"
    token_file.write_text("file-token\n", encoding="utf-8")
    env = {"SOURCE_INGEST_CONTROLLER_TOKEN_FILE": str(token_file)}
    assert bootstrap.source_ingest_controller_token(env) == "file-token"


def test_controller_token_empty_when_unconfigured() -> None:
    assert bootstrap.source_ingest_controller_token({}) == ""
