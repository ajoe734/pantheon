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


def test_llm_short_bracket_feedback_recovery_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "077"
    strategy_id = "strategy-llm-short-bracket"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-077-us-prices",
                provider="E2E Loop 077 Static US Prices",
                dataset="us_llm_short_bracket_price_daily",
                feature_target="features/llm_short_bracket_inputs",
                schema_hash="us_llm_short_bracket_price_daily.e2e_loop_077.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-077-zm",
                        dataset="us_llm_short_bracket_price_daily",
                        symbol="ZM",
                        trade_date="2026-06-12",
                        close=70.0,
                        volume=1_900_000,
                    )
                ],
                "next_watermark": "2026-06-12T21:17:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={"connector_id": "conn-e2e-loop-077-us-prices", "trace_id": "trace-e2e-loop-077-data-fetch"},
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["close"]) for row in normalized_rows] == [("ZM", 70.0)]

    signal = signal_from_market_row(
        normalized_rows[0],
        signal_id="llm-zm-short-bracket-077",
        strategy_id=strategy_id,
        symbol="ZM.US",
        action="SELL",
        direction="SHORT",
        quantity=4.0,
        quantity_type="SHARES",
        source_worker="mock-llm-short-bracket-normalizer",
        alpha_source="llm_short_bracket_agent",
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.83,
        extra_metadata={
            "model_id": "gpt-short-bracket-e2e-077",
            "prompt_bundle_id": "prompt-bundle-e2e-077-short-bracket",
            "llm_prompt_id": "prompt-e2e-077-zm-short",
            "llm_response_id": "response-e2e-077-zm-short",
            "llm_decision_id": "decision-e2e-077-zm-short",
            "research_note_ref": "memory://research/e2e-077/zm-short-note",
            "llm_note_ref": "memory://llm/e2e-077/zm-short-decision",
            "research_data_ref": ["research://zm/e2e-077/enterprise-churn"],
            "risk_parameters": {"stop_loss_pct": 0.05, "take_profit_pct": 0.10},
        },
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-short-bracket",
        artifact_version="8.0.0",
        plan_id="plan-paper-llm-short-bracket",
        persona_capital_binding_id="pcb-paper-llm-short-bracket",
        default_strategy_id="paper-runtime-llm-short-bracket",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-short-bracket",
            artifact_version="8.0.0",
            plan_id="plan-paper-llm-short-bracket",
            persona_capital_binding_id="pcb-paper-llm-short-bracket",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["positions"] == [{"symbol": "ZM", "quantity": -4.0, "price": 70.0}]
    assert len(snapshot["paper_state"]["open_bracket_orders"]) == 2

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["fill_quantity"] == -4.0
    assert fill_event["metrics"]["fill_price"] == 70.0
    assert fill_event["metadata"]["model_id"] == "gpt-short-bracket-e2e-077"

    bracket_event = next(event for event in telemetry.events if event["event_type"] == "bracket_order_logged")
    assert bracket_event["metrics"]["action"] == "bracket_submitted_to_broker"
    assert bracket_event["metadata"]["submitted_to_broker"] is True
    submission = bracket_event["metadata"]["submission"]
    stop_leg = next(leg for leg in submission["legs"] if leg["leg_type"] == "stop_loss")
    target_leg = next(leg for leg in submission["legs"] if leg["leg_type"] == "take_profit")
    assert stop_leg["quantity"] == 4.0
    assert stop_leg["stop_price"] == 73.5
    assert target_leg["quantity"] == 4.0
    assert target_leg["limit_price"] == 63.0

    pnl_event = next(event for event in telemetry.events if event["event_type"] == "pnl_snapshot")
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 2

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    writer_adapter.ingest_telemetry_event(fill_event, strategy_id=strategy_id, promotion_state="paper")
    stored_bracket = writer_adapter.ingest_telemetry_event(
        bracket_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-077")
    bracket_record = next(record for record in recovered_records if record["event_type"] == "bracket_order_logged")
    assert bracket_record["alpha_context"]["llm_response_id"] == "response-e2e-077-zm-short"
    assert bracket_record["order_context"]["submitted_legs"][0]["quantity"] == 4.0
    assert bracket_record["order_context"]["submitted_legs"][1]["limit_price"] == 63.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_bracket,
        sponsor_persona_id="persona-llm-short-bracket-sponsor",
        contributing_persona_ids=["persona-llm-short-bracket-ops"],
        summary="ZM LLM short bracket opened a paper short, submitted cover-side bracket legs, recovered adapter feedback, and wrote the child-order lineage into memory.",
        contributor_feedback=[
            {
                "persona_id": "persona-llm-short-bracket-ops",
                "summary": "Short bracket feedback preserved cover quantities, prompt lineage, and submitted child legs.",
                "proposal_ids": [signal["signal_id"], stored_bracket["event_id"]],
                "tags": ["llm_short_bracket", "child_order", "adapter_recovery"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_bracket["event_id"]],
    )
    writeback_payload["tags"].extend(["llm_short_bracket", "child_order", "adapter_recovery"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    assert write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ZM LLM short bracket cover child legs",
        tags=["llm_short_bracket", "child_order"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    memory_lineage = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert memory_lineage["alpha_context"]["model_id"] == "gpt-short-bracket-e2e-077"
    assert memory_lineage["order_context"]["submitted_legs"][0]["stop_price"] == 73.5

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-short-bracket-ops",
        query="short bracket cover legs",
        tags=["adapter_recovery"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]["lineage"]
    assert persona_lineage["order_context"]["submitted_legs"][1]["limit_price"] == 63.0
