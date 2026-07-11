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

def calculate_rollup_status(stages: dict) -> str:
    # Deterministic roll-up status implementation
    statuses = {name: info["status"] for name, info in stages.items()}
    
    if any(s == "waiting_human" for s in statuses.values()):
        return "waiting_human"
    if any(s == "blocked" for s in statuses.values()):
        return "blocked"
    if any(s in ("rejected", "failed") for s in [statuses["risk_evaluation"], statuses["order_submission"], statuses["broker_acknowledgement"]]):
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
    
    if any(statuses[stage] == "unknown" for stage in required_stages):
        return "incomplete"
        
    if statuses["fill_management"] == "partially_succeeded":
        return "partially_filled"
    if statuses["reconciliation"] == "succeeded":
        return "completed"
    if statuses["reconciliation"] in ("failed", "partially_succeeded"):
        return "completed_with_variance"
    if statuses["order_submission"] == "succeeded" and statuses["fill_management"] == "active":
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
        stage: {"status": "succeeded", "updated_at": "2026-07-11T23:00:00Z"}
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
        "block_reason": "Risk limit exceeded"
    }
    # Downstream execution stages not applicable
    for stage in ["order_submission", "broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation"]:
        payload["stages"][stage] = {
            "status": "not_applicable",
            "updated_at": "2026-07-11T23:01:00Z"
        }
    payload["status"] = "failed"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "failed"

def test_cancel_validation():
    payload = get_base_trade_journey()
    # Order cancelled by operator
    payload["stages"]["order_submission"] = {
        "status": "cancelled",
        "updated_at": "2026-07-11T23:01:00Z"
    }
    for stage in ["broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation"]:
        payload["stages"][stage] = {
            "status": "not_applicable",
            "updated_at": "2026-07-11T23:01:00Z"
        }
    payload["status"] = "cancelled"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "cancelled"

def test_partial_fill_validation():
    payload = get_base_trade_journey()
    # Partial fill
    payload["stages"]["fill_management"] = {
        "status": "partially_succeeded",
        "updated_at": "2026-07-11T23:03:00Z"
    }
    payload["stages"]["ledger_booking"] = {
        "status": "succeeded",
        "updated_at": "2026-07-11T23:04:00Z"
    }
    payload["stages"]["reconciliation"] = {
        "status": "succeeded",
        "updated_at": "2026-07-11T23:05:00Z"
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
        "block_reason": "Quantity mismatch: ledger=1.5, broker=1.4"
    }
    payload["status"] = "completed_with_variance"
    jsonschema.validate(instance=payload, schema=trade_journey_schema)
    assert calculate_rollup_status(payload["stages"]) == "completed_with_variance"
