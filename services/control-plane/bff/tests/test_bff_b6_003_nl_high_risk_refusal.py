"""
BFF-B6-003: contract tests for Management NL high-risk refusal policy.

Acceptance covered:
1. Live-capital mutation prompts return HTTP 403 with HIGH_RISK_QUERY_REFUSED.
2. Broker activation prompts report matched_category=broker_activation.
3. Strategy deployment prompts report matched_category=strategy_deployment.
4. Persona activation prompts report matched_category=persona_activation.
5. Runtime control prompts report matched_category=runtime_control.
6. Read-only questions continue to return HTTP 202.
7. Refusals do not create Agora sessions, idempotency records, or SSE events.
"""
from __future__ import annotations

import os
import sys
import tempfile
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b6-003:operator"}


def _fresh_client(td: str) -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.audit_events = []

    def _record_audit(event: dict) -> dict:
        evt = {"auditId": f"audit-{len(store.audit_events) + 1}", **event}
        store.audit_events.append(evt)
        return evt

    store.record_agora_audit_event = _record_audit
    store.get_agora_session = lambda session_id: None
    bff_main.read_store = store
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
    bff_main._sse_buffers["ask"].clear()
    bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
        storage_path="off",
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
    return TestClient(bff_main.app)


def _error_payload(response) -> dict:
    body = response.json()
    return body["error"]


@pytest.mark.parametrize(
    ("question", "expected_category", "expected_pattern"),
    [
        (
            "Please allocate capital from pool alpha to strategy beta",
            "live_capital_mutation",
            "allocate capital",
        ),
        (
            "Can you ENABLE THE LIVE BROKER for this workspace?",
            "broker_activation",
            "enable the live broker",
        ),
        (
            "Deploy strategy carry-arb-v4 now",
            "strategy_deployment",
            "deploy strategy",
        ),
        (
            "Launch persona alpha-ops immediately",
            "persona_activation",
            "launch persona",
        ),
        (
            "Restart runtime rt-paper-001",
            "runtime_control",
            "restart runtime",
        ),
    ],
)
def test_high_risk_questions_return_typed_403(
    question: str,
    expected_category: str,
    expected_pattern: str,
) -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": question},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": f"bff-b6-003-{expected_category}"},
            )

            assert resp.status_code == 403, resp.text
            error = _error_payload(resp)
            details = error["details"]
            assert error["code"] == "OPERATION_NOT_ALLOWED"
            assert error["message"] == (
                "NL query matches high-risk action pattern and was refused by policy"
            )
            assert details["precondition_failed"] == "high_risk_nl_policy"
            assert details["refused"] is True
            assert details["matched_category"] == expected_category
            assert details["matched_pattern"] == expected_pattern
            assert isinstance(details["safe_alternatives"], str)
            assert details["safe_alternatives"]
            assert details["followups"][0]["route"] == "/bff/management/human-inbox"
            assert isinstance(details["audit_id"], str)
            assert details["audit_id"]
        finally:
            bff_main.read_store = original_store
            bff_main._MGMT_NL_IDEMPOTENCY.clear()
            bff_main._sse_buffers["ask"].clear()


def test_read_only_question_still_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "What is the current PnL?", "focus": "portfolio"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-003-readonly"},
            )

            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["data"]["question"] == "What is the current PnL?"
        finally:
            bff_main.read_store = original_store
            bff_main._MGMT_NL_IDEMPOTENCY.clear()
            bff_main._sse_buffers["ask"].clear()


def test_refusal_does_not_create_session_idempotency_record_or_sse(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_collect = bff_main._mgmt_nl_collect_context
        try:
            client = _fresh_client(td)
            store = bff_main.read_store
            session_id = "bff-b6-003-refused-session"

            def fail_if_collected(focus: str, snapshot_at: str, **kwargs):
                raise AssertionError("high-risk refusal must run before surface collection")

            monkeypatch.setattr(bff_main, "_mgmt_nl_collect_context", fail_if_collected)
            resp = client.post(
                "/bff/management/nl/ask",
                json={
                    "question": "Please stop runtime rt-live-001",
                    "session_id": session_id,
                    "focus": "all",
                },
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "bff-b6-003-refused-side-effects"},
            )

            assert resp.status_code == 403, resp.text
            assert store.get_agora_session(session_id) is None
            assert bff_main._MGMT_NL_IDEMPOTENCY == {}
            assert list(bff_main._sse_buffers["ask"]) == []

            audits = store.audit_events
            assert len(audits) == 1
            audit = audits[0]
            assert audit["action"] == "management.nl.high_risk_refused"
            assert audit["reason"] == "high_risk_nl_policy"
            assert audit["matchedCategory"] == "runtime_control"
            assert audit["matchedPattern"] == "stop runtime"
        finally:
            monkeypatch.setattr(bff_main, "_mgmt_nl_collect_context", original_collect)
            bff_main.read_store = original_store
            bff_main._MGMT_NL_IDEMPOTENCY.clear()
            bff_main._sse_buffers["ask"].clear()
