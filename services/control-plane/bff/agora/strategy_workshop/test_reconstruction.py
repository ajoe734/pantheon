"""Unit and contract tests for Strategy Reconstruction engine & worker."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

from services.control_plane.bff.agora.strategy_workshop.reconstruction import (
    StrategyReconstructionResult,
    reconstruct_strategy_from_events,
)


def test_strategy_reconstruction_from_basic_messages() -> None:
    workshop_id = "ws-test-recon-001"
    messages = [
        "I want to create a momentum strategy for BTC and ETH.",
        "The signal will be a 20-day moving average crossover with 2% stop loss.",
    ]
    events = [
        {"event_id": "evt-1", "sequence_no": 1, "created_at": "2026-08-13T12:00:00Z"},
        {"event_id": "evt-2", "sequence_no": 2, "created_at": "2026-08-13T12:01:00Z"},
    ]

    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=2,
        events=events,
        messages_content=messages,
    )

    assert isinstance(result, StrategyReconstructionResult)
    assert result.workshop_id == workshop_id
    assert result.based_on_sequence_no == 2

    # The validator does not interpret messages or invent semantic facts.
    assert result.strategy_map.universe.status == "missing"
    assert result.strategy_map.signal_definition.status == "missing"
    assert result.strategy_map.risk_controls.status == "missing"
    assert result.completeness.grade == "insufficient"
    assert result.explicit_facts == []
    assert result.next_best_question is not None
    assert isinstance(result.next_best_question.text, str)
    assert len(result.next_best_question.resolves) > 0

    # Completeness check
    assert result.completeness.grade in {"draftable", "researchable", "trading_room_ready", "insufficient"}


def test_strategy_reconstruction_nbq_uniqueness_and_completeness_derivation() -> None:
    workshop_id = "ws-test-recon-002"
    messages = ["Hello, I want to trade."]
    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=1,
        events=[{"event_id": "evt-1", "sequence_no": 1}],
        messages_content=messages,
    )

    assert result.completeness.grade == "insufficient"
    assert "Semantic reconstruction unavailable" in result.completeness.blockers
    assert result.next_best_question is not None
    # NBQ must resolve hypothesis
    assert "hypothesis.summary" in result.next_best_question.resolves


def test_reconstruct_endpoint_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from fastapi import FastAPI, HTTPException
    from services.control_plane.bff.agora.strategy_workshop.router import create_strategy_workshop_router
    from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
    from types import SimpleNamespace

    store = MemoryWorkshopStore()
    
    def stub_extract_identity(auth_header: str | None, mfa_token: str | None = None):
        return SimpleNamespace(
            user_id="user-test",
            tenant_id="tenant-test",
            roles=["read", "write"],
            token_kind="stub",
            mfa_verified=True,
        )

    def stub_require_role(identity):
        pass

    def stub_error(status_code, code, message, reason=None, **kwargs):
        return HTTPException(status_code=status_code, detail=message)

    def stub_utc_now():
        return "2026-08-13T12:00:00Z"

    # Patch scope resolution to avoid top-level models import dependency in unit test
    import services.control_plane.bff.agora.identity.scope as scope_module
    monkeypatch.setattr(
        scope_module,
        "resolve_agora_user_scope",
        lambda identity, utc_now=None, requested_tenant_id=None: SimpleNamespace(
            user_id=identity.user_id, tenant_id=identity.tenant_id
        ),
    )

    router = create_strategy_workshop_router(
        extract_identity=stub_extract_identity,
        require_read_role=stub_require_role,
        require_write_role=stub_require_role,
        bff_error=stub_error,
        utc_now=stub_utc_now,
        workshop_store=store,
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    session = store.create_session({
        "workshop_id": "ws-test-recon-123",
        "tenant_id": "tenant-test",
        "user_id": "user-test",
    })
    ws_id = session["workshop_id"]

    resp = client.post(
        f"/bff/agora/workshops/{ws_id}/reconstruct",
        headers={"Authorization": "Bearer user-test:tenant-test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["workshop_id"] == ws_id
    assert "strategy_map" in body["data"]
    assert "next_best_question" in body["data"]

    from .semantic_provider import OpenClawOpsClient, OpenClawOpsClientError
    def unavailable(*args, **kwargs):
        raise OpenClawOpsClientError("private upstream error", status_code=503, error_code="UNAVAILABLE")
    monkeypatch.setattr(OpenClawOpsClient, "_request", unavailable)
    store.create_event({"workshop_id": ws_id, "event_type": "message", "actor_type": "operator",
                        "redacted_summary": "I have not defined an entry rule"})
    failed = client.post(f"/bff/agora/workshops/{ws_id}/reconstruct",
                         headers={"Authorization": "Bearer user-test:tenant-test"})
    assert failed.status_code == 503
    assert "private upstream error" not in failed.text



# --------------------------------------------------------------------------- #
# AGORA-WORKSHOP-RECONSTRUCTION-HOSTED-20261127: message admission only queues.
# --------------------------------------------------------------------------- #
_DRAFT = {
    "strategy_map": {"hypothesis": {"status": "confirmed", "summary": "Momentum alpha",
                                    "details": {"message_numbers": [1]}}},
    "explicit_facts": ["Momentum alpha"], "inferences": [], "assumptions": [],
    "contradictions": [], "strategy_spec": None,
    "next_best_question": {"question_id": "q1", "text": "Which signal?",
                           "resolves": ["signal_definition"], "why_now": "Signal is missing"},
}


def _route_client(monkeypatch, provider):
    # The shared BFF harness imports `agora.*` relative to the BFF root.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from services.control_plane.bff.agora.strategy_workshop.semantic_provider import OpenClawOpsClient
    from services.control_plane.bff.tests import test_agora_strategy_workshop as harness

    calls: list = []

    def request(self, method, path, **kwargs):
        calls.append(path)
        return provider()

    monkeypatch.setattr(OpenClawOpsClient, "_request", request)
    client = harness._workshop_client(monkeypatch)
    created = client.post("/bff/agora/workshops", headers={
        "Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": "recon-hosted-create",
    }, json={"initial_message": "Hypothesis: momentum alpha."})
    assert created.status_code == 201, created.text
    workshop_id = created.json()["data"]["workshop_id"]

    def post_message(text: str, key: str):
        return client.post(f"/bff/agora/workshops/{workshop_id}/messages", headers={
            "Authorization": harness._OPERATOR_AUTH, "Idempotency-Key": key,
            "If-Match": harness._get_current_etag(client, workshop_id),
        }, json={"content": text})

    def reconstruct():
        return client.post(f"/bff/agora/workshops/{workshop_id}/reconstruct",
                           headers={"Authorization": harness._OPERATOR_AUTH})

    def card():
        listed = client.app_instance.workshop_store.list_workshop_cards(workshop_id)
        return next(c for c in listed if c["card_type"] == "strategy_reconstruction")

    return calls, post_message, reconstruct, card


def _envelope():
    return {"status": "ok", "data": {"provider": "openclaw", "status": "completed",
                                        "output": {"structured_data": _DRAFT}}}


def test_message_route_queues_the_job_without_running_the_provider(monkeypatch):
    from services.control_plane.bff.agora.strategy_workshop.routes import session as session_routes

    deferred: list = []
    monkeypatch.setattr(session_routes, "run_reconstruction_job", lambda **kw: deferred.append(kw))
    monkeypatch.setenv("PANTHEON_WORKSHOP_RECONSTRUCTION_WAIT_SECONDS", "0")
    calls, post_message, reconstruct, card = _route_client(monkeypatch, _envelope)

    response = post_message("Universe: SPY equities, daily.", "recon-hosted-msg-1")
    assert response.status_code == 202, response.text
    job = response.json()["data"]["reconstruction_job"]
    assert job["job_status"] == "queued" and job["sequence_no"] == response.json()["data"]["sequence_no"]
    assert calls == [], "message admission must not call the provider"
    assert [kw["job_id"] for kw in deferred] == [job["job_id"]]
    assert card()["payload"]["job_status"] == "queued"
    # /reconstruct while the job is queued returns it (202) without a call.
    pending = reconstruct()
    assert pending.status_code == 202, pending.text
    assert pending.json()["meta"]["job_status"] == "queued"
    assert pending.json()["meta"]["job_id"] == job["job_id"]
    assert calls == []


def test_one_provider_call_per_message_and_reconstruct_replays_it(monkeypatch):
    calls, post_message, reconstruct, card = _route_client(monkeypatch, _envelope)
    for index in range(2):
        response = post_message(f"Universe step {index}", f"recon-hosted-msg-{index}")
        assert response.status_code == 202, response.text
        # The queued job ran after the response (background task).
        assert len(calls) == index + 1
        assert card()["status"] == "completed"
        result = reconstruct()
        assert result.status_code == 200, result.text
        assert result.json()["meta"]["job_status"] == "replayed"
        assert result.json()["data"]["reconstruction_id"]
        assert result.json()["data"]["based_on_sequence_no"] == response.json()["data"]["sequence_no"]
        assert len(calls) == index + 1, "a second /reconstruct for the same message must not call the provider"


def test_failed_job_card_and_api_carry_the_typed_reason(monkeypatch):
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClientError

    def refused():
        raise OpenClawOpsClientError("tool call argument 'root' declares unsupported schema keyword(s) ['$defs']",
                                     status_code=422, error_code="OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH")

    calls, post_message, reconstruct, card = _route_client(monkeypatch, refused)
    response = post_message("A long strategy description.", "recon-hosted-fail")
    assert response.status_code == 202, response.text
    failed = card()
    assert failed["status"] == "failed"
    assert failed["payload"]["failure_reason"] == "provider_output_schema_mismatch"
    assert failed["payload"]["adapter_error_code"] == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
    api = reconstruct()
    assert api.status_code == 502, api.text
    details = api.json()["error"]["details"]
    assert details["reason"] == "provider_output_schema_mismatch"
    assert details["adapter_error_code"] == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
    assert "$defs" not in api.text and "$defs" not in str(failed)
    assert len(calls) == 1
