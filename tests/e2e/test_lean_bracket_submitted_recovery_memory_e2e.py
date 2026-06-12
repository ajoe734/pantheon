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


def test_market_data_to_long_bracket_feedback_recovery_memory_readback_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [
                    _market_record("src-e2e-loop-040-msft", "MSFT", close=300.0),
                ],
                "next_watermark": "2026-06-12T20:40:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-040-us-prices",
            "trace_id": "trace-e2e-loop-040-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    normalized_rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert len(normalized_rows) == 1
    assert normalized_rows[0]["metadata"]["symbol"] == "MSFT"
    assert normalized_rows[0]["metadata"]["close"] == 300.0

    signal = _long_bracket_signal(
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
    assert positions["MSFT"]["quantity"] == 5.0
    assert positions["MSFT"]["price"] == 300.0
    open_bracket_orders = snapshot["paper_state"]["open_bracket_orders"]
    assert len(open_bracket_orders) == 2
    assert {order["leg_type"] for order in open_bracket_orders} == {"stop_loss", "take_profit"}

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["fill_quantity"] == 5.0
    assert fill_event["metrics"]["fill_price"] == 300.0
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_breakout_model"
    assert fill_event["metadata"]["market_price"] == 300.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    bracket_event = next(event for event in telemetry.events if event["event_type"] == "bracket_order_logged")
    assert bracket_event["metrics"]["action"] == "bracket_submitted_to_broker"
    assert bracket_event["metrics"]["submitted_to_broker"] is True
    assert bracket_event["metadata"]["signal_id"] == "quant-breakout-msft-bracket-040"
    assert bracket_event["metadata"]["alpha_source"] == "pure_quant_breakout_model"
    assert bracket_event["metadata"]["broker_submission_status"] == "submitted_to_broker"
    assert bracket_event["metadata"]["entry_price"] == 300.0
    assert bracket_event["metadata"]["entry_quantity"] == 5.0
    assert bracket_event["metadata"]["guard_stage"] == "paper"
    assert bracket_event["metadata"]["guard_reason"] == "paper/sim bracket execution guard passed"
    assert bracket_event["metadata"]["submitted_to_broker"] is True
    assert bracket_event["metadata"]["is_real_order"] is False
    assert bracket_event["metadata"]["is_real_capital"] is False
    bracket_submission = bracket_event["metadata"]["submission"]
    assert bracket_submission["leg_count"] == 2
    assert len(bracket_submission["legs"]) == 2
    assert bracket_submission["bracket_order_id"]
    submitted_stop = next(leg for leg in bracket_submission["legs"] if leg["leg_type"] == "stop_loss")
    submitted_target = next(leg for leg in bracket_submission["legs"] if leg["leg_type"] == "take_profit")
    assert submitted_stop["quantity"] == -5.0
    assert submitted_stop["stop_price"] == 291.0
    assert submitted_stop["status"] == "open"
    assert submitted_target["quantity"] == -5.0
    assert submitted_target["limit_price"] == 318.0
    assert submitted_target["status"] == "open"

    pnl_event = next(event for event in telemetry.events if event["event_type"] == "pnl_snapshot")
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 2

    feedback_store_path = tmp_path / "feedback-store.jsonl"
    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_store_path))
    feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-quant-breakout-bracket",
        promotion_state="paper",
    )
    stored_bracket = feedback_adapter.ingest_telemetry_event(
        bracket_event,
        strategy_id="strategy-quant-breakout-bracket",
        promotion_state="paper",
    )
    feedback_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-quant-breakout-bracket",
        promotion_state="paper",
    )

    recovered_feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_store_path))
    recovered_records = recovered_feedback_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-040",
    )
    records_by_type = {record["event_type"]: record for record in recovered_records}
    assert {"paper_fill_simulated", "bracket_order_logged", "pnl_snapshot"} <= set(records_by_type)
    recovered_fill_context = records_by_type["paper_fill_simulated"]["order_context"]
    assert recovered_fill_context["fill_quantity"] == 5.0
    assert recovered_fill_context["fill_price"] == 300.0
    assert recovered_fill_context["submitted_to_broker"] is False
    recovered_bracket_context = records_by_type["bracket_order_logged"]["order_context"]
    assert recovered_bracket_context["bracket_order_id"] == bracket_submission["bracket_order_id"]
    assert recovered_bracket_context["bracket_leg_count"] == 2
    assert recovered_bracket_context["entry_price"] == 300.0
    assert recovered_bracket_context["entry_quantity"] == 5.0
    assert recovered_bracket_context["guard_stage"] == "paper"
    assert recovered_bracket_context["submitted_to_broker"] is True
    assert recovered_bracket_context["broker_submission_status"] == "submitted_to_broker"
    assert recovered_bracket_context["submitted_legs"][0]["bracket_order_id"] == bracket_submission["bracket_order_id"]
    assert recovered_bracket_context["submitted_legs"][1]["limit_price"] == 318.0
    recovered_pnl_context = records_by_type["pnl_snapshot"]["order_context"]
    assert recovered_pnl_context["pnl"] == 0.0
    assert recovered_pnl_context["processed_signal_count"] == 1
    assert recovered_pnl_context["execution_event_count"] == 2
    assert recovered_pnl_context["fill_event_count"] == 1
    assert recovered_pnl_context["open_bracket_order_count"] == 2

    writeback_payload = recovered_feedback_adapter.build_learn_feedback_writeback_payload(
        stored_bracket,
        sponsor_persona_id="persona-bracket-risk-sponsor",
        contributing_persona_ids=["persona-quant-ops"],
        summary=(
            "MSFT pure quant breakout consumed fetched close=300.0, opened a paper long, "
            "submitted two paper bracket child orders, and recovered the adapter lineage."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-quant-ops",
                "summary": "Bracket child-order feedback, performance counters, and recovery readback matched.",
                "proposal_ids": ["quant-breakout-msft-bracket-040"],
                "tags": ["pure_quant_alpha", "bracket_order", "child_order", "adapter_recovery"],
            }
        ],
        proposal_ids=["quant-breakout-msft-bracket-040"],
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
        query="MSFT bracket child order recovered adapter lineage performance",
        tags=["bracket_order", "child_order"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_bracket["event_id"]
    institutional_payload = institutional_hits[0].entry.content["structured_payload"]
    institutional_lineage = institutional_payload["runtime_telemetry_evidence"][0]["lineage"]
    memory_bracket_context = institutional_lineage["order_context"]
    assert memory_bracket_context["bracket_order_id"] == bracket_submission["bracket_order_id"]
    assert memory_bracket_context["bracket_leg_count"] == 2
    assert memory_bracket_context["submitted_legs"][0]["status"] == "open"
    assert memory_bracket_context["submitted_legs"][1]["limit_price"] == 318.0

    reloaded_persona = PersonaMemoryStore(path=persona_path)
    persona_hits = reloaded_persona.retrieve(
        persona_id="persona-quant-ops",
        query="adapter recovery bracket child orders",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_payload = persona_hits[0].entry.content["structured_payload"]
    persona_lineage = persona_payload["runtime_telemetry_evidence"][0]["lineage"]
    assert persona_lineage["alpha_context"]["alpha_source"] == "pure_quant_breakout_model"
    assert persona_lineage["order_context"]["bracket_order_id"] == bracket_submission["bracket_order_id"]


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
        "connector_id": "conn-e2e-loop-040-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 040 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_quant_breakout_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_040.v1",
        },
    }


