from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig, KrakenOrderIntent
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_kraken_validate_only_order_feedback_performance_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _crypto_market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_crypto_market_record()],
                "next_watermark": "2026-06-12T23:15:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-017-kraken-prices",
            "trace_id": "trace-e2e-loop-017-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "ETHUSDT"
    assert row["metadata"]["close"] == 3500.5

    adapter = KrakenAdapter(KrakenConfig(api_key="paper-key", api_secret="paper-secret", validate_only=True))
    market_request = adapter.build_market_data_request("ETH/USDT.KRAKEN", interval=5, since=1781280000)
    quote = adapter.normalize_quote(
        {
            "ts": row["metadata"]["timestamp"],
            "last": row["metadata"]["last"],
            "close": row["metadata"]["close"],
            "bid": row["metadata"]["bid"],
            "ask": row["metadata"]["ask"],
            "vwap": row["metadata"]["vwap"],
            "volume": row["metadata"]["volume"],
            "provider": "Kraken",
        },
        "ETH/USDT.KRAKEN",
    )
    signal = _kraken_validate_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    order_payload = adapter.build_order(
        KrakenOrderIntent(
            symbol=signal["symbol"],
            side="buy",
            quantity=signal["quantity"],
            order_type="limit",
            price=row["metadata"]["close"],
            validate=True,
            metadata={"signal_id": signal["signal_id"]},
        )
    )

    assert market_request["pair"] == "ETH/USDT"
    assert market_request["venue"] == "KRAKEN"
    assert quote.symbol == "ETHUSDT"
    assert quote.base_asset == "ETH"
    assert quote.quote_asset == "USDT"
    assert order_payload["pair"] == "ETH/USDT"
    assert order_payload["validate"] is True
    assert order_payload["provider"] == "Kraken"
    assert order_payload["volume"] == "0.75"
    assert order_payload["price"] == "3500.5"

    validation_ack = {
        "status": "accepted",
        "client_order_id": "client-kraken-validate-017",
        "validation_status": "accepted",
        "submitted_to_broker": False,
        "validate_only": True,
        "message": "Kraken validate-only order accepted by execution boundary.",
    }
    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    accepted_event = _kraken_validation_event(
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
    assert projected["runtime_id"] == "kraken-validation-runtime-017"
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
        sponsor_persona_id="persona-kraken-sponsor",
        contributing_persona_ids=["persona-kraken-ops"],
        summary=(
            "ETHUSDT Kraken data was fetched and normalized, a validate-only Kraken order was built from "
            "the crypto alpha signal, the adapter returned an accepted validation acknowledgement without "
            "submitting to a live broker, and recovered feedback wrote the venue contract into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-kraken-ops",
                "summary": "Kraken validate-only feedback preserved pair, venue, base/quote, and validation status.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["kraken_validate_only", "crypto_adapter", "venue_contract"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_accepted["event_id"]],
    )
    writeback_payload["tags"].extend(["kraken_validate_only", "crypto_adapter", "venue_contract"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="Kraken validate-only ETHUSDT venue contract",
        tags=["kraken_validate_only", "venue_contract"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "kraken-validate-ethusdt-017"
    assert alpha_context["alpha_source"] == "crypto_validate_only_quant"
    assert order_context["adapter"] == "kraken_execution_boundary"
    assert order_context["broker"] == "kraken"
    assert order_context["provider"] == "Kraken"
    assert order_context["client_order_id"] == "client-kraken-validate-017"
    assert order_context["venue"] == "KRAKEN"
    assert order_context["pair"] == "ETH/USDT"
    assert order_context["base_asset"] == "ETH"
    assert order_context["quote_asset"] == "USDT"
    assert order_context["order_type"] == "limit"
    assert order_context["side"] == "buy"
    assert order_context["price"] == "3500.5"
    assert order_context["volume"] == "0.75"
    assert order_context["validate_only"] is True
    assert order_context["validation_status"] == "accepted"
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-kraken-ops",
        query="base quote validation status",
        tags=["crypto_adapter"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["fill_rate"] == 0.0
    assert persona_order_context["total_trades"] == 0
    assert persona_order_context["pnl"] == 0.0


def _crypto_market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-017-kraken-prices",
        "source_type": "market",
        "provider": "E2E Loop 017 Static Kraken Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "kraken_crypto_price_daily",
            "feature_targets": ["features/kraken_validate_order_inputs"],
            "schema_hash": "kraken_crypto_price_daily.e2e_loop_017.v1",
        },
    }


def _crypto_market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-017-ethusdt",
        "title": "ETHUSDT Kraken close for E2E loop 017",
        "content_ref": "market://kraken_crypto_price_daily/ETHUSDT/2026-06-12",
        "metadata": {
            "dataset": "kraken_crypto_price_daily",
            "timestamp": "2026-06-12T16:00:00Z",
            "date": "2026-06-12",
            "symbol": "ETHUSDT",
            "venue": "KRAKEN",
            "base_asset": "ETH",
            "quote_asset": "USDT",
            "last": 3500.8,
            "bid": 3500.1,
            "ask": 3501.0,
            "close": 3500.5,
            "vwap": 3498.4,
            "volume": 18250.0,
        },
    }


def _kraken_validate_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "kraken-validate-ethusdt-017",
        "version": "1.0",
        "strategy_id": "strategy-kraken-validate-only",
        "timestamp": _iso_now(),
        "symbol": "ETH/USDT.KRAKEN",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.75,
        "quantity_type": "BASE_ASSET",
        "source_worker": "mock-kraken-validate-normalizer",
        "metadata": {
            "alpha_source": "crypto_validate_only_quant",
            "confidence_score": 0.87,
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
            "order_adapter": "kraken_execution_boundary",
        },
    }


def _kraken_validation_event(
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    market_request: dict[str, Any],
    quote: dict[str, Any],
    order_payload: dict[str, Any],
    validation_ack: dict[str, Any],
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
        "market_data_ref": signal["metadata"]["market_data"]["content_ref"],
        "adapter": "kraken_execution_boundary",
        "broker": "kraken",
        "provider": "Kraken",
        "client_order_id": validation_ack["client_order_id"],
        "venue": order_payload["venue"],
        "pair": order_payload["pair"],
        "base_asset": quote["base_asset"],
        "quote_asset": quote["quote_asset"],
        "order_type": order_payload["ordertype"],
        "side": order_payload["type"],
        "price": order_payload["price"],
        "volume": order_payload["volume"],
        "validate_only": validation_ack["validate_only"],
        "validation_status": validation_ack["validation_status"],
        "order_status": validation_ack["status"],
        "broker_submission_status": "validate_only_accepted",
        "submitted_to_broker": validation_ack["submitted_to_broker"],
        "is_real_order": False,
        "is_real_capital": False,
        "deployment_stage": "paper",
        "market_data_request_pair": market_request["pair"],
        "market_data_request_interval": market_request["interval"],
    }
    return {
        "event_id": "e2e-loop-017-kraken-validate-accepted",
        "event_type": "order_accepted",
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-e2e-loop-017",
        "runtime_id": "kraken-validation-runtime-017",
        "capital_pool_id": "pool-kraken-validation",
        "artifact_id": "artifact-kraken-validation",
        "artifact_version": "17.0.0",
        "plan_id": "plan-kraken-validation",
        "persona_capital_binding_id": "pcb-kraken-validation",
        "target": {
            "registry_id": "artifact-kraken-validation",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "17.0.0",
            "artifact_type": "kraken_execution_boundary",
            "promotion_state": "paper",
        },
        "metrics": {
            "requested_quantity": signal["quantity"],
            "fill_quantity": 0.0,
            "fill_rate": 0.0,
            "avg_slippage_bps": 0.0,
            "pnl": 0.0,
            "total_trades": 0,
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-017-kraken-adapter",
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
