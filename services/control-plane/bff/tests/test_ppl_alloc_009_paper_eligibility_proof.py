from __future__ import annotations

import io
import os
import sys
import urllib.error
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from paper_eligibility_proof import (
    BENCHMARK_VERSION,
    EXPECTED_IDEMPOTENCY_KEY,
    PaperEligibilityObservationStore,
    RUN_KEY,
    TASK_ID,
    build_telemetry_event,
    run_positive_control,
)


PERSONA_ID = "persona-34ac77f34d030185079d"
RUNTIME_BINDING_ID = "00000000-0000-4000-8000-000000000009"
RUNTIME_ID = "runtime-ppl-alloc-009"
TEST_OBSERVED_AT = "2026-07-24T17:30:00Z"


def _identity(
    authorization: str | None,
    mfa_token: str | None = None,
    session_cookie: str | None = None,
) -> bff_main.OperatorIdentity:
    del session_cookie
    token = str(authorization or "").removeprefix("Bearer ")
    roles = ["viewer"] if token.startswith("viewer") else ["operator"]
    return bff_main.OperatorIdentity(
        operator_id=token or "missing",
        roles=roles,
        mfa_verified=bool(mfa_token) or token.endswith(":mfa"),
        claims={
            "sub": token or "missing",
            "tenant_id": "tenant-dev",
            "roles": roles,
        },
        token_kind="structured",
    )


def _runtime_binding() -> dict[str, Any]:
    return {
        "runtime_binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
        "persona_id": PERSONA_ID,
        "capital_pool_id": "pool-persona-paper-ppl-alloc-009",
        "artifact_id": "artifact-persona-paper-ppl-alloc-009",
        "artifact_version": "1.0.0",
        "plan_id": "plan-persona-paper-ppl-alloc-009",
        "persona_capital_binding_id": "pcb-persona-paper-ppl-alloc-009",
        "strategy_id": "strategy-persona-ppl-alloc-009",
        "deployment_mode": "paper",
        "execution_mode": "paper",
        "status": "running",
        "effective_at": "2026-07-01T00:00:00Z",
        "retired_at": None,
    }


def _request_body() -> dict[str, str]:
    return {
        "task_id": TASK_ID,
        "run_key": RUN_KEY,
        "benchmark_version": BENCHMARK_VERSION,
    }


