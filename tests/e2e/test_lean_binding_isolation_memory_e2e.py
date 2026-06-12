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


def test_binding_isolation_filters_misrouted_signal_feedback_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-012-aapl", "AAPL", close=210.0),
                    _market_record("src-e2e-loop-012-nvda", "NVDA", close=130.0),
                ],
                "next_watermark": "2026-06-12T23:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-012-us-prices",
            "trace_id": "trace-e2e-loop-012-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in rows} == {"AAPL", "NVDA"}

    signals = _binding_signals(
        rows,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(signals),
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    positions = {position["symbol"]: position for position in snapshot["paper_state"]["positions"]}
    assert set(positions) == {"AAPL"}
    assert positions["AAPL"]["quantity"] == 5.0
    assert positions["AAPL"]["price"] == 210.0
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert len(noop_events) == 1
    fill_event = fill_events[0]
    noop_event = noop_events[0]
    assert fill_event["metadata"]["signal_id"] == "binding-aapl-valid-012"
    assert fill_event["metadata"]["binding_id"] == "binding-e2e-loop-012"
    assert fill_event["metadata"]["alpha_source"] == "binding_isolated_quant"
    assert "binding-nvda-misrouted-012" not in {event["metadata"].get("signal_id") for event in fill_events}
    assert noop_event["metadata"]["signal_id"] == "binding-nvda-misrouted-012"
    assert noop_event["metadata"]["noop_reason"] == "binding_mismatch"
    assert noop_event["metadata"]["filter_reason"] == "binding_mismatch"
    assert noop_event["metadata"]["expected_binding_id"] == "binding-e2e-loop-012"
    assert noop_event["metadata"]["signal_binding_id"] == "binding-other-runtime"
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_filtered"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-binding-isolation",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-binding-sponsor",
        contributing_persona_ids=["persona-binding-ops"],
        summary=(
            "Runtime binding isolation consumed fetched AAPL/NVDA data, filtered the NVDA signal routed "
            "to another binding, executed only the AAPL signal, and received a paper fill."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-binding-ops",
                "summary": "Binding isolation feedback confirmed misrouted signals do not reach order execution.",
                "proposal_ids": ["binding-aapl-valid-012"],
                "tags": ["binding_isolation", "paper_fill", "misroute_filtered"],
            }
        ],
        proposal_ids=["binding-aapl-valid-012", "binding-nvda-misrouted-012"],
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
        query="binding isolation misrouted signal filtered",
        tags=["binding_isolation", "misroute_filtered"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    alpha_context = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["alpha_context"]
    assert alpha_context["signal_id"] == "binding-aapl-valid-012"
    assert alpha_context["binding_id"] == "binding-e2e-loop-012"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-binding-ops",
        query="misrouted signals do not reach order execution",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"][
        "alpha_context"
    ]
    assert persona_context["source_worker"] == "mock-binding-valid-normalizer"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-012-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 012 Static Binding Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_binding_price_daily",
            "feature_targets": ["features/binding_isolation_inputs"],
            "schema_hash": "us_binding_price_daily.e2e_loop_012.v1",
        },
    }


def _market_record(source_id: str, symbol: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 012",
        "content_ref": f"market://us_binding_price_daily/{symbol}/2026-06-12",
        "metadata": {
            "dataset": "us_binding_price_daily",
            "date": "2026-06-12",
            "symbol": symbol,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1700000,
        },
    }


def _binding_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    return [
        _signal(
            by_symbol["NVDA"],
            signal_id="binding-nvda-misrouted-012",
            binding_id="binding-other-runtime",
            source_worker="mock-binding-misrouted-normalizer",
            alpha_source="binding_misrouted_quant",
            quantity=7,
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
        _signal(
            by_symbol["AAPL"],
            signal_id="binding-aapl-valid-012",
            binding_id="binding-e2e-loop-012",
            source_worker="mock-binding-valid-normalizer",
            alpha_source="binding_isolated_quant",
            quantity=5,
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    binding_id: str,
    source_worker: str,
    alpha_source: str,
    quantity: float,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strategy-binding-isolation",
        "timestamp": _iso_now(),
        "symbol": f"{metadata['symbol']}.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": "SHARES",
        "binding_id": binding_id,
        "source_worker": source_worker,
        "metadata": {
            "binding_id": binding_id,
            "alpha_source": alpha_source,
            "confidence_score": 0.9,
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
            "PANTHEON_RUNTIME_BINDING_ID": "binding-e2e-loop-012",
            "PANTHEON_RUNTIME_ID": "paper-runtime-012",
            "PANTHEON_CAPITAL_POOL_ID": "pool-paper",
            "PANTHEON_ARTIFACT_ID": "artifact-paper-binding",
            "PANTHEON_ARTIFACT_VERSION": "12.0.0",
            "PANTHEON_DEPLOYMENT_STAGE": "paper",
            "PANTHEON_DEPLOYMENT_PLAN_ID": "plan-paper-binding",
            "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-paper-binding",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-012-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-012",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-012",
                "runtime_id": "paper-runtime-012",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-binding",
                "artifact_version": "12.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-binding",
                "persona_capital_binding_id": "pcb-paper-binding",
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
            "event_id": f"e2e-loop-012-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-012",
            "runtime_id": "paper-runtime-012",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-binding",
            "artifact_version": "12.0.0",
            "plan_id": "plan-paper-binding",
            "persona_capital_binding_id": "pcb-paper-binding",
            "target": {
                "registry_id": "artifact-paper-binding",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-binding",
                "artifact_version": "12.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-012-runtime",
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
