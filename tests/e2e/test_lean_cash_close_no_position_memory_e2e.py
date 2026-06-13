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


def test_cash_value_sell_long_without_position_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T21:01:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-061-us-prices",
            "trace_id": "trace-e2e-loop-061-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "ZS"
    assert row["metadata"]["close"] == 75.0

    signal = _cash_close_no_position_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    assert snapshot["paper_state"]["execution_event_count"] == 1
    assert snapshot["paper_state"]["positions"] == []
    assert snapshot["paper_state"]["recent_order_events"][0]["event_type"] == "paper_order_simulated"

    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(noop_events) == 1
    assert fill_events == []
    noop_event = noop_events[0]
    assert noop_event["metrics"]["action"] == "liquidate_without_position_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 500.0
    assert noop_event["metrics"]["computed_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["signal_id"] == "quant-zs-cash-close-empty-061"
    assert noop_event["metadata"]["alpha_source"] == "pure_quant_cash_close_empty"
    assert noop_event["metadata"]["noop_reason"] == "liquidate_without_position"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "CASH_VALUE"
    assert noop_event["metadata"]["order_type"] == "MARKET"
    assert noop_event["metadata"]["requested_quantity"] == 500.0
    assert noop_event["metadata"]["computed_quantity"] == 0.0
    assert noop_event["metadata"]["position_quantity"] == 0.0
    assert noop_event["metadata"]["price"] == 75.0
    assert noop_event["metadata"]["market_price"] == 75.0
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_noop"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 0
    assert pnl_event["metrics"]["fill_rate"] == 0.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_noop = writer_adapter.ingest_telemetry_event(
        noop_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-061",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_noop_context = records_by_id[stored_noop["event_id"]]["order_context"]
    assert recovered_noop_context["noop_reason"] == "liquidate_without_position"
    assert recovered_noop_context["quantity_type"] == "CASH_VALUE"
    assert recovered_noop_context["order_type"] == "MARKET"
    assert recovered_noop_context["requested_quantity"] == 500.0
    assert recovered_noop_context["computed_quantity"] == 0.0
    assert recovered_noop_context["position_quantity"] == 0.0
    assert recovered_noop_context["market_price"] == 75.0
    assert recovered_noop_context["fill_rate"] == 0.0
    assert recovered_noop_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["fill_event_count"] == 0
    assert recovered_pnl_context["fill_rate"] == 0.0
    assert recovered_pnl_context["open_position_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_noop,
        sponsor_persona_id="persona-cash-close-empty-sponsor",
        contributing_persona_ids=["persona-cash-close-empty-ops"],
        summary=(
            "ZS fetched close=75.0 produced a SELL/LONG CASH_VALUE close signal for 500.0 cash, "
            "but LEAN found no long position; it emitted recoverable no-order feedback and wrote "
            "the requested cash close context into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-cash-close-empty-ops",
                "summary": "Cash close no-position feedback preserved requested cash, zero fill, and no-order status.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["cash_close_no_position", "paper_noop", "paper_performance"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_noop["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["tags"].extend(["cash_close_no_position", "paper_noop", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ZS cash close no position requested cash",
        tags=["cash_close_no_position", "paper_noop"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    assert lineage["alpha_context"]["signal_id"] == "quant-zs-cash-close-empty-061"
    assert lineage["alpha_context"]["alpha_source"] == "pure_quant_cash_close_empty"
    assert lineage["order_context"]["requested_quantity"] == 500.0
    assert lineage["order_context"]["quantity_type"] == "CASH_VALUE"
    assert lineage["order_context"]["noop_reason"] == "liquidate_without_position"
    assert lineage["order_context"]["fill_rate"] == 0.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-cash-close-empty-ops",
        query="cash close no position zero fill",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == "strategy-cash-close-no-position"
    assert persona_lineage["order_context"]["computed_quantity"] == 0.0
    assert persona_lineage["order_context"]["requested_quantity"] == 500.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-061-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 061 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_cash_close_empty_price_daily",
            "feature_targets": ["features/quant_cash_close_empty_inputs"],
            "schema_hash": "us_cash_close_empty_price_daily.e2e_loop_061.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-061-zs",
        "title": "ZS daily close for E2E loop 061",
        "content_ref": "market://us_cash_close_empty_price_daily/ZS/2026-06-12",
        "metadata": {
            "dataset": "us_cash_close_empty_price_daily",
            "date": "2026-06-12",
            "symbol": "ZS",
            "open": 76.0,
            "high": 77.0,
            "low": 73.0,
            "close": 75.0,
            "volume": 940000,
        },
    }


def _cash_close_no_position_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "quant-zs-cash-close-empty-061",
        "version": "1.0",
        "strategy_id": "strategy-cash-close-no-position",
        "timestamp": _iso_now(),
        "symbol": "ZS.US",
        "action": "SELL",
        "direction": "LONG",
        "quantity": 500.0,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-cash-close-empty-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_cash_close_empty",
            "confidence_score": 0.81,
            "market_data_ref": normalized_ref["uri"],
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "date": metadata["date"],
                "close": metadata["close"],
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref["uri"],
            "source_dataset_ref": normalized_ref["dataset"],
            "ingest_run_id": ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-061",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-061-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-061",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-061",
                "runtime_id": "paper-runtime-061",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-cash-close-empty",
                "artifact_version": "6.4.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-cash-close-empty",
                "persona_capital_binding_id": "pcb-paper-cash-close-empty",
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
            "event_id": f"e2e-loop-061-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-061",
            "runtime_id": "paper-runtime-061",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-cash-close-empty",
            "artifact_version": "6.4.0",
            "plan_id": "plan-paper-cash-close-empty",
            "persona_capital_binding_id": "pcb-paper-cash-close-empty",
            "target": {
                "registry_id": "artifact-paper-cash-close-empty",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-cash-close-empty",
                "artifact_version": "6.4.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-061-runtime",
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
