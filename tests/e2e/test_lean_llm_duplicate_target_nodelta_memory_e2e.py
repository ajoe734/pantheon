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
    "model_id": "gpt-alpha-e2e-088",
    "prompt_bundle_id": "prompt-bundle-e2e-088",
    "llm_prompt_id": "prompt-e2e-088",
    "llm_response_id": "response-e2e-088",
    "llm_decision_id": "decision-e2e-088",
    "research_note_ref": "memory://research/e2e-088/ddog-duplicate-target",
    "llm_note_ref": "memory://llm/e2e-088/ddog-rebalance-repeat",
    "research_data_ref": ["research://ddog/e2e-088/platform-demand"],
}


def test_llm_duplicate_target_no_delta_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "088"
    strategy_id = "strategy-llm-duplicate-target-nodelta"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-088-us-prices",
                provider="E2E Loop 088 Static US Prices",
                dataset="us_llm_duplicate_target_price_daily",
                feature_target="features/llm_duplicate_target_inputs",
                schema_hash="us_llm_duplicate_target_price_daily.e2e_loop_088.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-088-ddog",
                        dataset="us_llm_duplicate_target_price_daily",
                        symbol="DDOG",
                        trade_date="2026-06-10",
                        close=200.0,
                        volume=1_030_000,
                    )
                ],
                "next_watermark": "2026-06-10T21:20:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-088-us-prices",
            "trace_id": "trace-e2e-loop-088-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "DDOG"
    assert row["metadata"]["close"] == 200.0

    first_signal = _llm_percent_signal(
        row,
        signal_id="llm-ddog-target-entry-088",
        strategy_id=strategy_id,
        normalized_ref_uri=normalized_ref["uri"],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        alpha_source="llm_research_duplicate_target_entry",
    )
    duplicate_signal = _llm_percent_signal(
        row,
        signal_id="llm-ddog-target-duplicate-088",
        strategy_id=strategy_id,
        normalized_ref_uri=normalized_ref["uri"],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        alpha_source="llm_research_duplicate_target_repeat",
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-duplicate-target",
        artifact_version="9.0.0",
        plan_id="plan-paper-llm-duplicate-target",
        persona_capital_binding_id="pcb-paper-llm-duplicate-target",
        default_strategy_id="paper-runtime-llm-duplicate-target",
    )
    pending_store = InMemoryPendingSignalStore([first_signal])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-duplicate-target",
            artifact_version="9.0.0",
            plan_id="plan-paper-llm-duplicate-target",
            persona_capital_binding_id="pcb-paper-llm-duplicate-target",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first_snapshot = runtime.drain_once()
    assert first_snapshot["status"] == "ok"
    assert first_snapshot["paper_state"]["processed_signal_count"] == 1
    assert first_snapshot["paper_state"]["execution_event_count"] == 1

    pending_store.enqueue(duplicate_signal)
    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    assert snapshot["paper_state"]["positions"] == [
        {"symbol": "DDOG", "quantity": pytest.approx(50.0), "price": 200.0}
    ]
    assert [event["event_type"] for event in snapshot["paper_state"]["recent_order_events"]] == [
        "paper_fill_simulated",
        "paper_order_simulated",
    ]

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    noop_event = next(event for event in telemetry.events if event["event_type"] == "paper_order_simulated")
    assert fill_event["metadata"]["signal_id"] == "llm-ddog-target-entry-088"
    assert fill_event["metrics"]["action"] == "set_holdings"
    assert fill_event["metrics"]["fill_quantity"] == pytest.approx(50.0)
    assert fill_event["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert fill_event["metadata"]["requested_quantity"] == 0.10
    assert fill_event["metadata"]["model_id"] == "gpt-alpha-e2e-088"

    assert noop_event["metadata"]["signal_id"] == "llm-ddog-target-duplicate-088"
    assert noop_event["metrics"]["action"] == "set_holdings_no_delta_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 0.10
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["noop_reason"] == "set_holdings_no_delta"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["position_quantity"] == pytest.approx(50.0)
    assert noop_event["metadata"]["target_quantity"] == pytest.approx(50.0)
    assert noop_event["metadata"]["target_percent"] == 0.10
    assert noop_event["metadata"]["llm_decision_id"] == "decision-e2e-088"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-088")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill = records_by_id[stored_fill["event_id"]]
    recovered_noop = records_by_id[stored_noop["event_id"]]
    assert recovered_fill["order_context"]["fill_quantity"] == pytest.approx(50.0)
    assert recovered_noop["alpha_context"]["llm_response_id"] == "response-e2e-088"
    assert recovered_noop["order_context"]["noop_reason"] == "set_holdings_no_delta"
    assert recovered_noop["order_context"]["target_quantity"] == pytest.approx(50.0)
    assert recovered_noop["order_context"]["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_rate"] == 0.5

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_noop,
        sponsor_persona_id="persona-llm-duplicate-target-sponsor",
        contributing_persona_ids=["persona-llm-duplicate-target-ops"],
        summary=(
            "DDOG LLM duplicate target consumed fetched market data, filled the first 10 percent target, "
            "then returned SetHoldings no-delta feedback for the repeated target, recovered adapter records, "
            "and wrote duplicate-target evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-duplicate-target-ops",
                "summary": "LLM duplicate target feedback preserved initial fill, no-delta context, and fill-rate evidence.",
                "proposal_ids": [first_signal["signal_id"], duplicate_signal["signal_id"]],
                "tags": ["llm_duplicate_target", "setholdings_no_delta", "paper_performance"],
            }
        ],
        proposal_ids=[
            first_signal["signal_id"],
            duplicate_signal["signal_id"],
            stored_fill["event_id"],
            stored_noop["event_id"],
            stored_pnl["event_id"],
        ],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_fill["event_id"],
            "event_type": stored_fill["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_fill),
        }
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_pnl["event_id"],
            "event_type": stored_pnl["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_pnl),
        }
    )
    writeback_payload["tags"].extend(["llm_duplicate_target", "setholdings_no_delta", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="DDOG LLM duplicate target no delta",
        tags=["llm_duplicate_target", "setholdings_no_delta"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    assert noop_lineage["alpha_context"]["llm_decision_id"] == "decision-e2e-088"
    assert noop_lineage["order_context"]["noop_reason"] == "set_holdings_no_delta"
    assert noop_lineage["order_context"]["target_quantity"] == pytest.approx(50.0)
    assert pnl_lineage["order_context"]["fill_rate"] == 0.5

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-duplicate-target-ops",
        query="duplicate target no delta",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == strategy_id
    assert persona_lineage["order_context"]["noop_reason"] == "set_holdings_no_delta"


def _llm_percent_signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    strategy_id: str,
    normalized_ref_uri: str,
    ingest_run_id: str,
    alpha_source: str,
) -> dict[str, Any]:
    return signal_from_market_row(
        row,
        signal_id=signal_id,
        strategy_id=strategy_id,
        symbol="DDOG.US",
        action="BUY",
        direction="LONG",
        quantity=0.10,
        quantity_type="PERCENT_PORTFOLIO",
        source_worker="mock-llm-duplicate-target-normalizer",
        alpha_source=alpha_source,
        normalized_ref_uris=[normalized_ref_uri],
        ingest_run_id=ingest_run_id,
        confidence_score=1.0,
        order_type="MARKET",
        extra_metadata=LLM_REFS,
    )
