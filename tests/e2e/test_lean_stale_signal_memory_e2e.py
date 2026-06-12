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


def test_stale_signal_filtered_fresh_order_feedback_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-013-amzn", "AMZN", close=190.0),
                    _market_record("src-e2e-loop-013-googl", "GOOGL", close=175.0),
                ],
                "next_watermark": "2026-06-12T23:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-013-us-prices",
            "trace_id": "trace-e2e-loop-013-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in rows} == {"AMZN", "GOOGL"}

    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            _signals(rows, normalized_ref=normalized_ref, ingest_run_id=ingest_body["run"]["ingest_run_id"])
        ),
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
    assert set(positions) == {"GOOGL"}
    assert positions["GOOGL"]["quantity"] == 6.0
    assert positions["GOOGL"]["price"] == 175.0
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert len(noop_events) == 1
    fill_event = fill_events[0]
    assert fill_event["metadata"]["signal_id"] == "fresh-googl-shares-013"
    assert fill_event["metadata"]["alpha_source"] == "fresh_signal_quant"
    assert "stale-amzn-shares-013" not in {event["metadata"].get("signal_id") for event in fill_events}
    noop_event = noop_events[0]
    assert noop_event["metadata"]["signal_id"] == "stale-amzn-shares-013"
    assert noop_event["metadata"]["noop_reason"] == "stale_signal"
    assert noop_event["metadata"]["filter_reason"] == "stale_signal"
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_filtered"
    assert noop_event["metadata"]["submitted_to_broker"] is False
    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 0.5

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-staleness-filter",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-staleness-sponsor",
        contributing_persona_ids=["persona-staleness-ops"],
        summary=(
            "Signal freshness filtering consumed fetched AMZN/GOOGL data, emitted no-order feedback for "
            "the stale AMZN signal, executed the fresh GOOGL signal, and received one paper fill."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-staleness-ops",
                "summary": "Feedback confirmed stale signals produce no-order feedback and do not reach broker execution.",
                "proposal_ids": ["fresh-googl-shares-013"],
                "tags": ["signal_staleness", "fresh_signal", "paper_fill"],
            }
        ],
        proposal_ids=["fresh-googl-shares-013", "stale-amzn-shares-013"],
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
        query="stale signal filtered fresh GOOGL order",
        tags=["signal_staleness", "fresh_signal"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    alpha_context = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["alpha_context"]
    assert alpha_context["signal_id"] == "fresh-googl-shares-013"
    assert alpha_context["alpha_source"] == "fresh_signal_quant"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-staleness-ops",
        query="stale signals do not reach order execution",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"][
        "alpha_context"
    ]
    assert persona_context["source_worker"] == "mock-fresh-signal-normalizer"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-013-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 013 Static Staleness Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_staleness_price_daily",
            "feature_targets": ["features/signal_staleness_inputs"],
            "schema_hash": "us_staleness_price_daily.e2e_loop_013.v1",
        },
    }


def _market_record(source_id: str, symbol: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 013",
        "content_ref": f"market://us_staleness_price_daily/{symbol}/2026-06-12",
        "metadata": {
            "dataset": "us_staleness_price_daily",
            "date": "2026-06-12",
            "symbol": symbol,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1100000,
        },
    }


def _signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return [
        _signal(
            by_symbol["AMZN"],
            signal_id="stale-amzn-shares-013",
            timestamp=now - timedelta(hours=30),
            source_worker="mock-stale-signal-normalizer",
            alpha_source="stale_signal_quant",
            quantity=4,
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
        _signal(
            by_symbol["GOOGL"],
            signal_id="fresh-googl-shares-013",
            timestamp=now,
            source_worker="mock-fresh-signal-normalizer",
            alpha_source="fresh_signal_quant",
            quantity=6,
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    timestamp: datetime,
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
        "strategy_id": "strategy-staleness-filter",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": f"{metadata['symbol']}.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": "SHARES",
        "source_worker": source_worker,
        "metadata": {
            "alpha_source": alpha_source,
            "confidence_score": 0.89,
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-013",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-013-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-013",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-013",
                "runtime_id": "paper-runtime-013",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-staleness",
                "artifact_version": "13.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-staleness",
                "persona_capital_binding_id": "pcb-paper-staleness",
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
            "event_id": f"e2e-loop-013-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-013",
            "runtime_id": "paper-runtime-013",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-staleness",
            "artifact_version": "13.0.0",
            "plan_id": "plan-paper-staleness",
            "persona_capital_binding_id": "pcb-paper-staleness",
            "target": {
                "registry_id": "artifact-paper-staleness",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-staleness",
                "artifact_version": "13.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-013-runtime",
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
