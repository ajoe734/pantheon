from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.broker.shioaji.adapter import ShioajiBrokerAdapter
from services.broker.shioaji.facade import PROOF_BOUNDARY
from services.execution.lean_runtime.symbol_parser import SymbolParseError, parse
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_shioaji_adapter_filled_readback_feedback_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _tw_market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_tw_market_record()],
                "next_watermark": "2026-06-12T23:59:59Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-037-tw-filled-prices",
            "trace_id": "trace-e2e-loop-037-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "2454"
    assert row["metadata"]["close"] == 1210.0

    signal = _tw_filled_signal(row, normalized_ref=normalized_ref, ingest_run_id=ingest_body["run"]["ingest_run_id"])
    with pytest.raises(SymbolParseError):
        parse(signal["symbol"])

    api = _make_mock_shioaji_api()
    adapter = ShioajiBrokerAdapter(
        sandbox_enabled=True,
        _api=api,
        submit_spacing_seconds=0.0,
    )
    submitted = adapter.submit(
        capital_pool_id="pool-paper-tw-filled",
        strategy_id=signal["strategy_id"],
        symbol=row["metadata"]["symbol"],
        qty=signal["quantity"],
        side="buy",
        order_type="limit",
        limit_price=row["metadata"]["close"],
        account_kind="stock",
    )
    assert submitted.status == "submitted"
    assert submitted.shioaji_trade_id == "mock-e2e-loop-037-trade"

    api.place_order.return_value.status = SimpleNamespace(
        id="mock-e2e-loop-037-trade",
        status="Filled",
        status_code="0",
        msg="filled by sandbox readback",
    )
    readback = adapter.get_status(submitted.order_id)
    assert readback.status == "filled"
    assert readback.fill_qty == 2.0
    assert readback.fill_price == 1210.0
    assert readback.filled_at is not None
    assert readback.shioaji_order_status == "Filled"
    assert readback.shioaji_order_status_message == "filled by sandbox readback"
    assert readback.is_real_order is False
    assert readback.is_real_capital is False
    assert readback.deployment_stage == "sandbox"

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    submitted_event = _adapter_event(
        "order_submitted",
        signal=signal,
        normalized_ref=normalized_ref,
        order_payload=submitted.to_dict(),
        status_field="order_status",
    )
    filled_event = _adapter_event(
        "order_filled",
        signal=signal,
        normalized_ref=normalized_ref,
        order_payload=readback.to_dict(),
        status_field="readback_status",
    )
    stored_submitted = writer_adapter.ingest_telemetry_event(
        submitted_event,
        strategy_id=signal["strategy_id"],
        promotion_state="sandbox",
    )
    stored_filled = writer_adapter.ingest_telemetry_event(
        filled_event,
        strategy_id=signal["strategy_id"],
        promotion_state="sandbox",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_submits = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_submitted",
        promotion_state="sandbox",
        limit=3,
    )
    recovered_fills = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_filled",
        promotion_state="sandbox",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_submits] == [stored_submitted["event_id"]]
    assert [event["event_id"] for event in recovered_fills] == [stored_filled["event_id"]]
    assert recovered_fills[0]["metrics"]["fill_quantity"] == 2.0
    assert recovered_fills[0]["metrics"]["fill_rate"] == 1.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_fills[0],
        sponsor_persona_id="persona-shioaji-filled-sponsor",
        contributing_persona_ids=["persona-shioaji-fill-ops"],
        summary=(
            "TW 2454 market data routed to the Shioaji sandbox adapter; the adapter submitted a "
            "limit order, refreshed broker readback to Filled, recovered the fill feedback after "
            "adapter restart, and wrote the sandbox fill lineage into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-shioaji-fill-ops",
                "summary": "Shioaji filled-readback feedback preserved order id, trade id, fill quantity, and no-real-capital flags.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["shioaji_filled_readback", "adapter_recovery", "paper_fill"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_submitted["event_id"], stored_filled["event_id"]],
    )
    writeback_payload["tags"].extend(["shioaji_filled_readback", "adapter_recovery", "paper_fill"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="Shioaji filled readback 2454 sandbox fill",
        tags=["shioaji_filled_readback", "adapter_recovery"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert evidence["event_type"] == "order_filled"
    assert alpha_context["signal_id"] == "shioaji-filled-2454-037"
    assert alpha_context["alpha_source"] == "tw_adapter_fill_quant"
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["adapter"] == "shioaji_sandbox"
    assert order_context["broker"] == "shioaji"
    assert order_context["order_id"] == submitted.order_id
    assert order_context["adapter_order_id"] == submitted.order_id
    assert order_context["broker_order_id"] == "mock-e2e-loop-037-trade"
    assert order_context["shioaji_trade_id"] == "mock-e2e-loop-037-trade"
    assert order_context["order_status"] == "filled"
    assert order_context["readback_status"] == "filled"
    assert order_context["fill_status"] == "filled"
    assert order_context["side"] == "buy"
    assert order_context["order_type"] == "limit"
    assert order_context["limit_price"] == 1210.0
    assert order_context["requested_quantity"] == 2.0
    assert order_context["fill_quantity"] == 2.0
    assert order_context["filled_quantity"] == 2.0
    assert order_context["fill_price"] == 1210.0
    assert order_context["fill_rate"] == 1.0
    assert order_context["total_trades"] == 1
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False
    assert order_context["proof_boundary"] == PROOF_BOUNDARY

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-shioaji-fill-ops",
        query="trade id fill quantity",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["shioaji_order_status"] == "Filled"
    assert persona_order_context["shioaji_order_status_message"] == "filled by sandbox readback"
    assert persona_order_context["is_real_capital"] is False


def _tw_market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-037-tw-filled-prices",
        "source_type": "market",
        "provider": "E2E Loop 037 Static TW Filled Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "tw_filled_price_daily",
            "feature_targets": ["features/tw_adapter_filled_readback_inputs"],
            "schema_hash": "tw_filled_price_daily.e2e_loop_037.v1",
        },
    }


def _tw_market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-037-2454",
        "title": "2454 TW daily close for E2E loop 037",
        "content_ref": "market://tw_filled_price_daily/2454/2026-06-12",
        "metadata": {
            "dataset": "tw_filled_price_daily",
            "date": "2026-06-12",
            "symbol": "2454",
            "venue": "TWSE",
            "open": 1195.0,
            "high": 1220.0,
            "low": 1185.0,
            "close": 1210.0,
            "volume": 8000000,
        },
    }


