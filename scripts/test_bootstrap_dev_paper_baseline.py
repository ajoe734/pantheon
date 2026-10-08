from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
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
    assert bootstrap.DEFAULT_NAME == "Pantheon Dev Paper Baseline 4"
    assert bootstrap.DEFAULT_IDEMPOTENCY_KEY == "dev-paper-bootstrap-20261007-operator-a-tw-v4"


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


def test_ensure_paper_baseline_creates_tw_without_contacting_source_ingest() -> None:
    env = {
        **DEV_ENV,
        "SOURCE_MANAGEMENT_API_URL": "http://127.0.0.1:8097",
        "SOURCE_INGEST_CONTROLLER_TOKEN": "controller-token-test",
    }
    bff_base_url = "http://127.0.0.1:8001"
    responses = [
        (
            200,
            {"access_token": "short-lived", "meta": {"identity": "operator_a"}},
        ),
        (
            201,
            {
                "data": {
                    "id": "persona-tw-1",
                    "state": "paper_running",
                    "capitalMode": "paper",
                },
                "meta": {
                    "provisioning_state": "succeeded",
                    "provisioning_step": "authoritative_readback_complete",
                    "runtime_id": "rt-tw-1",
                    "runtime_binding_id": "rb-tw-1",
                    "deployment_plan_id": "plan-tw-1",
                    "live_capital_side_effects": False,
                },
            },
        ),
    ]
    with patch.dict(os.environ, env, clear=True), patch.object(
        bootstrap, "_post_json", side_effect=responses
    ) as post, patch.object(
        bootstrap, "_get_json", side_effect=AssertionError("No GET requests expected")
    ):
        result = bootstrap.ensure_paper_baseline(
            base_url=bff_base_url,
            name=bootstrap.DEFAULT_NAME,
            idempotency_key=bootstrap.DEFAULT_IDEMPOTENCY_KEY,
            timeout_seconds=30,
            poll_seconds=0.1,
            request_timeout_seconds=5,
            market_symbol="2330.TW",
            monotonic=lambda: 0,
            sleep=lambda _seconds: None,
        )

    assert result["status"] == "ok"
    assert result["persona_id"] == "persona-tw-1"
    assert post.call_count == 2
    for call in post.call_args_list:
        target_url = call.args[0]
        assert target_url.startswith(bff_base_url), f"request targeted unexpected URL: {target_url}"

    create_call = post.call_args_list[1]
    assert create_call.args[0] == f"{bff_base_url}/bff/management/personas/create-paper-bundle"
    payload = create_call.args[1]
    assert payload["market"] == "TW"
    assert payload["symbols"] == ["2330.TW"]
    assert payload["strategy_family"] == "dev_paper_baseline"




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


