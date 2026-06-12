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
    "model_id": "gpt-alpha-e2e-079",
    "prompt_bundle_id": "prompt-bundle-e2e-079",
    "llm_prompt_id": "prompt-e2e-079",
    "llm_response_id": "response-e2e-079",
    "llm_decision_id": "decision-e2e-079",
    "research_note_ref": "memory://research/e2e-079/adbe-risk-note",
    "llm_note_ref": "memory://llm/e2e-079/adbe-decision-note",
    "research_data_ref": ["research://adbe/e2e-079/creative-cloud-risk"],
}


def test_llm_market_long_loss_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "079"
    strategy_id = "strategy-llm-market-long-loss"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-079-us-prices",
                provider="E2E Loop 079 Static US Prices",
                dataset="us_llm_market_loss_price_daily",
                feature_target="features/llm_market_loss_inputs",
                schema_hash="us_llm_market_loss_price_daily.e2e_loop_079.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-079-adbe-entry",
                        dataset="us_llm_market_loss_price_daily",
                        symbol="ADBE",
                        trade_date="2026-06-05",
                        close=122.0,
                        volume=980_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-079-adbe-exit",
                        dataset="us_llm_market_loss_price_daily",
                        symbol="ADBE",
                        trade_date="2026-06-08",
                        close=118.5,
                        volume=1_050_000,
                    ),
                ],
                "next_watermark": "2026-06-08T21:11:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-079-us-prices",
            "trace_id": "trace-e2e-loop-079-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-05", 122.0),
        ("2026-06-08", 118.5),
    ]

    signals = _llm_market_loss_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-market-loss",
        artifact_version="8.1.0",
        plan_id="plan-paper-llm-market-loss",
        persona_capital_binding_id="pcb-paper-llm-market-loss",
        default_strategy_id="paper-runtime-llm-market-loss",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-market-loss",
            artifact_version="8.1.0",
            plan_id="plan-paper-llm-market-loss",
            persona_capital_binding_id="pcb-paper-llm-market-loss",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "ADBE", "quantity": 6.0, "price": 122.0}
    ]

    pending_store.enqueue(signals[1])
    exit_snapshot = runtime.drain_once()

    assert exit_snapshot["status"] == "ok"
    assert exit_snapshot["paper_state"]["processed_signal_count"] == 2
    assert exit_snapshot["paper_state"]["execution_event_count"] == 2
    assert exit_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "llm-adbe-market-entry-079",
        "llm-adbe-market-exit-079",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "liquidate"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [6.0, -6.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [122.0, 118.5]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metadata"]["alpha_source"] == "llm_research_market_loss_entry"
    assert entry_fill["metadata"]["model_id"] == "gpt-alpha-e2e-079"
    assert entry_fill["metadata"]["order_type"] == "MARKET"
    assert exit_fill["metadata"]["alpha_source"] == "llm_research_market_loss_exit"
    assert exit_fill["metadata"]["llm_decision_id"] == "decision-e2e-079"
    assert exit_fill["metadata"]["market_price"] == 118.5
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == -21.0
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-079")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_exit = records_by_id[stored_fills[1]["event_id"]]
    assert recovered_exit["alpha_context"]["model_id"] == "gpt-alpha-e2e-079"
    assert recovered_exit["alpha_context"]["llm_response_id"] == "response-e2e-079"
    assert recovered_exit["order_context"]["fill_quantity"] == -6.0
    assert recovered_exit["order_context"]["fill_price"] == 118.5
    assert recovered_exit["order_context"]["order_type"] == "MARKET"
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == -21.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-market-loss-sponsor",
        contributing_persona_ids=["persona-llm-market-loss-ops"],
        summary=(
            "ADBE LLM market long roundtrip consumed fetched closes, opened 6 shares at 122.0, "
            "closed at 118.5 for -21.0 paper PnL, recovered feedback, and wrote loss evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-market-loss-ops",
                "summary": "LLM MARKET loss feedback preserved model, prompt, exit fill, and negative PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_market_loss", "paper_fill", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            stored_fills[1]["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_fills[1]["event_id"],
            "event_type": stored_fills[1]["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_fills[1]),
        }
    )
    writeback_payload["tags"].extend(["llm_market_loss", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ADBE LLM market negative PnL",
        tags=["llm_market_loss", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    exit_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert pnl_lineage["order_context"]["pnl"] == -21.0
    assert exit_lineage["alpha_context"]["llm_decision_id"] == "decision-e2e-079"
    assert exit_lineage["order_context"]["fill_price"] == 118.5

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-market-loss-ops",
        query="llm market loss negative pnl",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_pnl_lineage = next(item["lineage"] for item in persona_evidence if item["event_type"] == "pnl_snapshot")
    assert persona_pnl_lineage["strategy_id"] == strategy_id
    assert persona_pnl_lineage["order_context"]["pnl"] == -21.0


def _llm_market_loss_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common: dict[str, Any] = {
        "strategy_id": strategy_id,
        "symbol": "ADBE.US",
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-market-loss-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.74,
        "order_type": "MARKET",
        "extra_metadata": LLM_REFS,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-05"],
            signal_id="llm-adbe-market-entry-079",
            action="BUY",
            direction="LONG",
            quantity=6.0,
            alpha_source="llm_research_market_loss_entry",
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-08"],
            signal_id="llm-adbe-market-exit-079",
            action="SELL",
            direction="LONG",
            quantity=6.0,
            alpha_source="llm_research_market_loss_exit",
            **common,
        ),
    ]