def _tw_filled_signal(row: dict[str, Any], *, normalized_ref: dict[str, Any], ingest_run_id: str) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "shioaji-filled-2454-037",
        "version": "1.0",
        "strategy_id": "strategy-shioaji-filled-readback",
        "timestamp": _iso_now(),
        "symbol": "2454.TWSE",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 2,
        "quantity_type": "SHARES",
        "source_worker": "mock-shioaji-filled-normalizer",
        "metadata": {
            "alpha_source": "tw_adapter_fill_quant",
            "confidence_score": 0.89,
            "market_data_ref": normalized_ref["uri"],
            "source_evidence_refs": [
                {
                    "ref_type": "normalized_rows",
                    "dataset": normalized_ref["dataset"],
                    "uri": normalized_ref["uri"],
                    "ingest_run_id": ingest_run_id,
                }
            ],
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
            "adapter_route_reason": "Taiwan symbols are routed to Shioaji sandbox for broker readback.",
        },
    }


def _adapter_event(
    event_type: str,
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    order_payload: dict[str, Any],
    status_field: str,
) -> dict[str, Any]:
    requested_quantity = float(order_payload["qty"])
    fill_quantity = float(order_payload["fill_qty"])
    fill_price = float(order_payload["fill_price"] or order_payload["limit_price"] or 0.0)
    fill_rate = 1.0 if fill_quantity >= requested_quantity else 0.0
    metadata = {
        "signal_id": signal["signal_id"],
        "strategy_id": signal["strategy_id"],
        "source_worker": signal["source_worker"],
        "alpha_source": signal["metadata"]["alpha_source"],
        "confidence_score": signal["metadata"]["confidence_score"],
        "market_data_ref": normalized_ref["uri"],
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
        "side": order_payload["side"],
        "order_type": order_payload["order_type"],
        "limit_price": order_payload["limit_price"],
        "order_quantity": requested_quantity,
        "requested_quantity": requested_quantity,
        "fill_status": "filled" if order_payload["status"] == "filled" else order_payload["status"],
        "fill_quantity": fill_quantity,
        "filled_quantity": fill_quantity,
        "fill_price": fill_price,
        "avg_fill_price": fill_price,
        "order_status": order_payload["status"],
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
        "proof_boundary": PROOF_BOUNDARY,
    }
    return {
        "event_id": f"e2e-loop-037-{event_type}",
        "event_type": event_type,
        "created_at": _iso_now(),
        "execution_mode": "sandbox",
        "environment": "sandbox",
        "deployment_stage": "sandbox",
        "binding_id": "binding-e2e-loop-037",
        "runtime_id": "adapter-runtime-037",
        "capital_pool_id": order_payload["capital_pool_id"],
        "artifact_id": "artifact-shioaji-filled-readback",
        "artifact_version": "37.0.0",
        "plan_id": "plan-shioaji-filled-readback",
        "persona_capital_binding_id": "pcb-shioaji-filled-readback",
        "target": {
            "registry_id": "artifact-shioaji-filled-readback",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "37.0.0",
            "artifact_type": "broker_adapter_fill",
            "promotion_state": "sandbox",
        },
        "metrics": {
            "order_quantity": requested_quantity,
            "requested_quantity": requested_quantity,
            "fill_quantity": fill_quantity,
            "filled_quantity": fill_quantity,
            "fill_price": fill_price,
            "avg_fill_price": fill_price,
            "fill_rate": fill_rate,
            "total_trades": 1 if fill_quantity else 0,
            "pnl": 0.0,
            "is_real_order": float(order_payload["is_real_order"]),
            "is_real_capital": float(order_payload["is_real_capital"]),
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-037-adapter",
    }


def _make_mock_shioaji_api() -> MagicMock:
    mock_trade = MagicMock()
    mock_trade.trade_id = "mock-e2e-loop-037-trade"
    mock_trade.status = SimpleNamespace(
        id="mock-e2e-loop-037-trade",
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
        account_id="stock-e2e-loop-037",
        person_id="person-e2e-loop-037",
        signed=True,
    )
    api.futopt_account = SimpleNamespace(
        account_type="futures",
        broker_id="F002000",
        account_id="future-e2e-loop-037",
        person_id="person-e2e-loop-037",
        signed=True,
    )
    return api


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
