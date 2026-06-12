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


LLM_HOLD_REFS = {
    "model_id": "gpt-riskoff-e2e-069",
    "prompt_bundle_id": "prompt-bundle-e2e-069-riskoff",
    "llm_prompt_id": "prompt-e2e-069-msft-hold",
    "llm_response_id": "response-e2e-069-msft-hold",
    "llm_decision_id": "decision-e2e-069-msft-hold",
    "research_note_ref": "memory://research/e2e-069/msft-riskoff-note",
    "llm_note_ref": "memory://llm/e2e-069/msft-hold-decision",
    "research_data_ref": ["research://msft/e2e-069/macro-riskoff"],
}


def test_llm_hold_noop_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "069"
    strategy_id = "strategy-llm-riskoff-hold"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-069-us-prices",
                provider="E2E Loop 069 Static US Prices",
                dataset="us_llm_hold_price_daily",
                feature_target="features/llm_hold_noop_inputs",
                schema_hash="us_llm_hold_price_daily.e2e_loop_069.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-069-msft",
                        dataset="us_llm_hold_price_daily",
                        symbol="MSFT",
                        trade_date="2026-06-05",
                        close=420.0,
                        volume=2_240_000,
                    )
                ],
                "next_watermark": "2026-06-05T21:09:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-069-us-prices",
            "trace_id": "trace-e2e-loop-069-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("MSFT", "2026-06-05", 420.0)
    ]

    signal = signal_from_market_row(
        normalized_rows[0],
        signal_id="llm-msft-riskoff-hold-069",
        strategy_id=strategy_id,
        symbol="MSFT.US",
        action="HOLD",
        direction="LONG",
        quantity=0.0,
        quantity_type="SHARES",
        source_worker="mock-llm-riskoff-normalizer",
        alpha_source="llm_riskoff_hold_agent",
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.91,
        extra_metadata={
            **LLM_HOLD_REFS,
            "risk_regime": "macro_riskoff",
            "decision_rationale": "wait_for_lower_volatility_before_entering",
        },
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-hold",
        artifact_version="7.2.0",
        plan_id="plan-paper-llm-hold",
        persona_capital_binding_id="pcb-paper-llm-hold",
        default_strategy_id="paper-runtime-llm-hold",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-hold",
            artifact_version="7.2.0",
            plan_id="plan-paper-llm-hold",
            persona_capital_binding_id="pcb-paper-llm-hold",
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
    assert snapshot["paper_state"]["recent_order_events"][0]["action"] == "hold_signal_noop"

    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(noop_events) == 1
    assert fill_events == []
    noop_event = noop_events[0]
    assert noop_event["metrics"]["action"] == "hold_signal_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metrics"]["submitted_to_broker"] is False
    assert noop_event["metadata"]["signal_id"] == "llm-msft-riskoff-hold-069"
    assert noop_event["metadata"]["alpha_source"] == "llm_riskoff_hold_agent"
    assert noop_event["metadata"]["model_id"] == "gpt-riskoff-e2e-069"
    assert noop_event["metadata"]["llm_decision_id"] == "decision-e2e-069-msft-hold"
    assert noop_event["metadata"]["noop_reason"] == "hold_signal"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "SHARES"
    assert noop_event["metadata"]["order_type"] == "MARKET"
    assert noop_event["metadata"]["requested_quantity"] == 0.0
    assert noop_event["metadata"]["price"] == 420.0
    assert noop_event["metadata"]["market_price"] == 420.0
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
    recovered_noops = recovered_adapter.query_telemetry(
        strategy_id=strategy_id,
        event_type="paper_order_simulated",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_noops] == [stored_noop["event_id"]]
    recovered_lineage = recovered_adapter.build_lineage_record(recovered_noops[0])
    assert recovered_lineage["alpha_context"]["model_id"] == "gpt-riskoff-e2e-069"
    assert recovered_lineage["alpha_context"]["llm_prompt_id"] == "prompt-e2e-069-msft-hold"
    assert recovered_lineage["alpha_context"]["research_note_ref"] == "memory://research/e2e-069/msft-riskoff-note"
    assert recovered_lineage["alpha_context"]["market_data_ref"] == [normalized_refs[0]["uri"]]
    assert recovered_lineage["order_context"]["noop_reason"] == "hold_signal"
    assert recovered_lineage["order_context"]["order_status"] == "not_submitted"
    assert recovered_lineage["order_context"]["fill_rate"] == 0.0
    assert recovered_lineage["order_context"]["submitted_to_broker"] is False

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-hold-sponsor",
        contributing_persona_ids=["persona-llm-riskoff-ops"],
        summary=(
            "MSFT market data and LLM risk-off research produced a HOLD decision. LEAN consumed "
            "the signal, placed no broker order, emitted no-order feedback with zero fills and "
            "zero PnL, recovered that feedback after adapter restart, and wrote the decision "
            "lineage into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-riskoff-ops",
                "summary": "LLM HOLD feedback preserved prompt, decision, no-order status, and zero-fill performance.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["llm_hold_noop", "paper_noop", "paper_performance"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_noop["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_noop["event_id"],
            "event_type": stored_noop["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_noop),
        }
    )
    writeback_payload["tags"].extend(["llm_hold_noop", "paper_noop", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="MSFT LLM HOLD no order zero fill PnL",
        tags=["llm_hold_noop", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    assert pnl_lineage["order_context"]["pnl"] == 0.0
    assert pnl_lineage["order_context"]["fill_event_count"] == 0
    assert noop_lineage["alpha_context"]["model_id"] == "gpt-riskoff-e2e-069"
    assert noop_lineage["alpha_context"]["llm_decision_id"] == "decision-e2e-069-msft-hold"
    assert noop_lineage["order_context"]["noop_reason"] == "hold_signal"
    assert noop_lineage["order_context"]["broker_submission_status"] == "not_submitted_signal_noop"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-riskoff-ops",
        query="llm hold no order zero fill",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_noop_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_order_simulated"
    )
    assert persona_noop_lineage["strategy_id"] == strategy_id
    assert persona_noop_lineage["alpha_context"]["llm_response_id"] == "response-e2e-069-msft-hold"
    assert persona_noop_lineage["order_context"]["fill_rate"] == 0.0
