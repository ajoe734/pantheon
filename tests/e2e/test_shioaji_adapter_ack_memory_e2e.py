from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.broker.shioaji.adapter import ShioajiBrokerAdapter
from services.broker.shioaji.facade import PROOF_BOUNDARY, ShioajiSandboxFacade
from services.execution.lean_runtime.symbol_parser import SymbolParseError, parse
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_shioaji_adapter_ack_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _tw_market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_tw_market_record()],
                "next_watermark": "2026-06-12T21:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-008-tw-prices",
            "trace_id": "trace-e2e-loop-008-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "2330"
    assert row["metadata"]["close"] == 950.0

    signal = _tw_adapter_signal(row, normalized_ref=normalized_ref, ingest_run_id=ingest_body["run"]["ingest_run_id"])
    with pytest.raises(SymbolParseError):
        parse(signal["symbol"])

    facade = ShioajiSandboxFacade(
        ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=_make_mock_shioaji_api(),
            submit_spacing_seconds=0.0,
        )
    )
    lifecycle = facade.run_lifecycle(
        capital_pool_id="pool-paper-tw-adapter",
        strategy_id=signal["strategy_id"],
        symbol=row["metadata"]["symbol"],
        qty=signal["quantity"],
        side="buy",
        order_type="limit",
        limit_price=row["metadata"]["close"],
        account_kind="stock",
    )

    assert lifecycle["status"] == "passed"
    assert lifecycle["place_result"]["status"] == "submitted"
    assert lifecycle["place_result"]["shioaji_trade_id"] == "mock-e2e-loop-008-trade"
    assert lifecycle["place_result"]["shioaji_order_status"] == "Submitted"
    assert lifecycle["cancel_result"]["status"] == "cancelled"
    assert lifecycle["readback_result"]["status"] == "cancelled"
    assert lifecycle["reconcile_result"]["status"] == "passed"
    assert lifecycle["live_disabled_result"]["status"] == "rejected"
    assert lifecycle["live_disabled_result"]["response"]["error_code"] == "SHIOAJI_LIVE_DISABLED"

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    submitted_event = _adapter_ack_event(
        "order_submitted",
        signal=signal,
        normalized_ref=normalized_ref,
        lifecycle=lifecycle,
        order_payload=lifecycle["place_result"],
        status_field="order_status",
    )
    canceled_event = _adapter_ack_event(
        "order_canceled",
        signal=signal,
        normalized_ref=normalized_ref,
        lifecycle=lifecycle,
        order_payload=lifecycle["readback_result"],
        status_field="readback_status",
    )
    stored_submitted = feedback_adapter.ingest_telemetry_event(
        submitted_event,
        strategy_id=signal["strategy_id"],
        promotion_state="sandbox",
    )
    stored_canceled = feedback_adapter.ingest_telemetry_event(
        canceled_event,
        strategy_id=signal["strategy_id"],
        promotion_state="sandbox",
    )

    recovered = feedback_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_canceled",
        promotion_state="sandbox",
        limit=3,
    )
    assert [event["event_id"] for event in recovered] == [stored_canceled["event_id"]]

    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_canceled,
        sponsor_persona_id="persona-broker-sponsor",
        contributing_persona_ids=["persona-broker-ops"],
        summary=(
            "TW 2330 price data routed to the Shioaji sandbox adapter; the adapter returned "
            "submit, cancel, and readback acknowledgements with no live-capital side effects."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-broker-ops",
                "summary": "Shioaji sandbox order ack and readback were persisted into memory lineage.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["adapter_ack", "shioaji_sandbox", "order_readback"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_submitted["event_id"]],
    )

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="Shioaji sandbox adapter ack readback 2330",
        tags=["adapter_ack", "order_readback"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    order_context = evidence["lineage"]["order_context"]
    assert order_context["adapter"] == "shioaji_sandbox"
    assert order_context["order_id"] == lifecycle["place_result"]["order_id"]
    assert order_context["shioaji_trade_id"] == "mock-e2e-loop-008-trade"
    assert order_context["readback_status"] == "cancelled"
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-broker-ops",
        query="adapter returned cancel readback",
        tags=["shioaji_sandbox"],
        limit=3,
    )
    assert persona_hits
    persona_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"][
        "order_context"
    ]
    assert persona_context["proof_boundary"] == PROOF_BOUNDARY
    assert persona_context["shioaji_order_status_code"] == "0"


def _tw_market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-008-tw-prices",
        "source_type": "market",
        "provider": "E2E Loop 008 Static TW Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "tw_price_daily",
            "feature_targets": ["features/tw_adapter_order_inputs"],
            "schema_hash": "tw_price_daily.e2e_loop_008.v1",
        },
    }


def _tw_market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-008-2330",
        "title": "2330 TW daily close for E2E loop 008",
        "content_ref": "market://tw_price_daily/2330/2026-06-12",
        "metadata": {
            "dataset": "tw_price_daily",
            "date": "2026-06-12",
            "symbol": "2330",
            "venue": "TWSE",
            "open": 944.0,
            "high": 956.0,
            "low": 940.0,
            "close": 950.0,
            "volume": 26000000,
        },
    }


