from __future__ import annotations

import os
import sys
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from paper_eligibility_proof import (
    BENCHMARK_VERSION,
    EXPECTED_IDEMPOTENCY_KEY,
    OBSERVED_AT,
    RUN_KEY,
    TASK_ID,
    build_telemetry_event,
    run_positive_control,
)


PERSONA_ID = "persona-34ac77f34d030185079d"
RUNTIME_BINDING_ID = "00000000-0000-4000-8000-000000000009"
RUNTIME_ID = "runtime-ppl-alloc-009"


def _identity(
    authorization: str | None,
    mfa_token: str | None = None,
    session_cookie: str | None = None,
) -> bff_main.OperatorIdentity:
    del session_cookie
    token = str(authorization or "").removeprefix("Bearer ")
    return bff_main.OperatorIdentity(
        operator_id=token or "missing",
        roles=["operator"],
        mfa_verified=bool(mfa_token) or token.endswith(":mfa"),
        claims={"sub": token or "missing", "tenant_id": "tenant-dev"},
        token_kind="structured",
    )


def _runtime_binding() -> dict[str, Any]:
    return {
        "runtime_binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
        "capital_pool_id": "pool-persona-paper-ppl-alloc-009",
        "artifact_id": "artifact-persona-paper-ppl-alloc-009",
        "artifact_version": "1.0.0",
        "plan_id": "plan-persona-paper-ppl-alloc-009",
        "persona_capital_binding_id": "pcb-persona-paper-ppl-alloc-009",
        "deployment_mode": "paper",
        "execution_mode": "paper",
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
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry:8083")
    monkeypatch.setattr(bff_main, "_extract_identity", _identity)


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
        runtime_binding=_runtime_binding(),
        strategy_id="strategy-persona-ppl-alloc-009",
    )
    second, _ = build_telemetry_event(
        persona_id=PERSONA_ID,
        actor_id="paper-operator",
        idempotency_key=EXPECTED_IDEMPOTENCY_KEY,
        runtime_binding=_runtime_binding(),
        strategy_id="strategy-persona-ppl-alloc-009",
    )

    assert first == second
    assert first["created_at"] == OBSERVED_AT
    assert first["deployment_stage"] == "paper"
    assert first["execution_mode"] == "paper"
    assert first["metrics"] == benchmark["metrics"]
    assert first["metadata"]["submitted_to_broker"] is False
    assert first["metadata"]["is_real_capital"] is False
    assert first["metadata"]["canary_execution_enabled"] is False
    assert first["metadata"]["live_execution_enabled"] is False


def test_route_rejects_client_metrics_before_owner_write(monkeypatch) -> None:
    _enable_strict_dev(monkeypatch)
    owner_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bff_main,
        "_post_json",
        lambda _url, payload: owner_calls.append(payload),
    )

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        (
            f"/bff/management/personas/{PERSONA_ID}/"
            "ppl-alloc-009-paper-eligibility-proof"
        ),
        headers={
            "Authorization": "Bearer paper-operator:mfa",
            "Idempotency-Key": EXPECTED_IDEMPOTENCY_KEY,
        },
        json={**_request_body(), "metrics": {"pnl": 1.0}},
    )

    assert response.status_code == 422, response.text
    assert owner_calls == []


def test_route_emits_to_owner_and_requires_attributed_readback(monkeypatch) -> None:
    _enable_strict_dev(monkeypatch)
    runtime_binding = _runtime_binding()
    context = {
        "runtime_binding": runtime_binding,
        "strategy_id": "strategy-persona-ppl-alloc-009",
        "paper_session_id": "session-ppl-alloc-009",
        "paper_ledger_id": "paper-ledger-ppl-alloc-009",
        "capital": {
            "capital_pool_id": runtime_binding["capital_pool_id"],
            "binding_id": runtime_binding["persona_capital_binding_id"],
        },
    }
    monkeypatch.setattr(
        bff_main,
        "_ppl_alloc_009_paper_eligibility_context",
        lambda **_kwargs: context,
    )
    emitted: list[dict[str, Any]] = []

    def owner_post(_url: str, event: dict[str, Any]) -> dict[str, str]:
        emitted.append(event)
        return {"status": "accepted"}

    benchmark = run_positive_control()
    readback: dict[str, Any] = {
        **benchmark["metrics"],
        "last_event_id": "a-concurrent-heartbeat-may-be-newer",
        "collected_at": OBSERVED_AT,
    }
    for field in benchmark["metrics"]:
        readback[f"{field}_at"] = OBSERVED_AT
        readback[f"{field}_binding_id"] = RUNTIME_BINDING_ID
    monkeypatch.setattr(bff_main, "_post_json", owner_post)
    monkeypatch.setattr(bff_main, "_get_json", lambda _url: readback)
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

    response = TestClient(bff_main.app, raise_server_exceptions=False).post(
        (
            f"/bff/management/personas/{PERSONA_ID}/"
            "ppl-alloc-009-paper-eligibility-proof"
        ),
        headers={
            "Authorization": "Bearer paper-operator:mfa",
            "Idempotency-Key": EXPECTED_IDEMPOTENCY_KEY,
        },
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
    assert body["safety"]["real_capital_side_effects"] is False
