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


def test_forex_cash_value_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _fx_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_fx_record()],
                "next_watermark": "2026-06-12T21:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-009-fx-rates",
            "trace_id": "trace-e2e-loop-009-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "EURUSD"
    assert row["metadata"]["close"] == 1.25

    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            [
                _fx_signal(
                    row,
                    normalized_ref=normalized_ref,
                    ingest_run_id=ingest_body["run"]["ingest_run_id"],
                )
            ]
        ),
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    position = snapshot["paper_state"]["positions"][0]
    assert position["symbol"] == "EURUSD"
    assert position["quantity"] == 100000.0
    assert position["price"] == 1.25
    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "market_order"
    assert fill_event["metrics"]["fill_quantity"] == 100000.0
    assert fill_event["metrics"]["fill_price"] == 1.25
    assert fill_event["metadata"]["alpha_source"] == "fx_carry_quant"
    assert fill_event["metadata"]["market_price"] == 1.25
    assert fill_event["metadata"]["source_dataset_ref"] == "fx_rate_daily"

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-fx-carry-cash",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-fx-sponsor",
        contributing_persona_ids=["persona-fx-ops"],
        summary=(
            "EURUSD.FX alpha consumed fetched fx_rate_daily close=1.25, placed a "
            "paper CASH_VALUE order, and received a 100000 unit paper fill."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-fx-ops",
                "summary": "Forex CASH_VALUE fill feedback confirmed LEAN FX symbol routing and sizing.",
                "proposal_ids": ["fx-carry-eurusd-cash-009"],
                "tags": ["forex_cash_value", "paper_fill", "market_data_fetch"],
            }
        ],
        proposal_ids=["fx-carry-eurusd-cash-009"],
    )

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="EURUSD Forex CASH_VALUE paper fill",
        tags=["forex_cash_value", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    assert evidence["ref_id"] == stored_fill["event_id"]
    assert evidence["lineage"]["strategy_id"] == "strategy-fx-carry-cash"
    assert evidence["lineage"]["alpha_context"]["source_dataset_ref"] == "fx_rate_daily"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-fx-ops",
        query="Forex symbol routing sizing",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"][
        "alpha_context"
    ]
    assert persona_context["signal_id"] == "fx-carry-eurusd-cash-009"
    assert persona_context["market_price"] == 1.25


def _fx_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-009-fx-rates",
        "source_type": "market",
        "provider": "E2E Loop 009 Static FX Rates",
        "license_scope": "internal",
        "metadata": {
            "dataset": "fx_rate_daily",
            "feature_targets": ["features/fx_cash_inputs"],
            "schema_hash": "fx_rate_daily.e2e_loop_009.v1",
        },
    }


def _fx_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-009-eurusd",
        "title": "EURUSD daily close for E2E loop 009",
        "content_ref": "market://fx_rate_daily/EURUSD/2026-06-12",
        "metadata": {
            "dataset": "fx_rate_daily",
            "date": "2026-06-12",
            "symbol": "EURUSD",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "venue": "OANDA",
            "open": 1.245,
            "high": 1.255,
            "low": 1.24,
            "close": 1.25,
            "volume": 920000,
        },
    }


def _fx_signal(row: dict[str, Any], *, normalized_ref: dict[str, Any], ingest_run_id: str) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "fx-carry-eurusd-cash-009",
        "version": "1.0",
        "strategy_id": "strategy-fx-carry-cash",
        "timestamp": _iso_now(),
        "symbol": "EURUSD.FX",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 125000,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-fx-carry-normalizer",
        "metadata": {
            "alpha_source": "fx_carry_quant",
            "confidence_score": 0.87,
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "base_currency": metadata["base_currency"],
                "quote_currency": metadata["quote_currency"],
                "venue": metadata["venue"],
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-009",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-009-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-009",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-009",
                "runtime_id": "paper-runtime-009",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-forex",
                "artifact_version": "9.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-forex",
                "persona_capital_binding_id": "pcb-paper-forex",
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
            "event_id": f"e2e-loop-009-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-009",
            "runtime_id": "paper-runtime-009",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-forex",
            "artifact_version": "9.0.0",
            "plan_id": "plan-paper-forex",
            "persona_capital_binding_id": "pcb-paper-forex",
            "target": {
                "registry_id": "artifact-paper-forex",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-forex",
                "artifact_version": "9.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-009-runtime",
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
        metrics = {"pnl": pnl}
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
