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


def test_close_signal_with_bracket_risk_logs_non_entry_feedback_memory_readback_e2e(
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
                    _market_record("src-e2e-loop-043-intc-day1", "2026-06-11", close=100.0),
                    _market_record("src-e2e-loop-043-intc-day2", "2026-06-12", close=110.0),
                ],
                "next_watermark": "2026-06-12T20:43:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-043-us-prices",
            "trace_id": "trace-e2e-loop-043-data-fetch",
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
        ("2026-06-11", 100.0),
        ("2026-06-12", 110.0),
    ]

    signals = _entry_and_close_signals(
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
        {"symbol": "INTC", "quantity": 4.0, "price": 100.0}
    ]

    store.enqueue(signals[1])
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 3
    assert snapshot["paper_state"]["positions"] == []
    assert snapshot["paper_state"]["open_bracket_orders"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-intc-entry-043",
        "quant-intc-close-risk-043",
    ]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [4.0, -4.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [100.0, 110.0]
    entry_fill, close_fill = fill_events
    assert entry_fill["metrics"]["action"] == "market_order"
    assert entry_fill["metadata"]["market_price"] == 100.0
    assert close_fill["metrics"]["action"] == "liquidate"
    assert close_fill["metadata"]["alpha_source"] == "pure_quant_close_with_risk"
    assert close_fill["metadata"]["market_price"] == 110.0
    assert close_fill["metadata"]["submitted_to_broker"] is False

    bracket_event = next(event for event in telemetry.events if event["event_type"] == "bracket_order_logged")
    assert bracket_event["metrics"]["action"] == "bracket_logged_only"
    assert bracket_event["metrics"]["submitted_to_broker"] is False
    assert bracket_event["metadata"]["signal_id"] == "quant-intc-close-risk-043"
    assert bracket_event["metadata"]["broker_submission_status"] == "logged_only"
    assert bracket_event["metadata"]["submitted_to_broker"] is False
    assert bracket_event["metadata"]["guard_stage"] == "paper"
    assert bracket_event["metadata"]["guard_reason"] == "paper/sim bracket execution guard passed"
    assert bracket_event["metadata"]["reason"] == "not_entry_signal"
    assert bracket_event["metadata"]["stop_loss_pct"] == 0.02
    assert bracket_event["metadata"]["take_profit_pct"] == 0.05
    assert "submission" not in bracket_event["metadata"]

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 40.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 3
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(
            fill_event,
            strategy_id="strategy-close-risk-logged",
            promotion_state="paper",
        )
        for fill_event in fill_events
    ]
    stored_bracket = writer_adapter.ingest_telemetry_event(
        bracket_event,
        strategy_id="strategy-close-risk-logged",
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-close-risk-logged",
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-043",
    )
    records_by_type = {record["event_type"]: record for record in recovered_records}
    assert {"paper_fill_simulated", "bracket_order_logged", "pnl_snapshot"} <= set(records_by_type)
    recovered_close_context = records_by_type["paper_fill_simulated"]["order_context"]
    assert recovered_close_context["fill_quantity"] == -4.0
    assert recovered_close_context["fill_price"] == 110.0
    recovered_bracket_context = records_by_type["bracket_order_logged"]["order_context"]
    assert recovered_bracket_context["broker_submission_status"] == "logged_only"
    assert recovered_bracket_context["submitted_to_broker"] is False
    assert recovered_bracket_context["reason"] == "not_entry_signal"
    assert recovered_bracket_context["guard_reason"] == "paper/sim bracket execution guard passed"
    assert "bracket_order_id" not in recovered_bracket_context
    recovered_pnl_context = records_by_type["pnl_snapshot"]["order_context"]
    assert recovered_pnl_context["pnl"] == 40.0
    assert recovered_pnl_context["execution_event_count"] == 3
    assert recovered_pnl_context["fill_event_count"] == 2
    assert stored_fills[1]["event_id"] != stored_bracket["event_id"]
    assert stored_pnl["event_id"] != stored_bracket["event_id"]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_bracket,
        sponsor_persona_id="persona-close-risk-sponsor",
        contributing_persona_ids=["persona-close-risk-ops"],
        summary=(
            "INTC fetched market data opened a long at 100.0, closed it at 110.0 for 40.0 PnL, "
            "and logged bracket risk as not_entry_signal instead of submitting child orders."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-close-risk-ops",
                "summary": "Close signal bracket feedback preserved the not-entry reason and realized PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["close_signal", "bracket_logged_only", "not_entry_signal", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_bracket["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["tags"].extend(["close_signal", "bracket_logged_only", "not_entry_signal"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="INTC close signal bracket not entry performance",
        tags=["bracket_logged_only", "not_entry_signal"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_bracket["event_id"]
    institutional_payload = institutional_hits[0].entry.content["structured_payload"]
    institutional_lineage = institutional_payload["runtime_telemetry_evidence"][0]["lineage"]
    assert institutional_lineage["alpha_context"]["signal_id"] == "quant-intc-close-risk-043"
    assert institutional_lineage["order_context"]["reason"] == "not_entry_signal"
    assert institutional_lineage["order_context"]["broker_submission_status"] == "logged_only"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-close-risk-ops",
        query="not entry bracket close pnl",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_payload = persona_hits[0].entry.content["structured_payload"]
    persona_lineage = persona_payload["runtime_telemetry_evidence"][0]["lineage"]
    assert persona_lineage["alpha_context"]["alpha_source"] == "pure_quant_close_with_risk"
    assert persona_lineage["order_context"]["reason"] == "not_entry_signal"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-043-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 043 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_close_risk_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_043.v1",
        },
    }


def _market_record(source_id: str, date: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"INTC daily close for E2E loop 043 on {date}",
        "content_ref": f"market://us_price_daily/INTC/{date}",
        "metadata": {
            "dataset": "us_price_daily",
            "date": date,
            "symbol": "INTC",
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1800000,
        },
    }


def _entry_and_close_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    row_by_date = {row["metadata"]["date"]: row for row in rows}
    return [
        {
            "signal_id": "quant-intc-entry-043",
            "version": "1.0",
            "strategy_id": "strategy-close-risk-logged",
            "timestamp": _iso_now(),
            "symbol": "INTC.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 4,
            "quantity_type": "SHARES",
            "source_worker": "mock-close-risk-normalizer",
            "metadata": _metadata(
                row_by_date["2026-06-11"],
                normalized_ref_uris=normalized_ref_uris,
                ingest_run_id=ingest_run_id,
                alpha_source="pure_quant_entry_probe",
            ),
        },
        {
            "signal_id": "quant-intc-close-risk-043",
            "version": "1.0",
            "strategy_id": "strategy-close-risk-logged",
            "timestamp": _iso_now(),
            "symbol": "INTC.US",
            "action": "SELL",
            "direction": "LONG",
            "quantity": 4,
            "quantity_type": "SHARES",
            "source_worker": "mock-close-risk-normalizer",
            "metadata": {
                **_metadata(
                    row_by_date["2026-06-12"],
                    normalized_ref_uris=normalized_ref_uris,
                    ingest_run_id=ingest_run_id,
                    alpha_source="pure_quant_close_with_risk",
                ),
                "risk_parameters": {
                    "stop_loss_pct": 0.02,
                    "take_profit_pct": 0.05,
                },
            },
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
        "confidence_score": 0.93,
        "market_data": {
            "dataset": row["metadata"]["dataset"],
            "symbol": row["metadata"]["symbol"],
            "date": row["metadata"]["date"],
            "close": row["metadata"]["close"],
            "content_ref": row["content_ref"],
        },
        "normalized_data_ref": normalized_ref_uris,
        "source_dataset_ref": "us_price_daily",
        "ingest_run_id": ingest_run_id,
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-043",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-043-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-043",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-043",
                "runtime_id": "paper-runtime-043",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-close-risk",
                "artifact_version": "2.3.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-close-risk",
                "persona_capital_binding_id": "pcb-paper-close-risk",
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
            "event_id": f"e2e-loop-043-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-043",
            "runtime_id": "paper-runtime-043",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-close-risk",
            "artifact_version": "2.3.0",
            "plan_id": "plan-paper-close-risk",
            "persona_capital_binding_id": "pcb-paper-close-risk",
            "target": {
                "registry_id": "artifact-paper-close-risk",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-close-risk",
                "artifact_version": "2.3.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-043-runtime",
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