def test_compose_source_ingest_wires_pantheon_env() -> None:
    """Regression for DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001 P1:
    services.source_ingestion.persona_source_reconciler.is_dev_environment()
    reads PANTHEON_ENV from the process environment, but docker-compose.yml's
    source-ingest service previously omitted it from its environment block,
    so the real API container never saw PANTHEON_ENV=dev and the dev-only
    synthetic simulation connector factory stayed disabled regardless of how
    correct the reconciler/bootstrap code was."""
    import yaml

    repo_root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    source_ingest_env = compose["services"]["source-ingest"]["environment"]
    assert source_ingest_env["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"


def test_compose_paper_fleet_reconciler_wires_pantheon_env() -> None:
    """Regression for DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001: the
    market-input bootstrap grace period
    (PaperFleetReconciler.__init__'s ``is_dev`` check in
    services/paper_fleet_reconciler/paper_fleet_reconciler.py, which raises
    the grace default from 0s to 120s) only ever activates when the process
    sees PANTHEON_ENV=dev. docker-compose.yml's paper-fleet-reconciler
    service previously omitted PANTHEON_ENV from its environment block
    entirely (unlike every other PANTHEON_ENV-scoped service in this file),
    so a never-provisioned connector's market_input_missing pause was never
    deferred and paused the brand-new RuntimeBinding before the dev
    synthetic connector had any chance to produce its first snapshot -- the
    exact ordering gap this task's title names, independent of the
    market_input_missing resume-gap fix in the reconciler's own resume
    defense."""
    import yaml

    repo_root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    reconciler_env = compose["services"]["paper-fleet-reconciler"]["environment"]
    assert reconciler_env["PANTHEON_ENV"] == "${PANTHEON_ENV:-dev}"


def test_compose_operator_bff_wires_owner_service_jwt_credentials() -> None:
    """Regression for DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001 AC5:

    A real, full first-deploy reproduction against the unmodified stack (see
    docs/deployment/evidence/DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001/)
    reached past the source-ingest snapshot precondition this task's P1 fixed
    and hit a second, independent class of gap: the persona provisioning
    coordinator's owner transport
    (_PersonaOwnerHttpTransport._service_jwt in
    services/control-plane/bff/personas/service.py) signs a strict service
    JWT for capital/registry from PANTHEON_CAPITAL_JWT_SECRET /
    PANTHEON_REGISTRY_JWT_SECRET and CAPITAL_JWT_ISSUER / CAPITAL_JWT_AUDIENCE
    (owner-agnostic issuer/audience names used for every strict owner).
    docker-compose.yml's own capital/registry/governance service blocks
    validate against their own, differently-named or differently-defaulted
    env vars (CAPITAL_JWT_SECRET, PANTHEON_REGISTRY_JWT_SECRET,
    PANTHEON_REGISTRY_JWT_ISSUER/AUDIENCE, PANTHEON_GOVERNANCE_JWT_SECRET/
    ISSUER/AUDIENCE) -- so every capital-pool and registry-approval call in a
    first deploy returned 401 regardless of the P1 snapshot-ordering fix.
    This asserts operator-bff and its owner services now chain to the same
    resolved default so a fresh deploy's persona provisioning can sign and
    validate consistently without a second unprovisioned credential."""
    import yaml

    repo_root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    operator_bff_env = services["operator-bff"]["environment"]
    assert operator_bff_env["PANTHEON_CAPITAL_JWT_SECRET"] == (
        "${PANTHEON_CAPITAL_JWT_SECRET:-${CAPITAL_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}}"
    )
    assert operator_bff_env["CAPITAL_JWT_ISSUER"] == (
        "${CAPITAL_JWT_ISSUER:-${PANTHEON_DEV_BFF_JWT_ISSUER:-pantheon-dev-control-plane}}"
    )
    assert operator_bff_env["CAPITAL_JWT_AUDIENCE"] == (
        "${CAPITAL_JWT_AUDIENCE:-${PANTHEON_DEV_BFF_JWT_AUDIENCE:-pantheon-dev-owners}}"
    )
    assert operator_bff_env["PANTHEON_REGISTRY_JWT_SECRET"] == (
        "${PANTHEON_REGISTRY_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}"
    )

    capital_env = services["capital"]["environment"]
    assert capital_env["CAPITAL_JWT_SECRET"] == (
        "${PANTHEON_CAPITAL_JWT_SECRET:-${CAPITAL_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}}"
    )

    registry_env = services["registry"]["environment"]
    assert registry_env["PANTHEON_REGISTRY_JWT_SECRET"] == (
        "${PANTHEON_REGISTRY_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}"
    )
    assert registry_env["PANTHEON_REGISTRY_JWT_ISSUER"] == (
        "${PANTHEON_REGISTRY_JWT_ISSUER:-${CAPITAL_JWT_ISSUER:-${PANTHEON_DEV_BFF_JWT_ISSUER:-pantheon-dev-control-plane}}}"
    )
    assert registry_env["PANTHEON_REGISTRY_JWT_AUDIENCE"] == (
        "${PANTHEON_REGISTRY_JWT_AUDIENCE:-${CAPITAL_JWT_AUDIENCE:-${PANTHEON_DEV_BFF_JWT_AUDIENCE:-pantheon-dev-owners}}}"
    )

    governance_env = services["governance"]["environment"]
    assert governance_env["PANTHEON_GOVERNANCE_JWT_SECRET"] == (
        "${PANTHEON_GOVERNANCE_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}"
    )
    assert governance_env["PANTHEON_GOVERNANCE_JWT_ISSUER"] == (
        "${PANTHEON_GOVERNANCE_JWT_ISSUER:-${PANTHEON_DEV_BFF_JWT_ISSUER:-pantheon-dev-control-plane}}"
    )
    assert governance_env["PANTHEON_GOVERNANCE_JWT_AUDIENCE"] == (
        "${PANTHEON_GOVERNANCE_JWT_AUDIENCE:-${PANTHEON_DEV_BFF_JWT_AUDIENCE:-pantheon-dev-owners}}"
    )

    source_ingest_env = services["source-ingest"]["environment"]
    assert source_ingest_env["PANTHEON_RUNTIME_JWT_SECRET"] == (
        "${PANTHEON_RUNTIME_JWT_SECRET:-${PANTHEON_BFF_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}}"
    )
    assert source_ingest_env["PANTHEON_RUNTIME_JWT_ISSUER"] == (
        "${PANTHEON_RUNTIME_JWT_ISSUER:-${PANTHEON_BFF_JWT_ISSUER:-${PANTHEON_DEV_BFF_JWT_ISSUER:-}}}"
    )
    assert source_ingest_env["PANTHEON_RUNTIME_JWT_AUDIENCE"] == (
        "${PANTHEON_RUNTIME_JWT_AUDIENCE:-${PANTHEON_BFF_JWT_AUDIENCE:-${PANTHEON_DEV_BFF_JWT_AUDIENCE:-}}}"
    )

    deployment_env = services["deployment"]["environment"]
    assert deployment_env["PANTHEON_DEPLOYMENT_JWT_SECRET"] == (
        "${PANTHEON_DEPLOYMENT_JWT_SECRET:-${PANTHEON_BFF_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}}"
    )
    assert deployment_env["PANTHEON_DEPLOYMENT_JWT_ISSUER"] == (
        "${PANTHEON_DEPLOYMENT_JWT_ISSUER:-${PANTHEON_BFF_JWT_ISSUER:-${PANTHEON_DEV_BFF_JWT_ISSUER:-}}}"
    )
    assert deployment_env["PANTHEON_DEPLOYMENT_JWT_AUDIENCE"] == (
        "${PANTHEON_DEPLOYMENT_JWT_AUDIENCE:-${PANTHEON_BFF_JWT_AUDIENCE:-${PANTHEON_DEV_BFF_JWT_AUDIENCE:-}}}"
    )
    assert deployment_env["PANTHEON_DEPLOYMENT_JWKS_URI"] == (
        "${PANTHEON_DEPLOYMENT_JWKS_URI:-${PANTHEON_BFF_JWKS_URI:-${PANTHEON_DEV_BFF_JWKS_URI:-}}}"
    )


def test_compose_resolves_owner_issuer_audience_for_nondefault_dev_values() -> None:
    """Regression for DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001 P2:

    The literal-string assertions above only prove the YAML text of each
    fallback chain; they do not prove what docker compose actually resolves
    a chain to. Independent review reproduced a real mismatch that a literal
    assertion cannot catch: `docker compose --env-file /dev/null config`,
    with only PANTHEON_DEV_BFF_JWT_ISSUER/AUDIENCE set to non-default values
    and CAPITAL_JWT_*/PANTHEON_REGISTRY_JWT_* issuer/audience left unset,
    resolved operator-bff's signed CAPITAL_JWT_ISSUER/AUDIENCE to the
    configured non-default values while registry's
    PANTHEON_REGISTRY_JWT_ISSUER/AUDIENCE fell through to the unrelated
    literal default -- because compose interpolation resolves every
    service's ${VAR:-...} independently against the top-level shell/.env
    environment, never against another service's own already-resolved
    container value, so chaining only through ${CAPITAL_JWT_ISSUER:-...}
    silently breaks whenever the deploy environment exports
    PANTHEON_DEV_BFF_JWT_ISSUER/AUDIENCE but not CAPITAL_JWT_ISSUER/AUDIENCE
    itself (the dev-paper-principal-issuer profile's actual export
    contract). This runs the exact reviewer repro through `docker compose
    config` and asserts operator-bff, registry, and governance all resolve
    to the same issuer/audience the owner-agnostic signer actually emits.
    """
    import shutil
    import subprocess

    import yaml

    if shutil.which("docker") is None:
        pytest.skip("docker is not available in this environment")

    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for key in (
        "CAPITAL_JWT_ISSUER",
        "CAPITAL_JWT_AUDIENCE",
        "PANTHEON_REGISTRY_JWT_ISSUER",
        "PANTHEON_REGISTRY_JWT_AUDIENCE",
        "PANTHEON_GOVERNANCE_JWT_ISSUER",
        "PANTHEON_GOVERNANCE_JWT_AUDIENCE",
        "PANTHEON_DEPLOYMENT_JWT_ISSUER",
        "PANTHEON_DEPLOYMENT_JWT_AUDIENCE",
        "PANTHEON_RUNTIME_JWT_ISSUER",
        "PANTHEON_RUNTIME_JWT_AUDIENCE",
        "PANTHEON_BFF_JWT_ISSUER",
        "PANTHEON_BFF_JWT_AUDIENCE",
    ):
        env.pop(key, None)
    env["PANTHEON_DEV_BFF_JWT_ISSUER"] = "review-test-issuer"
    env["PANTHEON_DEV_BFF_JWT_AUDIENCE"] = "review-test-audience"

    result = subprocess.run(
        ["docker", "compose", "--profile", "root", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr.strip()}")

    compose = yaml.safe_load(result.stdout) if result.stdout.strip().startswith(("{", "[")) else None
    if compose is None:
        import json

        compose = json.loads(result.stdout)
    services = compose["services"]

    bff_env = services["operator-bff"]["environment"]
    registry_env = services["registry"]["environment"]
    governance_env = services["governance"]["environment"]
    deployment_env = services["deployment"]["environment"]
    source_ingest_env = services["source-ingest"]["environment"]

    assert bff_env["CAPITAL_JWT_ISSUER"] == "review-test-issuer"
    assert bff_env["CAPITAL_JWT_AUDIENCE"] == "review-test-audience"
    assert registry_env["PANTHEON_REGISTRY_JWT_ISSUER"] == "review-test-issuer"
    assert registry_env["PANTHEON_REGISTRY_JWT_AUDIENCE"] == "review-test-audience"
    assert governance_env["PANTHEON_GOVERNANCE_JWT_ISSUER"] == "review-test-issuer"
    assert governance_env["PANTHEON_GOVERNANCE_JWT_AUDIENCE"] == "review-test-audience"
    assert deployment_env["PANTHEON_DEPLOYMENT_JWT_ISSUER"] == "review-test-issuer"
    assert deployment_env["PANTHEON_DEPLOYMENT_JWT_AUDIENCE"] == "review-test-audience"
    assert source_ingest_env["PANTHEON_RUNTIME_JWT_ISSUER"] == "review-test-issuer"
    assert source_ingest_env["PANTHEON_RUNTIME_JWT_AUDIENCE"] == "review-test-audience"


def test_compose_deployment_tenant_id_chains_like_every_other_service() -> None:
    """Regression for DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001 AC5 (new,
    this delivery):

    A full hosted reproduction against the complete, unmodified real dev
    paper stack (docs/deployment/evidence/
    DEV-PAPER-SNAPSHOT-PRECONDITION-ORDERING-001/evidence.json) reached
    authoritative paper_running and, on the way, found runtime-manager's and
    deployment-outbox-consumer's PANTHEON_DEPLOYMENT_TENANT_ID hardcoded to
    the literal default `default`, unlike every other service in this file
    which chains through `${PANTHEON_TENANT_ID:-${PANTHEON_BFF_TENANT_ID:-
    default}}`. The dev-paper bootstrap's persona/plan/saga all live under
    tenant `tenant-dev` (the dev-login operator's default allowed tenant,
    matching PANTHEON_DEV_BFF_TENANT_ID's own `tenant-dev` default), so the
    outbox consumer's `X-Tenant-Id: default` header never matched any queued
    `tenant-dev` event and silently claimed zero events forever: the
    deployment saga never advanced past `runtime.binding.requested`, and the
    provisioning coordinator's readback eventually timed out and compensated
    -- with no error surfaced anywhere in the wiring, since the consumer's
    own tenant scoping "worked" for its own (empty) tenant.
    """
    import yaml

    repo_root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    expected = (
        "${PANTHEON_DEPLOYMENT_TENANT_ID:-${PANTHEON_TENANT_ID:-"
        "${PANTHEON_BFF_TENANT_ID:-${PANTHEON_DEV_BFF_TENANT_ID:-default}}}}"
    )
    assert services["runtime-manager"]["environment"]["PANTHEON_DEPLOYMENT_TENANT_ID"] == expected
    assert (
        services["deployment-outbox-consumer"]["environment"]["PANTHEON_DEPLOYMENT_TENANT_ID"]
        == expected
    )


def test_compose_resolves_deployment_tenant_id_for_dev_paper_tenant() -> None:
    """Behavioral counterpart to the literal-string assertion above: proves
    what docker compose actually resolves PANTHEON_DEPLOYMENT_TENANT_ID to
    when only PANTHEON_DEV_BFF_TENANT_ID (the dev-paper-principal-issuer's
    own tenant variable) is set, exactly as a real dev-paper-principals
    activation does. Before this fix both services resolved to the literal
    `default` here regardless, which is the exact mismatch that silently
    stalled the deployment saga in the hosted reproduction."""
    import shutil
    import subprocess

    import yaml

    if shutil.which("docker") is None:
        pytest.skip("docker is not available in this environment")

    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for key in ("PANTHEON_TENANT_ID", "PANTHEON_BFF_TENANT_ID", "PANTHEON_DEPLOYMENT_TENANT_ID"):
        env.pop(key, None)
    env["PANTHEON_DEV_BFF_TENANT_ID"] = "tenant-dev"

    result = subprocess.run(
        ["docker", "compose", "--profile", "root", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr.strip()}")

    compose = yaml.safe_load(result.stdout) if result.stdout.strip().startswith(("{", "[")) else None
    if compose is None:
        import json

        compose = json.loads(result.stdout)
    services = compose["services"]

    assert services["runtime-manager"]["environment"]["PANTHEON_DEPLOYMENT_TENANT_ID"] == "tenant-dev"
    assert (
        services["deployment-outbox-consumer"]["environment"]["PANTHEON_DEPLOYMENT_TENANT_ID"]
        == "tenant-dev"
    )


def test_compose_resolves_owner_secret_and_jwks_with_precedence() -> None:
    """Regression for DEV-EXISTING-OWNER-VERIFIER-BINDINGS-20261005:

    Asserts that docker compose root config resolves deployment and source-ingest
    verifier environment variables from PANTHEON_DEV_BFF_* names and respects
    service-specific overrides.
    """
    import shutil
    import subprocess
    import yaml

    if shutil.which("docker") is None:
        pytest.skip("docker is not available in this environment")

    repo_root = Path(__file__).resolve().parents[1]

    # Baseline case: Only PANTHEON_DEV_BFF_* set
    env = dict(os.environ)
    for key in (
        "PANTHEON_DEPLOYMENT_JWT_SECRET",
        "PANTHEON_DEPLOYMENT_JWKS_URI",
        "PANTHEON_RUNTIME_JWT_SECRET",
        "PANTHEON_BFF_JWT_SECRET",
        "PANTHEON_BFF_JWKS_URI",
    ):
        env.pop(key, None)
    env["PANTHEON_DEV_BFF_JWT_SECRET"] = "dev-bff-secret-value"
    env["PANTHEON_DEV_BFF_JWKS_URI"] = "https://dev.auth.example.com/jwks.json"

    result = subprocess.run(
        ["docker", "compose", "--profile", "root", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr.strip()}")

    compose = yaml.safe_load(result.stdout) if result.stdout.strip().startswith(("{", "[")) else None
    if compose is None:
        import json

        compose = json.loads(result.stdout)
    services = compose["services"]

    assert services["deployment"]["environment"]["PANTHEON_DEPLOYMENT_JWT_SECRET"] == "dev-bff-secret-value"
    assert services["deployment"]["environment"]["PANTHEON_DEPLOYMENT_JWKS_URI"] == "https://dev.auth.example.com/jwks.json"
    assert services["source-ingest"]["environment"]["PANTHEON_RUNTIME_JWT_SECRET"] == "dev-bff-secret-value"

    # Override case: Service-specific overrides take precedence
    env["PANTHEON_DEPLOYMENT_JWT_SECRET"] = "custom-deployment-secret"
    env["PANTHEON_RUNTIME_JWT_SECRET"] = "custom-runtime-secret"
    result_override = subprocess.run(
        ["docker", "compose", "--profile", "root", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result_override.returncode == 0
    compose_override = (
        yaml.safe_load(result_override.stdout)
        if result_override.stdout.strip().startswith(("{", "["))
        else None
    )
    if compose_override is None:
        import json

        compose_override = json.loads(result_override.stdout)
    services_override = compose_override["services"]
    assert services_override["deployment"]["environment"]["PANTHEON_DEPLOYMENT_JWT_SECRET"] == "custom-deployment-secret"
    assert services_override["source-ingest"]["environment"]["PANTHEON_RUNTIME_JWT_SECRET"] == "custom-runtime-secret"


def test_deployment_owner_read_auth_boundary_with_default_bff_consumer(tmp_path, monkeypatch) -> None:
    """Regression for DEV-EXISTING-OWNER-VERIFIER-BINDINGS-20261005 AC1, AC3, AC4:

    Tests real deployment service app with real default BFF consumer
    (create_owner_domain_ports().deployment and read_records). Verifies:
    1. Unconfigured verifier in deployment service rejects real operator token with
       401 AUTH_JWT_UNVERIFIED and BFF existing plan read raises / reports unavailable.
    2. Configured verifier accepts genuine signed operator token and returns
       populated 200 read of existing deployment plan.
    3. Boundary negatives (mismatched tenant, invalid signature, expired token,
       missing token, forbidden role) are rejected with zero store side effects.
    """
    import io
    import sys
    import time
    import urllib.error
    import urllib.request
    from pathlib import Path
    from fastapi.testclient import TestClient

    _cp_gov = Path(__file__).resolve().parent.parent / "services" / "control-plane" / "governance"
    if str(_cp_gov) not in sys.path:
        sys.path.insert(0, str(_cp_gov))

    from services.deployment import service
    from deployment_plan import (
        DeploymentPlan,
        DeploymentPlanStore,
        DeploymentStage,
        PlanStatus,
        RuntimeAction,
        TransitionType,
    )
    from services.control_plane.bff.command_adapters.base import deployment_url
    from services.control_plane.bff.core.owner_reads import (
        authorization,
        create_owner_domain_ports,
        read_records,
        selected_tenant,
    )
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "synthetic-dev-principal-unit-key-4444"
    issuer = "pantheon-dev-control-plane"
    audience = "pantheon-dev-owners"

    plan_store_path = tmp_path / "deployment_plans.json"
    test_store = DeploymentPlanStore(str(plan_store_path))
    plan_id = "plan-persona-paper-32b8fd84b82f35138206"
    test_plan = DeploymentPlan(
        plan_id=plan_id,
        approval_decision_id="approval-paper-test-001",
        artifact_id="artifact-paper-test-001",
        artifact_version="1.0.0",
        artifact_type="model",
        strategy_id="strat-paper-001",
        capital_pool_id="pool-paper-001",
        current_stage=DeploymentStage.PAPER,
        target_stage=DeploymentStage.PAPER,
        transition_type=TransitionType.ACTIVATE,
        runtime_action=RuntimeAction.DEPLOY_NEW_BINDING,
        status=PlanStatus.APPROVED,
        created_at="2026-10-05T00:00:00Z",
        created_by="operator_a",
        metadata={"tenant_id": "tenant-dev"},
    )
    test_store.put(test_plan)

    monkeypatch.setattr(service, "store", test_store)
    monkeypatch.setattr(
        service,
        "planner_service",
        service.DeploymentPlannerService(plan_store=test_store),
    )

    client = TestClient(service.app)

    def fake_urlopen(req, timeout=30):
        method = req.get_method()
        url = req.full_url
        headers = dict(req.header_items())
        body = req.data
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        res = client.request(method, path, headers=headers, content=body)
        if res.status_code >= 400:
            raise urllib.error.HTTPError(
                url, res.status_code, res.text, res.headers, io.BytesIO(res.content)
            )

        class FakeResp:
            status = res.status_code

            def read(self):
                return res.content

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", "http://127.0.0.1:8000")

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
        },
        secret=secret,
    )

    # 1. Unconfigured verifier in deployment service (prior root state)
    monkeypatch.delenv("PANTHEON_DEPLOYMENT_JWT_SECRET", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_JWT_SECRET", raising=False)
    monkeypatch.delenv("PANTHEON_RUNTIME_JWT_SECRET", raising=False)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_AUTH_MODE", "permissive")

    tok_ref = authorization.set(f"Bearer {token}")
    ten_ref = selected_tenant.set("tenant-dev")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            read_records(deployment_url, "/api/deployment/plans")
        assert exc_info.value.code == 401

        ports = create_owner_domain_ports()
        assert ports.deployment.get_surface_status()["status"] == "unavailable"
    finally:
        selected_tenant.reset(ten_ref)
        authorization.reset(tok_ref)

    # 2. Configured verifier (fixed root state)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_ISSUER", issuer)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_AUDIENCE", audience)

    tok_ref = authorization.set(f"Bearer {token}")
    ten_ref = selected_tenant.set("tenant-dev")
    try:
        plans = read_records(deployment_url, "/api/deployment/plans")
        assert len(plans) == 1
        assert plans[0]["plan_id"] == plan_id

        ports = create_owner_domain_ports()
        assert ports.deployment.get_surface_status()["status"] == "ok"
        plans_from_port = ports.deployment.list_deployment_plans()
        assert len(plans_from_port) == 1
        assert plans_from_port[0]["plan_id"] == plan_id
        single_plan = ports.deployment.get_deployment_plan(plan_id)
        assert single_plan is not None
        assert single_plan["plan_id"] == plan_id
    finally:
        selected_tenant.reset(ten_ref)
        authorization.reset(tok_ref)

    # 3. Negatives: tenant, signature, expiry, missing token, forbidden role
    # Missing token
    r_missing = client.get("/api/deployment/plans", headers={"X-Tenant-Id": "tenant-dev"})
    assert r_missing.status_code == 401
    assert r_missing.json()["error_code"] in ("401", "AUTH_TOKEN_MISSING")

    # Invalid signature
    bad_token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
        },
        secret="wrong-secret-key",
    )
    r_bad_sig = client.get(
        "/api/deployment/plans",
        headers={"Authorization": f"Bearer {bad_token}", "X-Tenant-Id": "tenant-dev"},
    )
    assert r_bad_sig.status_code == 401
    assert r_bad_sig.json()["error_code"] == "AUTH_JWT_BAD_SIGNATURE"

    # Expired token
    exp_token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now - 7200,
            "exp": now - 3600,
        },
        secret=secret,
    )
    r_exp = client.get(
        "/api/deployment/plans",
        headers={"Authorization": f"Bearer {exp_token}", "X-Tenant-Id": "tenant-dev"},
    )
    assert r_exp.status_code == 401
    assert r_exp.json()["error_code"] == "AUTH_JWT_EXPIRED"

    # Forbidden role
    role_token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["viewer"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
        },
        secret=secret,
    )
    r_role = client.get(
        "/api/deployment/plans",
        headers={"Authorization": f"Bearer {role_token}", "X-Tenant-Id": "tenant-dev"},
    )
    assert r_role.status_code == 403
    assert r_role.json()["error_code"] == "AUTH_FORBIDDEN"

    # Mismatched tenant
    r_tenant = client.get(
        "/api/deployment/plans",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "other-tenant"},
    )
    assert r_tenant.status_code == 403
    assert r_tenant.json()["error_code"] == "TENANT_BOUNDARY_DENIED"

    # Zero side effects on store
    assert len(test_store.list_all()) == 1
    assert test_store.get(plan_id).plan_id == plan_id


