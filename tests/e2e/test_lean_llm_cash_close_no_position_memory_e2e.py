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
    "model_id": "gpt-alpha-e2e-098",
    "prompt_bundle_id": "prompt-bundle-e2e-098",
    "llm_prompt_id": "prompt-e2e-098",
    "llm_response_id": "response-e2e-098",
    "llm_decision_id": "decision-e2e-098",
    "research_note_ref": "memory://research/e2e-098/bill-cash-close-empty",
    "llm_note_ref": "memory://llm/e2e-098/bill-cash-close-empty",
    "research_data_ref": ["research://bill/e2e-098/deleveraging"],
}


def test_llm_sell_long_cash_close_without_position_feedback_memory_readback_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    loop_id = "098"
    strategy_id = "strategy-llm-cash-close-no-position"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-098-us-prices",
                provider="E2E Loop 098 Static US Prices",
                dataset="us_llm_cash_close_empty_price_daily",
                feature_target="features/llm_cash_close_empty_inputs",
                schema_hash="us_llm_cash_close_empty_price_daily.e2e_loop_098.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-098-bill",
                        dataset="us_llm_cash_close_empty_price_daily",
                        symbol="BILL",
                        trade_date="2026-06-10",
                        close=45.0,
                        volume=880_000,
                    )
                ],
                "next_watermark": "2026-06-10T22:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-098-us-prices",
            "trace_id": "trace-e2e-loop-098-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "BILL"
    assert row["metadata"]["close"] == 45.0

    signal = signal_from_market_row(
        row,
        signal_id="llm-bill-cash-close-empty-098",
        strategy_id=strategy_id,
        symbol="BILL.US",
        action="SELL",
        direction="LONG",
        quantity=540.0,
        quantity_type="CASH_VALUE",
        source_worker="mock-llm-cash-close-empty-normalizer",
        alpha_source="llm_cash_close_no_position",
        normalized_ref_uris=[normalized_ref["uri"]],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.77,
        order_type="MARKET",
        extra_metadata=LLM_REFS,
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-cash-close-empty",
        artifact_version="9.10.0",
        plan_id="plan-paper-llm-cash-close-empty",
        persona_capital_binding_id="pcb-paper-llm-cash-close-empty",
        default_strategy_id="paper-runtime-llm-cash-close-empty",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-cash-close-empty",
            artifact_version="9.10.0",
            plan_id="plan-paper-llm-cash-close-empty",
            persona_capital_binding_id="pcb-paper-llm-cash-close-empty",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    assert snapshot["paper_state"]["execution_event_count"] == 1
    assert snapshot["paper_state"]["positions"] == []

    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(noop_events) == 1
    assert fill_events == []
    noop_event = noop_events[0]
    assert noop_event["metrics"]["action"] == "liquidate_without_position_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 540.0
    assert noop_event["metrics"]["computed_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["signal_id"] == "llm-bill-cash-close-empty-098"
    assert noop_event["metadata"]["alpha_source"] == "llm_cash_close_no_position"
    assert noop_event["metadata"]["model_id"] == "gpt-alpha-e2e-098"
    assert noop_event["metadata"]["noop_reason"] == "liquidate_without_position"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "CASH_VALUE"
    assert noop_event["metadata"]["order_type"] == "MARKET"
    assert noop_event["metadata"]["market_price"] == 45.0
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 0
    assert pnl_event["metrics"]["fill_rate"] == 0.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_noop = writer_adapter.ingest_telemetry_event(noop_event, strategy_id=strategy_id, promotion_state="paper")
    stored_pnl = writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-098")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_noop = records_by_id[stored_noop["event_id"]]
    assert recovered_noop["alpha_context"]["model_id"] == "gpt-alpha-e2e-098"
    assert recovered_noop["alpha_context"]["llm_decision_id"] == "decision-e2e-098"
    assert recovered_noop["order_context"]["noop_reason"] == "liquidate_without_position"
    assert recovered_noop["order_context"]["quantity_type"] == "CASH_VALUE"
    assert recovered_noop["order_context"]["requested_quantity"] == 540.0
    assert recovered_noop["order_context"]["computed_quantity"] == 0.0
    assert recovered_noop["order_context"]["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_event_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_noop,
        sponsor_persona_id="persona-llm-cash-close-empty-sponsor",
        contributing_persona_ids=["persona-llm-cash-close-empty-ops"],
        summary=(
            "BILL LLM cash-value market close fetched market data and requested a SELL/LONG cash close, "
            "but paper Liquidate found no long position, emitted no-order feedback, recovered adapter lineage, "
            "and wrote zero-fill performance evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-cash-close-empty-ops",
                "summary": "LLM cash close no-position feedback preserved cash sizing, no broker submit, and zero fill-rate.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["llm_cash_close_no_position", "paper_noop", "paper_performance"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_noop["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_pnl["event_id"],
            "event_type": stored_pnl["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_pnl),
        }
    )
    writeback_payload["tags"].extend(["llm_cash_close_no_position", "paper_noop", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="BILL LLM cash close no position zero fill",
        tags=["llm_cash_close_no_position", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    assert noop_lineage["alpha_context"]["signal_id"] == "llm-bill-cash-close-empty-098"
    assert noop_lineage["order_context"]["noop_reason"] == "liquidate_without_position"
    assert noop_lineage["order_context"]["requested_quantity"] == 540.0
    assert pnl_lineage["order_context"]["fill_rate"] == 0.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-cash-close-empty-ops",
        query="cash close no broker submit zero fill",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_noop_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_order_simulated"
    )
    assert persona_noop_lineage["strategy_id"] == strategy_id
    assert persona_noop_lineage["order_context"]["submitted_to_broker"] is False