def _enable_strict_dev(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_LIVE_BROKER_ENABLED", "false")
    monkeypatch.setenv("PANTHEON_CANARY_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry:8083")
    monkeypatch.setenv("PANTHEON_PPL_ALLOC_009_READBACK_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(bff_main, "_extract_identity", _identity)
    monkeypatch.setattr(
        bff_main,
        "_ppl_alloc_009_eligibility_observation_store",
        type(
            "FixedObservationStore",
            (),
            {
                "reserve": staticmethod(
                    lambda *, idempotency_key, proposed_at: TEST_OBSERVED_AT
                )
            },
        )(),
    )


def _route_path() -> str:
    return (
        f"/bff/management/personas/{PERSONA_ID}/"
        "ppl-alloc-009-paper-eligibility-proof"
    )


def _headers(
    *,
    authorization: str = "Bearer paper-operator:mfa",
    idempotency_key: str = EXPECTED_IDEMPOTENCY_KEY,
) -> dict[str, str]:
    return {
        "Authorization": authorization,
        "Idempotency-Key": idempotency_key,
    }


def _success_context() -> dict[str, Any]:
    runtime_binding = _runtime_binding()
    return {
        "runtime_binding": runtime_binding,
        "strategy_id": "strategy-persona-ppl-alloc-009",
        "paper_session_id": "session-ppl-alloc-009",
        "paper_ledger_id": "paper-ledger-ppl-alloc-009",
        "capital": {
            "capital_pool_id": runtime_binding["capital_pool_id"],
            "binding_id": runtime_binding["persona_capital_binding_id"],
        },
    }


def _accepted_event_readback() -> dict[str, Any]:
    event, _benchmark = build_telemetry_event(
        persona_id=PERSONA_ID,
        actor_id="paper-operator:mfa",
        idempotency_key=EXPECTED_IDEMPOTENCY_KEY,
        observed_at=TEST_OBSERVED_AT,
        runtime_binding=_runtime_binding(),
        strategy_id="strategy-persona-ppl-alloc-009",
    )
    return event


def _mock_success_dependencies(monkeypatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(
        bff_main,
        "_ppl_alloc_009_paper_eligibility_context",
        lambda **_kwargs: _success_context(),
    )
    emitted: list[dict[str, Any]] = []

    def owner_post(_url: str, event: dict[str, Any]) -> dict[str, str]:
        emitted.append(event)
        return {"status": "accepted"}

    monkeypatch.setattr(bff_main, "_post_json", owner_post)
    monkeypatch.setattr(
        bff_main,
        "_get_json",
        lambda _url: _accepted_event_readback(),
    )
    monkeypatch.setattr(
        bff_main,
        "_pm12_persona_league_rows",
        lambda **_kwargs: [{"persona_id": PERSONA_ID}],
    )
    monkeypatch.setattr(
        bff_main,
        "_pm12_persona_league_ranking_item",
        lambda row: {
            **row,
            "eligible": True,
            "overall_score": 90.0,
            "components": {
                "overall_score": 90.0,
                "risk_score": 93.0,
                "execution_score": 99.0,
                "activity_score": 30.0,
            },
        },
    )
    return emitted


def _context_records() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    runtime_binding = _runtime_binding()
    persona = {
        "persona_id": PERSONA_ID,
        "name": f"PPL ALLOC 009 {RUN_KEY}",
        "lifecycle_state": "paper_running",
        "metadata": {
            "tenant_id": "tenant-dev",
            "provisioning_idempotency_key": (
                "ppl-alloc-009-30095677466-persona-create"
            ),
            "capital_mode": "paper",
            "deployment_stage": "paper",
            "live_capital_enabled": False,
            "live_write_enabled": False,
            "order_side_effects_allowed": False,
            "capital_side_effects_allowed": False,
            "runtime_binding_id": RUNTIME_BINDING_ID,
        },
    }
    ranking_item = {
        "persona_id": PERSONA_ID,
        "stage": "paper_running",
        "capital_scope": "paper_ledger",
        "runtime_resolution": "active",
        "session_resolution": "active",
        "session_id": "session-ppl-alloc-009",
        "paper_ledger_id": "paper-ledger-ppl-alloc-009",
        "runtime_ids": [RUNTIME_ID],
    }
    plan = {
        "plan_id": runtime_binding["plan_id"],
        "strategy_id": runtime_binding["strategy_id"],
    }
    return persona, ranking_item, runtime_binding, plan


def _mock_context_dependencies(
    monkeypatch,
    *,
    persona: dict[str, Any],
    ranking_item: dict[str, Any],
    runtime_binding: dict[str, Any],
    plan: dict[str, Any] | None,
) -> None:
    monkeypatch.setattr(
        bff_main,
        "_bff_me_tenant_payload",
        lambda _identity, requested_tenant=None: {"id": "tenant-dev"},
    )
    monkeypatch.setattr(
        bff_main.read_store,
        "get_persona",
        lambda _persona_id: persona,
    )
    monkeypatch.setattr(
        bff_main,
        "_pm12_persona_league_rows",
        lambda **_kwargs: [{"persona_id": PERSONA_ID}],
    )
    monkeypatch.setattr(
        bff_main,
        "_pm12_persona_league_ranking_item",
        lambda _row: ranking_item,
    )
    monkeypatch.setattr(
        bff_main,
        "_ppl_alloc_009_paper_capital_context",
        lambda **_kwargs: {
            "capital_pool_id": "pool-persona-paper-ppl-alloc-009",
            "binding_id": "pcb-persona-paper-ppl-alloc-009",
            "current_weight": 0.0,
        },
    )
    monkeypatch.setattr(
        bff_main.read_store,
        "list_runtime_bindings",
        lambda: [runtime_binding],
    )
    monkeypatch.setattr(
        bff_main.read_store,
        "get_deployment_plan",
        lambda _plan_id: plan,
    )


def test_positive_control_calculates_metrics_from_paper_trades() -> None:
    result = run_positive_control()

    assert result["metrics"] == {
        "pnl": 0.8,
        "drawdown": 0.0,
        "fill_rate": 1.0,
        "avg_slippage_bps": 0.5,
        "total_trades": 64,
    }
    assert result["scenario"]["trade_attempts"] == 64
    assert result["scenario"]["filled_trades"] == 64
    assert len(result["scenario_digest"]) == 64


def test_event_is_immutable_paper_only_and_idempotent() -> None:
    first, benchmark = build_telemetry_event(
        persona_id=PERSONA_ID,
        actor_id="paper-operator",
        idempotency_key=EXPECTED_IDEMPOTENCY_KEY,
        observed_at=TEST_OBSERVED_AT,
        runtime_binding=_runtime_binding(),
        strategy_id="strategy-persona-ppl-alloc-009",
    )
    second, _ = build_telemetry_event(
        persona_id=PERSONA_ID,
        actor_id="paper-operator",
        idempotency_key=EXPECTED_IDEMPOTENCY_KEY,
        observed_at=TEST_OBSERVED_AT,
        runtime_binding=_runtime_binding(),
        strategy_id="strategy-persona-ppl-alloc-009",
    )

    assert first == second
    assert first["created_at"] == TEST_OBSERVED_AT
    assert first["deployment_stage"] == "paper"
    assert first["execution_mode"] == "paper"
    assert first["metrics"] == benchmark["metrics"]
    assert first["metadata"]["submitted_to_broker"] is False
    assert first["metadata"]["is_real_capital"] is False
    assert first["metadata"]["canary_execution_enabled"] is False
    assert first["metadata"]["live_execution_enabled"] is False


def test_observation_store_reuses_first_timestamp_across_retries(tmp_path) -> None:
    store = PaperEligibilityObservationStore(
        str(tmp_path / "proof-observations.sqlite3")
    )

    first = store.reserve(
        idempotency_key=EXPECTED_IDEMPOTENCY_KEY,
        proposed_at=TEST_OBSERVED_AT,
    )
    second = store.reserve(
        idempotency_key=EXPECTED_IDEMPOTENCY_KEY,
        proposed_at="2026-07-24T17:31:00Z",
    )

    assert first == TEST_OBSERVED_AT
    assert second == TEST_OBSERVED_AT


def test_route_feature_flag_defaults_off_before_owner_write(monkeypatch) -> None:
    _enable_strict_dev(monkeypatch)
    monkeypatch.delenv("PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED")
    owner_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, payload: owner_calls.append(payload),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 403, response.text
    assert owner_calls == []
    assert "dev proof is disabled" in response.text


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("PANTHEON_ENV", "prod"),
        ("PANTHEON_BFF_AUTH_MODE", "permissive"),
        ("PANTHEON_BFF_AUTH_STUB", "true"),
        ("PANTHEON_LIVE_BROKER_ENABLED", "true"),
        ("PANTHEON_CANARY_EXECUTION_ENABLED", "true"),
    ],
)
def test_route_strict_dev_safety_matrix_fails_before_owner_write(
    monkeypatch,
    env_name: str,
    value: str,
) -> None:
    _enable_strict_dev(monkeypatch)
    monkeypatch.setenv(env_name, value)
    owner_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, payload: owner_calls.append(payload),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 403, response.text
    assert owner_calls == []


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer paper-operator",
        "Bearer viewer:mfa",
    ],
)
def test_route_requires_operator_and_mfa(
    monkeypatch,
    authorization: str,
) -> None:
    _enable_strict_dev(monkeypatch)
    owner_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, payload: owner_calls.append(payload),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(authorization=authorization),
        json=_request_body(),
    )

    assert response.status_code == 403, response.text
    assert owner_calls == []


