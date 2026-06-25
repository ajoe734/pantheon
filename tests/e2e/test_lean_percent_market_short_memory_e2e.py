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


def test_percent_market_short_feedback_recovery_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T20:55:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-055-us-prices",
            "trace_id": "trace-e2e-loop-055-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "OKTA"
    assert row["metadata"]["close"] == 80.0

    signal = _percent_market_short_signal(
        row,
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
    assert snapshot["paper_state"]["execution_event_count"] == 1
    assert snapshot["paper_state"]["open_bracket_orders"] == []
    assert snapshot["paper_state"]["positions"] == [
        {"symbol": "OKTA", "quantity": pytest.approx(-125.0), "price": 80.0}
    ]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    bracket_events = [event for event in telemetry.events if event["event_type"] == "bracket_order_logged"]
    assert len(fill_events) == 1
    assert bracket_events == []
    fill_event = fill_events[0]
    assert fill_event["metrics"]["action"] == "set_holdings"
    assert fill_event["metrics"]["fill_quantity"] == pytest.approx(-125.0)
    assert fill_event["metrics"]["fill_price"] == 80.0
    assert fill_event["metadata"]["signal_id"] == "quant-okta-percent-market-short-055"
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_percent_market_short"
    assert fill_event["metadata"]["confidence_score"] == 1.0
    assert fill_event["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert fill_event["metadata"]["order_type"] == "MARKET"
    assert fill_event["metadata"]["requested_quantity"] == 0.1
    assert fill_event["metadata"]["market_price"] == 80.0
    assert fill_event["metadata"]["normalized_data_ref"] == normalized_ref["uri"]
    assert fill_event["metadata"]["submitted_to_broker"] is False
    assert fill_event["metadata"]["is_real_order"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-055",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill_context = records_by_id[stored_fill["event_id"]]["order_context"]
    assert recovered_fill_context["quantity_type"] == "PERCENT_PORTFOLIO"
    assert recovered_fill_context["order_type"] == "MARKET"
    assert recovered_fill_context["requested_quantity"] == 0.1
    assert recovered_fill_context["fill_quantity"] == pytest.approx(-125.0)
    assert recovered_fill_context["fill_price"] == 80.0
    assert recovered_fill_context["market_price"] == 80.0
    assert recovered_fill_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["fill_event_count"] == 1
    assert recovered_pnl_context["fill_rate"] == 1.0
    assert recovered_pnl_context["open_position_count"] == 1
    assert recovered_pnl_context["open_bracket_order_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-percent-market-short-sponsor",
        contributing_persona_ids=["persona-percent-short-ops"],
        summary=(
            "OKTA fetched close=80.0 produced a SELL/SHORT PERCENT_PORTFOLIO market signal; "
            "LEAN used SetHoldings to open a -125 share paper short, recovered fill and PnL "
            "feedback, and wrote the percent short context into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-percent-short-ops",
                "summary": "Percent market short feedback preserved SetHoldings fill and no-bracket performance counters.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["percent_market_short", "paper_fill", "paper_performance"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["tags"].extend(["percent_market_short", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="OKTA percent market short SetHoldings",
        tags=["percent_market_short", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "quant-okta-percent-market-short-055"
    assert alpha_context["alpha_source"] == "pure_quant_percent_market_short"
    assert alpha_context["market_price"] == 80.0
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["quantity_type"] == "PERCENT_PORTFOLIO"
    assert order_context["order_type"] == "MARKET"
    assert order_context["fill_quantity"] == pytest.approx(-125.0)
    assert order_context["requested_quantity"] == 0.1
    assert order_context["submitted_to_broker"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-percent-short-ops",
        query="percent market short no bracket",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == "strategy-percent-market-short"
    assert "bracket_order_id" not in persona_lineage["order_context"]
    assert persona_lineage["order_context"]["fill_quantity"] == pytest.approx(-125.0)


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-055-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 055 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_percent_market_short_price_daily",
            "feature_targets": ["features/quant_percent_market_short_inputs"],
            "schema_hash": "us_percent_market_short_price_daily.e2e_loop_055.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-055-okta",
        "title": "OKTA daily close for E2E loop 055",
        "content_ref": "market://us_percent_market_short_price_daily/OKTA/2026-06-12",
        "metadata": {
            "dataset": "us_percent_market_short_price_daily",
            "date": "2026-06-12",
            "symbol": "OKTA",
            "open": 81.0,
            "high": 82.0,
            "low": 79.0,
            "close": 80.0,
            "volume": 620000,
        },
    }


def _percent_market_short_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "quant-okta-percent-market-short-055",
        "version": "1.0",
        "strategy_id": "strategy-percent-market-short",
        "timestamp": _iso_now(),
        "symbol": "OKTA.US",
        "action": "SELL",
        "direction": "SHORT",
        "quantity": 0.1,
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-percent-market-short-normalizer",
        "metadata": {
            "alpha_source": "pure_quant_percent_market_short",
            "confidence_score": 1.0,
            "market_data_ref": normalized_ref["uri"],
            "source_evidence_refs": [
                {
                    "ref_type": "normalized_rows",
                    "dataset": normalized_ref["dataset"],
                    "uri": normalized_ref["uri"],
                    "ingest_run_id": ingest_run_id,
                }
            ],
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-055",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-055-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-055",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-055",
                "runtime_id": "paper-runtime-055",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-percent-market-short",
                "artifact_version": "4.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-percent-market-short",
                "persona_capital_binding_id": "pcb-paper-percent-market-short",
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
            "event_id": f"e2e-loop-055-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-055",
            "runtime_id": "paper-runtime-055",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-percent-market-short",
            "artifact_version": "4.0.0",
            "plan_id": "plan-paper-percent-market-short",
            "persona_capital_binding_id": "pcb-paper-percent-market-short",
            "target": {
                "registry_id": "artifact-paper-percent-market-short",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-percent-market-short",
                "artifact_version": "4.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-055-runtime",
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
