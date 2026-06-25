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


def test_market_data_to_lean_order_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-001-aapl", "AAPL", close=205.0),
                    _market_record("src-e2e-loop-001-nvda", "NVDA", close=125.0),
                ],
                "next_watermark": "2026-06-12T20:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-001-us-prices",
            "trace_id": "trace-e2e-loop-001-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    assert normalized_ref["dataset"] == "us_price_daily"
    normalized_rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in normalized_rows} == {"AAPL", "NVDA"}

    signals = _signals_from_market_rows(
        normalized_rows,
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
    positions = {position["symbol"]: position for position in snapshot["paper_state"]["positions"]}
    assert positions["AAPL"]["quantity"] == 3.0
    assert positions["AAPL"]["price"] == 205.0
    assert positions["NVDA"]["quantity"] == 4.0
    assert positions["NVDA"]["price"] == 125.0
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(fill_events) == 2
    nvda_fill = next(event for event in fill_events if event["metadata"]["symbol"] == "NVDA")
    assert nvda_fill["metrics"]["fill_quantity"] == 4.0
    assert nvda_fill["metrics"]["fill_price"] == 125.0
    nvda_order_event = next(
        event for event in snapshot["paper_state"]["recent_order_events"] if event["symbol"] == "NVDA"
    )
    assert nvda_order_event["quantity"] == 4.0
    assert nvda_order_event["fill_price"] == 125.0
    assert nvda_fill["metadata"]["alpha_source"] == "llm_research_agent"
    assert nvda_fill["metadata"]["market_price"] == 125.0
    assert nvda_fill["metadata"]["normalized_data_ref"] == normalized_ref["uri"]
    assert nvda_fill["metadata"]["is_real_order"] is False
    assert nvda_fill["metadata"]["submitted_to_broker"] is False

    feedback_adapter = FeedbackStoreAdapter(
        feedback_store_path=str(tmp_path / "feedback-store.jsonl"),
    )
    stored_fill = feedback_adapter.ingest_telemetry_event(
        nvda_fill,
        strategy_id="strategy-llm-alpha",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-paper-sponsor",
        contributing_persona_ids=["persona-paper-ops"],
        summary=(
            "NVDA LLM alpha consumed fetched us_price_daily close=125.0, "
            "placed a paper CASH_VALUE order, and received a 4 share paper fill."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-paper-ops",
                "summary": "Paper fill feedback confirmed the LLM alpha order used fetched market data.",
                "proposal_ids": ["llm-alpha-nvda-cash-001"],
                "tags": ["llm_alpha", "paper_fill", "market_data_fetch"],
            }
        ],
        proposal_ids=["llm-alpha-nvda-cash-001"],
    )

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )

    assert writeback["created"] is True
    assert writeback["institutional_entry_id"]
    assert writeback["persona_memory_ids"]

    reloaded_institutional = InstitutionalMemoryStore(path=institutional_path)
    institutional_hits = reloaded_institutional.retrieve(
        query="NVDA CASH_VALUE fetched market data paper fill",
        tags=["paper_fill", "market_data_fetch"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    institutional_entry = institutional_hits[0].entry
    assert institutional_entry.source_event_id == stored_fill["event_id"]
    assert institutional_entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["ref_id"] == stored_fill["event_id"]

    reloaded_persona = PersonaMemoryStore(path=persona_path)
    persona_hits = reloaded_persona.retrieve(
        persona_id="persona-paper-ops",
        query="LLM alpha fetched market data",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_entry = persona_hits[0].entry
    assert persona_entry.source_event_id == stored_fill["event_id"]
    assert persona_entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"]["strategy_id"] == "strategy-llm-alpha"


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
        "connector_id": "conn-e2e-loop-001-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 001 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_alpha_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_001.v1",
        },
    }


def _market_record(source_id: str, symbol: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 001",
        "content_ref": f"market://us_price_daily/{symbol}/2026-06-12",
        "metadata": {
            "dataset": "us_price_daily",
            "date": "2026-06-12",
            "symbol": symbol,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1000000,
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _signals_from_market_rows(
    rows: list[dict[str, Any]],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    return [
        _signal_from_row(
            by_symbol["AAPL"],
            signal_id="quant-alpha-aapl-shares-001",
            strategy_id="strategy-mean-reversion",
            source_worker="mock-quant-alpha-normalizer",
            alpha_source="pure_quant",
            quantity=3,
            quantity_type="SHARES",
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
        ),
        _signal_from_row(
            by_symbol["NVDA"],
            signal_id="llm-alpha-nvda-cash-001",
            strategy_id="strategy-llm-alpha",
            source_worker="mock-llm-alpha-normalizer",
            alpha_source="llm_research_agent",
            quantity=500,
            quantity_type="CASH_VALUE",
            normalized_ref=normalized_ref,
            ingest_run_id=ingest_run_id,
            model_id="gpt-research-paper",
        ),
    ]


def _signal_from_row(
    row: dict[str, Any],
    *,
    signal_id: str,
    strategy_id: str,
    source_worker: str,
    alpha_source: str,
    quantity: float,
    quantity_type: str,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
    model_id: str | None = None,
) -> dict[str, Any]:
    symbol = row["metadata"]["symbol"]
    close = float(row["metadata"]["close"])
    metadata: dict[str, Any] = {
        "alpha_source": alpha_source,
        "confidence_score": 0.82 if alpha_source == "llm_research_agent" else 0.91,
        "market_data": {
            "dataset": row["metadata"]["dataset"],
            "symbol": symbol,
            "date": row["metadata"]["date"],
            "close": close,
            "content_ref": row["content_ref"],
        },
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": ingest_run_id,
    }
    if model_id:
        metadata["model_id"] = model_id
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": strategy_id,
        "timestamp": _iso_now(),
        "symbol": f"{symbol}.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": quantity_type,
        "source_worker": source_worker,
        "metadata": metadata,
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-001",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-001-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-001",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-001",
                "runtime_id": "paper-runtime-001",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper",
                "artifact_version": "1.2.3",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper",
                "persona_capital_binding_id": "pcb-paper",
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
            "event_id": f"e2e-loop-001-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-001",
            "runtime_id": "paper-runtime-001",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper",
            "artifact_version": "1.2.3",
            "plan_id": "plan-paper",
            "persona_capital_binding_id": "pcb-paper",
            "target": {
                "registry_id": "artifact-paper",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime",
                "artifact_version": "1.2.3",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-001-runtime",
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
