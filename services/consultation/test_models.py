"""Tests for ConsultRequest and ConsultMemo schema-backed contracts (ASK-002)."""

from __future__ import annotations

import pytest
from jsonschema import Draft7Validator

from services.consultation.models import (
    ConsultMemo,
    ConsultRequest,
    ConsultValidationError,
    load_consult_memo_schema,
    load_consult_request_schema,
    validate_consult_memo_against_request,
    validate_consult_memo_payload,
    validate_consult_request_payload,
)


def _request_payload(**overrides: object) -> dict:
    payload = {
        "request_id": "cr-ask002-001",
        "request_type": "execution_risk",
        "requested_by": {"actor_type": "operator", "actor_id": "operator-risk"},
        "from_persona_id": "persona-risk-chief",
        "target_type": "deployment_plan",
        "target_id": "plan-ask002-001",
        "task": "Review execution risk before the next paper-stage promotion.",
        "consultation_type": "risk_review",
        "context_refs": [
            {"type": "deployment_plan", "id": "plan-ask002-001"},
            "artifact:strategy-alpha-v3",
        ],
        "evidence_refs": ["ev-ask002-001"],
        "priority": "high",
        "status": "submitted",
        "policy_id": "consult-policy-paper-risk-v1",
        "linked_session_id": "cs-ask002-001",
        "request_to_session_status": "session_running",
        "completed_at": None,
        "canceled_at": None,
        "session_handoff_note": "Persona Plane materialized the committee session.",
        "metadata": {"research_only": True},
        "trace_id": "trace-ask002-001",
        "created_at": "2026-05-16T10:20:00Z",
    }
    payload.update(overrides)
    return payload


def _memo_payload(**overrides: object) -> dict:
    payload = {
        "memo_id": "mem-ask002-001",
        "request_id": "cr-ask002-001",
        "memo_type": "risk_review",
        "author_type": "committee",
        "author_ref": "committee-risk",
        "target_type": "deployment_plan",
        "target_id": "plan-ask002-001",
        "summary": "Paper-stage promotion may proceed after tightening slippage guardrails.",
        "findings": [
            {
                "severity": "medium",
                "category": "execution",
                "claim": "Slippage stress remains inside the paper-stage tolerance band.",
                "evidence_refs": ["ev-ask002-001"],
                "recommendation": "Keep the deployment paper-only until one more heartbeat window passes.",
            }
        ],
        "recommendation": "approve_with_conditions",
        "confidence": 0.82,
        "status": "published",
        "trace_id": "trace-ask002-001",
        "created_at": "2026-05-16T10:25:00Z",
        "published_at": "2026-05-16T10:30:00Z",
    }
    payload.update(overrides)
    return payload


def test_consult_schemas_are_valid_draft7() -> None:
    Draft7Validator.check_schema(load_consult_request_schema())
    Draft7Validator.check_schema(load_consult_memo_schema())


def test_consult_request_round_trips_through_model_and_schema() -> None:
    payload = _request_payload()

    validate_consult_request_payload(payload)
    request = ConsultRequest.model_validate(payload)

    assert request.request_id == "cr-ask002-001"
    assert request.context_refs[0].type == "deployment_plan"
    assert request.context_refs[1] == "artifact:strategy-alpha-v3"
    assert request.model_dump(mode="json") == payload
    validate_consult_request_payload(request.model_dump(mode="json"))


def test_consult_request_rejects_unowned_execution_command_fields() -> None:
    payload = _request_payload()
    payload["deployment_command"] = {"action": "rollback"}

    with pytest.raises(ConsultValidationError, match="Additional properties"):
        validate_consult_request_payload(payload)


def test_consult_request_model_rejects_missing_target_identity() -> None:
    with pytest.raises(ValueError, match="target_id"):
        ConsultRequest.model_validate(_request_payload(target_id=""))


def test_consult_memo_round_trips_through_model_and_schema() -> None:
    payload = _memo_payload()

    validate_consult_memo_payload(payload)
    memo = ConsultMemo.model_validate(payload)

    assert memo.memo_id == "mem-ask002-001"
    assert memo.recommendation == "approve_with_conditions"
    assert memo.model_dump(mode="json") == payload
    validate_consult_memo_payload(memo.model_dump(mode="json"))


def test_published_consult_memo_requires_published_at() -> None:
    payload = _memo_payload(published_at=None)

    with pytest.raises(ConsultValidationError, match="published_at"):
        validate_consult_memo_payload(payload)


def test_consult_memo_rejects_direct_side_effect_payloads() -> None:
    payload = _memo_payload()
    payload["broker_order"] = {"symbol": "AAPL", "side": "buy"}

    with pytest.raises(ConsultValidationError, match="Additional properties"):
        validate_consult_memo_payload(payload)


def test_consult_memo_cross_validation_links_to_parent_request() -> None:
    request = ConsultRequest.model_validate(_request_payload())
    memo = ConsultMemo.model_validate(_memo_payload())
    wrong_target = ConsultMemo.model_validate(_memo_payload(target_id="plan-other"))

    assert validate_consult_memo_against_request(memo, request) == []
    assert validate_consult_memo_against_request(wrong_target, request) == [
        "memo.target_id must match request.target_id"
    ]
