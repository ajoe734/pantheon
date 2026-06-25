from __future__ import annotations

from pathlib import Path

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


LLM_ZERO_REFS = {
    "model_id": "gpt-sizing-e2e-075",
    "prompt_bundle_id": "prompt-bundle-e2e-075-zero-share",
    "llm_prompt_id": "prompt-e2e-075-cost-small-cash",
    "llm_response_id": "response-e2e-075-cost-small-cash",
    "llm_decision_id": "decision-e2e-075-cost-small-cash",
    "research_note_ref": "memory://research/e2e-075/cost-sizing-note",
    "llm_note_ref": "memory://llm/e2e-075/cost-small-cash-decision",
    "research_data_ref": ["research://cost/e2e-075/retail-margin-risk"],
}


def test_llm_zero_share_cash_value_rejection_feedback_memory_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "075"
    strategy_id = "strategy-llm-zero-share-rejection"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-075-us-prices",
                provider="E2E Loop 075 Static US Prices",
                dataset="us_llm_zero_share_price_daily",
                feature_target="features/llm_zero_share_cash_inputs",
                schema_hash="us_llm_zero_share_price_daily.e2e_loop_075.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-075-cost",
                        dataset="us_llm_zero_share_price_daily",
                        symbol="COST",
                        trade_date="2026-06-12",
                        close=800.0,
                        volume=2_500_000,
                    )
                ],
                "next_watermark": "2026-06-12T21:15:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-075-us-prices",
            "trace_id": "trace-e2e-loop-075-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("COST", "2026-06-12", 800.0)
    ]

    signal = signal_from_market_row(
        normalized_rows[0],
        signal_id="llm-zero-share-cost-cash-075",
        strategy_id=strategy_id,
        symbol="COST.US",
        action="BUY",
        direction="LONG",
        quantity=10.0,
        quantity_type="CASH_VALUE",
        source_worker="mock-llm-zero-share-normalizer",
        alpha_source="llm_small_cash_sizing_agent",
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.79,
        extra_metadata={
            **LLM_ZERO_REFS,
            "sizing_policy": "small_cash_probe",
            "expected_sizing_failure": "cash_value_below_one_share",
        },
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-zero-share",
        artifact_version="7.8.0",
        plan_id="plan-paper-llm-zero-share",
        persona_capital_binding_id="pcb-paper-llm-zero-share",
        default_strategy_id="paper-runtime-llm-zero-share",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-zero-share",
            artifact_version="7.8.0",
            plan_id="plan-paper-llm-zero-share",
            persona_capital_binding_id="pcb-paper-llm-zero-share",
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
    assert snapshot["paper_state"]["recent_order_events"][0]["event_type"] == "order_rejection"

    rejection_events = [event for event in telemetry.events if event["event_type"] == "order_rejection"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(rejection_events) == 1
    assert fill_events == []
    rejection_event = rejection_events[0]
    assert rejection_event["metrics"]["action"] == "order_rejected"
    assert rejection_event["metrics"]["requested_quantity"] == 10.0
    assert rejection_event["metrics"]["computed_quantity"] == 0.0
    assert rejection_event["metrics"]["fill_quantity"] == 0.0
    assert rejection_event["metrics"]["fill_rate"] == 0.0
    assert rejection_event["metadata"]["signal_id"] == "llm-zero-share-cost-cash-075"
    assert rejection_event["metadata"]["alpha_source"] == "llm_small_cash_sizing_agent"
    assert rejection_event["metadata"]["model_id"] == "gpt-sizing-e2e-075"
    assert rejection_event["metadata"]["llm_decision_id"] == "decision-e2e-075-cost-small-cash"
    assert rejection_event["metadata"]["reject_reason"] == "cash_value_resolved_to_zero_shares"
    assert rejection_event["metadata"]["order_status"] == "rejected"
    assert rejection_event["metadata"]["quantity_type"] == "CASH_VALUE"
    assert rejection_event["metadata"]["order_type"] == "MARKET"
    assert rejection_event["metadata"]["requested_quantity"] == 10.0
    assert rejection_event["metadata"]["computed_quantity"] == 0.0
    assert rejection_event["metadata"]["price"] == 800.0
    assert rejection_event["metadata"]["market_price"] == 800.0
    assert rejection_event["metadata"]["broker_submission_status"] == "rejected_before_broker"
    assert rejection_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 0
    assert pnl_event["metrics"]["fill_rate"] == 0.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_rejection = writer_adapter.ingest_telemetry_event(
        rejection_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-075")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    rejection_lineage = records_by_id[stored_rejection["event_id"]]
    assert rejection_lineage["alpha_context"]["model_id"] == "gpt-sizing-e2e-075"
    assert rejection_lineage["alpha_context"]["llm_response_id"] == "response-e2e-075-cost-small-cash"
    assert rejection_lineage["order_context"]["reject_reason"] == "cash_value_resolved_to_zero_shares"
    assert rejection_lineage["order_context"]["quantity_type"] == "CASH_VALUE"
    assert rejection_lineage["order_context"]["computed_quantity"] == 0.0
    assert rejection_lineage["order_context"]["broker_submission_status"] == "rejected_before_broker"

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_rejection,
        sponsor_persona_id="persona-llm-zero-share-sponsor",
        contributing_persona_ids=["persona-llm-sizing-ops"],
        summary=(
            "COST market data and LLM sizing research produced a small CASH_VALUE BUY signal. "
            "LEAN resolved it to zero shares, emitted an order rejection before broker submission, "
            "recovered the rejection and zero-fill performance after adapter restart, and wrote "
            "the sizing failure into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-sizing-ops",
                "summary": "LLM zero-share rejection feedback preserved prompt, decision, requested cash, computed shares, and broker non-submission.",
                "proposal_ids": [signal["signal_id"], stored_rejection["event_id"]],
                "tags": ["llm_zero_share_rejection", "cash_value_sizing", "paper_rejection"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_rejection["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_pnl["event_id"],
            "event_type": stored_pnl["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_pnl),
        }
    )
    writeback_payload["tags"].extend(["llm_zero_share_rejection", "cash_value_sizing", "paper_rejection"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="COST LLM zero share cash value rejection",
        tags=["llm_zero_share_rejection", "cash_value_sizing"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    rejection_memory = next(item["lineage"] for item in evidence_items if item["event_type"] == "order_rejection")
    pnl_memory = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    assert rejection_memory["alpha_context"]["model_id"] == "gpt-sizing-e2e-075"
    assert rejection_memory["alpha_context"]["llm_decision_id"] == "decision-e2e-075-cost-small-cash"
    assert rejection_memory["order_context"]["reject_reason"] == "cash_value_resolved_to_zero_shares"
    assert rejection_memory["order_context"]["requested_quantity"] == 10.0
    assert rejection_memory["order_context"]["computed_quantity"] == 0.0
    assert pnl_memory["order_context"]["fill_event_count"] == 0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-sizing-ops",
        query="llm zero share rejected before broker",
        tags=["paper_rejection"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_rejection = next(item["lineage"] for item in persona_evidence if item["event_type"] == "order_rejection")
    assert persona_rejection["strategy_id"] == strategy_id
    assert persona_rejection["alpha_context"]["llm_prompt_id"] == "prompt-e2e-075-cost-small-cash"
    assert persona_rejection["order_context"]["submitted_to_broker"] is False
