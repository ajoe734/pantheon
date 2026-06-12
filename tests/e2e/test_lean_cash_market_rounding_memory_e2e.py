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


def test_cash_value_market_rounding_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T20:57:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-057-us-prices",
            "trace_id": "trace-e2e-loop-057-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "NET"
    assert row["metadata"]["close"] == 100.0

    signal = _cash_market_rounding_signal(
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
    assert snapshot["paper_state"]["positions"] == [
        {"symbol": "NET", "quantity": 10.0, "price": 100.0}
    ]

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "market_order"
    assert fill_event["metrics"]["fill_quantity"] == 10.0
    assert fill_event["metrics"]["fill_price"] == 100.0
    assert fill_event["metadata"]["signal_id"] == "quant-net-cash-market-rounding-057"
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_cash_market_rounding"
    assert fill_event["metadata"]["quantity_type"] == "CASH_VALUE"
    assert fill_event["metadata"]["order_type"] == "MARKET"
    assert fill_event["metadata"]["requested_quantity"] == 1049.0
    assert fill_event["metadata"]["market_price"] == 100.0
    assert fill_event["metadata"]["submitted_to_broker"] is False
    assert fill_event["metadata"]["is_real_order"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
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
        "binding-e2e-loop-057",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill = records_by_id[stored_fill["event_id"]]
    assert recovered_fill["alpha_context"]["market_data_ref"] == normalized_ref["uri"]
    assert recovered_fill["order_context"]["quantity_type"] == "CASH_VALUE"
    assert recovered_fill["order_context"]["order_type"] == "MARKET"
    assert recovered_fill["order_context"]["requested_quantity"] == 1049.0
    assert recovered_fill["order_context"]["fill_quantity"] == 10.0
    assert recovered_fill["order_context"]["fill_price"] == 100.0
    assert recovered_fill["order_context"]["market_price"] == 100.0
    assert recovered_fill["order_context"]["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["fill_event_count"] == 1
    assert recovered_pnl_context["fill_rate"] == 1.0
    assert recovered_pnl_context["open_position_count"] == 1

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-cash-market-rounding-sponsor",
        contributing_persona_ids=["persona-cash-rounding-ops"],
        summary=(
            "NET fetched close=100.0 produced a BUY/LONG CASH_VALUE market signal for 1049.0 cash; "
            "LEAN rounded sizing to a 10 share paper fill, recovered adapter feedback, and wrote "
            "the cash market rounding context into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-cash-rounding-ops",
                "summary": "Cash market feedback preserved requested cash and rounded share fill.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["cash_market_rounding", "paper_fill", "sizing_audit"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["tags"].extend(["cash_market_rounding", "paper_fill", "sizing_audit"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="NET cash market rounding requested cash fill quantity",
        tags=["cash_market_rounding", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    assert lineage["alpha_context"]["signal_id"] == "quant-net-cash-market-rounding-057"
    assert lineage["alpha_context"]["alpha_source"] == "pure_quant_cash_market_rounding"
    assert lineage["order_context"]["requested_quantity"] == 1049.0
    assert lineage["order_context"]["fill_quantity"] == 10.0
    assert lineage["order_context"]["market_price"] == 100.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-cash-rounding-ops",
        query="requested cash rounded shares",
        tags=["sizing_audit"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == "strategy-cash-market-rounding"
    assert persona_lineage["order_context"]["quantity_type"] == "CASH_VALUE"
    assert persona_lineage["order_context"]["fill_quantity"] == 10.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-057-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 057 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_cash_market_rounding_price_daily",
            "feature_targets": ["features/quant_cash_market_rounding_inputs"],
            "schema_hash": "us_cash_market_rounding_price_daily.e2e_loop_057.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-057-net",
        "title": "NET daily close for E2E loop 057",
        "content_ref": "market://us_cash_market_rounding_price_daily/NET/2026-06-12",
        "metadata": {
            "dataset": "us_cash_market_rounding_price_daily",
            "date": "2026-06-12",
            "symbol": "NET",
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 710000,
        },
    }


def _cash_market_rounding_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "quant-net-cash-market-rounding-057",
        "version": "1.0",
        "strategy_id": "strategy-cash-market-rounding",
        "timestamp": _iso_now(),
        "symbol": "NET.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 1049.0,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-cash-market-rounding-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_cash_market_rounding",
            "confidence_score": 0.92,
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-057",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-057-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-057",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-057",
                "runtime_id": "paper-runtime-057",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-cash-market-rounding",
                "artifact_version": "6.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-cash-market-rounding",
                "persona_capital_binding_id": "pcb-paper-cash-market-rounding",
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
            "event_id": f"e2e-loop-057-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-057",
            "runtime_id": "paper-runtime-057",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-cash-market-rounding",
            "artifact_version": "6.0.0",
            "plan_id": "plan-paper-cash-market-rounding",
            "persona_capital_binding_id": "pcb-paper-cash-market-rounding",
            "target": {
                "registry_id": "artifact-paper-cash-market-rounding",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-cash-market-rounding",
                "artifact_version": "6.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-057-runtime",
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
