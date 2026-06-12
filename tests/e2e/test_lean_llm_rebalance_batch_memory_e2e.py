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
    "model_id": "gpt-alpha-e2e-091",
    "prompt_bundle_id": "prompt-bundle-e2e-091",
    "llm_prompt_id": "prompt-e2e-091",
    "llm_response_id": "response-e2e-091",
    "llm_decision_id": "decision-e2e-091",
    "research_note_ref": "memory://research/e2e-091/rebalance-batch",
}


def test_llm_rebalance_batch_timeout_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "091"
    strategy_id = "strategy-llm-rebalance-batch"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-091-us-prices",
                provider="E2E Loop 091 Static US Prices",
                dataset="us_llm_rebalance_batch_price_daily",
                feature_target="features/llm_rebalance_batch_inputs",
                schema_hash="us_llm_rebalance_batch_price_daily.e2e_loop_091.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-091-qqq",
                        dataset="us_llm_rebalance_batch_price_daily",
                        symbol="QQQ",
                        trade_date="2026-06-10",
                        close=300.0,
                        volume=1_900_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-091-spy",
                        dataset="us_llm_rebalance_batch_price_daily",
                        symbol="SPY",
                        trade_date="2026-06-10",
                        close=400.0,
                        volume=2_100_000,
                    ),
                ],
                "next_watermark": "2026-06-10T21:23:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-091-us-prices",
            "trace_id": "trace-e2e-loop-091-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in rows} == {"QQQ", "SPY"}

    signals = _rebalance_signals(
        rows,
        strategy_id=strategy_id,
        normalized_ref_uri=normalized_ref["uri"],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-rebalance-batch",
        artifact_version="9.3.0",
        plan_id="plan-paper-llm-rebalance-batch",
        persona_capital_binding_id="pcb-paper-llm-rebalance-batch",
        default_strategy_id="paper-runtime-llm-rebalance-batch",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(signals),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-rebalance-batch",
            artifact_version="9.3.0",
            plan_id="plan-paper-llm-rebalance-batch",
            persona_capital_binding_id="pcb-paper-llm-rebalance-batch",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first = runtime.drain_once()
    second = runtime.drain_once()
    third = runtime.drain_once()

    assert first["paper_state"]["processed_signal_count"] == 0
    assert second["paper_state"]["processed_signal_count"] == 0
    assert third["status"] == "ok"
    assert third["paper_state"]["processed_signal_count"] == 2
    assert third["paper_state"]["execution_event_count"] == 2
    positions = {position["symbol"]: position for position in third["paper_state"]["positions"]}
    assert positions["QQQ"]["quantity"] == pytest.approx(36.0)
    assert positions["QQQ"]["price"] == 300.0
    assert positions["SPY"]["quantity"] == pytest.approx(16.0)
    assert positions["SPY"]["price"] == 400.0

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert {event["metadata"]["signal_id"] for event in fill_events} == {
        "llm-rebalance-qqq-091",
        "llm-rebalance-spy-091",
    }
    assert {event["metadata"]["run_id"] for event in fill_events} == {"llm-rebalance-run-091"}
    assert {event["metadata"]["model_id"] for event in fill_events} == {"gpt-alpha-e2e-091"}
    assert {event["metrics"]["action"] for event in fill_events} == {"set_holdings"}
    fills_by_signal = {event["metadata"]["signal_id"]: event for event in fill_events}
    assert fills_by_signal["llm-rebalance-qqq-091"]["metrics"]["fill_quantity"] == pytest.approx(36.0)
    assert fills_by_signal["llm-rebalance-spy-091"]["metrics"]["fill_quantity"] == pytest.approx(16.0)

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 2

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(event, strategy_id=strategy_id, promotion_state="paper")
        for event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-091")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    assert {records_by_id[event["event_id"]]["alpha_context"]["run_id"] for event in stored_fills} == {
        "llm-rebalance-run-091"
    }
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["open_position_count"] == 2

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-rebalance-sponsor",
        contributing_persona_ids=["persona-llm-rebalance-ops"],
        summary=(
            "LLM rebalance run llm-rebalance-run-091 buffered QQQ and SPY targets until timeout, "
            "then filled both SetHoldings orders, recovered adapter feedback, and wrote batch evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-rebalance-ops",
                "summary": "LLM rebalance batch feedback preserved shared run_id, both fills, and open-position metrics.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_rebalance_batch", "paper_fill", "paper_performance"],
            }
        ],
        proposal_ids=[
            "llm-rebalance-run-091",
            *(signal["signal_id"] for signal in signals),
            *(event["event_id"] for event in stored_fills),
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
    writeback_payload["tags"].extend(["llm_rebalance_batch", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="LLM rebalance batch QQQ SPY run_id fills",
        tags=["llm_rebalance_batch", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    fill_lineages = [item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated"]
    assert {lineage["alpha_context"]["signal_id"] for lineage in fill_lineages} == {
        "llm-rebalance-qqq-091",
        "llm-rebalance-spy-091",
    }
    assert {lineage["alpha_context"]["model_id"] for lineage in fill_lineages} == {"gpt-alpha-e2e-091"}

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-rebalance-ops",
        query="rebalance batch shared run id",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert any(item["event_type"] == "pnl_snapshot" for item in persona_evidence)


def _rebalance_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uri: str,
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    return [
        _rebalance_signal(
            by_symbol["QQQ"],
            signal_id="llm-rebalance-qqq-091",
            strategy_id=strategy_id,
            quantity=0.12,
            confidence_score=0.90,
            normalized_ref_uri=normalized_ref_uri,
            ingest_run_id=ingest_run_id,
        ),
        _rebalance_signal(
            by_symbol["SPY"],
            signal_id="llm-rebalance-spy-091",
            strategy_id=strategy_id,
            quantity=0.08,
            confidence_score=0.80,
            normalized_ref_uri=normalized_ref_uri,
            ingest_run_id=ingest_run_id,
        ),
    ]


def _rebalance_signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    strategy_id: str,
    quantity: float,
    confidence_score: float,
    normalized_ref_uri: str,
    ingest_run_id: str,
) -> dict[str, Any]:
    signal = signal_from_market_row(
        row,
        signal_id=signal_id,
        strategy_id=strategy_id,
        symbol=f"{row['metadata']['symbol']}.US",
        action="BUY",
        direction="LONG",
        quantity=quantity,
        quantity_type="PERCENT_PORTFOLIO",
        source_worker="mock-llm-rebalance-normalizer",
        alpha_source="llm_rebalance_batch",
        normalized_ref_uris=[normalized_ref_uri],
        ingest_run_id=ingest_run_id,
        confidence_score=confidence_score,
        order_type="MARKET",
        extra_metadata=LLM_REFS,
    )
    signal["run_id"] = "llm-rebalance-run-091"
    signal["metadata"]["run_id"] = "llm-rebalance-run-091"
    return signal
