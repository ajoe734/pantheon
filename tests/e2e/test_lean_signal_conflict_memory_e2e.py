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


def test_signal_conflict_winner_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
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
            "connector_id": "conn-e2e-loop-010-us-prices",
            "trace_id": "trace-e2e-loop-010-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "MSFT"
    assert row["metadata"]["close"] == 250.0

    older_quant, newer_llm = _conflicting_signals(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([older_quant, newer_llm]),
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
    assert position["symbol"] == "MSFT"
    assert position["quantity"] == 4.0
    assert position["price"] == 250.0
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(fill_events) == 1
    fill_event = fill_events[0]
    assert fill_event["metadata"]["signal_id"] == "llm-conflict-msft-winner-010"
    assert fill_event["metadata"]["alpha_source"] == "llm_conflict_resolution_winner"
    assert fill_event["metadata"]["model_id"] == "gpt-signal-arb"
    assert fill_event["metrics"]["fill_quantity"] == 4.0
    assert "quant-conflict-msft-loser-010" not in {event["metadata"].get("signal_id") for event in fill_events}

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-conflict-resolution",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-conflict-sponsor",
        contributing_persona_ids=["persona-conflict-ops"],
        summary=(
            "MSFT conflict resolution consumed fetched close=250.0, discarded the older quant candidate, "
            "executed the newer LLM CASH_VALUE signal, and received a 4 share paper fill."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-conflict-ops",
                "summary": "Feedback confirmed only the winning LLM alpha signal reached order execution.",
                "proposal_ids": ["llm-conflict-msft-winner-010"],
                "tags": ["signal_conflict", "llm_winner", "paper_fill"],
            }
        ],
        proposal_ids=["llm-conflict-msft-winner-010", "quant-conflict-msft-loser-010"],
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
        query="MSFT signal conflict LLM winner",
        tags=["signal_conflict", "llm_winner"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    alpha_context = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["alpha_context"]
    assert alpha_context["signal_id"] == "llm-conflict-msft-winner-010"
    assert alpha_context["alpha_source"] == "llm_conflict_resolution_winner"
    assert alpha_context["model_id"] == "gpt-signal-arb"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-conflict-ops",
        query="only winning LLM alpha reached order execution",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"][
        "alpha_context"
    ]
    assert persona_context["source_worker"] == "mock-llm-conflict-normalizer"
    assert persona_context["market_price"] == 250.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-010-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 010 Static US Conflict Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_conflict_price_daily",
            "feature_targets": ["features/signal_conflict_inputs"],
            "schema_hash": "us_conflict_price_daily.e2e_loop_010.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-010-msft",
        "title": "MSFT daily close for E2E loop 010",
        "content_ref": "market://us_conflict_price_daily/MSFT/2026-06-12",
        "metadata": {
            "dataset": "us_conflict_price_daily",
            "date": "2026-06-12",
            "symbol": "MSFT",
            "open": 248.0,
            "high": 253.0,
            "low": 246.0,
            "close": 250.0,
            "volume": 1900000,
        },
    }


def _conflicting_signals(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    older = _signal(
        row,
        signal_id="quant-conflict-msft-loser-010",
        strategy_id="strategy-conflict-resolution",
        timestamp=base_time - timedelta(minutes=3),
        quantity=10,
        quantity_type="SHARES",
        source_worker="mock-quant-conflict-normalizer",
        alpha_source="pure_quant_conflict_loser",
        confidence_score=0.94,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_run_id,
    )
    newer = _signal(
        row,
        signal_id="llm-conflict-msft-winner-010",
        strategy_id="strategy-conflict-resolution",
        timestamp=base_time,
        quantity=1000,
        quantity_type="CASH_VALUE",
        source_worker="mock-llm-conflict-normalizer",
        alpha_source="llm_conflict_resolution_winner",
        confidence_score=0.82,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_run_id,
        model_id="gpt-signal-arb",
        prompt_bundle_id="prompt-bundle-conflict-resolution",
    )
    return older, newer


def _signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    strategy_id: str,
    timestamp: datetime,
    quantity: float,
    quantity_type: str,
    source_worker: str,
    alpha_source: str,
    confidence_score: float,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
    model_id: str | None = None,
    prompt_bundle_id: str | None = None,
) -> dict[str, Any]:
    metadata = row["metadata"]
    signal_metadata: dict[str, Any] = {
        "alpha_source": alpha_source,
        "confidence_score": confidence_score,
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
    }
    if model_id:
        signal_metadata["model_id"] = model_id
    if prompt_bundle_id:
        signal_metadata["prompt_bundle_id"] = prompt_bundle_id
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": strategy_id,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": "MSFT.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": quantity_type,
        "source_worker": source_worker,
        "metadata": signal_metadata,
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-010",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-010-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-010",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-010",
                "runtime_id": "paper-runtime-010",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-conflict",
                "artifact_version": "10.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-conflict",
                "persona_capital_binding_id": "pcb-paper-conflict",
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
            "event_id": f"e2e-loop-010-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-010",
            "runtime_id": "paper-runtime-010",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-conflict",
            "artifact_version": "10.0.0",
            "plan_id": "plan-paper-conflict",
            "persona_capital_binding_id": "pcb-paper-conflict",
            "target": {
                "registry_id": "artifact-paper-conflict",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-conflict",
                "artifact_version": "10.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-010-runtime",
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
