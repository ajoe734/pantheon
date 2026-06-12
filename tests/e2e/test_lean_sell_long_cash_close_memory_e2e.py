from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_lean_sell_long_cash_value_close_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-035-adbe-day1", "ADBE", "2026-06-11", close=120.0),
                    _market_record("src-e2e-loop-035-adbe-day2", "ADBE", "2026-06-12", close=125.0),
                ],
                "next_watermark": "2026-06-12T23:59:59Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-035-us-sell-long-cash-prices",
            "trace_id": "trace-e2e-loop-035-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [
        row
        for ref in normalized_refs
        for row in _read_jsonl(Path(ref["uri"]))
    ]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-11", 120.0),
        ("2026-06-12", 125.0),
    ]

    signals = _sell_long_cash_signals(
        normalized_rows,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    store = InMemoryPendingSignalStore([signals[0]])
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=store,
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first_snapshot = runtime.drain_once()

    assert first_snapshot["status"] == "ok"
    assert first_snapshot["paper_state"]["processed_signal_count"] == 1
    assert first_snapshot["paper_state"]["positions"] == [
        {"symbol": "ADBE", "quantity": 10.0, "price": 120.0}
    ]

    store.enqueue(signals[1])
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    assert snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert noop_events == []
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-adbe-cash-entry-035",
        "quant-adbe-sell-long-cash-close-035",
    ]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [10.0, -10.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [120.0, 125.0]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metrics"]["action"] == "market_order"
    assert entry_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert entry_fill["metadata"]["requested_quantity"] == 1200.0
    assert entry_fill["metadata"]["market_price"] == 120.0
    assert exit_fill["metrics"]["action"] == "liquidate"
    assert exit_fill["metadata"]["alpha_source"] == "pure_quant_cash_close"
    assert exit_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert exit_fill["metadata"]["order_type"] == "MARKET"
    assert exit_fill["metadata"]["requested_quantity"] == 600.0
    assert exit_fill["metadata"]["market_price"] == 125.0
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 50.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0
    assert pnl_event["metrics"]["avg_slippage_bps"] == 0.0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(
            fill_event,
            strategy_id="strategy-quant-sell-long-cash-close",
            promotion_state="paper",
        )
        for fill_event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-quant-sell-long-cash-close",
        promotion_state="paper",
    )
    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_fills = recovered_adapter.query_telemetry(
        strategy_id="strategy-quant-sell-long-cash-close",
        event_type="paper_fill_simulated",
        promotion_state="paper",
        limit=5,
    )
    recovered_pnls = recovered_adapter.query_telemetry(
        strategy_id="strategy-quant-sell-long-cash-close",
        event_type="pnl_snapshot",
        promotion_state="paper",
        limit=5,
    )
    assert [event["event_id"] for event in recovered_fills] == [
        event["event_id"] for event in stored_fills
    ]
    assert [event["event_id"] for event in recovered_pnls] == [stored_pnl["event_id"]]
    assert recovered_pnls[0]["metrics"]["pnl"] == 50.0
    assert recovered_pnls[0]["metrics"]["fill_rate"] == 1.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_fills[1],
        sponsor_persona_id="persona-sell-long-cash-sponsor",
        contributing_persona_ids=["persona-quant-cash-close"],
        summary=(
            "ADBE market data produced a CASH_VALUE entry followed by a SELL/LONG CASH_VALUE close; "
            "LEAN created a 10 share paper position, liquidated it at the second close, recovered "
            "the exit fill and PnL through the feedback adapter, and wrote sell-long cash close "
            "context into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-quant-cash-close",
                "summary": "Sell-long cash close feedback preserved liquidate fill quantity, requested cash, and PnL.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["sell_long_cash_close", "paper_fill", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_fills[1]["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["tags"].extend(["sell_long_cash_close", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ADBE sell long cash close liquidate performance",
        tags=["sell_long_cash_close", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert evidence["event_type"] == "paper_fill_simulated"
    assert alpha_context["signal_id"] == "quant-adbe-sell-long-cash-close-035"
    assert alpha_context["alpha_source"] == "pure_quant_cash_close"
    assert alpha_context["market_price"] == 125.0
    assert alpha_context["normalized_data_ref"] == [ref["uri"] for ref in normalized_refs]
    assert alpha_context["ingest_run_id"] == ingest_body["run"]["ingest_run_id"]
    assert order_context["quantity_type"] == "CASH_VALUE"
    assert order_context["order_type"] == "MARKET"
    assert order_context["requested_quantity"] == 600.0
    assert order_context["fill_quantity"] == -10.0
    assert order_context["fill_price"] == 125.0
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-quant-cash-close",
        query="liquidate requested cash PnL",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == "strategy-quant-sell-long-cash-close"
    assert persona_lineage["order_context"]["fill_quantity"] == -10.0
    assert persona_lineage["order_context"]["requested_quantity"] == 600.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-035-us-sell-long-cash-prices",
        "source_type": "market",
        "provider": "E2E Loop 035 Static Sell Long Cash Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_sell_long_cash_price_daily",
            "feature_targets": ["features/quant_sell_long_cash_close_inputs"],
            "schema_hash": "us_sell_long_cash_price_daily.e2e_loop_035.v1",
        },
    }


def _market_record(source_id: str, symbol: str, trade_date: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 035 {trade_date}",
        "content_ref": f"market://us_sell_long_cash_price_daily/{symbol}/{trade_date}",
        "metadata": {
            "dataset": "us_sell_long_cash_price_daily",
            "date": trade_date,
            "symbol": symbol,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1750000,
        },
    }


def _sell_long_cash_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    return [
        _signal_from_row(
            rows_by_date["2026-06-11"],
            signal_id="quant-adbe-cash-entry-035",
            action="BUY",
            direction="LONG",
            quantity=1200,
            alpha_source="pure_quant_cash_entry",
            normalized_ref_uris=normalized_ref_uris,
            ingest_run_id=ingest_run_id,
        ),
        _signal_from_row(
            rows_by_date["2026-06-12"],
            signal_id="quant-adbe-sell-long-cash-close-035",
            action="SELL",
            direction="LONG",
            quantity=600,
            alpha_source="pure_quant_cash_close",
            normalized_ref_uris=normalized_ref_uris,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _signal_from_row(
    row: dict[str, Any],
    *,
    signal_id: str,
    action: str,
    direction: str,
    quantity: float,
    alpha_source: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> dict[str, Any]:
    symbol = row["metadata"]["symbol"]
    close = float(row["metadata"]["close"])
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strategy-quant-sell-long-cash-close",
        "timestamp": _iso_now(),
        "symbol": f"{symbol}.US",
        "action": action,
        "direction": direction,
        "order_type": "MARKET",
        "quantity": quantity,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-sell-long-cash-normalizer",
        "metadata": {
            "alpha_source": alpha_source,
            "confidence_score": 0.9,
            "market_data_ref": normalized_ref_uris,
            "source_evidence_refs": [
                {
                    "ref_type": "normalized_rows",
                    "dataset": row["metadata"]["dataset"],
                    "uri": uri,
                    "ingest_run_id": ingest_run_id,
                }
                for uri in normalized_ref_uris
            ],
            "market_data": {
                "dataset": row["metadata"]["dataset"],
                "symbol": symbol,
                "date": row["metadata"]["date"],
                "close": close,
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref_uris,
            "source_dataset_ref": row["metadata"]["dataset"],
            "ingest_run_id": ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-035",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-035-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-035",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-035",
                "runtime_id": "paper-runtime-035",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-sell-long-cash",
                "artifact_version": "35.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-sell-long-cash",
                "persona_capital_binding_id": "pcb-paper-sell-long-cash",
                "status": "active",
            }
        ]


class _CanonicalTelemetryRecorder:
    enabled = True

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        metadata = dict(metadata or {})
        index = len(self.events) + 1
        event = {
            "event_id": f"e2e-loop-035-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-035",
            "runtime_id": "paper-runtime-035",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-sell-long-cash",
            "artifact_version": "35.0.0",
            "plan_id": "plan-paper-sell-long-cash",
            "persona_capital_binding_id": "pcb-paper-sell-long-cash",
            "target": {
                "registry_id": "artifact-paper-sell-long-cash",
                "strategy_id": metadata.get("strategy_id") or "strategy-quant-sell-long-cash-close",
                "artifact_version": "35.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-035-runtime",
        }
        self.events.append(event)
        return True

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl)}
        if extra_metrics:
            metrics.update(extra_metrics)
        return self.emit("pnl_snapshot", metrics, metadata)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "url": "memory://telemetry",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
