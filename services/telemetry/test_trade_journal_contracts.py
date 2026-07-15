"""Contract and schema integration tests for Trade Episode Projections and Trade Journal events."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
import pytest

try:
    import jsonschema
except ImportError:
    jsonschema = None

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTION_SCHEMA_PATH = REPO_ROOT / "services" / "telemetry" / "trade_episode_projection.schema.json"
EVENT_SCHEMA_PATH = REPO_ROOT / "services" / "telemetry" / "trade_journal_event.schema.json"


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_trade_episode_projection_schema_checks() -> None:
    schema = load_schema(PROJECTION_SCHEMA_PATH)
    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "TradeEpisodeProjection"
    assert "trade_episode_id" in schema["required"]
    assert "status" in schema["required"]
    assert "coverage" in schema["required"]


def test_validate_valid_trade_episode_projection() -> None:
    schema = load_schema(PROJECTION_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # A complete valid long trade episode projection fixture
    valid_projection = {
        "trade_episode_id": str(uuid.uuid4()),
        "environment": "paper",
        "persona_id": "persona-macro",
        "strategy_id": "strategy-quant-01",
        "artifact_id": "art-01",
        "artifact_version": "1.0.0",
        "runtime_binding_id": str(uuid.uuid4()),
        "capital_pool_id": "pool-a",
        "instrument_id": "SPY",
        "side": "long",
        "status": "open",
        "decision_id": str(uuid.uuid4()),
        "proposal_id": str(uuid.uuid4()),
        "evidence_refs": ["ref-doc-123"],
        "trace_ids": [str(uuid.uuid4())],
        "order_ids": ["ord-1", "ord-2"],
        "fill_ids": ["fill-1", "fill-2"],
        "position_snapshot_refs": ["pos-snap-1"],
        "attribution_ref": "attr-456",
        "reflection_id": str(uuid.uuid4()),
        "opened_at": "2026-07-11T12:00:00Z",
        "closed_at": None,
        "entry_actor": "persona",
        "exit_actor": None,
        "exit_reason": None,
        "requested_quantity": 100.0,
        "filled_quantity": 50.0,
        "remaining_quantity": 50.0,
        "vwap": 450.25,
        "fees": 1.50,
        "slippage": 0.05,
        "rejects": [
            {
                "reject_id": "rej-1",
                "rejected_at": "2026-07-11T12:01:00Z",
                "reason": "Size exceeds limit"
            }
        ],
        "realized_pnl": 0.0,
        "unrealized_pnl": 120.50,
        "return_percent": 0.27,
        "mae": -10.0,
        "mfe": 150.0,
        "holding_duration_seconds": None,
        "benchmark_delta": 0.02,
        "thesis": "Macro economic indicators signal upward trend",
        "expected_catalyst": "Fed meeting readout",
        "invalidation_conditions": ["Price falls below 440"],
        "time_horizon": "1d",
        "confidence": 0.85,
        "rationale_source_ref": "decision-journal-789",
        "limits": {"max_drawdown": 500.0},
        "expected_loss": -200.0,
        "stop_exit_plan": "Exit if price drops below invalidation level",
        "approval_refs": ["app-01"],
        "coverage": {
            "state": "complete",
            "missing_refs": [],
            "as_of": "2026-07-11T12:15:00Z",
            "source_system": "lean-telemetry"
        },
        "reflection_summary": None,
        "memory_governance_refs": [],
        "last_event_sequence": 100
    }

    jsonschema.validate(instance=valid_projection, schema=schema)


def test_validate_degraded_unresolved_trade_episode_projection() -> None:
    schema = load_schema(PROJECTION_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # A degraded trade episode projection representing an unresolved join
    degraded_projection = {
        "trade_episode_id": str(uuid.uuid4()),
        "environment": "live",
        "persona_id": "persona-swing",
        "strategy_id": "strategy-unknown",
        "artifact_id": "art-unknown",
        "artifact_version": "unknown",
        "runtime_binding_id": str(uuid.uuid4()),
        "capital_pool_id": "pool-unresolved",
        "instrument_id": "AAPL",
        "side": "short",
        "status": "reflection_failed",
        "coverage": {
            "state": "degraded",
            "missing_refs": ["decision_id", "attribution_ref"],
            "as_of": "2026-07-11T22:00:00Z",
            "source_system": "reconciliation-drift-detector"
        },
        "requested_quantity": 200.0,
        "filled_quantity": 0.0,
        "remaining_quantity": 200.0,
        "fees": 0.0
    }

    jsonschema.validate(instance=degraded_projection, schema=schema)


def test_trade_journal_event_schema_checks() -> None:
    schema = load_schema(EVENT_SCHEMA_PATH)
    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "TradeJournalEvent"
    assert "event_id" in schema["required"]
    assert "event_type" in schema["required"]
    assert "trade_episode_id" in schema["required"]


@pytest.mark.parametrize(
    "event_type,payload",
    [
        ("trade_episode.opened", {"opened_by": "persona"}),
        ("trade_reflection.requested", {"reason": "episode_closed"}),
        ("trade_lesson.proposed", {"lesson_candidate_id": str(uuid.uuid4())})
    ]
)
def test_validate_trade_journal_events(event_type: str, payload: dict[str, Any]) -> None:
    schema = load_schema(EVENT_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "event_type": event_type,
        "occurred_at": "2026-07-11T12:00:00Z",
        "ingested_at": "2026-07-11T12:00:05Z",
        "trace_id": str(uuid.uuid4()),
        "trade_episode_id": str(uuid.uuid4()),
        "persona_id": "persona-macro",
        "environment": "paper",
        "producer": "trade-journal-service",
        "sequence_number": 42,
        "payload": payload
    }

    jsonschema.validate(instance=event, schema=schema)


def test_projection_schema_negative_cases() -> None:
    schema = load_schema(PROJECTION_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # Case 1: Invalid status value
    invalid_projection = {
        "trade_episode_id": str(uuid.uuid4()),
        "environment": "paper",
        "persona_id": "persona-macro",
        "strategy_id": "strategy-quant-01",
        "artifact_id": "art-01",
        "artifact_version": "1.0.0",
        "runtime_binding_id": str(uuid.uuid4()),
        "capital_pool_id": "pool-a",
        "instrument_id": "SPY",
        "side": "long",
        "status": "invalid_status_value_here",
        "coverage": {
            "state": "complete",
            "missing_refs": [],
            "as_of": "2026-07-11T12:15:00Z",
            "source_system": "lean-telemetry"
        }
    }
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(instance=invalid_projection, schema=schema)
    assert "invalid_status_value_here" in str(exc_info.value) or "enum" in str(exc_info.value)

    # Case 2: Negative last_event_sequence value
    invalid_projection_seq = {
        "trade_episode_id": str(uuid.uuid4()),
        "environment": "paper",
        "persona_id": "persona-macro",
        "strategy_id": "strategy-quant-01",
        "artifact_id": "art-01",
        "artifact_version": "1.0.0",
        "runtime_binding_id": str(uuid.uuid4()),
        "capital_pool_id": "pool-a",
        "instrument_id": "SPY",
        "side": "long",
        "status": "open",
        "coverage": {
            "state": "complete",
            "missing_refs": [],
            "as_of": "2026-07-11T12:15:00Z",
            "source_system": "lean-telemetry"
        },
        "last_event_sequence": -5
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_projection_seq, schema=schema)


def test_event_schema_negative_cases() -> None:
    schema = load_schema(EVENT_SCHEMA_PATH)
    if jsonschema is None:
        pytest.skip("jsonschema library not available")

    # Case 1: Missing sequence_number
    invalid_event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "event_type": "trade_episode.opened",
        "occurred_at": "2026-07-11T12:00:00Z",
        "ingested_at": "2026-07-11T12:00:05Z",
        "trace_id": str(uuid.uuid4()),
        "trade_episode_id": str(uuid.uuid4()),
        "persona_id": "persona-macro",
        "environment": "paper",
        "producer": "trade-journal-service"
        # sequence_number is required but missing
    }
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(instance=invalid_event, schema=schema)
    assert "sequence_number" in str(exc_info.value)

    # Case 2: Invalid sequence_number type (string instead of integer)
    invalid_event_seq_type = {
        "event_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "event_type": "trade_episode.opened",
        "occurred_at": "2026-07-11T12:00:00Z",
        "ingested_at": "2026-07-11T12:00:05Z",
        "trace_id": str(uuid.uuid4()),
        "trade_episode_id": str(uuid.uuid4()),
        "persona_id": "persona-macro",
        "environment": "paper",
        "producer": "trade-journal-service",
        "sequence_number": "not_an_integer"
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_event_seq_type, schema=schema)
