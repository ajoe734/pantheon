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


def test_cash_value_limit_close_caps_to_open_position_feedback_memory_readback_e2e(
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
                    _market_record("src-e2e-loop-050-zm-day1", "2026-06-11", close=70.0),
                    _market_record("src-e2e-loop-050-zm-day2", "2026-06-12", close=84.0),
                ],
                "next_watermark": "2026-06-12T20:50:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-050-us-prices",
            "trace_id": "trace-e2e-loop-050-data-fetch",
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
        ("2026-06-11", 70.0),
        ("2026-06-12", 84.0),
    ]

    signals = _capped_cash_limit_close_signals(
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
    assert first_snapshot["paper_state"]["positions"] == [
        {"symbol": "ZM", "quantity": 3.0, "price": 70.0}
    ]

    store.enqueue(signals[1])
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    assert snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-zm-entry-050",
        "quant-zm-cash-limit-close-cap-050",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "limit_order"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [3.0, -3.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [70.0, 84.0]
    _, limit_close_fill = fill_events
    assert limit_close_fill["metadata"]["alpha_source"] == "pure_quant_cash_limit_close_cap"
    assert limit_close_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert limit_close_fill["metadata"]["order_type"] == "LIMIT"
    assert limit_close_fill["metadata"]["limit_price"] == 84.0
    assert limit_close_fill["metadata"]["requested_quantity"] == 10000.0
    assert limit_close_fill["metadata"]["market_price"] == 84.0
    assert limit_close_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 42.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(
            fill_event,
            strategy_id="strategy-cash-limit-close-cap",
            promotion_state="paper",
        )
        for fill_event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id="strategy-cash-limit-close-cap",
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-050",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_close_context = records_by_id[stored_fills[1]["event_id"]]["order_context"]
    assert recovered_close_context["fill_quantity"] == -3.0
    assert recovered_close_context["fill_price"] == 84.0
    assert recovered_close_context["quantity_type"] == "CASH_VALUE"
    assert recovered_close_context["order_type"] == "LIMIT"
    assert recovered_close_context["limit_price"] == 84.0
    assert recovered_close_context["requested_quantity"] == 10000.0
    assert recovered_close_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["pnl"] == 42.0
    assert recovered_pnl_context["open_position_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fills[1],
        sponsor_persona_id="persona-cash-limit-cap-sponsor",
        contributing_persona_ids=["persona-cash-limit-cap-ops"],
        summary=(
            "ZM fetched prices opened 3 shares at 70.0, then a 10000.0 CASH_VALUE limit close "
            "was capped to the open 3 shares at 84.0, recovered adapter feedback, and closed flat."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-cash-limit-cap-ops",
                "summary": "Cash-value limit close cap preserved requested cash, actual fill quantity, and flat PnL.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["cash_value", "limit_close_cap", "adapter_recovery", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_fills[1]["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["tags"].extend(["cash_value", "limit_close_cap", "adapter_recovery"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ZM cash value limit close capped to position",
        tags=["cash_value", "limit_close_cap"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_fills[1]["event_id"]
    institutional_payload = institutional_hits[0].entry.content["structured_payload"]
    institutional_lineage = institutional_payload["runtime_telemetry_evidence"][0]["lineage"]
    assert institutional_lineage["alpha_context"]["signal_id"] == "quant-zm-cash-limit-close-cap-050"
    assert institutional_lineage["order_context"]["requested_quantity"] == 10000.0
    assert institutional_lineage["order_context"]["fill_quantity"] == -3.0
    assert institutional_lineage["order_context"]["limit_price"] == 84.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-cash-limit-cap-ops",
        query="cash value cap actual fill quantity",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["alpha_context"]["alpha_source"] == "pure_quant_cash_limit_close_cap"
    assert persona_lineage["order_context"]["fill_quantity"] == -3.0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-050-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 050 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_cash_limit_close_cap_price_daily",
            "feature_targets": ["features/us_cash_limit_close_cap_inputs"],
            "schema_hash": "us_cash_limit_close_cap_price_daily.e2e_loop_050.v1",
        },
    }


def _market_record(source_id: str, date: str, *, close: float) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"ZM daily close for E2E loop 050 on {date}",
        "content_ref": f"market://us_cash_limit_close_cap_price_daily/ZM/{date}",
        "metadata": {
            "dataset": "us_cash_limit_close_cap_price_daily",
            "date": date,
            "symbol": "ZM",
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 640000,
        },
    }


def _capped_cash_limit_close_signals(
    rows: list[dict[str, Any]],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    row_by_date = {row["metadata"]["date"]: row for row in rows}
    return [
        {
            "signal_id": "quant-zm-entry-050",
            "version": "1.0",
            "strategy_id": "strategy-cash-limit-close-cap",
            "timestamp": _iso_now(),
            "symbol": "ZM.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 3,
            "quantity_type": "SHARES",
            "source_worker": "mock-cash-limit-cap-normalizer",
            "metadata": _metadata(
                row_by_date["2026-06-11"],
                normalized_ref_uris=normalized_ref_uris,
                ingest_run_id=ingest_run_id,
                alpha_source="pure_quant_cash_limit_cap_entry",
            ),
        },
        {
            "signal_id": "quant-zm-cash-limit-close-cap-050",
            "version": "1.0",
            "strategy_id": "strategy-cash-limit-close-cap",
            "timestamp": _iso_now(),
            "symbol": "ZM.US",
            "action": "SELL",
            "direction": "LONG",
            "quantity": 10000.0,
            "quantity_type": "CASH_VALUE",
            "order_type": "LIMIT",
            "limit_price": 84.0,
            "source_worker": "mock-cash-limit-cap-normalizer",
            "metadata": _metadata(
                row_by_date["2026-06-12"],
                normalized_ref_uris=normalized_ref_uris,
                ingest_run_id=ingest_run_id,
                alpha_source="pure_quant_cash_limit_close_cap",
            ),
        },
    ]


def _metadata(
    row: dict[str, Any],
    *,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
    alpha_source: str,
) -> dict[str, Any]:
    return {
        "alpha_source": alpha_source,
        "confidence_score": 0.86,
        "market_data": {
            "dataset": row["metadata"]["dataset"],
            "symbol": row["metadata"]["symbol"],
            "date": row["metadata"]["date"],
            "close": row["metadata"]["close"],
            "content_ref": row["content_ref"],
        },
        "normalized_data_ref": normalized_ref_uris,
        "source_dataset_ref": "us_cash_limit_close_cap_price_daily",
        "ingest_run_id": ingest_run_id,
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-050",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-050-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-050",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-050",
                "runtime_id": "paper-runtime-050",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-cash-limit-cap",
                "artifact_version": "3.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-cash-limit-cap",
                "persona_capital_binding_id": "pcb-paper-cash-limit-cap",
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
            "event_id": f"e2e-loop-050-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-050",
            "runtime_id": "paper-runtime-050",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-cash-limit-cap",
            "artifact_version": "3.0.0",
            "plan_id": "plan-paper-cash-limit-cap",
            "persona_capital_binding_id": "pcb-paper-cash-limit-cap",
            "target": {
                "registry_id": "artifact-paper-cash-limit-cap",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-cash-limit-cap",
                "artifact_version": "3.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-050-runtime",
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
