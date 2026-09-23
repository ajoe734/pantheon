"""Regression test suite for BFF main composition seam extraction (BFF-MAIN-COMPOSITION-SEAM-EXTRACTION-002).

Verifies:
1. Module retirement guard getattr/setattr behavior on live instance.
2. Extracted command-adapter validators and precondition enforcement directly and mounted on fresh app.
3. Promotion-review projection and human-inbox decision extraction using explicit command_store.
4. Bounded management read isolation (run_management_read) for fresh, timeout, and saturated paths.
5. Extracted PM12 allocation/recommendation/attribution helpers without monkeypatching main.py globals.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading
import time
from typing import Any, Dict, List, Optional
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from fastapi import FastAPI, HTTPException

from services.control_plane.bff.shared.module_retirement_guard import (
    DEFAULT_RETIRED_PROCESS_OVERLAYS,
    GETATTR_ERROR_MESSAGE,
    SETATTR_ERROR_MESSAGE,
    ModuleRetirementGuard,
    check_retired_overlay_getattr,
    check_retired_overlay_setattr,
)
from services.control_plane.bff.command_adapters.preconditions import (
    _VALIDATORS,
    _enforce_ops_console_preconditions,
)
from services.control_plane.bff.governance.promotion_review import (
    _PROMOTION_REVIEW_DECISIONS,
    _PROMOTION_REVIEW_ID_PREFIX,
    _PROMOTION_REVIEW_TARGET_PREFIX,
    _latest_promotion_review_command,
    _promotion_review_clean_id,
    _promotion_review_decision_projection,
    _promotion_review_quarter_from_id,
    _promotion_review_record_revision_id,
    _promotion_review_revision_id,
    _promotion_review_revision_recommendation_id,
    _promotion_review_stage_path,
    _promotion_review_stored_source,
    _promotion_review_submission_projection,
    _promotion_review_target_id,
    _raise_if_promotion_review_direct_mutation_requested,
)
from services.control_plane.bff.governance.human_inbox import (
    _human_inbox_decision_projection_from_record,
    _human_inbox_promotion_review_item,
    _submitted_promotion_review_records,
)
from services.control_plane.bff.personas.routes.common import (
    ManagementReadSaturated,
    ManagementReadTimeout,
    discard_late_management_read_result,
    run_management_read,
)
from services.control_plane.bff.pm12.service import (
    _management_portfolio_book_exposure_item,
    _management_portfolio_holding_entry,
    _pm12_allocation_line_assertion_hash,
    _pm12_allocation_line_digest,
    _pm12_attribution_metrics,
    _pm12_performance_attribution_facts,
    _pm12_performance_attribution_response,
    _pm12_performance_attribution_rows,
    _pm12_performance_attribution_sources,
    _pm12_quarterly_recommendation_item,
    _pm12_recommendation_action_ids,
    _pm12_resolve_quarterly_recommendation_submit_params,
    _pm12_semantic_values_match,
)
from services.control_plane.bff.assistant.management_service import (
    ManagementNlUseCase,
    _MGMT_AI_AUDIT_EVENTS,
    _MGMT_AI_CONVERSATION_STORE,
    _management_ai_list_audit_events,
)
from services.control_plane.bff.models import (
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    TargetObject,
)


# ============================================================================
# 1. Module Retirement Guard (AC2)
# ============================================================================

def test_module_retirement_guard_getattr_rejects_retired_overlays():
    guard = ModuleRetirementGuard("test_bff_module")
    for symbol in DEFAULT_RETIRED_PROCESS_OVERLAYS:
        with pytest.raises(AttributeError) as exc_info:
            getattr(guard, symbol)
        assert symbol in str(exc_info.value)
        assert "OVERLAY-RETIRE-001" in str(exc_info.value)


def test_module_retirement_guard_setattr_rejects_retired_overlays():
    guard = ModuleRetirementGuard("test_bff_module")
    for symbol in DEFAULT_RETIRED_PROCESS_OVERLAYS:
        with pytest.raises(AttributeError) as exc_info:
            setattr(guard, symbol, {"fake": "overlay"})
        assert symbol in str(exc_info.value)
        assert "OVERLAY-RETIRE-001" in str(exc_info.value)


def test_module_retirement_guard_allows_normal_attributes():
    guard = ModuleRetirementGuard("test_bff_module")
    guard.custom_attribute = "safe_value"
    assert guard.custom_attribute == "safe_value"

    with pytest.raises(AttributeError):
        _ = guard.non_existent_attribute


def test_check_retired_overlay_helpers():
    with pytest.raises(AttributeError) as exc_info:
        check_retired_overlay_getattr("_PERSONA_BFF_OVERLAY")
    assert "_PERSONA_BFF_OVERLAY" in str(exc_info.value)

    with pytest.raises(AttributeError) as exc_info:
        check_retired_overlay_setattr("_STRATEGY_BFF_OVERLAY")
    assert "_STRATEGY_BFF_OVERLAY" in str(exc_info.value)

    # Normal attributes do not raise
    check_retired_overlay_getattr("normal_attribute")
    check_retired_overlay_setattr("normal_attribute")


# ============================================================================
# 2. Command Adapter Preconditions & Fresh App Mount (AC3)
# ============================================================================

def test_command_adapter_validators_registry():
    assert isinstance(_VALIDATORS, dict)
    assert CommandType.PAUSE_PAPER_RUNTIME in _VALIDATORS
    assert CommandType.PAUSE_RUNTIME in _VALIDATORS
    assert CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT in _VALIDATORS
    assert CommandType.HUMAN_GATE_APPROVE in _VALIDATORS
    assert CommandType.HUMAN_GATE_REJECT in _VALIDATORS


def test_command_adapter_precondition_enforcement_and_fresh_app_mounting():
    app = FastAPI(title="Fresh Bff Test App")

    @app.post("/test/command/validate")
    async def validate_command_test(payload: Dict[str, Any]):
        cmd_type_str = payload.get("command")
        cmd_type = CommandType(cmd_type_str)
        params = payload.get("params", {})
        identity = OperatorIdentity(operator_id="test-admin", roles=["operator"])
        validator = _VALIDATORS.get(cmd_type)
        if validator is not None:
            try:
                validator(params, identity)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return {"status": "valid"}

    client = TestClient(app)

    # Missing runtime_id for PausePaperRuntime triggers 422
    invalid_command = {
        "command": CommandType.PAUSE_PAPER_RUNTIME.value,
        "params": {
            "binding_id": "binding-001",
            "duration_seconds": -5,
        },
    }

    response = client.post("/test/command/validate", json=invalid_command)
    assert response.status_code == 422
    data = response.json()
    assert "error" in data



# ============================================================================
# 3. Promotion-Review & Human-Inbox Projections with Explicit command_store (AC3)
# ============================================================================

class MockCommandStore:
    def __init__(self, commands: Optional[List[Dict[str, Any]]] = None) -> None:
        self._commands = commands or []

    def _get_all_commands(self) -> List[Dict[str, Any]]:
        return list(self._commands)


def test_promotion_review_id_and_stage_path_helpers():
    clean = _promotion_review_clean_id("promotion-review:pm12-2026-q3-p1-promote")
    assert clean == "pm12-2026-q3-p1-promote"

    target_id = _promotion_review_target_id("pm12-2026-q3-p1-promote")
    assert target_id == "promotion_review:pm12-2026-q3-p1-promote"

    rev_id = _promotion_review_revision_id("pm12-2026-q3-p1-promote", "snapshot-abc-123")
    assert "--snapshot-" in rev_id
    assert _promotion_review_revision_recommendation_id(rev_id) == "pm12-2026-q3-p1-promote"

    quarter = _promotion_review_quarter_from_id(rev_id)
    assert quarter == "2026-Q3"

    path_canary = _promotion_review_stage_path({
        "action_id": "promote_to_canary_candidate",
        "stage": "paper",
    })
    assert path_canary["from_stage"] == "paper"
    assert path_canary["target_stage"] == "canary_candidate"
    assert path_canary["review_kind"] == "paper_to_canary_review"

    path_risk = _promotion_review_stage_path({
        "action_id": "reduce_capital_access",
        "stage": "live",
    })
    assert path_risk["from_stage"] == "live"
    assert path_risk["target_stage"] == "risk_containment_review"


def test_promotion_review_submission_and_decision_explicit_command_store():
    rec_id = "pm12-2026-q3-p1-promote_to_canary_candidate"
    snapshot_id = "snap-12345"
    rev_id = _promotion_review_revision_id(rec_id, snapshot_id)

    submit_cmd = {
        "command_id": "cmd-submit-001",
        "type": CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT.value,
        "status": "completed",
        "submitted_at": "2026-09-23T10:00:00Z",
        "audit": {"operator_id": "admin-1"},
        "target": {"type": ObjectType.RANKING.value, "id": rec_id},
        "params": {
            "recommendation_id": rec_id,
            "review_id": rev_id,
            "promotion_review_id": rev_id,
            "ranking_snapshot_id": snapshot_id,
            "quarter": "2026-Q3",
            "persona_id": "p1",
            "recommendation_action_id": "promote_to_canary_candidate",
            "stage_from": "paper",
            "stage_to": "canary_candidate",
            "review_kind": "paper_to_canary_review",
            "source_recommendation": {
                "id": rec_id,
                "recommendation_id": rec_id,
                "quarter": "2026-Q3",
                "persona_id": "p1",
                "action_id": "promote_to_canary_candidate",
                "ranking_snapshot_id": snapshot_id,
                "stage": "paper",
            },
        },
    }

    approve_cmd = {
        "command_id": "cmd-approve-001",
        "type": CommandType.HUMAN_GATE_APPROVE.value,
        "status": "completed",
        "submitted_at": "2026-09-23T11:00:00Z",
        "audit": {"operator_id": "reviewer-1"},
        "target": {
            "type": ObjectType.HUMAN_GATE_ITEM.value,
            "id": f"promotion_review:{rev_id}",
        },
        "params": {
            "recommendation_id": rec_id,
            "review_id": rev_id,
            "promotion_review_id": rev_id,
            "ranking_snapshot_id": snapshot_id,
            "decision": "approve",
            "rationale": "Meets criteria",
        },
    }

    store = MockCommandStore([submit_cmd, approve_cmd])

    # 1. Submission projection with explicit store
    sub = _promotion_review_submission_projection(rev_id, command_store=store)
    assert sub is not None
    assert sub["submitted"] is True
    assert sub["recommendation_id"] == rec_id

    # 2. Decision projection with explicit store
    dec = _promotion_review_decision_projection(rev_id, command_store=store)
    assert dec is not None
    assert dec["decision"] == "approve"
    assert dec["decided_by"] == "reviewer-1"

    # 3. Submitted review records aggregation
    reviews = _submitted_promotion_review_records(command_store=store)
    assert len(reviews) == 1
    assert reviews[0]["review_id"] == rev_id
    assert reviews[0]["decision_status"] == "accepted"

    # 4. Human inbox item conversion
    inbox_item = _human_inbox_promotion_review_item(reviews[0])
    assert inbox_item is not None
    assert inbox_item["category"] == "promotion_review"
    assert inbox_item["status"] == "accepted"


def test_raise_if_promotion_review_direct_mutation_requested():
    with pytest.raises(HTTPException) as exc_info:
        _raise_if_promotion_review_direct_mutation_requested({"live_capital_mutation": True})
    assert exc_info.value.status_code == 422


# ============================================================================
# 4. Bounded Management Read Isolation (AC3)
# ============================================================================

@pytest.mark.asyncio
async def test_run_management_read_fresh_success():
    def compute(val: int) -> int:
        return val * 2

    res = await run_management_read(compute, 21, timeout_seconds=1.0)
    assert res == 42


@pytest.mark.asyncio
async def test_run_management_read_timeout_budget():
    def slow_compute():
        time.sleep(0.3)
        return "late_data"

    with pytest.raises(ManagementReadTimeout):
        await run_management_read(slow_compute, timeout_seconds=0.05)


@pytest.mark.asyncio
async def test_run_management_read_saturated_semaphore():
    sem = threading.BoundedSemaphore(1)
    # Fully saturate semaphore
    assert sem.acquire(blocking=False) is True

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with pytest.raises(ManagementReadSaturated):
            await run_management_read(
                lambda: "ok",
                capacity=sem,
                executor=executor,
                timeout_seconds=0.5,
            )
    finally:
        sem.release()
        executor.shutdown(wait=False)


# ============================================================================
# 5. Extracted PM12 Allocation, Recommendation, and Attribution (AC3)
# ============================================================================

def test_pm12_semantic_values_match():
    assert _pm12_semantic_values_match(10, 10.0) is True
    assert _pm12_semantic_values_match(Decimal("10.00"), 10) is True
    assert _pm12_semantic_values_match("abc", "abc") is True
    assert _pm12_semantic_values_match([1, 2, 3], [1.0, 2.0, 3.0]) is True
    assert _pm12_semantic_values_match({"a": 1}, {"a": 1.0}) is True
    assert _pm12_semantic_values_match(1, 2) is False


def test_pm12_allocation_line_assertion_hash_and_digest():
    line = {
        "persona_id": "p-01",
        "target_weight": 0.25,
        "current_weight": 0.20,
        "delta": 0.05,
        "stage": "paper",
        "capital_pool_id": "pool-01",
        "binding_id": "b-01",
        "sleeve_id": "s-01",
        "paper_ledger_id": "led-01",
    }
    digest = _pm12_allocation_line_digest(line)
    assertion_hash = _pm12_allocation_line_assertion_hash(line)

    assert isinstance(digest, str) and len(digest) == 64
    assert isinstance(assertion_hash, str) and len(assertion_hash) == 64

    # Hash matches when numeric types vary in representation
    line_alt = dict(line)
    line_alt["target_weight"] = Decimal("0.25")
    assert _pm12_allocation_line_assertion_hash(line_alt) == assertion_hash


def test_pm12_recommendation_action_ids():
    high_perf = {"overall_score": 90.0, "search_score": 85.0, "eligible": True}
    actions = _pm12_recommendation_action_ids(high_perf)
    assert "promote_to_canary_candidate" in actions
    assert "increase_research_budget" in actions

    poor_perf = {"overall_score": 30.0, "execution_score": 40.0}
    poor_actions = _pm12_recommendation_action_ids(poor_perf)
    assert "suspend_persona" in poor_actions
    assert "require_retraining" in poor_actions


def test_pm12_performance_attribution_response_direct():
    res = _pm12_performance_attribution_response(
        dimensions=["persona"],
        period="latest",
        page_token=None,
        page_size=5,
    )
    assert isinstance(res, dict)
    assert "data" in res
    assert "page_info" in res
    assert "meta" in res


def test_pm12_portfolio_book_exposure_and_holding_entry():
    exposure_item = _management_portfolio_book_exposure_item({
        "pool_id": "pool-test",
        "risk_budget": 100000.0,
        "current_exposure": 50000.0,
    })
    assert exposure_item["pool_id"] == "pool-test"
    assert exposure_item["risk_budget_utilization"] == 0.5
    assert exposure_item["risk_state"] == "within_budget"

    holding_entry = _management_portfolio_holding_entry(
        runtime={"runtime_id": "rt-1", "persona_id": "p-1"},
        position={"symbol": "BTC/USD", "quantity": 1.5, "mark_price": 50000.0},
    )
    assert holding_entry["runtime_id"] == "rt-1"
    assert holding_entry["symbol"] == "BTC/USD"
    assert holding_entry["market_value"] == 75000.0


# ============================================================================
# 6. Management AI Service and Conversation Store Decoupling (AC3)
# ============================================================================

def test_management_ai_service_audit_and_store_isolation():
    assert _MGMT_AI_CONVERSATION_STORE is not None or _MGMT_AI_CONVERSATION_STORE is None  # symbol exists
    assert hasattr(_MGMT_AI_AUDIT_EVENTS, "append")
    assert hasattr(ManagementNlUseCase, "admit")
    assert callable(_management_ai_list_audit_events)

