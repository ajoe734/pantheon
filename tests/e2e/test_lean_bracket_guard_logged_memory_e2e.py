from __future__ import annotations

import json
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
from tests.e2e.test_lean_bracket_submitted_recovery_memory_e2e import _source_ingest_client


def test_bracket_guard_logged_feedback_recovery_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T20:42:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-042-us-prices",
            "trace_id": "trace-e2e-loop-042-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    normalized_rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert len(normalized_rows) == 1
    assert normalized_rows[0]["metadata"]["symbol"] == "AMD"
    assert normalized_rows[0]["metadata"]["close"] == 120.0

    telemetry = _CanonicalTelemetryRecorder()
    monkeypatch.setenv("PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED", "false")
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            [
                _llm_guarded_bracket_signal(
                    normalized_rows[0],
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
    assert snapshot["paper_state"]["bracket_order_execution_enabled"] is False
    assert snapshot["paper_state"]["open_bracket_orders"] == []
    positions = {position["symbol"]: position for position in snapshot["paper_state"]["positions"]}
    assert positions["AMD"]["quantity"] == 3.0
    assert positions["AMD"]["price"] == 120.0

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "market_order"
    assert fill_event["metrics"]["fill_quantity"] == 3.0
    assert fill_event["metrics"]["fill_price"] == 120.0
    assert fill_event["metadata"]["alpha_source"] == "llm_risk_gate_agent"
    assert fill_event["metadata"]["market_price"] == 120.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    bracket_event = next(event for event in telemetry.events if event["event_type"] == "bracket_order_logged")
    assert bracket_event["metrics"]["action"] == "bracket_logged_only"
    assert bracket_event["metrics"]["submitted_to_broker"] is False
    assert bracket_event["metadata"]["signal_id"] == "llm-amd-bracket-guard-042"
    assert bracket_event["metadata"]["broker_submission_status"] == "logged_only"
    assert bracket_event["metadata"]["submitted_to_broker"] is False
    assert bracket_event["metadata"]["stop_loss_pct"] == 0.04
    assert bracket_event["metadata"]["take_profit_pct"] == 0.09
    assert bracket_event["metadata"]["guard_stage"] == "paper"
    assert bracket_event["metadata"]["guard_reason"] == "paper/sim bracket execution guard is disabled"
    assert "submission" not in bracket_event["metadata"]

    pnl_event = next(event for event in telemetry.events if event["event_type"] == "pnl_snapshot")
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_store_path = tmp_path / "feedback-store.jsonl"
    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_store_path))
    feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-llm-bracket-guard",
        promotion_state="paper",
    )
    stored_bracket = feedback_adapter.ingest_telemetry_event(
        bracket_event,
        strategy_id="strategy-llm-bracket-guard",
        promotion_state="paper",
    )
    feedback_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-llm-bracket-guard",
        promotion_state="paper",
    )

    recovered_feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_store_path))
    recovered_records = recovered_feedback_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-042",
    )
    records_by_type = {record["event_type"]: record for record in recovered_records}
    assert {"paper_fill_simulated", "bracket_order_logged", "pnl_snapshot"} <= set(records_by_type)
    recovered_fill_context = records_by_type["paper_fill_simulated"]["order_context"]
    assert recovered_fill_context["fill_quantity"] == 3.0
    assert recovered_fill_context["fill_price"] == 120.0
    assert recovered_fill_context["submitted_to_broker"] is False
    recovered_bracket_context = records_by_type["bracket_order_logged"]["order_context"]
    assert recovered_bracket_context["broker_submission_status"] == "logged_only"
    assert recovered_bracket_context["submitted_to_broker"] is False
    assert recovered_bracket_context["stop_loss_pct"] == 0.04
    assert recovered_bracket_context["take_profit_pct"] == 0.09
    assert recovered_bracket_context["guard_reason"] == "paper/sim bracket execution guard is disabled"
    assert "bracket_order_id" not in recovered_bracket_context
    recovered_pnl_context = records_by_type["pnl_snapshot"]["order_context"]
    assert recovered_pnl_context["processed_signal_count"] == 1
    assert recovered_pnl_context["execution_event_count"] == 2
    assert recovered_pnl_context["fill_event_count"] == 1
    assert recovered_pnl_context["open_bracket_order_count"] == 0

    writeback_payload = recovered_feedback_adapter.build_learn_feedback_writeback_payload(
        stored_bracket,
        sponsor_persona_id="persona-bracket-guard-sponsor",
        contributing_persona_ids=["persona-llm-risk-ops"],
        summary=(
            "AMD LLM risk-gate alpha consumed fetched close=120.0, opened a paper long, "
            "logged bracket risk without child-order submission because the guard was disabled, "
            "and recovered the adapter lineage."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-risk-ops",
                "summary": "Guard-disabled bracket feedback preserved logged_only status and performance counters.",
                "proposal_ids": ["llm-amd-bracket-guard-042"],
                "tags": ["llm_alpha", "bracket_guard", "logged_only", "adapter_recovery"],
            }
        ],
        proposal_ids=["llm-amd-bracket-guard-042"],
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
        query="AMD bracket guard logged only adapter recovery",
        tags=["bracket_guard", "logged_only"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_bracket["event_id"]
    institutional_payload = institutional_hits[0].entry.content["structured_payload"]
    institutional_context = institutional_payload["runtime_telemetry_evidence"][0]["lineage"]["order_context"]
    assert institutional_context["broker_submission_status"] == "logged_only"
    assert institutional_context["submitted_to_broker"] is False
    assert institutional_context["guard_reason"] == "paper/sim bracket execution guard is disabled"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-risk-ops",
        query="guard disabled bracket feedback",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_payload = persona_hits[0].entry.content["structured_payload"]
    persona_lineage = persona_payload["runtime_telemetry_evidence"][0]["lineage"]
    assert persona_lineage["alpha_context"]["alpha_source"] == "llm_risk_gate_agent"
    assert persona_lineage["order_context"]["broker_submission_status"] == "logged_only"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-042-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 042 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_price_daily",
            "feature_targets": ["features/us_llm_risk_guard_inputs"],
            "schema_hash": "us_price_daily.e2e_loop_042.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-042-amd",
        "title": "AMD daily close for E2E loop 042",
        "content_ref": "market://us_price_daily/AMD/2026-06-12",
        "metadata": {
            "dataset": "us_price_daily",
            "date": "2026-06-12",
            "symbol": "AMD",
            "open": 118.0,
            "high": 122.0,
            "low": 117.0,
            "close": 120.0,
            "volume": 2800000,
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _llm_guarded_bracket_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    return {
        "signal_id": "llm-amd-bracket-guard-042",
        "version": "1.0",
        "strategy_id": "strategy-llm-bracket-guard",
        "timestamp": _iso_now(),
        "symbol": "AMD.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 3,
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-risk-guard-normalizer",
        "metadata": {
            "alpha_source": "llm_risk_gate_agent",
            "confidence_score": 0.88,
            "model_id": "gpt-risk-guard-paper",
            "prompt_bundle_id": "risk-guard-bracket-v1",
            "llm_decision_id": "llm-decision-amd-guard-042",
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
            "risk_parameters": {
                "stop_loss_pct": 0.04,
                "take_profit_pct": 0.09,
            },
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-042",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-042-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-042",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-042",
                "runtime_id": "paper-runtime-042",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-bracket-guard",
                "artifact_version": "2.2.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-bracket-guard",
                "persona_capital_binding_id": "pcb-paper-bracket-guard",
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
            "event_id": f"e2e-loop-042-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-042",
            "runtime_id": "paper-runtime-042",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-bracket-guard",
            "artifact_version": "2.2.0",
            "plan_id": "plan-paper-bracket-guard",
            "persona_capital_binding_id": "pcb-paper-bracket-guard",
            "target": {
                "registry_id": "artifact-paper-bracket-guard",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-bracket-guard",
                "artifact_version": "2.2.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-042-runtime",
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