@pytest.mark.parametrize(
    "override",
    [
        {"metrics": {"pnl": 1.0}},
        {"capital_pool_id": "pool-real"},
        {"target_weight": 1.0},
        {"live_execution_enabled": True},
        {"canary_execution_enabled": True},
    ],
)
def test_route_rejects_client_metric_capital_and_execution_overrides(
    monkeypatch,
    override: dict[str, Any],
) -> None:
    _enable_strict_dev(monkeypatch)
    owner_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, payload: owner_calls.append(payload),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json={**_request_body(), **override},
    )

    assert response.status_code == 422, response.text
    assert owner_calls == []


def test_route_rejects_wrong_idempotency_key_before_owner_write(monkeypatch) -> None:
    _enable_strict_dev(monkeypatch)
    owner_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, payload: owner_calls.append(payload),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(idempotency_key="ppl-alloc-009-wrong-retry"),
        json=_request_body(),
    )

    assert response.status_code == 422, response.text
    assert owner_calls == []


@pytest.mark.parametrize(
    "invalid_case",
    [
        "persona_name",
        "provisioning_key",
        "runtime_identity",
        "declared_binding",
        "capital_pool",
        "persona_capital_binding",
        "artifact",
        "strategy_identity",
        "deployment_plan",
        "effective_after_observation",
        "retired_before_observation",
    ],
)
def test_context_rejects_wrong_authority_and_lineage(
    monkeypatch,
    invalid_case: str,
) -> None:
    persona, ranking_item, runtime_binding, plan = _context_records()
    if invalid_case == "persona_name":
        persona["name"] = "some other Persona"
    elif invalid_case == "provisioning_key":
        persona["metadata"]["provisioning_idempotency_key"] = "wrong"
    elif invalid_case == "runtime_identity":
        ranking_item["runtime_ids"] = ["runtime-other"]
    elif invalid_case == "declared_binding":
        persona["metadata"]["runtime_binding_id"] = "binding-other"
    elif invalid_case == "capital_pool":
        runtime_binding["capital_pool_id"] = "pool-other"
    elif invalid_case == "persona_capital_binding":
        runtime_binding["persona_capital_binding_id"] = "pcb-other"
    elif invalid_case == "artifact":
        runtime_binding.pop("artifact_id")
    elif invalid_case == "strategy_identity":
        runtime_binding.pop("strategy_id")
        plan.pop("strategy_id")
    elif invalid_case == "deployment_plan":
        plan = None
    elif invalid_case == "effective_after_observation":
        runtime_binding["effective_at"] = "2026-07-25T00:00:00Z"
    elif invalid_case == "retired_before_observation":
        runtime_binding["retired_at"] = "2026-07-23T00:00:00Z"

    _mock_context_dependencies(
        monkeypatch,
        persona=persona,
        ranking_item=ranking_item,
        runtime_binding=runtime_binding,
        plan=plan,
    )

    with pytest.raises(Exception) as exc_info:
        bff_main._ppl_alloc_009_paper_eligibility_context(
            persona_id=PERSONA_ID,
            identity=_identity("Bearer paper-operator:mfa"),
            observed_at=TEST_OBSERVED_AT,
        )

    assert getattr(exc_info.value, "status_code", None) in {404, 422}


