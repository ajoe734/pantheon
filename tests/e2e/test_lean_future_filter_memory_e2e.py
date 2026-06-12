from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def test_lean_future_signal_filter_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T23:59:45Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-026-us-future-prices",
            "trace_id": "trace-e2e-loop-026-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "DOCU"
    assert row["metadata"]["close"] == 55.0

    signal = _future_signal(
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
    assert noop_event["metrics"]["action"] == "future_signal_anomaly_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 0.20
    assert noop_event["metrics"]["computed_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["signal_id"] == "future-filter-docu-026"
    assert noop_event["metadata"]["alpha_source"] == "quant_future_clock_guard"
    assert noop_event["metadata"]["noop_reason"] == "future_signal_anomaly"
    assert noop_event["metadata"]["filter_reason"] == "future_signal_anomaly"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert noop_event["metadata"]["requested_quantity"] == 0.20
    assert noop_event["metadata"]["computed_quantity"] == 0.0
    assert noop_event["metadata"]["price"] == 55.0
    assert noop_event["metadata"]["market_price"] == 55.0
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_filtered"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
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
    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_noops = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="paper_order_simulated",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_noops] == [stored_noop["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_noops[0],
        sponsor_persona_id="persona-future-filter-sponsor",
        contributing_persona_ids=["persona-clock-guard"],
        summary=(
            "DOCU market data produced a signal timestamped more than one hour in the future; "
            "the signal consumer filtered it as a clock anomaly, emitted no-order feedback, "
            "recovered the feedback after adapter restart, and wrote future-filter context into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-clock-guard",
                "summary": "Future timestamp feedback preserved filter reason, requested quantity, and broker non-submission.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["future_filter", "clock_guard", "paper_noop"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_noop["event_id"]],
    )
    writeback_payload["tags"].extend(["future_filter", "clock_guard", "paper_noop"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="DOCU future timestamp clock anomaly no order",
        tags=["future_filter", "clock_guard"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "future-filter-docu-026"
    assert alpha_context["alpha_source"] == "quant_future_clock_guard"
    assert alpha_context["market_price"] == 55.0
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["noop_reason"] == "future_signal_anomaly"
    assert order_context["filter_reason"] == "future_signal_anomaly"
    assert order_context["decision_status"] == "no_order"
    assert order_context["order_status"] == "not_submitted"
    assert order_context["quantity_type"] == "PERCENT_PORTFOLIO"
    assert order_context["requested_quantity"] == 0.20
    assert order_context["computed_quantity"] == 0.0
    assert order_context["price"] == 55.0
    assert order_context["fill_rate"] == 0.0
    assert order_context["noop_count"] == 1
    assert order_context["broker_submission_status"] == "not_submitted_signal_filtered"
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-clock-guard",
        query="future timestamp filter reason",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["filter_reason"] == "future_signal_anomaly"
    assert persona_order_context["computed_quantity"] == 0.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-026-us-future-prices",
        "source_type": "market",
        "provider": "E2E Loop 026 Static US Future Filter Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_future_filter_price_daily",
            "feature_targets": ["features/quant_future_filter_inputs"],
            "schema_hash": "us_future_filter_price_daily.e2e_loop_026.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-026-docu",
        "title": "DOCU daily close for E2E loop 026",
        "content_ref": "market://us_future_filter_price_daily/DOCU/2026-06-12",
        "metadata": {
            "dataset": "us_future_filter_price_daily",
            "date": "2026-06-12",
            "symbol": "DOCU",
            "open": 56.0,
            "high": 57.0,
            "low": 54.0,
            "close": 55.0,
            "volume": 1700000,
        },
    }


def _future_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "future-filter-docu-026",
        "version": "1.0",
        "strategy_id": "strategy-quant-future-filter",
        "timestamp": (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat(),
        "symbol": "DOCU.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.20,
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-future-filter-normalizer",
        "metadata": {
            "alpha_source": "quant_future_clock_guard",
            "confidence_score": 0.81,
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-026",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-026-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-026",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-026",
                "runtime_id": "paper-runtime-026",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-future-filter",
                "artifact_version": "26.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-future-filter",
                "persona_capital_binding_id": "pcb-paper-future-filter",
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
            "event_id": f"e2e-loop-026-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-026",
            "runtime_id": "paper-runtime-026",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-future-filter",
            "artifact_version": "26.0.0",
            "plan_id": "plan-paper-future-filter",
            "persona_capital_binding_id": "pcb-paper-future-filter",
            "target": {
                "registry_id": "artifact-paper-future-filter",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-future-filter",
                "artifact_version": "26.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-026-runtime",
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