def test_source_ingest_owner_read_auth_boundary_with_runtime_verifier(monkeypatch) -> None:
    """Regression for DEV-EXISTING-OWNER-VERIFIER-BINDINGS-20261005 AC1, AC4:

    Tests source-ingest runtime verifier in strict auth mode:
    1. Unconfigured verifier raises 503 AUTH_JWT_SECRET_MISSING.
    2. Configured verifier with matching secret resolves admitted tenant.
    3. Mismatched tenant, invalid signature, expired, and missing token are rejected.
    """
    import time
    from fastapi import HTTPException
    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.source_ingestion.routers.ingest_operations import _source_read_tenant

    secret = "synthetic-dev-principal-unit-key-4444"
    issuer = "pantheon-dev-control-plane"
    audience = "pantheon-dev-owners"

    now = int(time.time())
    token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
        },
        secret=secret,
    )

    # 1. Unconfigured runtime verifier secret
    monkeypatch.delenv("PANTHEON_RUNTIME_JWT_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        _source_read_tenant(f"Bearer {token}", "tenant-dev")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "AUTH_JWT_SECRET_MISSING"

    # 2. Configured runtime verifier
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_ISSUER", issuer)
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_AUDIENCE", audience)

    resolved = _source_read_tenant(f"Bearer {token}", "tenant-dev")
    assert resolved == "tenant-dev"

    # 3. Negatives
    # Missing token
    with pytest.raises(HTTPException) as exc_info:
        _source_read_tenant(None, "tenant-dev")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] in ("401", "AUTH_TOKEN_MISSING")

    # Invalid signature
    bad_token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
        },
        secret="wrong-secret",
    )
    with pytest.raises(HTTPException) as exc_info:
        _source_read_tenant(f"Bearer {bad_token}", "tenant-dev")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "AUTH_JWT_BAD_SIGNATURE"

    # Expired token
    exp_token = encode_jwt_hs256(
        {
            "sub": "operator_a",
            "roles": ["operator"],
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "iss": issuer,
            "aud": audience,
            "iat": now - 7200,
            "exp": now - 3600,
        },
        secret=secret,
    )
    with pytest.raises(HTTPException) as exc_info:
        _source_read_tenant(f"Bearer {exp_token}", "tenant-dev")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "AUTH_JWT_EXPIRED"

    # Mismatched tenant
    with pytest.raises(HTTPException) as exc_info:
        _source_read_tenant(f"Bearer {token}", "other-tenant")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "TENANT_SCOPE_DENIED"