def test_context_accepts_authoritative_deployment_plan_strategy_id(
    monkeypatch,
) -> None:
    persona, ranking_item, runtime_binding, plan = _context_records()
    runtime_binding.pop("strategy_id")
    _mock_context_dependencies(
        monkeypatch,
        persona=persona,
        ranking_item=ranking_item,
        runtime_binding=runtime_binding,
        plan=plan,
    )

    context = bff_main._ppl_alloc_009_paper_eligibility_context(
        persona_id=PERSONA_ID,
        identity=_identity("Bearer paper-operator:mfa"),
        observed_at=TEST_OBSERVED_AT,
    )

    assert context["strategy_id"] == plan["strategy_id"]


def test_route_emits_to_owner_and_returns_governed_response_schema(
    monkeypatch,
) -> None:
    _enable_strict_dev(monkeypatch)
    emitted = _mock_success_dependencies(monkeypatch)
    benchmark = run_positive_control()

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 202, response.text
    body = response.json()["data"]
    assert len(emitted) == 1
    assert emitted[0]["metrics"] == benchmark["metrics"]
    assert body["owner_receipt"]["status"] == "accepted"
    assert body["ranking"]["recommendation_action_ids"][0] == (
        "promote_to_canary_candidate"
    )
    assert body["owner_receipt"] == {
        "service": "telemetry",
        "status": "accepted",
        "accepted_event_id": body["event_id"],
        "reconciliation": "accepted",
        "readback_attempts": 1,
        "readback_event_id": body["event_id"],
        "readback_created_at": TEST_OBSERVED_AT,
    }
    assert body["safety"] == {
        "paper_only": True,
        "real_capital_side_effects": False,
        "real_order_side_effects": False,
        "canary_execution_enabled": False,
        "live_execution_enabled": False,
    }


def _http_error(status_code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://telemetry:8083/api/telemetry/ingest",
        status_code,
        "owner response",
        {},
        io.BytesIO(b"{}"),
    )


def test_route_reconciles_owner_http_409_as_idempotent_replay(
    monkeypatch,
) -> None:
    _enable_strict_dev(monkeypatch)
    _mock_success_dependencies(monkeypatch)
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, _event: (_ for _ in ()).throw(_http_error(409)),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 202, response.text
    receipt = response.json()["data"]["owner_receipt"]
    assert receipt["status"] == "idempotent_replay"
    assert receipt["reconciliation"] == "http_409_readback"
    assert receipt["readback_attempts"] == 1


def test_route_reconciles_uncertain_timeout_when_owner_readback_exists(
    monkeypatch,
) -> None:
    _enable_strict_dev(monkeypatch)
    _mock_success_dependencies(monkeypatch)
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, _event: (_ for _ in ()).throw(
            TimeoutError("owner timed out after accepting")
        ),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 202, response.text
    receipt = response.json()["data"]["owner_receipt"]
    assert receipt["status"] == "write_outcome_uncertain"
    assert receipt["reconciliation"] == "uncertain_write_readback"


