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


def test_exit_long_liquidate_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-062-wday-entry", "2026-06-11", close=30.0),
                    _market_record("src-e2e-loop-062-wday-exit", "2026-06-12", close=33.0),
                ],
                "next_watermark": "2026-06-12T21:02:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-062-us-prices",
            "trace_id": "trace-e2e-loop-062-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-11", 30.0),
        ("2026-06-12", 33.0),
    ]

    signals = _exit_long_signals(
        normalized_rows,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = _CanonicalTelemetryRecorder()
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["processed_signal_count"] == 1
    assert entry_snapshot["paper_state"]["execution_event_count"] == 1
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "WDAY", "quantity": 7.0, "price": 30.0}
    ]

    pending_store.enqueue(signals[1])
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    assert snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-wday-entry-062",
        "quant-wday-exit-long-062",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "liquidate"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [7.0, -7.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [30.0, 33.0]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metadata"]["quantity_type"] == "SHARES"
    assert entry_fill["metadata"]["requested_quantity"] == 7.0
    assert entry_fill["metadata"]["market_price"] == 30.0
    assert exit_fill["metadata"]["alpha_source"] == "pure_quant_exit_long_liquidate"
    assert exit_fill["metadata"]["quantity_type"] == "SHARES"
    assert exit_fill["metadata"]["order_type"] == "MARKET"
    assert exit_fill["metadata"]["requested_quantity"] == 0.0
    assert exit_fill["metadata"]["market_price"] == 33.0
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 21.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(
            fill_event,
            strategy_id="strategy-exit-long-liquidate",
            promotion_state="paper",
        )
        for fill_event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-exit-long-liquidate",
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-062",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_exit_context = records_by_id[stored_fills[1]["event_id"]]["order_context"]
    assert recovered_exit_context["fill_quantity"] == -7.0
    assert recovered_exit_context["fill_price"] == 33.0
    assert recovered_exit_context["quantity_type"] == "SHARES"
    assert recovered_exit_context["order_type"] == "MARKET"
    assert recovered_exit_context["market_price"] == 33.0
    assert recovered_exit_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["pnl"] == 21.0
    assert recovered_pnl_context["fill_event_count"] == 2
    assert recovered_pnl_context["open_position_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fills[1],
        sponsor_persona_id="persona-exit-long-sponsor",
        contributing_persona_ids=["persona-exit-long-ops"],
        summary=(
            "WDAY fetched prices opened 7 long shares at 30.0, then EXIT/LONG liquidated them "
            "at 33.0 for 21.0 paper PnL, recovered adapter feedback, and wrote the exit context "
            "into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-exit-long-ops",
                "summary": "EXIT/LONG liquidation feedback preserved close fill quantity, price, and realized PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["exit_long_liquidate", "paper_fill", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_fills[1]["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["tags"].extend(["exit_long_liquidate", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="WDAY EXIT LONG liquidate PnL",
        tags=["exit_long_liquidate", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    assert lineage["alpha_context"]["signal_id"] == "quant-wday-exit-long-062"
    assert lineage["alpha_context"]["market_price"] == 33.0
    assert lineage["order_context"]["fill_quantity"] == -7.0
    assert lineage["order_context"]["fill_price"] == 33.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-exit-long-ops",
        query="liquidate close fill pnl",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == "strategy-exit-long-liquidate"
    assert persona_lineage["order_context"]["fill_quantity"] == -7.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-062-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 062 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_exit_long_price_daily",
            "feature_targets": ["features/quant_exit_long_inputs"],
            "schema_hash": "us_exit_long_price_daily.e2e_loop_062.v1",
        },
    }


def _market_record(source_id: str, trade_date: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"WDAY daily close for E2E loop 062 {trade_date}",
        "content_ref": f"market://us_exit_long_price_daily/WDAY/{trade_date}",
        "metadata": {
            "dataset": "us_exit_long_price_daily",
            "date": trade_date,
            "symbol": "WDAY",
            "open": close - 1.0,
            "high": close + 1.0,
            "low": close - 2.0,
            "close": close,
            "volume": 830000,
        },
    }


def _exit_long_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    return [
        _signal(
            rows_by_date["2026-06-11"],
            signal_id="quant-wday-entry-062",
            action="BUY",
            direction="LONG",
            quantity=7.0,
            alpha_source="pure_quant_exit_long_entry",
            normalized_ref_uris=normalized_ref_uris,
            ingest_run_id=ingest_run_id,
        ),
        _signal(
            rows_by_date["2026-06-12"],
            signal_id="quant-wday-exit-long-062",
            action="EXIT",
            direction="LONG",
            quantity=0.0,
            alpha_source="pure_quant_exit_long_liquidate",
            normalized_ref_uris=normalized_ref_uris,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _signal(
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
    metadata = row["metadata"]
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strategy-exit-long-liquidate",
        "timestamp": _iso_now(),
        "symbol": "WDAY.US",
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": "SHARES",
        "source_worker": "mock-exit-long-normalizer",
        "metadata": {
            "alpha_source": alpha_source,
            "confidence_score": 0.89,
            "market_data_ref": normalized_ref_uris,
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "date": metadata["date"],
                "close": metadata["close"],
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref_uris,
            "source_dataset_ref": metadata["dataset"],
            "ingest_run_id": ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-062",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-062-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-062",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-062",
                "runtime_id": "paper-runtime-062",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-exit-long",
                "artifact_version": "6.5.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-exit-long",
                "persona_capital_binding_id": "pcb-paper-exit-long",
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
            "event_id": f"e2e-loop-062-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-062",
            "runtime_id": "paper-runtime-062",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-exit-long",
            "artifact_version": "6.5.0",
            "plan_id": "plan-paper-exit-long",
            "persona_capital_binding_id": "pcb-paper-exit-long",
            "target": {
                "registry_id": "artifact-paper-exit-long",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-exit-long",
                "artifact_version": "6.5.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-062-runtime",
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