def _market_record(source_id: str, symbol: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for E2E loop 040",
        "content_ref": f"market://us_price_daily/{symbol}/2026-06-12",
        "metadata": {
            "dataset": "us_price_daily",
            "date": "2026-06-12",
            "symbol": symbol,
            "open": close - 2.0,
            "high": close + 5.0,
            "low": close - 5.0,
            "close": close,
            "volume": 3100000,
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _long_bracket_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    symbol = row["metadata"]["symbol"]
    close = float(row["metadata"]["close"])
    return {
        "signal_id": "quant-breakout-msft-bracket-040",
        "version": "1.0",
        "strategy_id": "strategy-quant-breakout-bracket",
        "timestamp": _iso_now(),
        "symbol": f"{symbol}.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 5,
        "quantity_type": "SHARES",
        "source_worker": "mock-pure-quant-breakout-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_breakout_model",
            "confidence_score": 0.91,
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
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.06,
            },
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-040",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-040-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-040",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-040",
                "runtime_id": "paper-runtime-040",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-bracket",
                "artifact_version": "2.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-bracket",
                "persona_capital_binding_id": "pcb-paper-bracket",
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
            "event_id": f"e2e-loop-040-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-040",
            "runtime_id": "paper-runtime-040",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-bracket",
            "artifact_version": "2.0.0",
            "plan_id": "plan-paper-bracket",
            "persona_capital_binding_id": "pcb-paper-bracket",
            "target": {
                "registry_id": "artifact-paper-bracket",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-bracket",
                "artifact_version": "2.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-040-runtime",
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
