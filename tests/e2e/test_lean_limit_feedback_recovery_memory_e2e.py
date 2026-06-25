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


def test_limit_order_feedback_adapter_recovery_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T21:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-004-us-prices",
            "trace_id": "trace-e2e-loop-004-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert rows[0]["metadata"]["symbol"] == "META"
    assert rows[0]["metadata"]["close"] == 180.0

    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            [
                _limit_signal(
                    rows[0],
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
    assert snapshot["paper_state"]["positions"][0]["symbol"] == "META"
    assert snapshot["paper_state"]["positions"][0]["quantity"] == 7.0
    assert snapshot["paper_state"]["positions"][0]["price"] == 179.5
    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "limit_order"
    assert fill_event["metrics"]["fill_quantity"] == 7.0
    assert fill_event["metrics"]["fill_price"] == 179.5
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_limit_entry"
    assert fill_event["metadata"]["normalized_data_ref"] == normalized_ref["uri"]

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-limit-recovery",
        promotion_state="paper",
    )
    assert feedback_path.exists()

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_events = recovered_adapter.query_telemetry(
        strategy_id="strategy-limit-recovery",
        event_type="paper_fill_simulated",
        limit=5,
    )
    assert [event["event_id"] for event in recovered_events] == [stored_fill["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_events[0],
        sponsor_persona_id="persona-recovery-sponsor",
        contributing_persona_ids=["persona-recovery-ops"],
        summary=(
            "META limit order consumed fetched close=180.0, filled 7 shares at 179.5, "
            "and the feedback adapter recovered the fill from durable store before memory writeback."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-recovery-ops",
                "summary": "Recovered fill feedback stayed queryable after adapter restart.",
                "proposal_ids": ["quant-meta-limit-004"],
                "tags": ["limit_order", "adapter_recovery", "paper_fill", "market_data_fetch"],
            }
        ],
        proposal_ids=["quant-meta-limit-004"],
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
        query="META limit order adapter recovery",
        tags=["adapter_recovery", "limit_order"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_fill["event_id"]

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-recovery-ops",
        query="recovered fill feedback",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    assert evidence["lineage"]["strategy_id"] == "strategy-limit-recovery"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-004-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 004 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_limit_order_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_004.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-004-meta",
        "title": "META daily close for E2E loop 004",
        "content_ref": "market://us_price_daily/META/2026-06-12",
        "metadata": {
            "dataset": "us_price_daily",
            "date": "2026-06-12",
            "symbol": "META",
            "open": 181.0,
            "high": 183.0,
            "low": 178.5,
            "close": 180.0,
            "volume": 1200000,
        },
    }


def _limit_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    return {
        "signal_id": "quant-meta-limit-004",
        "version": "1.0",
        "strategy_id": "strategy-limit-recovery",
        "timestamp": _iso_now(),
        "symbol": "META.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 7,
        "quantity_type": "SHARES",
        "order_type": "LIMIT",
        "limit_price": 179.5,
        "source_worker": "mock-quant-limit-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_limit_entry",
            "confidence_score": 0.93,
            "market_data": {
                "dataset": row["metadata"]["dataset"],
                "symbol": row["metadata"]["symbol"],
                "date": row["metadata"]["date"],
                "close": row["metadata"]["close"],
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-004",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-004-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-004",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-004",
                "runtime_id": "paper-runtime-004",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-recovery",
                "artifact_version": "4.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-recovery",
                "persona_capital_binding_id": "pcb-paper-recovery",
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
            "event_id": f"e2e-loop-004-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-004",
            "runtime_id": "paper-runtime-004",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-recovery",
            "artifact_version": "4.0.0",
            "plan_id": "plan-paper-recovery",
            "persona_capital_binding_id": "pcb-paper-recovery",
            "target": {
                "registry_id": "artifact-paper-recovery",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-recovery",
                "artifact_version": "4.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-004-runtime",
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
