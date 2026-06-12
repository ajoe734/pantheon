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


def test_shares_sell_short_limit_feedback_recovery_memory_readback_e2e(
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
                "records": [_market_record()],
                "next_watermark": "2026-06-12T20:51:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-051-us-prices",
            "trace_id": "trace-e2e-loop-051-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "AFRM"
    assert row["metadata"]["close"] == 25.0

    signal = _shares_limit_short_signal(
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
        {"symbol": "AFRM", "quantity": -8.0, "price": 24.5}
    ]

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "limit_order"
    assert fill_event["metrics"]["fill_quantity"] == -8.0
    assert fill_event["metrics"]["fill_price"] == 24.5
    assert fill_event["metadata"]["signal_id"] == "quant-afrm-shares-limit-short-051"
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_shares_limit_short"
    assert fill_event["metadata"]["quantity_type"] == "SHARES"
    assert fill_event["metadata"]["order_type"] == "LIMIT"
    assert fill_event["metadata"]["limit_price"] == 24.5
    assert fill_event["metadata"]["requested_quantity"] == 8.0
    assert fill_event["metadata"]["market_price"] == 25.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    pnl_event = next(event for event in telemetry.events if event["event_type"] == "pnl_snapshot")
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
        "binding-e2e-loop-051",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill_context = records_by_id[stored_fill["event_id"]]["order_context"]
    assert recovered_fill_context["fill_quantity"] == -8.0
    assert recovered_fill_context["fill_price"] == 24.5
    assert recovered_fill_context["quantity_type"] == "SHARES"
    assert recovered_fill_context["order_type"] == "LIMIT"
    assert recovered_fill_context["limit_price"] == 24.5
    assert recovered_fill_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["fill_event_count"] == 1
    assert recovered_pnl_context["open_position_count"] == 1

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-shares-limit-short-sponsor",
        contributing_persona_ids=["persona-shares-limit-short-ops"],
        summary=(
            "AFRM fetched close=25.0 produced a SELL/SHORT SHARES limit order, "
            "filled 8 short shares at limit price 24.5, recovered adapter feedback, "
            "and wrote the short limit context into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-shares-limit-short-ops",
                "summary": "Shares limit short feedback preserved negative fill quantity and limit price.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["shares_limit_short", "paper_fill", "adapter_recovery"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["tags"].extend(["shares_limit_short", "paper_fill", "adapter_recovery"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="AFRM shares limit short negative fill",
        tags=["shares_limit_short", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_fill["event_id"]
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    assert lineage["alpha_context"]["signal_id"] == "quant-afrm-shares-limit-short-051"
    assert lineage["alpha_context"]["market_price"] == 25.0
    assert lineage["order_context"]["fill_quantity"] == -8.0
    assert lineage["order_context"]["limit_price"] == 24.5

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-shares-limit-short-ops",
        query="limit short negative fill",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["alpha_context"]["alpha_source"] == "pure_quant_shares_limit_short"
    assert persona_lineage["order_context"]["order_type"] == "LIMIT"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-051-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 051 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_shares_limit_short_price_daily",
            "feature_targets": ["features/us_shares_limit_short_inputs"],
            "schema_hash": "us_shares_limit_short_price_daily.e2e_loop_051.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-051-afrm",
        "title": "AFRM daily close for E2E loop 051",
        "content_ref": "market://us_shares_limit_short_price_daily/AFRM/2026-06-12",
        "metadata": {
            "dataset": "us_shares_limit_short_price_daily",
            "date": "2026-06-12",
            "symbol": "AFRM",
            "open": 25.5,
            "high": 26.0,
            "low": 24.0,
            "close": 25.0,
            "volume": 840000,
        },
    }


def _shares_limit_short_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "quant-afrm-shares-limit-short-051",
        "version": "1.0",
        "strategy_id": "strategy-shares-limit-short",
        "timestamp": _iso_now(),
        "symbol": "AFRM.US",
        "action": "SELL",
        "direction": "SHORT",
        "quantity": 8,
        "quantity_type": "SHARES",
        "order_type": "LIMIT",
        "limit_price": 24.5,
        "source_worker": "mock-shares-limit-short-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_shares_limit_short",
            "confidence_score": 0.84,
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-051",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-051-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-051",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-051",
                "runtime_id": "paper-runtime-051",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-shares-limit-short",
                "artifact_version": "3.1.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-shares-limit-short",
                "persona_capital_binding_id": "pcb-paper-shares-limit-short",
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
            "event_id": f"e2e-loop-051-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-051",
            "runtime_id": "paper-runtime-051",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-shares-limit-short",
            "artifact_version": "3.1.0",
            "plan_id": "plan-paper-shares-limit-short",
            "persona_capital_binding_id": "pcb-paper-shares-limit-short",
            "target": {
                "registry_id": "artifact-paper-shares-limit-short",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-shares-limit-short",
                "artifact_version": "3.1.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-051-runtime",
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
