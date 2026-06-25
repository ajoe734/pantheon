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


LLM_BRACKET_REFS = {
    "model_id": "gpt-bracket-risk-e2e-076",
    "prompt_bundle_id": "prompt-bundle-e2e-076-bracket",
    "llm_prompt_id": "prompt-e2e-076-hubs-bracket",
    "llm_response_id": "response-e2e-076-hubs-bracket",
    "llm_decision_id": "decision-e2e-076-hubs-bracket",
    "research_note_ref": "memory://research/e2e-076/hubs-bracket-note",
    "llm_note_ref": "memory://llm/e2e-076/hubs-bracket-decision",
    "research_data_ref": ["research://hubs/e2e-076/pipeline-demand"],
}


def test_llm_bracket_submitted_feedback_recovery_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "076"
    strategy_id = "strategy-llm-bracket-submitted"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-076-us-prices",
                provider="E2E Loop 076 Static US Prices",
                dataset="us_llm_bracket_price_daily",
                feature_target="features/llm_bracket_submitted_inputs",
                schema_hash="us_llm_bracket_price_daily.e2e_loop_076.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-076-hubs",
                        dataset="us_llm_bracket_price_daily",
                        symbol="HUBS",
                        trade_date="2026-06-12",
                        close=600.0,
                        volume=640_000,
                    )
                ],
                "next_watermark": "2026-06-12T21:16:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-076-us-prices",
            "trace_id": "trace-e2e-loop-076-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("HUBS", "2026-06-12", 600.0)
    ]

    signal = signal_from_market_row(
        normalized_rows[0],
        signal_id="llm-hubs-bracket-submitted-076",
        strategy_id=strategy_id,
        symbol="HUBS.US",
        action="BUY",
        direction="LONG",
        quantity=2.0,
        quantity_type="SHARES",
        source_worker="mock-llm-bracket-normalizer",
        alpha_source="llm_bracket_risk_agent",
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.9,
        extra_metadata={
            **LLM_BRACKET_REFS,
            "risk_parameters": {"stop_loss_pct": 0.05, "take_profit_pct": 0.08},
        },
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-bracket-submitted",
        artifact_version="7.9.0",
        plan_id="plan-paper-llm-bracket-submitted",
        persona_capital_binding_id="pcb-paper-llm-bracket-submitted",
        default_strategy_id="paper-runtime-llm-bracket-submitted",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-bracket-submitted",
            artifact_version="7.9.0",
            plan_id="plan-paper-llm-bracket-submitted",
            persona_capital_binding_id="pcb-paper-llm-bracket-submitted",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    assert snapshot["paper_state"]["positions"] == [{"symbol": "HUBS", "quantity": 2.0, "price": 600.0}]
    assert len(snapshot["paper_state"]["open_bracket_orders"]) == 2

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["fill_quantity"] == 2.0
    assert fill_event["metrics"]["fill_price"] == 600.0
    assert fill_event["metadata"]["signal_id"] == "llm-hubs-bracket-submitted-076"
    assert fill_event["metadata"]["model_id"] == "gpt-bracket-risk-e2e-076"
    assert fill_event["metadata"]["submitted_to_broker"] is False

    bracket_event = next(event for event in telemetry.events if event["event_type"] == "bracket_order_logged")
    assert bracket_event["metrics"]["action"] == "bracket_submitted_to_broker"
    assert bracket_event["metrics"]["submitted_to_broker"] is True
    assert bracket_event["metadata"]["alpha_source"] == "llm_bracket_risk_agent"
    assert bracket_event["metadata"]["model_id"] == "gpt-bracket-risk-e2e-076"
    assert bracket_event["metadata"]["llm_decision_id"] == "decision-e2e-076-hubs-bracket"
    assert bracket_event["metadata"]["broker_submission_status"] == "submitted_to_broker"
    assert bracket_event["metadata"]["entry_price"] == 600.0
    assert bracket_event["metadata"]["entry_quantity"] == 2.0
    assert bracket_event["metadata"]["submitted_to_broker"] is True
    submission = bracket_event["metadata"]["submission"]
    assert submission["leg_count"] == 2
    stop_leg = next(leg for leg in submission["legs"] if leg["leg_type"] == "stop_loss")
    target_leg = next(leg for leg in submission["legs"] if leg["leg_type"] == "take_profit")
    assert stop_leg["quantity"] == -2.0
    assert stop_leg["stop_price"] == 570.0
    assert target_leg["quantity"] == -2.0
    assert target_leg["limit_price"] == 648.0

    pnl_event = next(event for event in telemetry.events if event["event_type"] == "pnl_snapshot")
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 2

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(fill_event, strategy_id=strategy_id, promotion_state="paper")
    stored_bracket = writer_adapter.ingest_telemetry_event(
        bracket_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-076")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill = records_by_id[stored_fill["event_id"]]["order_context"]
    recovered_bracket = records_by_id[stored_bracket["event_id"]]
    recovered_pnl = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_fill["fill_quantity"] == 2.0
    assert recovered_bracket["alpha_context"]["llm_response_id"] == "response-e2e-076-hubs-bracket"
    assert recovered_bracket["order_context"]["bracket_order_id"] == submission["bracket_order_id"]
    assert recovered_bracket["order_context"]["bracket_leg_count"] == 2
    assert recovered_bracket["order_context"]["submitted_legs"][1]["limit_price"] == 648.0
    assert recovered_pnl["open_bracket_order_count"] == 2

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_bracket,
        sponsor_persona_id="persona-llm-bracket-sponsor",
        contributing_persona_ids=["persona-llm-bracket-ops"],
        summary=(
            "HUBS market data and LLM risk research opened a 2-share paper long, submitted "
            "paper bracket child orders, recovered fill/bracket/PnL feedback after adapter restart, "
            "and wrote bracket lineage into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-bracket-ops",
                "summary": "LLM bracket feedback preserved child legs, bracket ID, prompt lineage, and performance counters.",
                "proposal_ids": [signal["signal_id"], stored_bracket["event_id"]],
                "tags": ["llm_bracket_submitted", "child_order", "adapter_recovery"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_bracket["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].extend(
        [
            {
                "ref_type": "telemetry_event",
                "ref_id": stored_fill["event_id"],
                "event_type": stored_fill["event_type"],
                "lineage": recovered_adapter.build_lineage_record(stored_fill),
            },
            {
                "ref_type": "telemetry_event",
                "ref_id": stored_pnl["event_id"],
                "event_type": stored_pnl["event_type"],
                "lineage": recovered_adapter.build_lineage_record(stored_pnl),
            },
        ]
    )
    writeback_payload["tags"].extend(["llm_bracket_submitted", "child_order", "adapter_recovery"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="HUBS LLM bracket child order recovered",
        tags=["llm_bracket_submitted", "child_order"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    bracket_memory = next(item["lineage"] for item in evidence_items if item["event_type"] == "bracket_order_logged")
    pnl_memory = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    assert bracket_memory["alpha_context"]["model_id"] == "gpt-bracket-risk-e2e-076"
    assert bracket_memory["order_context"]["submitted_legs"][0]["stop_price"] == 570.0
    assert pnl_memory["order_context"]["open_bracket_order_count"] == 2

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-bracket-ops",
        query="llm bracket child legs",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"]
    assert persona_lineage["alpha_context"]["llm_decision_id"] == "decision-e2e-076-hubs-bracket"
    assert persona_lineage["order_context"]["bracket_order_id"] == submission["bracket_order_id"]
