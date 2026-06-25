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


LLM_REFS = {
    "model_id": "gpt-alpha-e2e-080",
    "prompt_bundle_id": "prompt-bundle-e2e-080",
    "llm_prompt_id": "prompt-e2e-080",
    "llm_response_id": "response-e2e-080",
    "llm_decision_id": "decision-e2e-080",
    "research_note_ref": "memory://research/e2e-080/snow-cash-note",
    "llm_note_ref": "memory://llm/e2e-080/snow-sizing-note",
    "research_data_ref": ["research://snow/e2e-080/product-consumption"],
}


def test_llm_cash_value_market_roundtrip_feedback_performance_memory_readback_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    loop_id = "080"
    strategy_id = "strategy-llm-cash-market-roundtrip"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-080-us-prices",
                provider="E2E Loop 080 Static US Prices",
                dataset="us_llm_cash_market_price_daily",
                feature_target="features/llm_cash_market_roundtrip_inputs",
                schema_hash="us_llm_cash_market_price_daily.e2e_loop_080.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-080-snow-entry",
                        dataset="us_llm_cash_market_price_daily",
                        symbol="SNOW",
                        trade_date="2026-06-09",
                        close=49.0,
                        volume=1_170_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-080-snow-exit",
                        dataset="us_llm_cash_market_price_daily",
                        symbol="SNOW",
                        trade_date="2026-06-10",
                        close=51.5,
                        volume=1_290_000,
                    ),
                ],
                "next_watermark": "2026-06-10T21:12:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-080-us-prices",
            "trace_id": "trace-e2e-loop-080-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-09", 49.0),
        ("2026-06-10", 51.5),
    ]

    signals = _llm_cash_market_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-cash-market",
        artifact_version="8.2.0",
        plan_id="plan-paper-llm-cash-market",
        persona_capital_binding_id="pcb-paper-llm-cash-market",
        default_strategy_id="paper-runtime-llm-cash-market",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-cash-market",
            artifact_version="8.2.0",
            plan_id="plan-paper-llm-cash-market",
            persona_capital_binding_id="pcb-paper-llm-cash-market",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "SNOW", "quantity": 5.0, "price": 49.0}
    ]

    pending_store.enqueue(signals[1])
    exit_snapshot = runtime.drain_once()

    assert exit_snapshot["status"] == "ok"
    assert exit_snapshot["paper_state"]["processed_signal_count"] == 2
    assert exit_snapshot["paper_state"]["execution_event_count"] == 2
    assert exit_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "llm-snow-cash-market-entry-080",
        "llm-snow-cash-market-exit-080",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "liquidate"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [5.0, -5.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [49.0, 51.5]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metadata"]["alpha_source"] == "llm_research_cash_market_entry"
    assert entry_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert entry_fill["metadata"]["requested_quantity"] == 260.0
    assert entry_fill["metadata"]["market_price"] == 49.0
    assert entry_fill["metadata"]["model_id"] == "gpt-alpha-e2e-080"
    assert exit_fill["metadata"]["alpha_source"] == "llm_research_cash_market_exit"
    assert exit_fill["metadata"]["quantity_type"] == "SHARES"
    assert exit_fill["metadata"]["requested_quantity"] == 5.0
    assert exit_fill["metadata"]["llm_decision_id"] == "decision-e2e-080"
    assert exit_fill["metadata"]["market_price"] == 51.5
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 12.5
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-080")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_entry = records_by_id[stored_fills[0]["event_id"]]
    recovered_exit = records_by_id[stored_fills[1]["event_id"]]
    assert recovered_entry["alpha_context"]["model_id"] == "gpt-alpha-e2e-080"
    assert recovered_entry["order_context"]["quantity_type"] == "CASH_VALUE"
    assert recovered_entry["order_context"]["requested_quantity"] == 260.0
    assert recovered_entry["order_context"]["fill_quantity"] == 5.0
    assert recovered_exit["alpha_context"]["llm_response_id"] == "response-e2e-080"
    assert recovered_exit["order_context"]["fill_quantity"] == -5.0
    assert recovered_exit["order_context"]["fill_price"] == 51.5
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 12.5

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-cash-market-sponsor",
        contributing_persona_ids=["persona-llm-cash-market-ops"],
        summary=(
            "SNOW LLM CASH_VALUE market sizing converted 260.0 cash into 5 shares at 49.0, "
            "closed at 51.5 for 12.5 paper PnL, recovered feedback, and wrote sizing evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-cash-market-ops",
                "summary": "LLM CASH_VALUE market feedback preserved requested cash, rounded fill, close, and PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_cash_market_roundtrip", "paper_fill", "sizing_audit", "paper_performance"],
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
    for stored_fill in stored_fills:
        writeback_payload["runtime_telemetry_evidence"].append(
            {
                "ref_type": "telemetry_event",
                "ref_id": stored_fill["event_id"],
                "event_type": stored_fill["event_type"],
                "lineage": recovered_adapter.build_lineage_record(stored_fill),
            }
        )
    writeback_payload["tags"].extend(
        ["llm_cash_market_roundtrip", "paper_fill", "sizing_audit", "paper_performance"]
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
        query="SNOW LLM cash value sizing PnL",
        tags=["llm_cash_market_roundtrip", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    fill_lineages = [item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated"]
    assert pnl_lineage["order_context"]["pnl"] == 12.5
    assert fill_lineages[0]["order_context"]["quantity_type"] == "CASH_VALUE"
    assert fill_lineages[0]["order_context"]["requested_quantity"] == 260.0
    assert fill_lineages[0]["order_context"]["fill_quantity"] == 5.0
    assert fill_lineages[1]["alpha_context"]["llm_decision_id"] == "decision-e2e-080"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-cash-market-ops",
        query="cash value market rounded fill pnl",
        tags=["sizing_audit"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_entry_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_fill_simulated"
    )
    assert persona_entry_lineage["strategy_id"] == strategy_id
    assert persona_entry_lineage["order_context"]["requested_quantity"] == 260.0


def _llm_cash_market_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common: dict[str, Any] = {
        "strategy_id": strategy_id,
        "symbol": "SNOW.US",
        "source_worker": "mock-llm-cash-market-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.86,
        "order_type": "MARKET",
        "extra_metadata": LLM_REFS,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-09"],
            signal_id="llm-snow-cash-market-entry-080",
            action="BUY",
            direction="LONG",
            quantity=260.0,
            quantity_type="CASH_VALUE",
            alpha_source="llm_research_cash_market_entry",
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-10"],
            signal_id="llm-snow-cash-market-exit-080",
            action="SELL",
            direction="LONG",
            quantity=5.0,
            quantity_type="SHARES",
            alpha_source="llm_research_cash_market_exit",
            **common,
        ),
    ]
