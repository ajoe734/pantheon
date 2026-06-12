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


def test_market_data_to_short_bracket_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-002-tsla", "TSLA", close=250.0),
                ],
                "next_watermark": "2026-06-12T20:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-002-us-prices",
            "trace_id": "trace-e2e-loop-002-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    normalized_rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert len(normalized_rows) == 1
    assert normalized_rows[0]["metadata"]["symbol"] == "TSLA"

    signal = _short_bracket_signal(
        normalized_rows[0],
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
    positions = {position["symbol"]: position for position in snapshot["paper_state"]["positions"]}
    assert positions["TSLA"]["quantity"] == -4.0
    assert positions["TSLA"]["price"] == 250.0
    assert len(snapshot["paper_state"]["open_bracket_orders"]) == 2

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["fill_quantity"] == -4.0
    assert fill_event["metrics"]["fill_price"] == 250.0
    assert fill_event["metadata"]["alpha_source"] == "llm_risk_overlay_agent"
    assert fill_event["metadata"]["market_price"] == 250.0

    bracket_event = next(event for event in telemetry.events if event["event_type"] == "bracket_order_logged")
    assert bracket_event["metrics"]["action"] == "bracket_submitted_to_broker"
    assert bracket_event["metrics"]["submitted_to_broker"] is True
    assert bracket_event["metadata"]["signal_id"] == "llm-risk-tsla-short-bracket-002"
    assert bracket_event["metadata"]["alpha_source"] == "llm_risk_overlay_agent"
    assert bracket_event["metadata"]["broker_submission_status"] == "submitted_to_broker"
    legs = bracket_event["metadata"]["legs"]
    assert {leg["leg_type"] for leg in legs} == {"stop_loss", "take_profit"}
    assert next(leg for leg in legs if leg["leg_type"] == "stop_loss")["stop_price"] == 260.0
    assert next(leg for leg in legs if leg["leg_type"] == "take_profit")["limit_price"] == 230.0

    feedback_adapter = FeedbackStoreAdapter(
        feedback_store_path=str(tmp_path / "feedback-store.jsonl"),
    )
    stored_bracket = feedback_adapter.ingest_telemetry_event(
        bracket_event,
        strategy_id="strategy-llm-risk-short",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_bracket,
        sponsor_persona_id="persona-risk-sponsor",
        contributing_persona_ids=["persona-risk-ops"],
        summary=(
            "TSLA LLM risk overlay consumed fetched close=250.0, opened a paper short, "
            "and produced submitted paper bracket feedback with stop 260.0 and target 230.0."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-risk-ops",
                "summary": "Bracket feedback confirmed the short alpha risk legs were generated from fetched data.",
                "proposal_ids": ["llm-risk-tsla-short-bracket-002"],
                "tags": ["llm_risk_overlay", "bracket_order", "short_alpha", "market_data_fetch"],
            }
        ],
        proposal_ids=["llm-risk-tsla-short-bracket-002"],
    )

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )

    assert writeback["created"] is True
    reloaded_institutional = InstitutionalMemoryStore(path=institutional_path)
    institutional_hits = reloaded_institutional.retrieve(
        query="TSLA short bracket stop target fetched data",
        tags=["bracket_order", "short_alpha"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_bracket["event_id"]

    reloaded_persona = PersonaMemoryStore(path=persona_path)
    persona_hits = reloaded_persona.retrieve(
        persona_id="persona-risk-ops",
        query="risk overlay bracket",
        tags=["bracket_order"],
        limit=3,
    )
    assert persona_hits
    persona_payload = persona_hits[0].entry.content["structured_payload"]
    assert persona_payload["runtime_telemetry_evidence"][0]["lineage"]["strategy_id"] == "strategy-llm-risk-short"


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
        "connector_id": "conn-e2e-loop-002-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 002 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_short_risk_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_002.v1",
        },
    }


def _market_record(source_id: str, symbol: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 002",
        "content_ref": f"market://us_price_daily/{symbol}/2026-06-12",
        "metadata": {
            "dataset": "us_price_daily",
            "date": "2026-06-12",
            "symbol": symbol,
            "open": close + 1.0,
            "high": close + 4.0,
            "low": close - 4.0,
            "close": close,
            "volume": 2000000,
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _short_bracket_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    symbol = row["metadata"]["symbol"]
    close = float(row["metadata"]["close"])
    return {
        "signal_id": "llm-risk-tsla-short-bracket-002",
        "version": "1.0",
        "strategy_id": "strategy-llm-risk-short",
        "timestamp": _iso_now(),
        "symbol": f"{symbol}.US",
        "action": "SELL",
        "direction": "SHORT",
        "quantity": 1000,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-llm-risk-overlay-normalizer",
        "metadata": {
            "alpha_source": "llm_risk_overlay_agent",
            "confidence_score": 0.76,
            "model_id": "gpt-risk-paper",
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
            "risk_parameters": {
                "stop_loss_pct": 0.04,
                "take_profit_pct": 0.08,
            },
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-002",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-002-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-002",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-002",
                "runtime_id": "paper-runtime-002",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-risk",
                "artifact_version": "2.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-risk",
                "persona_capital_binding_id": "pcb-paper-risk",
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
            "event_id": f"e2e-loop-002-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-002",
            "runtime_id": "paper-runtime-002",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-risk",
            "artifact_version": "2.0.0",
            "plan_id": "plan-paper-risk",
            "persona_capital_binding_id": "pcb-paper-risk",
            "target": {
                "registry_id": "artifact-paper-risk",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-risk",
                "artifact_version": "2.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-002-runtime",
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
