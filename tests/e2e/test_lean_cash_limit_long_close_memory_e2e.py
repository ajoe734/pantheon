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


def test_cash_value_sell_long_limit_close_feedback_recovery_memory_readback_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-049-etsy-day1", "2026-06-11", close=30.0),
                    _market_record("src-e2e-loop-049-etsy-day2", "2026-06-12", close=36.0),
                ],
                "next_watermark": "2026-06-12T20:49:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-049-us-prices",
            "trace_id": "trace-e2e-loop-049-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [
        row
        for ref in normalized_refs
        for row in _read_jsonl(Path(ref["uri"]))
    ]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-11", 30.0),
        ("2026-06-12", 36.0),
    ]

    signals = _cash_limit_close_signals(
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
        {"symbol": "ETSY", "quantity": 10.0, "price": 30.0}
    ]

    store.enqueue(signals[1])
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    assert snapshot["paper_state"]["positions"] == [
        {"symbol": "ETSY", "quantity": 5.0, "price": 36.0}
    ]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-etsy-entry-049",
        "quant-etsy-cash-limit-close-049",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "limit_order"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [10.0, -5.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [30.0, 36.0]
    entry_fill, limit_close_fill = fill_events
    assert entry_fill["metadata"]["market_price"] == 30.0
    assert limit_close_fill["metadata"]["alpha_source"] == "pure_quant_cash_limit_close"
    assert limit_close_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert limit_close_fill["metadata"]["order_type"] == "LIMIT"
    assert limit_close_fill["metadata"]["limit_price"] == 36.0
    assert limit_close_fill["metadata"]["requested_quantity"] == 180.0
    assert limit_close_fill["metadata"]["market_price"] == 36.0
    assert limit_close_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 60.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(
            fill_event,
            strategy_id="strategy-cash-limit-long-close",
            promotion_state="paper",
        )
        for fill_event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-cash-limit-long-close",
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-049",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_close_context = records_by_id[stored_fills[1]["event_id"]]["order_context"]
    assert recovered_close_context["fill_quantity"] == -5.0
    assert recovered_close_context["fill_price"] == 36.0
    assert recovered_close_context["quantity_type"] == "CASH_VALUE"
    assert recovered_close_context["order_type"] == "LIMIT"
    assert recovered_close_context["limit_price"] == 36.0
    assert recovered_close_context["requested_quantity"] == 180.0
    assert recovered_close_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["pnl"] == 60.0
    assert recovered_pnl_context["open_position_count"] == 1
    assert recovered_pnl_context["fill_event_count"] == 2

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fills[1],
        sponsor_persona_id="persona-cash-limit-close-sponsor",
        contributing_persona_ids=["persona-cash-limit-close-ops"],
        summary=(
            "ETSY fetched prices opened 10 shares at 30.0, translated a 180.0 CASH_VALUE "
            "SELL/LONG limit close into 5 shares at limit price 36.0, recovered adapter feedback, "
            "and preserved the remaining position."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-cash-limit-close-ops",
                "summary": "Cash-value limit close feedback preserved limit-price sizing and PnL counters.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["cash_value", "limit_close", "adapter_recovery", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_fills[1]["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["tags"].extend(["cash_value", "limit_close", "adapter_recovery"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ETSY cash value limit close adapter recovery",
        tags=["cash_value", "limit_close"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_fills[1]["event_id"]
    institutional_payload = institutional_hits[0].entry.content["structured_payload"]
    institutional_lineage = institutional_payload["runtime_telemetry_evidence"][0]["lineage"]
    assert institutional_lineage["alpha_context"]["signal_id"] == "quant-etsy-cash-limit-close-049"
    assert institutional_lineage["order_context"]["quantity_type"] == "CASH_VALUE"
    assert institutional_lineage["order_context"]["limit_price"] == 36.0
    assert institutional_lineage["order_context"]["fill_quantity"] == -5.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-cash-limit-close-ops",
        query="cash limit close sizing",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["alpha_context"]["alpha_source"] == "pure_quant_cash_limit_close"
    assert persona_lineage["order_context"]["requested_quantity"] == 180.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-049-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 049 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_cash_limit_long_close_price_daily",
            "feature_targets": ["features/us_cash_limit_close_inputs"],
            "schema_hash": "us_cash_limit_long_close_price_daily.e2e_loop_049.v1",
        },
    }


def _market_record(source_id: str, date: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"ETSY daily close for E2E loop 049 on {date}",
        "content_ref": f"market://us_cash_limit_long_close_price_daily/ETSY/{date}",
        "metadata": {
            "dataset": "us_cash_limit_long_close_price_daily",
            "date": date,
            "symbol": "ETSY",
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 760000,
        },
    }


def _cash_limit_close_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    row_by_date = {row["metadata"]["date"]: row for row in rows}
    return [
        {
            "signal_id": "quant-etsy-entry-049",
            "version": "1.0",
            "strategy_id": "strategy-cash-limit-long-close",
            "timestamp": _iso_now(),
            "symbol": "ETSY.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 10,
            "quantity_type": "SHARES",
            "source_worker": "mock-cash-limit-close-normalizer",
            "metadata": _metadata(
                row_by_date["2026-06-11"],
                normalized_ref_uris=normalized_ref_uris,
                ingest_run_id=ingest_run_id,
                alpha_source="pure_quant_cash_limit_entry",
            ),
        },
        {
            "signal_id": "quant-etsy-cash-limit-close-049",
            "version": "1.0",
            "strategy_id": "strategy-cash-limit-long-close",
            "timestamp": _iso_now(),
            "symbol": "ETSY.US",
            "action": "SELL",
            "direction": "LONG",
            "quantity": 180.0,
            "quantity_type": "CASH_VALUE",
            "order_type": "LIMIT",
            "limit_price": 36.0,
            "source_worker": "mock-cash-limit-close-normalizer",
            "metadata": _metadata(
                row_by_date["2026-06-12"],
                normalized_ref_uris=normalized_ref_uris,
                ingest_run_id=ingest_run_id,
                alpha_source="pure_quant_cash_limit_close",
            ),
        },
    ]


def _metadata(
    row: dict[str, Any],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
    alpha_source: str,
) -> dict[str, Any]:
    return {
        "alpha_source": alpha_source,
        "confidence_score": 0.87,
        "market_data": {
            "dataset": row["metadata"]["dataset"],
            "symbol": row["metadata"]["symbol"],
            "date": row["metadata"]["date"],
            "close": row["metadata"]["close"],
            "content_ref": row["content_ref"],
        },
        "normalized_data_ref": normalized_ref_uris,
        "source_dataset_ref": "us_cash_limit_long_close_price_daily",
        "ingest_run_id": ingest_run_id,
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-049",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-049-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-049",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-049",
                "runtime_id": "paper-runtime-049",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-cash-limit-close",
                "artifact_version": "2.9.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-cash-limit-close",
                "persona_capital_binding_id": "pcb-paper-cash-limit-close",
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
            "event_id": f"e2e-loop-049-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-049",
            "runtime_id": "paper-runtime-049",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-cash-limit-close",
            "artifact_version": "2.9.0",
            "plan_id": "plan-paper-cash-limit-close",
            "persona_capital_binding_id": "pcb-paper-cash-limit-close",
            "target": {
                "registry_id": "artifact-paper-cash-limit-close",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-cash-limit-close",
                "artifact_version": "2.9.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-049-runtime",
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
        metrics.update(extra_metrics or {})
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
