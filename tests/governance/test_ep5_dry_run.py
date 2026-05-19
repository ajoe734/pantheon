"""Tests for the EP5 canary proof dry-run command."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from services.governance.ep5_proof.dry_run import (
    EP5DryRunError,
    create_app,
    run_canary_dry_run,
)
from services.governance.ep5_proof.packet_generator import (
    PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS,
    PROOF_FLAG_ORDER_ROUTE_MODE,
)


_VALID_REQUEST = {
    "proof_id": "ep5-proof-dry-run-001",
    "promotion_readiness_packet_id": "prp-dry-run-001",
    "run_id": "canary-dry-run-001",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-canary-001",
    "runtime_binding_id": "rb-canary-001",
    "artifact_id": "artifact-alpha-v1",
    "deployment_plan_id": "dep-canary-001",
    "mode": "validate_only",
    "order_route_mode": "validate_only",
    "started_at": "2026-05-19T18:00:00Z",
    "ended_at": "2026-05-19T18:01:00Z",
    "evidence_refs": ["support/evidence/EP5-006-V2/dry-run.json"],
}


def _client() -> TestClient:
    return TestClient(create_app())


def test_dry_run_endpoint_returns_a2_2_proof_without_live_side_effects():
    response = _client().post("/api/v1/ep5/proofs/dry-run", json=_VALID_REQUEST)

    assert response.status_code == 200
    payload = response.json()

    assert payload["dry_run_id"] == "ep5-proof-dry-run-001"
    assert payload["status"] == "passed"
    assert payload["live_capital_side_effects"] is False
    assert payload["canary_run"]["live_capital_side_effects"] is False

    proof_packet = payload["proof_packet"]
    assert proof_packet["promotion_readiness_packet_id"] == "prp-dry-run-001"
    assert proof_packet["environment"] == "canary"
    assert proof_packet["runtime"] == {
        "runtime_id": "rt-canary-001",
        "runtime_binding_id": "rb-canary-001",
        "artifact_id": "artifact-alpha-v1",
        "deployment_plan_id": "dep-canary-001",
    }
    assert proof_packet["proof"]["order_route_mode"] == "validate_only"
    assert proof_packet["proof"]["live_capital_side_effects"] is False
    assert proof_packet["result"]["pass"] is True

    readiness_packet = payload["promotion_readiness_packet"]
    assert readiness_packet["packet_id"] == "prp-dry-run-001"
    assert readiness_packet["can_proceed"] is True
    assert readiness_packet["flags"][PROOF_FLAG_ORDER_ROUTE_MODE] is True
    assert readiness_packet["flags"][PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS] is False


def test_sandbox_mode_is_safe_and_passes_generator_route_mode_check():
    payload = dict(_VALID_REQUEST, mode="sandbox", order_route_mode="sandbox")

    result = run_canary_dry_run(payload)

    assert result["status"] == "passed"
    assert result["proof_packet"]["proof"]["order_route_mode"] == "sandbox"
    assert result["promotion_readiness_packet"]["can_proceed"] is True
    assert result["promotion_readiness_packet"]["flags"][PROOF_FLAG_ORDER_ROUTE_MODE] is True


def test_live_capital_side_effect_request_is_rejected_fail_closed():
    payload = dict(_VALID_REQUEST, live_capital_side_effects=True)

    response = _client().post("/api/v1/ep5/proofs/dry-run", json=payload)

    assert response.status_code == 422


def test_live_order_route_is_rejected_before_packet_generation():
    payload = dict(_VALID_REQUEST, order_route_mode="live")

    response = _client().post("/api/v1/ep5/proofs/dry-run", json=payload)

    assert response.status_code == 422


def test_runtime_failure_returns_failed_packet_with_no_live_side_effects():
    payload = dict(_VALID_REQUEST, runtime_started=False)

    result = run_canary_dry_run(payload)

    assert result["status"] == "failed"
    assert result["live_capital_side_effects"] is False
    assert result["proof_packet"]["proof"]["live_capital_side_effects"] is False
    assert result["proof_packet"]["result"]["pass"] is False
    assert "CANARY_RUNTIME_NOT_STARTED" in result["proof_packet"]["result"]["blocking_reasons"]


def test_order_route_mode_must_match_mode_for_direct_api_use():
    payload = dict(_VALID_REQUEST, mode="sandbox", order_route_mode="validate_only")

    with pytest.raises(EP5DryRunError, match="order_route_mode"):
        run_canary_dry_run(payload)
