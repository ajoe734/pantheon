"""Unit and contract tests for Strategy Reconstruction engine & worker."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

_CONTROL_PLANE_DIR = Path(__file__).resolve().parents[3]
if str(_CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(_CONTROL_PLANE_DIR))

from agora.strategy_workshop.reconstruction import (
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

    # Check block statuses
    assert result.strategy_map.universe.status != "missing"
    assert result.strategy_map.signal_definition.status != "missing"
    assert result.strategy_map.risk_controls.status != "missing"

    # Check facts and Next Best Question
    assert len(result.explicit_facts) > 0
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
    assert "Missing core strategy hypothesis" in result.completeness.blockers
    assert result.next_best_question is not None
    # NBQ must resolve hypothesis
    assert "hypothesis.summary" in result.next_best_question.resolves


def test_reconstruct_endpoint_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from fastapi import FastAPI, HTTPException
    from agora.strategy_workshop.router import create_strategy_workshop_router
    from agora.strategy_workshop.store import MemoryWorkshopStore
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
    import agora.identity.scope as scope_module
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

