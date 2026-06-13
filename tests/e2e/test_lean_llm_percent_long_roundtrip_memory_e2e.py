from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
    "model_id": "gpt-alpha-e2e-081",
    "prompt_bundle_id": "prompt-bundle-e2e-081",
    "llm_prompt_id": "prompt-e2e-081",
    "llm_response_id": "response-e2e-081",
    "llm_decision_id": "decision-e2e-081",
    "research_note_ref": "memory://research/e2e-081/now-percent-note",
    "llm_note_ref": "memory://llm/e2e-081/now-percent-decision",
    "research_data_ref": ["research://now/e2e-081/workflow-demand"],
}


def test_llm_percent_long_roundtrip_feedback_performance_memory_readback_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    loop_id = "081"
    strategy_id = "strategy-llm-percent-long-roundtrip"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-081-us-prices",
                provider="E2E Loop 081 Static US Prices",
                dataset="us_llm_percent_long_price_daily",
                feature_target="features/llm_percent_long_roundtrip_inputs",
                schema_hash="us_llm_percent_long_price_daily.e2e_loop_081.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-081-now-entry",
                        dataset="us_llm_percent_long_price_daily",
                        symbol="NOW",
                        trade_date="2026-06-09",
                        close=65.0,
                        volume=1_070_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-081-now-close",
                        dataset="us_llm_percent_long_price_daily",
                        symbol="NOW",
                        trade_date="2026-06-10",
                        close=67.0,
                        volume=1_110_000,
                    ),
                ],
                "next_watermark": "2026-06-10T21:13:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-081-us-prices",
            "trace_id": "trace-e2e-loop-081-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-09", 65.0),
        ("2026-06-10", 67.0),
    ]

    signals = _llm_percent_long_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-percent-long",
        artifact_version="8.3.0",
        plan_id="plan-paper-llm-percent-long",
        persona_capital_binding_id="pcb-paper-llm-percent-long",
        default_strategy_id="paper-runtime-llm-percent-long",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-percent-long",
            artifact_version="8.3.0",
            plan_id="plan-paper-llm-percent-long",
            persona_capital_binding_id="pcb-paper-llm-percent-long",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "NOW", "quantity": pytest.approx(80.0), "price": 65.0}
    ]

    pending_store.enqueue(signals[1])
    close_snapshot = runtime.drain_once()

    assert close_snapshot["status"] == "ok"
    assert close_snapshot["paper_state"]["processed_signal_count"] == 2
    assert close_snapshot["paper_state"]["execution_event_count"] == 2
    assert close_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "llm-now-percent-long-entry-081",
        "llm-now-percent-long-close-081",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["set_holdings", "set_holdings"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [
        pytest.approx(80.0),
        pytest.approx(-80.0),
    ]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [65.0, 67.0]

    entry_fill, close_fill = fill_events
    assert entry_fill["metadata"]["alpha_source"] == "llm_research_percent_long_entry"
    assert entry_fill["metadata"]["confidence_score"] == 0.65
    assert entry_fill["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert entry_fill["metadata"]["requested_quantity"] == 0.08
    assert entry_fill["metadata"]["model_id"] == "gpt-alpha-e2e-081"
    assert close_fill["metadata"]["alpha_source"] == "llm_research_percent_long_close"
    assert close_fill["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert close_fill["metadata"]["requested_quantity"] == 0.5
    assert close_fill["metadata"]["llm_decision_id"] == "decision-e2e-081"
    assert close_fill["metadata"]["market_price"] == 67.0
    assert close_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == pytest.approx(160.0)
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-081")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_entry = records_by_id[stored_fills[0]["event_id"]]
    recovered_close = records_by_id[stored_fills[1]["event_id"]]
    assert recovered_entry["alpha_context"]["model_id"] == "gpt-alpha-e2e-081"
    assert recovered_entry["alpha_context"]["confidence_score"] == 0.65
    assert recovered_entry["order_context"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert recovered_entry["order_context"]["requested_quantity"] == 0.08
    assert recovered_entry["order_context"]["fill_quantity"] == pytest.approx(80.0)
    assert recovered_close["alpha_context"]["llm_response_id"] == "response-e2e-081"
    assert recovered_close["order_context"]["fill_quantity"] == pytest.approx(-80.0)
    assert recovered_close["order_context"]["fill_price"] == 67.0
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == pytest.approx(160.0)

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-percent-long-sponsor",
        contributing_persona_ids=["persona-llm-percent-long-ops"],
        summary=(
            "NOW LLM percent-portfolio alpha opened an 8 percent target scaled by confidence to 80 shares, "
            "closed through SetHoldings(0) at 67.0 for 160.0 paper PnL, recovered feedback, "
            "and wrote percent sizing evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-percent-long-ops",
                "summary": "LLM percent long feedback preserved requested percent, confidence-scaled fill, close, and PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_percent_long", "paper_fill", "paper_performance", "confidence_scaling"],
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
        ["llm_percent_long", "paper_fill", "paper_performance", "confidence_scaling"]
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
        query="NOW LLM percent SetHoldings confidence PnL",
        tags=["llm_percent_long", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    fill_lineages = [item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated"]
    assert pnl_lineage["order_context"]["pnl"] == pytest.approx(160.0)
    assert fill_lineages[0]["alpha_context"]["confidence_score"] == 0.65
    assert fill_lineages[0]["order_context"]["requested_quantity"] == 0.08
    assert fill_lineages[0]["order_context"]["fill_quantity"] == pytest.approx(80.0)
    assert fill_lineages[1]["alpha_context"]["llm_decision_id"] == "decision-e2e-081"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-percent-long-ops",
        query="percent confidence scaled close pnl",
        tags=["confidence_scaling"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_pnl_lineage = next(item["lineage"] for item in persona_evidence if item["event_type"] == "pnl_snapshot")
    assert persona_pnl_lineage["strategy_id"] == strategy_id
    assert persona_pnl_lineage["order_context"]["pnl"] == pytest.approx(160.0)


def _llm_percent_long_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common: dict[str, Any] = {
        "strategy_id": strategy_id,
        "symbol": "NOW.US",
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-llm-percent-long-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.65,
        "order_type": "MARKET",
        "extra_metadata": LLM_REFS,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-09"],
            signal_id="llm-now-percent-long-entry-081",
            action="BUY",
            direction="LONG",
            quantity=0.08,
            alpha_source="llm_research_percent_long_entry",
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-10"],
            signal_id="llm-now-percent-long-close-081",
            action="SELL",
            direction="LONG",
            quantity=0.5,
            alpha_source="llm_research_percent_long_close",
            **common,
        ),
    ]
