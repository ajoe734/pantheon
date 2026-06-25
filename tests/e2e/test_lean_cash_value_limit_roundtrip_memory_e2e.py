from __future__ import annotations

from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e._lean_memory_e2e_helpers import (
    CanonicalTelemetryRecorder,
    RuntimeManagerClient,
    market_connector,
    market_record,
    runtime_identity,
    signal_from_market_row,
)
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_cash_value_limit_roundtrip_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "067"
    strategy_id = "strategy-cash-value-limit-roundtrip"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-067-us-prices",
                provider="E2E Loop 067 Static US Prices",
                dataset="us_cash_value_limit_price_daily",
                feature_target="features/quant_cash_value_limit_inputs",
                schema_hash="us_cash_value_limit_price_daily.e2e_loop_067.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-067-net-entry",
                        dataset="us_cash_value_limit_price_daily",
                        symbol="NET",
                        trade_date="2026-06-04",
                        close=31.0,
                        volume=990000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-067-net-exit",
                        dataset="us_cash_value_limit_price_daily",
                        symbol="NET",
                        trade_date="2026-06-05",
                        close=33.5,
                        volume=1_050_000,
                    ),
                ],
                "next_watermark": "2026-06-05T21:07:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-067-us-prices",
            "trace_id": "trace-e2e-loop-067-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-04", 31.0),
        ("2026-06-05", 33.5),
    ]

    signals = _cash_value_limit_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-cash-value-limit",
        artifact_version="7.0.0",
        plan_id="plan-paper-cash-value-limit",
        persona_capital_binding_id="pcb-paper-cash-value-limit",
        default_strategy_id="paper-runtime-cash-value-limit",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-cash-value-limit",
            artifact_version="7.0.0",
            plan_id="plan-paper-cash-value-limit",
            persona_capital_binding_id="pcb-paper-cash-value-limit",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "NET", "quantity": 5.0, "price": 30.0}
    ]

    pending_store.enqueue(signals[1])
    exit_snapshot = runtime.drain_once()

    assert exit_snapshot["status"] == "ok"
    assert exit_snapshot["paper_state"]["processed_signal_count"] == 2
    assert exit_snapshot["paper_state"]["execution_event_count"] == 2
    assert exit_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-net-cash-limit-entry-067",
        "quant-net-cash-limit-exit-067",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["limit_order", "limit_order"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [5.0, -5.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [30.0, 34.0]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert entry_fill["metadata"]["requested_quantity"] == 151.0
    assert entry_fill["metadata"]["limit_price"] == 30.0
    assert entry_fill["metadata"]["market_price"] == 31.0
    assert exit_fill["metadata"]["quantity_type"] == "SHARES"
    assert exit_fill["metadata"]["requested_quantity"] == 5.0
    assert exit_fill["metadata"]["limit_price"] == 34.0
    assert exit_fill["metadata"]["market_price"] == 33.5
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 20.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(fill_event, strategy_id=strategy_id, promotion_state="paper")
        for fill_event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-067")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_entry_context = records_by_id[stored_fills[0]["event_id"]]["order_context"]
    assert recovered_entry_context["quantity_type"] == "CASH_VALUE"
    assert recovered_entry_context["requested_quantity"] == 151.0
    assert recovered_entry_context["fill_quantity"] == 5.0
    assert recovered_entry_context["limit_price"] == 30.0
    recovered_exit_context = records_by_id[stored_fills[1]["event_id"]]["order_context"]
    assert recovered_exit_context["fill_quantity"] == -5.0
    assert recovered_exit_context["fill_price"] == 34.0
    assert recovered_exit_context["order_type"] == "LIMIT"
    assert recovered_exit_context["limit_price"] == 34.0
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 20.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-cash-value-limit-sponsor",
        contributing_persona_ids=["persona-cash-value-limit-ops"],
        summary=(
            "NET fetched prices converted 151.0 cash value into 5 shares at a 30.0 limit, "
            "closed them at a 34.0 limit for 20.0 paper PnL, recovered adapter feedback, "
            "and wrote sizing evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-cash-value-limit-ops",
                "summary": "CASH_VALUE LIMIT feedback preserved sizing, fill prices, and final PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["cash_value_limit", "paper_fill", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_fills[0]["event_id"],
            stored_fills[1]["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_fills[0]["event_id"],
            "event_type": stored_fills[0]["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_fills[0]),
        }
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_fills[1]["event_id"],
            "event_type": stored_fills[1]["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_fills[1]),
        }
    )
    writeback_payload["tags"].extend(["cash_value_limit", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="NET cash value limit sizing PnL",
        tags=["cash_value_limit", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    fill_lineages = [item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated"]
    assert pnl_lineage["order_context"]["pnl"] == 20.0
    assert fill_lineages[0]["order_context"]["quantity_type"] == "CASH_VALUE"
    assert fill_lineages[0]["order_context"]["fill_quantity"] == 5.0
    assert fill_lineages[1]["order_context"]["limit_price"] == 34.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-cash-value-limit-ops",
        query="cash value limit sizing",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_pnl_lineage = next(item["lineage"] for item in persona_evidence if item["event_type"] == "pnl_snapshot")
    assert persona_pnl_lineage["strategy_id"] == strategy_id
    assert persona_pnl_lineage["order_context"]["pnl"] == 20.0


def _cash_value_limit_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common = {
        "strategy_id": strategy_id,
        "symbol": "NET.US",
        "source_worker": "mock-cash-value-limit-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.93,
        "order_type": "LIMIT",
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-04"],
            signal_id="quant-net-cash-limit-entry-067",
            action="BUY",
            direction="LONG",
            quantity=151.0,
            quantity_type="CASH_VALUE",
            alpha_source="pure_quant_cash_value_limit_entry",
            limit_price=30.0,
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-05"],
            signal_id="quant-net-cash-limit-exit-067",
            action="SELL",
            direction="LONG",
            quantity=5.0,
            quantity_type="SHARES",
            alpha_source="pure_quant_cash_value_limit_exit",
            limit_price=34.0,
            **common,
        ),
    ]
