from __future__ import annotations

import os
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
    assert post.call_count == 3
    login = post.call_args_list[0]
    assert login.args[1]["client_id"] == "operator-a-id"
    assert login.args[1]["client_secret"] == "operator-a-secret"
    first_create = post.call_args_list[1]
    second_create = post.call_args_list[2]
    assert first_create.kwargs["headers"]["Idempotency-Key"] == bootstrap.DEFAULT_IDEMPOTENCY_KEY
    assert second_create.kwargs["headers"]["Idempotency-Key"] == bootstrap.DEFAULT_IDEMPOTENCY_KEY
    assert first_create.kwargs["headers"]["Authorization"] == "Bearer short-lived"


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
