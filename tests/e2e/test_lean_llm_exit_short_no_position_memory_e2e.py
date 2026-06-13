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


LLM_REFS = {
    "model_id": "gpt-alpha-e2e-084",
    "prompt_bundle_id": "prompt-bundle-e2e-084",
    "llm_prompt_id": "prompt-e2e-084",
    "llm_response_id": "response-e2e-084",
    "llm_decision_id": "decision-e2e-084",
    "research_note_ref": "memory://research/e2e-084/etsy-cover-note",
    "llm_note_ref": "memory://llm/e2e-084/etsy-cover-risk",
    "research_data_ref": ["research://etsy/e2e-084/short-squeeze-risk"],
}


def test_llm_exit_short_without_position_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "084"
    strategy_id = "strategy-llm-exit-short-no-position"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-084-us-prices",
                provider="E2E Loop 084 Static US Prices",
                dataset="us_llm_exit_short_empty_price_daily",
                feature_target="features/llm_exit_short_empty_inputs",
                schema_hash="us_llm_exit_short_empty_price_daily.e2e_loop_084.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-084-etsy",
                        dataset="us_llm_exit_short_empty_price_daily",
                        symbol="ETSY",
                        trade_date="2026-06-10",
                        close=88.0,
                        volume=690_000,
                    )
                ],
                "next_watermark": "2026-06-10T21:16:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-084-us-prices",
            "trace_id": "trace-e2e-loop-084-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "ETSY"
    assert row["metadata"]["close"] == 88.0

    signal = signal_from_market_row(
        row,
        signal_id="llm-etsy-exit-short-empty-084",
        strategy_id=strategy_id,
        symbol="ETSY.US",
        action="EXIT",
        direction="SHORT",
        quantity=0.0,
        quantity_type="SHARES",
        source_worker="mock-llm-exit-short-normalizer",
        alpha_source="llm_research_exit_short_no_position",
        normalized_ref_uris=[normalized_ref["uri"]],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.78,
        order_type="MARKET",
        extra_metadata=LLM_REFS,
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-exit-short-empty",
        artifact_version="8.6.0",
        plan_id="plan-paper-llm-exit-short-empty",
        persona_capital_binding_id="pcb-paper-llm-exit-short-empty",
        default_strategy_id="paper-runtime-llm-exit-short-empty",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-exit-short-empty",
            artifact_version="8.6.0",
            plan_id="plan-paper-llm-exit-short-empty",
            persona_capital_binding_id="pcb-paper-llm-exit-short-empty",
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
    assert snapshot["paper_state"]["recent_order_events"][0]["event_type"] == "paper_order_simulated"

    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(noop_events) == 1
    assert fill_events == []
    noop_event = noop_events[0]
    assert noop_event["metrics"]["action"] == "exit_short_without_position_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["signal_id"] == "llm-etsy-exit-short-empty-084"
    assert noop_event["metadata"]["alpha_source"] == "llm_research_exit_short_no_position"
    assert noop_event["metadata"]["model_id"] == "gpt-alpha-e2e-084"
    assert noop_event["metadata"]["llm_prompt_id"] == "prompt-e2e-084"
    assert noop_event["metadata"]["noop_reason"] == "exit_short_without_position"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "SHARES"
    assert noop_event["metadata"]["position_quantity"] == 0.0
    assert noop_event["metadata"]["exit_direction"] == "SHORT"
    assert noop_event["metadata"]["market_price"] == 88.0
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_noop"
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
    stored_noop = writer_adapter.ingest_telemetry_event(
        noop_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-084")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_noop = records_by_id[stored_noop["event_id"]]
    assert recovered_noop["alpha_context"]["model_id"] == "gpt-alpha-e2e-084"
    assert recovered_noop["alpha_context"]["research_note_ref"] == "memory://research/e2e-084/etsy-cover-note"
    assert recovered_noop["order_context"]["noop_reason"] == "exit_short_without_position"
    assert recovered_noop["order_context"]["exit_direction"] == "SHORT"
    assert recovered_noop["order_context"]["position_quantity"] == 0.0
    assert recovered_noop["order_context"]["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["open_position_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_noop,
        sponsor_persona_id="persona-llm-exit-short-empty-sponsor",
        contributing_persona_ids=["persona-llm-exit-short-empty-ops"],
        summary=(
            "ETSY LLM short-cover signal consumed fetched market data, but LEAN found no short position; "
            "it emitted recoverable no-order feedback, recovered adapter lineage, and wrote zero-short "
            "position evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-exit-short-empty-ops",
                "summary": "LLM EXIT/SHORT no-position feedback preserved decision, no-order status, and zero short context.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["llm_exit_short_no_position", "paper_noop", "adapter_recovery"],
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
    writeback_payload["tags"].extend(["llm_exit_short_no_position", "paper_noop", "adapter_recovery"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ETSY LLM exit short no position no order",
        tags=["llm_exit_short_no_position", "paper_noop"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    assert noop_lineage["alpha_context"]["llm_prompt_id"] == "prompt-e2e-084"
    assert noop_lineage["order_context"]["noop_reason"] == "exit_short_without_position"
    assert noop_lineage["order_context"]["exit_direction"] == "SHORT"
    assert pnl_lineage["order_context"]["fill_event_count"] == 0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-exit-short-empty-ops",
        query="llm exit short zero position",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == strategy_id
    assert persona_lineage["order_context"]["decision_status"] == "no_order"
