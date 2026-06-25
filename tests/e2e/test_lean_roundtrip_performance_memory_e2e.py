from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter


def test_market_data_roundtrip_performance_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-003-amd-day1", "AMD", "2026-06-11", close=50.0),
                    _market_record("src-e2e-loop-003-amd-day2", "AMD", "2026-06-12", close=55.0),
                ],
                "next_watermark": "2026-06-12T21:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-003-us-prices",
            "trace_id": "trace-e2e-loop-003-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [
        row
        for ref in normalized_refs
        for row in _read_jsonl(Path(ref["uri"]))
    ]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-11", 50.0),
        ("2026-06-12", 55.0),
    ]

    signals = _roundtrip_signals(
        normalized_rows,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    store = InMemoryPendingSignalStore([signals[0]])
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=store,
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first_snapshot = runtime.drain_once()
    assert first_snapshot["status"] == "ok"
    assert first_snapshot["paper_state"]["processed_signal_count"] == 1
    assert first_snapshot["paper_state"]["positions"][0]["symbol"] == "AMD"
    assert first_snapshot["paper_state"]["positions"][0]["quantity"] == 10.0

    store.enqueue(signals[1])
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["positions"] == []
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [10.0, -10.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [50.0, 55.0]
    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 50.0
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0
    assert pnl_event["metrics"]["avg_slippage_bps"] == 0.0

    feedback_adapter = FeedbackStoreAdapter(
        feedback_store_path=str(tmp_path / "feedback-store.jsonl"),
    )
    stored_pnl = feedback_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-roundtrip-performance",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-performance-sponsor",
        contributing_persona_ids=["persona-performance-ops"],
        summary=(
            "AMD roundtrip consumed two fetched closes, bought 10 at 50.0, "
            "exited 10 at 55.0, and produced a 50.0 paper PnL with fill_rate=1.0."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-performance-ops",
                "summary": "Roundtrip PnL feedback confirmed performance calculation after order feedback.",
                "proposal_ids": ["quant-amd-buy-003", "quant-amd-exit-003"],
                "tags": ["roundtrip_pnl", "paper_performance", "market_data_fetch"],
            }
        ],
        proposal_ids=["quant-amd-buy-003", "quant-amd-exit-003"],
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
        query="AMD roundtrip PnL fill rate",
        tags=["roundtrip_pnl", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_pnl["event_id"]

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-performance-ops",
        query="performance calculation",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"]
    assert lineage["strategy_id"] == "strategy-roundtrip-performance"


def _source_ingest_client(tmp_path, monkeypatch) -> TestClient:
    data_dir = tmp_path / "source-ingest"
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "20")
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(data_dir / "market-data-store"))
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)
    return TestClient(module.app)


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-003-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 003 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_roundtrip_performance_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_003.v1",
        },
    }


def _market_record(source_id: str, symbol: str, trade_date: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 003 {trade_date}",
        "content_ref": f"market://us_price_daily/{symbol}/{trade_date}",
        "metadata": {
            "dataset": "us_price_daily",
            "date": trade_date,
            "symbol": symbol,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1500000,
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _roundtrip_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    return [
        _signal_from_row(
            rows_by_date["2026-06-11"],
            signal_id="quant-amd-buy-003",
            action="BUY",
            direction="LONG",
            quantity=10,
            quantity_type="SHARES",
            normalized_ref_uris=normalized_ref_uris,
            ingest_run_id=ingest_run_id,
        ),
        _signal_from_row(
            rows_by_date["2026-06-12"],
            signal_id="quant-amd-exit-003",
            action="EXIT",
            direction="LONG",
            quantity=10,
            quantity_type="SHARES",
            normalized_ref_uris=normalized_ref_uris,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _signal_from_row(
    row: dict[str, Any],
    *,
    signal_id: str,
    action: str,
    direction: str,
    quantity: float,
    quantity_type: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> dict[str, Any]:
    symbol = row["metadata"]["symbol"]
    close = float(row["metadata"]["close"])
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strategy-roundtrip-performance",
        "timestamp": _iso_now(),
        "symbol": f"{symbol}.US",
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": quantity_type,
        "source_worker": "mock-quant-roundtrip-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_roundtrip",
            "confidence_score": 0.88,
            "market_data": {
                "dataset": row["metadata"]["dataset"],
                "symbol": symbol,
                "date": row["metadata"]["date"],
                "close": close,
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref_uris,
            "source_dataset_ref": row["metadata"]["dataset"],
            "ingest_run_id": ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-003",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-003-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-003",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-003",
                "runtime_id": "paper-runtime-003",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-performance",
                "artifact_version": "3.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-performance",
                "persona_capital_binding_id": "pcb-paper-performance",
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
            "event_id": f"e2e-loop-003-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-003",
            "runtime_id": "paper-runtime-003",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-performance",
            "artifact_version": "3.0.0",
            "plan_id": "plan-paper-performance",
            "persona_capital_binding_id": "pcb-paper-performance",
            "target": {
                "registry_id": "artifact-paper-performance",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-performance",
                "artifact_version": "3.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-003-runtime",
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
