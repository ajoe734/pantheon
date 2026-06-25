from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_rebalance_batch_timeout_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-011-qqq", "QQQ", close=400.0),
                    _market_record("src-e2e-loop-011-spy", "SPY", close=500.0),
                ],
                "next_watermark": "2026-06-12T22:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-011-us-rebalance-prices",
            "trace_id": "trace-e2e-loop-011-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in rows} == {"QQQ", "SPY"}

    signals = _rebalance_signals(
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

    first = runtime.drain_once()
    second = runtime.drain_once()
    third = runtime.drain_once()

    assert first["paper_state"]["processed_signal_count"] == 0
    assert second["paper_state"]["processed_signal_count"] == 0
    assert third["status"] == "ok"
    assert third["paper_state"]["processed_signal_count"] == 2
    positions = {position["symbol"]: position for position in third["paper_state"]["positions"]}
    assert positions["QQQ"]["quantity"] == pytest.approx(67.5)
    assert positions["QQQ"]["price"] == 400.0
    assert positions["SPY"]["quantity"] == pytest.approx(34.0)
    assert positions["SPY"]["price"] == 500.0
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert {event["metadata"]["signal_id"] for event in fill_events} == {
        "rebalance-qqq-percent-011",
        "rebalance-spy-percent-011",
    }
    assert all(event["metadata"]["run_id"] == "rebalance-run-011" for event in fill_events)

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fills = [
        feedback_adapter.ingest_telemetry_event(
            event,
            strategy_id="strategy-finrl-rebalance",
            promotion_state="paper",
        )
        for event in fill_events
    ]
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fills[0],
        sponsor_persona_id="persona-rebalance-sponsor",
        contributing_persona_ids=["persona-rebalance-ops"],
        summary=(
            "FinRL rebalance run rebalance-run-011 buffered a QQQ/SPY batch until timeout, "
            "then placed two paper percent-portfolio orders and received both fills."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-rebalance-ops",
                "summary": "Batch feedback confirmed both rebalance fills carried the same run_id lineage.",
                "proposal_ids": ["rebalance-run-011"],
                "tags": ["rebalance_batch", "paper_fill", "run_id_timeout"],
            }
        ],
        proposal_ids=["rebalance-run-011"],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_fills[1]["event_id"],
            "event_type": stored_fills[1]["event_type"],
            "lineage": feedback_adapter.build_lineage_record(stored_fills[1]),
        }
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
        query="FinRL rebalance batch QQQ SPY timeout fills",
        tags=["rebalance_batch", "run_id_timeout"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert len(evidence) == 2
    contexts = [item["lineage"]["alpha_context"] for item in evidence]
    assert {context["signal_id"] for context in contexts} == {
        "rebalance-qqq-percent-011",
        "rebalance-spy-percent-011",
    }
    assert {context["run_id"] for context in contexts} == {"rebalance-run-011"}

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-rebalance-ops",
        query="rebalance run_id both fills",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert len(persona_evidence) == 2


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-011-us-rebalance-prices",
        "source_type": "market",
        "provider": "E2E Loop 011 Static Rebalance Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_rebalance_price_daily",
            "feature_targets": ["features/rebalance_batch_inputs"],
            "schema_hash": "us_rebalance_price_daily.e2e_loop_011.v1",
        },
    }


def _market_record(source_id: str, symbol: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 011",
        "content_ref": f"market://us_rebalance_price_daily/{symbol}/2026-06-12",
        "metadata": {
            "dataset": "us_rebalance_price_daily",
            "date": "2026-06-12",
            "symbol": symbol,
            "open": close - 2.0,
            "high": close + 3.0,
            "low": close - 3.0,
            "close": close,
            "volume": 1500000,
        },
    }


def _rebalance_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    return [
        _rebalance_signal(
            by_symbol["QQQ"],
            signal_id="rebalance-qqq-percent-011",
            quantity=0.30,
            confidence_score=0.90,
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
        _rebalance_signal(
            by_symbol["SPY"],
            signal_id="rebalance-spy-percent-011",
            quantity=0.20,
            confidence_score=0.85,
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _rebalance_signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    quantity: float,
    confidence_score: float,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strategy-finrl-rebalance",
        "run_id": "rebalance-run-011",
        "timestamp": _iso_now(),
        "symbol": f"{metadata['symbol']}.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-finrl-rebalance-normalizer",
        "metadata": {
            "alpha_source": "finrl_rebalance_batch",
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
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-011",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-011-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-011",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-011",
                "runtime_id": "paper-runtime-011",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-rebalance",
                "artifact_version": "11.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-rebalance",
                "persona_capital_binding_id": "pcb-paper-rebalance",
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
            "event_id": f"e2e-loop-011-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-011",
            "runtime_id": "paper-runtime-011",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-rebalance",
            "artifact_version": "11.0.0",
            "plan_id": "plan-paper-rebalance",
            "persona_capital_binding_id": "pcb-paper-rebalance",
            "target": {
                "registry_id": "artifact-paper-rebalance",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-rebalance",
                "artifact_version": "11.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-011-runtime",
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
