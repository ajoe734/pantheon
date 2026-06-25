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
    "model_id": "gpt-alpha-e2e-092",
    "prompt_bundle_id": "prompt-bundle-e2e-092",
    "llm_prompt_id": "prompt-e2e-092",
    "llm_response_id": "response-e2e-092",
    "llm_decision_id": "decision-e2e-092",
    "research_note_ref": "memory://research/e2e-092/partial-rebalance-timeout",
}


def test_llm_rebalance_partial_timeout_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "092"
    strategy_id = "strategy-llm-partial-rebalance-timeout"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-092-us-prices",
                provider="E2E Loop 092 Static US Prices",
                dataset="us_llm_partial_rebalance_price_daily",
                feature_target="features/llm_partial_rebalance_inputs",
                schema_hash="us_llm_partial_rebalance_price_daily.e2e_loop_092.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-092-xle",
                        dataset="us_llm_partial_rebalance_price_daily",
                        symbol="XLE",
                        trade_date="2026-06-11",
                        close=250.0,
                        volume=1_250_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-092-xlk",
                        dataset="us_llm_partial_rebalance_price_daily",
                        symbol="XLK",
                        trade_date="2026-06-11",
                        close=200.0,
                        volume=1_750_000,
                    ),
                ],
                "next_watermark": "2026-06-11T21:27:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-092-us-prices",
            "trace_id": "trace-e2e-loop-092-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in rows} == {"XLE", "XLK"}

    signal = _partial_rebalance_signal(
        rows,
        strategy_id=strategy_id,
        normalized_ref_uri=normalized_ref["uri"],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-partial-rebalance",
        artifact_version="9.4.0",
        plan_id="plan-paper-llm-partial-rebalance",
        persona_capital_binding_id="pcb-paper-llm-partial-rebalance",
        default_strategy_id="paper-runtime-llm-partial-rebalance",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-partial-rebalance",
            artifact_version="9.4.0",
            plan_id="plan-paper-llm-partial-rebalance",
            persona_capital_binding_id="pcb-paper-llm-partial-rebalance",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first = runtime.drain_once()
    second = runtime.drain_once()
    third = runtime.drain_once()

    assert first["paper_state"]["processed_signal_count"] == 0
    assert first["paper_state"]["execution_event_count"] == 0
    assert second["paper_state"]["processed_signal_count"] == 0
    assert second["paper_state"]["execution_event_count"] == 0
    assert third["status"] == "ok"
    assert third["paper_state"]["processed_signal_count"] == 1
    assert third["paper_state"]["execution_event_count"] == 1
    assert len(third["paper_state"]["positions"]) == 1
    position = third["paper_state"]["positions"][0]
    assert position["symbol"] == "XLE"
    assert position["quantity"] == pytest.approx(30.0)
    assert position["price"] == 250.0

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert noop_events == []
    assert len(fill_events) == 1
    fill_event = fill_events[0]
    assert fill_event["metrics"]["action"] == "set_holdings"
    assert fill_event["metrics"]["fill_quantity"] == pytest.approx(30.0)
    assert fill_event["metrics"]["fill_price"] == 250.0
    assert fill_event["metadata"]["signal_id"] == "llm-partial-rebalance-xle-092"
    assert fill_event["metadata"]["run_id"] == "llm-rebalance-partial-run-092"
    assert fill_event["metadata"]["model_id"] == "gpt-alpha-e2e-092"
    assert fill_event["metadata"]["alpha_source"] == "llm_partial_rebalance_timeout"
    assert fill_event["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert fill_event["metadata"]["requested_quantity"] == 0.10
    assert fill_event["metadata"]["market_price"] == 250.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["avg_slippage_bps"] == 0.0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_fills = recovered_adapter.query_telemetry(
        strategy_id=strategy_id,
        event_type="paper_fill_simulated",
        promotion_state="paper",
        limit=3,
    )
    recovered_pnls = recovered_adapter.query_telemetry(
        strategy_id=strategy_id,
        event_type="pnl_snapshot",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_fills] == [stored_fill["event_id"]]
    assert [event["event_id"] for event in recovered_pnls] == [stored_pnl["event_id"]]

    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-092")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    fill_lineage = records_by_id[stored_fill["event_id"]]
    assert fill_lineage["alpha_context"]["run_id"] == "llm-rebalance-partial-run-092"
    assert fill_lineage["alpha_context"]["model_id"] == "gpt-alpha-e2e-092"
    assert fill_lineage["alpha_context"]["normalized_data_ref"] == [normalized_ref["uri"]]
    assert fill_lineage["alpha_context"]["ingest_run_id"] == ingest_body["run"]["ingest_run_id"]
    assert fill_lineage["order_context"]["fill_quantity"] == pytest.approx(30.0)
    assert fill_lineage["order_context"]["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_rate"] == 1.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-llm-partial-rebalance-sponsor",
        contributing_persona_ids=["persona-llm-partial-rebalance-ops"],
        summary=(
            "LLM rebalance run llm-rebalance-partial-run-092 fetched XLE and XLK market data, "
            "but only emitted the XLE target. LEAN buffered the incomplete batch until timeout, "
            "executed the partial SetHoldings order, recovered adapter feedback, and wrote PnL evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-partial-rebalance-ops",
                "summary": "Partial LLM rebalance feedback preserved model lineage, missing XLK context, and fill-rate metrics.",
                "proposal_ids": [signal["signal_id"], "llm-rebalance-partial-run-092"],
                "tags": ["llm_partial_rebalance_timeout", "run_id_timeout", "paper_fill"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_pnl["event_id"],
            "event_type": stored_pnl["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_pnl),
        }
    )
    writeback_payload["tags"].extend(["llm_partial_rebalance_timeout", "run_id_timeout", "paper_fill"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="LLM partial rebalance timeout XLE missing XLK fill rate",
        tags=["llm_partial_rebalance_timeout", "run_id_timeout"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert len(evidence) == 2
    stored_fill_lineage = evidence[0]["lineage"]
    assert stored_fill_lineage["alpha_context"]["signal_id"] == "llm-partial-rebalance-xle-092"
    assert stored_fill_lineage["alpha_context"]["model_id"] == "gpt-alpha-e2e-092"
    assert stored_fill_lineage["order_context"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert stored_fill_lineage["order_context"]["requested_quantity"] == 0.10
    assert stored_fill_lineage["order_context"]["fill_quantity"] == pytest.approx(30.0)
    assert stored_fill_lineage["order_context"]["is_real_order"] is False
    stored_pnl_lineage = evidence[1]["lineage"]
    assert stored_pnl_lineage["order_context"]["fill_rate"] == 1.0
    assert stored_pnl_lineage["order_context"]["open_position_count"] == 1

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-partial-rebalance-ops",
        query="partial rebalance missing XLK",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert persona_evidence[0]["lineage"]["alpha_context"]["run_id"] == "llm-rebalance-partial-run-092"


def _partial_rebalance_signal(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uri: str,
    ingest_run_id: str,
) -> dict[str, Any]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    signal = signal_from_market_row(
        by_symbol["XLE"],
        signal_id="llm-partial-rebalance-xle-092",
        strategy_id=strategy_id,
        symbol="XLE.US",
        action="BUY",
        direction="LONG",
        quantity=0.10,
        quantity_type="PERCENT_PORTFOLIO",
        source_worker="mock-llm-partial-rebalance-normalizer",
        alpha_source="llm_partial_rebalance_timeout",
        normalized_ref_uris=[normalized_ref_uri],
        ingest_run_id=ingest_run_id,
        confidence_score=0.75,
        order_type="MARKET",
        extra_metadata={
            **LLM_REFS,
            "expected_rebalance_symbols": ["XLE.US", "XLK.US"],
            "missing_rebalance_symbols": ["XLK.US"],
        },
    )
    signal["run_id"] = "llm-rebalance-partial-run-092"
    signal["metadata"]["run_id"] = "llm-rebalance-partial-run-092"
    return signal
