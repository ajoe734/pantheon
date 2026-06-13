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


def test_lean_cash_value_limit_short_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T23:59:59Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-034-us-cash-limit-short-prices",
            "trace_id": "trace-e2e-loop-034-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "MDB"
    assert row["metadata"]["close"] == 51.0

    signal = _cash_limit_short_signal(
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
    position = snapshot["paper_state"]["positions"][0]
    assert position["symbol"] == "MDB"
    assert position["quantity"] == -20.0
    assert position["price"] == 50.0
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert noop_events == []
    fill_event = fill_events[0]
    assert fill_event["metrics"]["action"] == "limit_order"
    assert fill_event["metrics"]["fill_quantity"] == -20.0
    assert fill_event["metrics"]["fill_price"] == 50.0
    assert fill_event["metadata"]["signal_id"] == "cash-limit-short-mdb-034"
    assert fill_event["metadata"]["alpha_source"] == "quant_cash_limit_short"
    assert fill_event["metadata"]["order_type"] == "LIMIT"
    assert fill_event["metadata"]["limit_price"] == 50.0
    assert fill_event["metadata"]["quantity_type"] == "CASH_VALUE"
    assert fill_event["metadata"]["requested_quantity"] == 1000.0
    assert fill_event["metadata"]["market_price"] == 51.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )
    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_fills = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="paper_fill_simulated",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_fills] == [stored_fill["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_fills[0],
        sponsor_persona_id="persona-cash-limit-short-sponsor",
        contributing_persona_ids=["persona-short-limit-execution"],
        summary=(
            "MDB market data produced a SELL/SHORT CASH_VALUE LIMIT signal; LEAN sized the short order "
            "from the limit price, placed a simulated negative-quantity LimitOrder, recovered the fill "
            "through the adapter, and wrote short cash-limit execution context into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-short-limit-execution",
                "summary": "Short cash-limit feedback preserved negative fill quantity, limit_price, and requested cash.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["cash_value_limit", "short_limit", "paper_fill"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"]],
    )
    writeback_payload["tags"].extend(["cash_value_limit", "short_limit", "paper_fill"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="MDB short cash value limit order negative fill",
        tags=["cash_value_limit", "short_limit"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "cash-limit-short-mdb-034"
    assert alpha_context["alpha_source"] == "quant_cash_limit_short"
    assert alpha_context["market_price"] == 51.0
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["order_type"] == "LIMIT"
    assert order_context["limit_price"] == 50.0
    assert order_context["quantity_type"] == "CASH_VALUE"
    assert order_context["requested_quantity"] == 1000.0
    assert order_context["fill_quantity"] == -20.0
    assert order_context["fill_price"] == 50.0
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-short-limit-execution",
        query="negative fill quantity limit price",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["fill_quantity"] == -20.0
    assert persona_order_context["order_type"] == "LIMIT"
    assert persona_order_context["limit_price"] == 50.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-034-us-cash-limit-short-prices",
        "source_type": "market",
        "provider": "E2E Loop 034 Static Cash Limit Short Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_cash_limit_short_price_daily",
            "feature_targets": ["features/quant_cash_limit_short_inputs"],
            "schema_hash": "us_cash_limit_short_price_daily.e2e_loop_034.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-034-mdb",
        "title": "MDB daily close for E2E loop 034",
        "content_ref": "market://us_cash_limit_short_price_daily/MDB/2026-06-12",
        "metadata": {
            "dataset": "us_cash_limit_short_price_daily",
            "date": "2026-06-12",
            "symbol": "MDB",
            "open": 52.0,
            "high": 53.0,
            "low": 49.0,
            "close": 51.0,
            "volume": 1300000,
        },
    }


def _cash_limit_short_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "cash-limit-short-mdb-034",
        "version": "1.0",
        "strategy_id": "strategy-quant-cash-limit-short",
        "timestamp": _iso_now(),
        "symbol": "MDB.US",
        "action": "SELL",
        "direction": "SHORT",
        "order_type": "LIMIT",
        "limit_price": 50.0,
        "quantity": 1000,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-cash-limit-short-normalizer",
        "metadata": {
            "alpha_source": "quant_cash_limit_short",
            "confidence_score": 0.86,
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-034",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-034-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-034",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-034",
                "runtime_id": "paper-runtime-034",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-cash-limit-short",
                "artifact_version": "34.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-cash-limit-short",
                "persona_capital_binding_id": "pcb-paper-cash-limit-short",
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
            "event_id": f"e2e-loop-034-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-034",
            "runtime_id": "paper-runtime-034",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-cash-limit-short",
            "artifact_version": "34.0.0",
            "plan_id": "plan-paper-cash-limit-short",
            "persona_capital_binding_id": "pcb-paper-cash-limit-short",
            "target": {
                "registry_id": "artifact-paper-cash-limit-short",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-cash-limit-short",
                "artifact_version": "34.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-034-runtime",
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
