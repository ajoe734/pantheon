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


def test_percent_portfolio_confidence_floor_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T22:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-005-us-prices",
            "trace_id": "trace-e2e-loop-005-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "ORCL"
    assert row["metadata"]["close"] == 80.0

    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            [
                _percent_signal(
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
    assert position["symbol"] == "ORCL"
    assert position["quantity"] == 62.5
    assert position["price"] == 80.0
    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "set_holdings"
    assert fill_event["metrics"]["fill_quantity"] == 62.5
    assert fill_event["metrics"]["fill_price"] == 80.0
    assert fill_event["metadata"]["confidence_score"] == 0.2
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_low_confidence"
    assert fill_event["metadata"]["market_price"] == 80.0
    pnl_event = next(event for event in telemetry.events if event["event_type"] == "pnl_snapshot")
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-percent-confidence",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-confidence-sponsor",
        contributing_persona_ids=["persona-confidence-ops"],
        summary=(
            "ORCL low-confidence percent-portfolio alpha consumed fetched close=80.0; "
            "confidence floor sized the paper position to 62.5 shares."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-confidence-ops",
                "summary": "Fill feedback confirmed confidence-floor portfolio sizing.",
                "proposal_ids": ["quant-orcl-percent-confidence-005"],
                "tags": ["percent_portfolio", "confidence_floor", "paper_fill", "market_data_fetch"],
            }
        ],
        proposal_ids=["quant-orcl-percent-confidence-005"],
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
        query="ORCL confidence floor percent portfolio",
        tags=["confidence_floor", "percent_portfolio"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_fill["event_id"]

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-confidence-ops",
        query="portfolio sizing",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    assert evidence["lineage"]["strategy_id"] == "strategy-percent-confidence"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-005-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 005 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_percent_confidence_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_005.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-005-orcl",
        "title": "ORCL daily close for E2E loop 005",
        "content_ref": "market://us_price_daily/ORCL/2026-06-12",
        "metadata": {
            "dataset": "us_price_daily",
            "date": "2026-06-12",
            "symbol": "ORCL",
            "open": 79.5,
            "high": 81.0,
            "low": 78.5,
            "close": 80.0,
            "volume": 900000,
        },
    }


def _percent_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    return {
        "signal_id": "quant-orcl-percent-confidence-005",
        "version": "1.0",
        "strategy_id": "strategy-percent-confidence",
        "timestamp": _iso_now(),
        "symbol": "ORCL.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.10,
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-quant-percent-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_low_confidence",
            "confidence_score": 0.2,
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-005",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-005-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-005",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-005",
                "runtime_id": "paper-runtime-005",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-confidence",
                "artifact_version": "5.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-confidence",
                "persona_capital_binding_id": "pcb-paper-confidence",
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
            "event_id": f"e2e-loop-005-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-005",
            "runtime_id": "paper-runtime-005",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-confidence",
            "artifact_version": "5.0.0",
            "plan_id": "plan-paper-confidence",
            "persona_capital_binding_id": "pcb-paper-confidence",
            "target": {
                "registry_id": "artifact-paper-confidence",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-confidence",
                "artifact_version": "5.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-005-runtime",
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