@pytest.mark.parametrize(
    ("owner_result", "expected_detail"),
    [
        (_http_error(400), "HTTP 400"),
        ({}, "invalid receipt"),
    ],
)
def test_route_fails_closed_on_owner_rejection_or_malformed_receipt(
    monkeypatch,
    owner_result: Exception | dict[str, Any],
    expected_detail: str,
) -> None:
    _enable_strict_dev(monkeypatch)
    _mock_success_dependencies(monkeypatch)
    readback_calls: list[str] = []

    def owner_post(_url: str, _event: dict[str, Any]) -> dict[str, Any]:
        if isinstance(owner_result, Exception):
            raise owner_result
        return owner_result

    monkeypatch.setattr(bff_main, "_post_json", owner_post)
    monkeypatch.setattr(
        bff_main,
        "_get_json",
        lambda url: readback_calls.append(url),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 502, response.text
    assert expected_detail.lower() in response.text.lower()
    assert readback_calls == []


def test_route_fails_closed_when_timeout_has_no_owner_readback(
    monkeypatch,
) -> None:
    _enable_strict_dev(monkeypatch)
    _mock_success_dependencies(monkeypatch)
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, _event: (_ for _ in ()).throw(
            TimeoutError("owner outcome uncertain")
        ),
    )
    monkeypatch.setattr(
        bff_main,
        "_get_json",
        lambda _url: (_ for _ in ()).throw(_http_error(404)),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 502, response.text
    assert "readback did not prove" in response.text


def test_route_polls_boundedly_until_exact_owner_event_is_available(
    monkeypatch,
) -> None:
    _enable_strict_dev(monkeypatch)
    emitted = _mock_success_dependencies(monkeypatch)
    monkeypatch.setenv(
        "PANTHEON_PPL_ALLOC_009_READBACK_TIMEOUT_SECONDS",
        "0.25",
    )
    monkeypatch.setenv(
        "PANTHEON_PPL_ALLOC_009_READBACK_POLL_SECONDS",
        "0.01",
    )
    results: list[Exception | dict[str, Any]] = [
        _http_error(404),
        {"event_id": "wrong-event"},
    ]
    readback_urls: list[str] = []

    def eventual_readback(url: str) -> dict[str, Any]:
        readback_urls.append(url)
        result = results.pop(0) if results else dict(emitted[-1])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(bff_main, "_get_json", eventual_readback)

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 202, response.text
    assert response.json()["data"]["owner_receipt"]["readback_attempts"] == 3
    assert results == []
    assert readback_urls == [
        (
            "http://telemetry:8083/api/telemetry/events/"
            + emitted[-1]["event_id"]
        )
    ] * 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "event-other"),
        ("created_at", "2026-07-24T13:39:59Z"),
        ("binding_id", "binding-other"),
        ("metrics", {"pnl": 0.1}),
    ],
    ids=["event_id", "created_at", "binding", "metrics"],
)
def test_route_rejects_non_exact_owner_event_readback(
    monkeypatch,
    field: str,
    value: Any,
) -> None:
    _enable_strict_dev(monkeypatch)
    emitted = _mock_success_dependencies(monkeypatch)

    def mismatched_readback(_url: str) -> dict[str, Any]:
        return {**emitted[-1], field: value}

    monkeypatch.setattr(bff_main, "_get_json", mismatched_readback)

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        _route_path(),
        headers=_headers(),
        json=_request_body(),
    )

    assert response.status_code == 502, response.text
    assert "readback did not prove" in response.text


def test_pm12_promotion_thresholds_and_weights_remain_unchanged() -> None:
    assert bff_main._PM12_LEAGUE_SCORE_WEIGHTS == {
        "pnl": 0.35,
        "risk": 0.25,
        "execution": 0.25,
        "activity": 0.15,
    }
    below_threshold = {
        "overall_score": 84.999,
        "components": {
            "risk_score": 70.0,
            "execution_score": 65.0,
        },
    }
    at_threshold = {
        "overall_score": 85.0,
        "components": {
            "risk_score": 70.0,
            "execution_score": 65.0,
        },
    }

    assert "promote_to_canary_candidate" not in (
        bff_main._pm12_recommendation_action_ids(below_threshold)
    )
    assert "promote_to_canary_candidate" in (
        bff_main._pm12_recommendation_action_ids(at_threshold)
    )
