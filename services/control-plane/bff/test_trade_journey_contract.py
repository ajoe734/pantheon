from __future__ import annotations

import json
from pathlib import Path
import pytest
import jsonschema

SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "trade_journey"

def load_schema(filename: str) -> dict:
    path = SPEC_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

correlation_envelope_schema = load_schema("correlation_envelope.schema.json")
research_journey_schema = load_schema("research_journey.schema.json")
strategy_lifecycle_schema = load_schema("strategy_lifecycle.schema.json")
trade_journey_schema = load_schema("trade_journey.schema.json")

def calculate_rollup_status(stages: dict, correction_events: list | None = None) -> str:
    # Deterministic roll-up status implementation
    statuses = {name: info["status"] for name, info in stages.items()}
    
    # Check for conflicting mutually-exclusive terminal states
    # e.g., failed or cancelled stage status alongside succeeded execution/reconciliation stages
    has_failure = any(s in ("rejected", "failed") for s in [statuses.get("risk_evaluation"), statuses.get("order_submission"), statuses.get("broker_acknowledgement")])
    has_cancelled = any(s == "cancelled" for s in statuses.values())
    has_success_execution = any(s in ("succeeded", "partially_succeeded") for s in [statuses.get("fill_management"), statuses.get("ledger_booking"), statuses.get("reconciliation")])
    
    if (has_failure or has_cancelled) and has_success_execution:
        import logging
        logging.error("Data Quality Incident: Conflicting mutually-exclusive terminal states detected")
        return "incomplete"
        
    if any(s == "waiting_human" for s in statuses.values()):
        return "waiting_human"
    if any(s == "blocked" for s in statuses.values()):
        return "blocked"
    if any(s in ("rejected", "failed") for s in [statuses.get("risk_evaluation"), statuses.get("order_submission"), statuses.get("broker_acknowledgement")]):
        return "failed"
    if any(s == "cancelled" for s in statuses.values()):
        return "cancelled"
    
    # Check completeness
    required_stages = [
        "research_rationale", "strategy_candidate", "candidate_evaluation",
        "promotion_decision", "capital_binding", "deployment_runtime",
        "signal_generation", "trade_decision", "risk_evaluation",
        "order_submission", "broker_acknowledgement", "fill_management",
        "ledger_booking", "reconciliation"
    ]
    
    if any(statuses.get(stage) == "unknown" for stage in required_stages):
        return "incomplete"
        
    if statuses.get("fill_management") == "partially_succeeded":
        return "partially_filled"
    if statuses.get("reconciliation") == "succeeded":
        return "completed"
    if statuses.get("reconciliation") in ("failed", "partially_succeeded"):
        if correction_events:
            return "completed"
        return "completed_with_variance"
    if statuses.get("order_submission") == "succeeded" and statuses.get("fill_management") == "active":
        return "executing"
        
    return "open"

def get_base_stages() -> dict:
    stages = [
        "research_rationale", "strategy_candidate", "candidate_evaluation",
        "promotion_decision", "capital_binding", "deployment_runtime",
        "signal_generation", "trade_decision", "risk_evaluation",
        "order_submission", "broker_acknowledgement", "fill_management",
        "ledger_booking", "reconciliation"
    ]
    return {
        stage: {"status": "succeeded", "updated_at": "2026-07-11T23:00:00Z", "revision": 1}
        for stage in stages
    }

def get_base_trade_journey() -> dict:
    return {
        "journey_id": "tj_e2e_002_sample",
        "research_journey_id": "rj_e2e_002_sample",
        "strategy_lifecycle_id": "sl_e2e_002_sample",
        "correlation_id": "corr-12345",
        "trace_id": "trace-67890",
        "environment": "paper",
        "tenant_id": "tenant-pantheon",
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.5,
        "price": 30000.0,
        "status": "completed",
        "stages": get_base_stages(),
        "revision": 1,
        "created_at": "2026-07-11T23:00:00Z",
        "updated_at": "2026-07-11T23:05:00Z"
    }

def test_happy_path_validation():
    payload = get_base_trade_journey()
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "completed"

def test_risk_reject_validation():
    payload = get_base_trade_journey()
    # Risk evaluation rejected downstream execution
    payload["stages"]["risk_evaluation"] = {
        "status": "rejected",
        "updated_at": "2026-07-11T23:01:00Z",
        "block_reason": "Risk limit exceeded",
        "revision": 1
    }
    # Downstream execution stages not applicable
    for stage in ["order_submission", "broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation"]:
        payload["stages"][stage] = {
            "status": "not_applicable",
            "updated_at": "2026-07-11T23:01:00Z",
            "revision": 1
        }
    payload["status"] = "failed"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "failed"

