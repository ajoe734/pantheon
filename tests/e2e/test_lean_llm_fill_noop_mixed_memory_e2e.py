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
    "model_id": "gpt-alpha-e2e-099",
    "prompt_bundle_id": "prompt-bundle-e2e-099",
    "llm_prompt_id": "prompt-e2e-099",
    "llm_response_id": "response-e2e-099",
    "llm_decision_id": "decision-e2e-099",
    "research_note_ref": "memory://research/e2e-099/panw-fill-hold",
    "llm_note_ref": "memory://llm/e2e-099/panw-fill-hold",
    "research_data_ref": ["research://panw/e2e-099/security-demand"],
}


def test_llm_fill_then_hold_noop_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "099"
    strategy_id = "strategy-llm-fill-noop-mixed"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-099-us-prices",
                provider="E2E Loop 099 Static US Prices",
                dataset="us_llm_fill_noop_price_daily",
                feature_target="features/llm_fill_noop_inputs",
                schema_hash="us_llm_fill_noop_price_daily.e2e_loop_099.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-099-panw-entry",
                        dataset="us_llm_fill_noop_price_daily",
                        symbol="PANW",
                        trade_date="2026-06-09",
                        close=30.0,
                        volume=920_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-099-panw-hold",
                        dataset="us_llm_fill_noop_price_daily",
                        symbol="PANW",
                        trade_date="2026-06-10",
                        close=31.0,
                        volume=980_000,
                    ),
                ],
                "next_watermark": "2026-06-10T22:05:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-099-us-prices",
            "trace_id": "trace-e2e-loop-099-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in rows] == [
        ("2026-06-09", 30.0),
        ("2026-06-10", 31.0),
    ]

    signals = _mixed_signals(
        rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-fill-noop-mixed",
        artifact_version="9.11.0",
        plan_id="plan-paper-llm-fill-noop-mixed",
        persona_capital_binding_id="pcb-paper-llm-fill-noop-mixed",
        default_strategy_id="paper-runtime-llm-fill-noop-mixed",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-fill-noop-mixed",
            artifact_version="9.11.0",
            plan_id="plan-paper-llm-fill-noop-mixed",
            persona_capital_binding_id="pcb-paper-llm-fill-noop-mixed",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["processed_signal_count"] == 1
    assert entry_snapshot["paper_state"]["execution_event_count"] == 1
    assert entry_snapshot["paper_state"]["positions"] == [{"symbol": "PANW", "quantity": 2.0, "price": 30.0}]

    pending_store.enqueue(signals[1])
    hold_snapshot = runtime.drain_once()
    assert hold_snapshot["status"] == "ok"
    assert hold_snapshot["paper_state"]["processed_signal_count"] == 2
    assert hold_snapshot["paper_state"]["execution_event_count"] == 2
    assert hold_snapshot["paper_state"]["positions"] == [{"symbol": "PANW", "quantity": 2.0, "price": 31.0}]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert len(noop_events) == 1
    fill_event = fill_events[0]
    noop_event = noop_events[0]
    assert fill_event["metadata"]["signal_id"] == "llm-panw-entry-099"
    assert fill_event["metrics"]["action"] == "market_order"
    assert fill_event["metrics"]["fill_quantity"] == 2.0
    assert fill_event["metrics"]["fill_price"] == 30.0
    assert noop_event["metadata"]["signal_id"] == "llm-panw-hold-099"
    assert noop_event["metrics"]["action"] == "hold_signal_noop"
    assert noop_event["metadata"]["noop_reason"] == "hold_signal"
    assert noop_event["metadata"]["market_price"] == 31.0
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 2.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 0.5
    assert pnl_event["metrics"]["open_position_count"] == 1

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(fill_event, strategy_id=strategy_id, promotion_state="paper")
    stored_noop = writer_adapter.ingest_telemetry_event(noop_event, strategy_id=strategy_id, promotion_state="paper")
    stored_pnl = writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-099")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    assert records_by_id[stored_fill["event_id"]]["order_context"]["fill_quantity"] == 2.0
    assert records_by_id[stored_fill["event_id"]]["alpha_context"]["model_id"] == "gpt-alpha-e2e-099"
    assert records_by_id[stored_noop["event_id"]]["order_context"]["noop_reason"] == "hold_signal"
    assert records_by_id[stored_noop["event_id"]]["alpha_context"]["llm_response_id"] == "response-e2e-099"
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_rate"] == 0.5
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 2.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-fill-noop-sponsor",
        contributing_persona_ids=["persona-llm-fill-noop-ops"],
        summary=(
            "PANW LLM mixed run fetched two market rows, bought 2 shares at 30.0, then emitted a HOLD no-order "
            "at 31.0. Feedback adapter recovered both fill and noop events and memory retained the 0.5 fill-rate "
            "with 2.0 mark-to-market paper PnL."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-fill-noop-ops",
                "summary": "Mixed LLM feedback preserved fill, hold noop, mark-to-market PnL, and fill-rate metrics.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_fill_noop_mixed", "paper_noop", "paper_performance"],
            }
        ],
        proposal_ids=[signals[0]["signal_id"], signals[1]["signal_id"], stored_fill["event_id"], stored_noop["event_id"], stored_pnl["event_id"]],
    )
    for stored_event in (stored_fill, stored_noop):
        writeback_payload["runtime_telemetry_evidence"].append(
            {
                "ref_type": "telemetry_event",
                "ref_id": stored_event["event_id"],
                "event_type": stored_event["event_type"],
                "lineage": recovered_adapter.build_lineage_record(stored_event),
            }
        )
    writeback_payload["tags"].extend(["llm_fill_noop_mixed", "paper_noop", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="PANW LLM fill hold noop fill rate pnl",
        tags=["llm_fill_noop_mixed", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    fill_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    assert pnl_lineage["order_context"]["fill_rate"] == 0.5
    assert pnl_lineage["order_context"]["pnl"] == 2.0
    assert fill_lineage["order_context"]["fill_quantity"] == 2.0
    assert noop_lineage["order_context"]["noop_reason"] == "hold_signal"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-fill-noop-ops",
        query="fill hold noop mixed",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_noop_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_order_simulated"
    )
    assert persona_noop_lineage["strategy_id"] == strategy_id
    assert persona_noop_lineage["alpha_context"]["llm_decision_id"] == "decision-e2e-099"


def _mixed_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common: dict[str, Any] = {
        "strategy_id": strategy_id,
        "symbol": "PANW.US",
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-fill-noop-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.88,
        "order_type": "MARKET",
        "extra_metadata": LLM_REFS,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-09"],
            signal_id="llm-panw-entry-099",
            action="BUY",
            direction="LONG",
            quantity=2.0,
            alpha_source="llm_fill_noop_entry",
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-10"],
            signal_id="llm-panw-hold-099",
            action="HOLD",
            direction="LONG",
            quantity=0.0,
            alpha_source="llm_fill_noop_hold",
            **common,
        ),
    ]
