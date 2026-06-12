from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.execution.ibkr_adapter import IBKRAdapter, IBKRConfig, IBKROrderIntent
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_ibkr_validate_only_order_feedback_performance_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T23:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-018-ibkr-prices",
            "trace_id": "trace-e2e-loop-018-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "AAPL"
    assert row["metadata"]["close"] == 212.4

    adapter = IBKRAdapter(
        IBKRConfig(
            host="127.0.0.1",
            port=7497,
            client_id=18,
            account_id="DU1234567",
            readonly_market_data=True,
            market_data_type=3,
        )
    )
    market_request = adapter.build_market_data_request("AAPL.US", snapshot=True, generic_ticks="233")
    quote = adapter.normalize_quote(
        {
            "ts": row["metadata"]["timestamp"],
            "bidPrice": row["metadata"]["bid"],
            "askPrice": row["metadata"]["ask"],
            "lastPrice": row["metadata"]["last"],
            "close": row["metadata"]["close"],
            "volume": row["metadata"]["volume"],
            "contract": {"exchange": "SMART"},
            "provider": "IBKR market data",
        },
        "AAPL.US",
    )
    signal = _ibkr_validate_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    order_payload = adapter.build_order(
        IBKROrderIntent(
            symbol=signal["symbol"],
            side="buy",
            quantity=signal["quantity"],
            order_type="LMT",
            price=row["metadata"]["close"],
            tif="DAY",
            account="DU1234567",
            outside_rth=False,
            metadata={"signal_id": signal["signal_id"], "validate_only": True},
        )
    )

    assert market_request["contract"]["symbol"] == "AAPL"
    assert market_request["contract"]["exchange"] == "SMART"
    assert market_request["snapshot"] is True
    assert market_request["readonly"] is True
    assert market_request["marketDataType"] == 3
    assert quote.symbol == "AAPL"
    assert quote.exchange == "SMART"
    assert quote.last == 212.45
    assert order_payload["contract"]["symbol"] == "AAPL"
    assert order_payload["contract"]["exchange"] == "SMART"
    assert order_payload["contract"]["secType"] == "STK"
    assert order_payload["order"]["orderType"] == "LMT"
    assert order_payload["order"]["lmtPrice"] == 212.4
    assert order_payload["order"]["account"] == "DU1234567"

    validation_ack = {
        "status": "accepted",
        "client_order_id": "client-ibkr-validate-018",
        "validation_status": "accepted",
        "submitted_to_broker": False,
        "validate_only": True,
        "message": "IBKR validate-only order accepted by execution boundary.",
    }
    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    accepted_event = _ibkr_validation_event(
        signal=signal,
        normalized_ref=normalized_ref,
        market_request=market_request,
        quote=quote.to_dict(),
        order_payload=order_payload,
        validation_ack=validation_ack,
    )
    stored_accepted = writer_adapter.ingest_telemetry_event(
        accepted_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    summary_store = RuntimeSummaryProjectionStore(path=tmp_path / "runtime-summary.json")
    projected = summary_store.project_event(stored_accepted)
    assert projected is not None
    assert projected["runtime_id"] == "ibkr-validation-runtime-018"
    assert projected["fill_rate"] == 0.0
    assert projected["total_trades"] == 0
    assert projected["pnl"] == 0.0

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_accepts = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_accepted",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_accepts] == [stored_accepted["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_accepts[0],
        sponsor_persona_id="persona-ibkr-sponsor",
        contributing_persona_ids=["persona-ibkr-ops"],
        summary=(
            "AAPL IBKR market data was fetched and normalized, a SMART-routed validate-only limit order "
            "was built from the alpha signal, the adapter returned accepted validation feedback without "
            "submitting to live broker capital, and memory readback preserved the IBKR contract fields."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-ibkr-ops",
                "summary": "IBKR validate-only feedback preserved SMART route, account, TIF, and contract fields.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["ibkr_validate_only", "us_equity_adapter", "contract_route"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_accepted["event_id"]],
    )
    writeback_payload["tags"].extend(["ibkr_validate_only", "us_equity_adapter", "contract_route"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="IBKR validate-only AAPL SMART contract",
        tags=["ibkr_validate_only", "contract_route"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "ibkr-validate-aapl-018"
    assert alpha_context["alpha_source"] == "us_equity_validate_only_quant"
    assert order_context["adapter"] == "ibkr_execution_boundary"
    assert order_context["broker"] == "ibkr"
    assert order_context["provider"] == "IBKR"
    assert order_context["client_order_id"] == "client-ibkr-validate-018"
    assert order_context["contract_symbol"] == "AAPL"
    assert order_context["exchange"] == "SMART"
    assert order_context["sec_type"] == "STK"
    assert order_context["currency"] == "USD"
    assert order_context["order_type"] == "LMT"
    assert order_context["side"] == "BUY"
    assert order_context["price"] == 212.4
    assert order_context["tif"] == "DAY"
    assert order_context["outside_rth"] is False
    assert order_context["account"] == "DU1234567"
    assert order_context["readonly_market_data"] is True
    assert order_context["market_data_type"] == 3
    assert order_context["validate_only"] is True
    assert order_context["validation_status"] == "accepted"
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-ibkr-ops",
        query="SMART route account TIF",
        tags=["us_equity_adapter"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["fill_rate"] == 0.0
    assert persona_order_context["total_trades"] == 0
    assert persona_order_context["pnl"] == 0.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-018-ibkr-prices",
        "source_type": "market",
        "provider": "E2E Loop 018 Static IBKR Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "ibkr_us_equity_price_daily",
            "feature_targets": ["features/ibkr_validate_order_inputs"],
            "schema_hash": "ibkr_us_equity_price_daily.e2e_loop_018.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-018-aapl",
        "title": "AAPL IBKR close for E2E loop 018",
        "content_ref": "market://ibkr_us_equity_price_daily/AAPL/2026-06-12",
        "metadata": {
            "dataset": "ibkr_us_equity_price_daily",
            "timestamp": "2026-06-12T16:00:00Z",
            "date": "2026-06-12",
            "symbol": "AAPL",
            "exchange": "SMART",
            "open": 211.2,
            "high": 213.1,
            "low": 210.8,
            "last": 212.45,
            "bid": 212.35,
            "ask": 212.5,
            "close": 212.4,
            "volume": 52000000,
        },
    }


def _ibkr_validate_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "ibkr-validate-aapl-018",
        "version": "1.0",
        "strategy_id": "strategy-ibkr-validate-only",
        "timestamp": _iso_now(),
        "symbol": "AAPL.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 5,
        "quantity_type": "SHARES",
        "source_worker": "mock-ibkr-validate-normalizer",
        "metadata": {
            "alpha_source": "us_equity_validate_only_quant",
            "confidence_score": 0.9,
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "exchange": metadata["exchange"],
                "date": metadata["date"],
                "close": metadata["close"],
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref["uri"],
            "source_dataset_ref": normalized_ref["dataset"],
            "ingest_run_id": ingest_run_id,
            "order_adapter": "ibkr_execution_boundary",
        },
    }


def _ibkr_validation_event(
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    market_request: dict[str, Any],
    quote: dict[str, Any],
    order_payload: dict[str, Any],
    validation_ack: dict[str, Any],
) -> dict[str, Any]:
    contract = order_payload["contract"]
    order = order_payload["order"]
    metadata = {
        "signal_id": signal["signal_id"],
        "strategy_id": signal["strategy_id"],
        "source_worker": signal["source_worker"],
        "alpha_source": signal["metadata"]["alpha_source"],
        "confidence_score": signal["metadata"]["confidence_score"],
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": signal["metadata"]["ingest_run_id"],
        "market_data_ref": signal["metadata"]["market_data"]["content_ref"],
        "adapter": "ibkr_execution_boundary",
        "broker": "ibkr",
        "provider": "IBKR",
        "client_order_id": validation_ack["client_order_id"],
        "contract_symbol": contract["symbol"],
        "exchange": contract["exchange"],
        "primary_exchange": contract.get("primaryExchange"),
        "sec_type": contract["secType"],
        "currency": contract["currency"],
        "order_type": order["orderType"],
        "side": order["action"],
        "price": order["lmtPrice"],
        "tif": order["tif"],
        "outside_rth": order["outsideRth"],
        "account": order["account"],
        "readonly_market_data": market_request["readonly"],
        "market_data_type": market_request["marketDataType"],
        "validate_only": validation_ack["validate_only"],
        "validation_status": validation_ack["validation_status"],
        "order_status": validation_ack["status"],
        "broker_submission_status": "validate_only_accepted",
        "submitted_to_broker": validation_ack["submitted_to_broker"],
        "is_real_order": False,
        "is_real_capital": False,
        "deployment_stage": "paper",
        "market_data_request_symbol": market_request["contract"]["symbol"],
        "quote_exchange": quote["exchange"],
    }
    return {
        "event_id": "e2e-loop-018-ibkr-validate-accepted",
        "event_type": "order_accepted",
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-e2e-loop-018",
        "runtime_id": "ibkr-validation-runtime-018",
        "capital_pool_id": "pool-ibkr-validation",
        "artifact_id": "artifact-ibkr-validation",
        "artifact_version": "18.0.0",
        "plan_id": "plan-ibkr-validation",
        "persona_capital_binding_id": "pcb-ibkr-validation",
        "target": {
            "registry_id": "artifact-ibkr-validation",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "18.0.0",
            "artifact_type": "ibkr_execution_boundary",
            "promotion_state": "paper",
        },
        "metrics": {
            "requested_quantity": float(signal["quantity"]),
            "fill_quantity": 0.0,
            "fill_rate": 0.0,
            "avg_slippage_bps": 0.0,
            "pnl": 0.0,
            "total_trades": 0,
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-018-ibkr-adapter",
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