def test_cancel_validation():
    payload = get_base_trade_journey()
    # Order cancelled by operator
    payload["stages"]["order_submission"] = {
        "status": "cancelled",
        "updated_at": "2026-07-11T23:01:00Z",
        "revision": 1
    }
    for stage in ["broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation"]:
        payload["stages"][stage] = {
            "status": "not_applicable",
            "updated_at": "2026-07-11T23:01:00Z",
            "revision": 1
        }
    payload["status"] = "cancelled"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "cancelled"

def test_partial_fill_validation():
    payload = get_base_trade_journey()
    # Partial fill
    payload["stages"]["fill_management"] = {
        "status": "partially_succeeded",
        "updated_at": "2026-07-11T23:03:00Z",
        "revision": 1
    }
    payload["stages"]["ledger_booking"] = {
        "status": "succeeded",
        "updated_at": "2026-07-11T23:04:00Z",
        "revision": 1
    }
    payload["stages"]["reconciliation"] = {
        "status": "succeeded",
        "updated_at": "2026-07-11T23:05:00Z",
        "revision": 1
    }
    payload["status"] = "partially_filled"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "partially_filled"

def test_replace_chain_validation():
    payload = get_base_trade_journey()
    # Represent a replace chain in metadata
    payload["metadata"] = {
        "replace_chain": [
            {"order_id": "ord-1", "action": "replaced"},
            {"order_id": "ord-2", "action": "active"}
        ]
    }
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "completed"

def test_reconciliation_variance_validation():
    payload = get_base_trade_journey()
    # Reconciliation variance (completed with variance)
    payload["stages"]["reconciliation"] = {
        "status": "failed",
        "updated_at": "2026-07-11T23:05:00Z",
        "block_reason": "Quantity mismatch: ledger=1.5, broker=1.4",
        "revision": 1
    }
    payload["status"] = "completed_with_variance"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "completed_with_variance"

def test_conflicting_terminal_states_conflict():
    payload = get_base_trade_journey()
    # Risk evaluation rejected (failure)
    payload["stages"]["risk_evaluation"] = {
        "status": "rejected",
        "updated_at": "2026-07-11T23:01:00Z",
        "revision": 1
    }
    # But reconciliation succeeded (execution success) - mutually exclusive conflict
    payload["stages"]["reconciliation"] = {
        "status": "succeeded",
        "updated_at": "2026-07-11T23:05:00Z",
        "revision": 1
    }
    # This conflicting state must roll up to "incomplete"
    assert calculate_rollup_status(payload["stages"]) == "incomplete"

def test_reconciliation_variance_corrected():
    payload = get_base_trade_journey()
    
    # 1. Mismatch detected (completed_with_variance)
    payload["stages"]["reconciliation"] = {
        "status": "failed",
        "updated_at": "2026-07-11T23:05:00Z",
        "block_reason": "Quantity mismatch: ledger=1.5, broker=1.4",
        "revision": 1
    }
    payload["status"] = "completed_with_variance"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "completed_with_variance"
    
    # 2. Correction event received - read model increments revision and closes the variance
    payload["revision"] = 2
    payload["stages"]["reconciliation"] = {
        "status": "failed",
        "updated_at": "2026-07-11T23:10:00Z",
        "block_reason": "Quantity mismatch: ledger=1.5, broker=1.4 (corrected by evt_corr_001)",
        "revision": 2
    }
    
    correction_event = {
        "event_id": "evt_corr_001",
        "corrected_at": "2026-07-11T23:10:00Z",
        "original_variance_reason": "Quantity mismatch: ledger=1.5, broker=1.4",
        "resolution_action": "manual_fill_adjustment",
        "actor": {
            "type": "human",
            "id": "operator-1"
        }
    }
    payload["correction_events"] = [correction_event]
    payload["status"] = "completed"
    
    # Validate against updated schema
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    
    # Calculate rollup with correction events should resolve to completed
    assert calculate_rollup_status(payload["stages"], payload["correction_events"]) == "completed"


def test_pre_signal_envelope_validation():
    # Pre-signal envelope (no journey_id, carries research_journey_id)
    envelope = {
        "schema_version": "trade-journey-envelope/1",
        "tenant_id": "tenant-pantheon",
        "environment": "paper",
        "research_journey_id": "rj_e2e_002_sample",
        "correlation_id": "corr-12345",
        "trace_id": "trace-67890",
        "event_id": "evt-1",
        "causation_event_id": "evt-0",
        "producer": "persona.ooda",
        "event_time": "2026-07-11T22:00:00Z",
        "received_at": "2026-07-11T22:00:01Z",
        "producer_revision": 1,
    }
    # This should now validate successfully
    jsonschema.validate(instance=envelope, schema=correlation_envelope_schema)

    # Adding journey_id should also validate successfully
    envelope["journey_id"] = "tj_e2e_002_sample"
    jsonschema.validate(instance=envelope, schema=correlation_envelope_schema)

    # Omitting a required field (e.g. correlation_id) should fail validation
    invalid_envelope = envelope.copy()
    del invalid_envelope["correlation_id"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_envelope, schema=correlation_envelope_schema)