def _tw_adapter_signal(row: dict[str, Any], *, normalized_ref: dict[str, Any], ingest_run_id: str) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "adapter-ack-2330-008",
        "version": "1.0",
        "strategy_id": "strategy-shioaji-adapter-ack",
        "timestamp": _iso_now(),
        "symbol": "2330.TWSE",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 1,
        "quantity_type": "SHARES",
        "source_worker": "mock-tw-adapter-normalizer",
        "metadata": {
            "alpha_source": "tw_adapter_route_quant",
            "confidence_score": 0.91,
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "venue": metadata["venue"],
                "date": metadata["date"],
                "close": metadata["close"],
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref["uri"],
            "source_dataset_ref": normalized_ref["dataset"],
            "ingest_run_id": ingest_run_id,
            "order_adapter": "shioaji_sandbox",
            "adapter_route_reason": "Taiwan venue symbols are routed to Shioaji instead of LEAN Symbol.Create.",
        },
    }


def _adapter_ack_event(
    event_type: str,
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    lifecycle: dict[str, Any],
    order_payload: dict[str, Any],
    status_field: str,
) -> dict[str, Any]:
    metadata = {
        "signal_id": signal["signal_id"],
        "strategy_id": signal["strategy_id"],
        "source_worker": signal["source_worker"],
        "alpha_source": signal["metadata"]["alpha_source"],
        "confidence_score": signal["metadata"]["confidence_score"],
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": signal["metadata"]["ingest_run_id"],
        "adapter": "shioaji_sandbox",
        "broker": "shioaji",
        "provider": "Shioaji",
        "order_id": order_payload["order_id"],
        "adapter_order_id": order_payload["order_id"],
        "broker_order_id": order_payload["shioaji_trade_id"],
        "shioaji_trade_id": order_payload["shioaji_trade_id"],
        "account_kind": order_payload["account_kind"],
        status_field: order_payload["status"],
        "broker_submission_status": order_payload["status"],
        "submitted_to_broker": False,
        "shioaji_order_status_id": order_payload["shioaji_order_status_id"],
        "shioaji_order_status": order_payload["shioaji_order_status"],
        "shioaji_order_status_code": order_payload["shioaji_order_status_code"],
        "shioaji_order_status_message": order_payload["shioaji_order_status_message"],
        "is_real_order": order_payload["is_real_order"],
        "is_real_capital": order_payload["is_real_capital"],
        "deployment_stage": order_payload["deployment_stage"],
        "proof_boundary": lifecycle["proof_boundary"],
    }
    return {
        "event_id": f"e2e-loop-008-{event_type}",
        "event_type": event_type,
        "created_at": _iso_now(),
        "execution_mode": "sandbox",
        "environment": "sandbox",
        "deployment_stage": "sandbox",
        "binding_id": "binding-e2e-loop-008",
        "runtime_id": "adapter-runtime-008",
        "capital_pool_id": order_payload["capital_pool_id"],
        "artifact_id": "artifact-shioaji-adapter-ack",
        "artifact_version": "8.0.0",
        "plan_id": "plan-shioaji-adapter-ack",
        "persona_capital_binding_id": "pcb-shioaji-adapter-ack",
        "target": {
            "registry_id": "artifact-shioaji-adapter-ack",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "8.0.0",
            "artifact_type": "broker_adapter_ack",
            "promotion_state": "sandbox",
        },
        "metrics": {
            "quantity": order_payload["qty"],
            "limit_price": order_payload["limit_price"],
            "fill_quantity": order_payload["fill_qty"],
            "is_real_order": float(order_payload["is_real_order"]),
            "is_real_capital": float(order_payload["is_real_capital"]),
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-008-adapter",
    }


def _make_mock_shioaji_api() -> MagicMock:
    mock_trade = MagicMock()
    mock_trade.trade_id = "mock-e2e-loop-008-trade"
    mock_trade.status = SimpleNamespace(
        id="mock-e2e-loop-008-trade",
        status="Submitted",
        status_code="0",
        msg="accepted by sandbox",
    )

    api = MagicMock()
    api.Contracts.Stocks.__getitem__.return_value = MagicMock()
    api.Order.return_value = MagicMock()
    api.place_order.return_value = mock_trade
    api.cancel_order.return_value = None
    api.update_status.return_value = None
    api.stock_account = SimpleNamespace(
        account_type="stock",
        broker_id="9A95",
        account_id="stock-e2e-loop-008",
        person_id="person-e2e-loop-008",
        signed=True,
    )
    api.futopt_account = SimpleNamespace(
        account_type="futures",
        broker_id="F002000",
        account_id="future-e2e-loop-008",
        person_id="person-e2e-loop-008",
        signed=True,
    )
    return api


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
