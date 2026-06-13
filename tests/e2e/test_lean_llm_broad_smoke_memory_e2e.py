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
    "model_id": "gpt-alpha-e2e-100",
    "prompt_bundle_id": "prompt-bundle-e2e-100",
    "llm_prompt_id": "prompt-e2e-100",
    "llm_response_id": "response-e2e-100",
    "llm_decision_id": "decision-e2e-100",
    "research_note_ref": "memory://research/e2e-100/broad-smoke",
    "llm_note_ref": "memory://llm/e2e-100/broad-smoke",
    "research_data_ref": ["research://broad/e2e-100/final-smoke"],
}


def test_llm_broad_smoke_fill_noops_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "100"
    strategy_id = "strategy-llm-broad-smoke"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-100-us-prices",
                provider="E2E Loop 100 Static US Prices",
                dataset="us_llm_broad_smoke_price_daily",
                feature_target="features/llm_broad_smoke_inputs",
                schema_hash="us_llm_broad_smoke_price_daily.e2e_loop_100.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-100-app-entry",
                        dataset="us_llm_broad_smoke_price_daily",
                        symbol="APP",
                        trade_date="2026-06-09",
                        close=20.0,
                        volume=1_010_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-100-app-hold",
                        dataset="us_llm_broad_smoke_price_daily",
                        symbol="APP",
                        trade_date="2026-06-10",
                        close=21.0,
                        volume=1_120_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-100-shop-empty-close",
                        dataset="us_llm_broad_smoke_price_daily",
                        symbol="SHOP",
                        trade_date="2026-06-10",
                        close=50.0,
                        volume=1_220_000,
                    ),
                ],
                "next_watermark": "2026-06-10T22:10:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-100-us-prices",
            "trace_id": "trace-e2e-loop-100-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["date"], row["metadata"]["close"]) for row in rows] == [
        ("APP", "2026-06-09", 20.0),
        ("APP", "2026-06-10", 21.0),
        ("SHOP", "2026-06-10", 50.0),
    ]

    signals = _broad_smoke_signals(
        rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-broad-smoke",
        artifact_version="10.0.0",
        plan_id="plan-paper-llm-broad-smoke",
        persona_capital_binding_id="pcb-paper-llm-broad-smoke",
        default_strategy_id="paper-runtime-llm-broad-smoke",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-broad-smoke",
            artifact_version="10.0.0",
            plan_id="plan-paper-llm-broad-smoke",
            persona_capital_binding_id="pcb-paper-llm-broad-smoke",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["processed_signal_count"] == 1
    assert entry_snapshot["paper_state"]["execution_event_count"] == 1
    assert entry_snapshot["paper_state"]["positions"] == [{"symbol": "APP", "quantity": 3.0, "price": 20.0}]

    pending_store.enqueue(signals[1])
    hold_snapshot = runtime.drain_once()
    assert hold_snapshot["status"] == "ok"
    assert hold_snapshot["paper_state"]["processed_signal_count"] == 2
    assert hold_snapshot["paper_state"]["execution_event_count"] == 2
    assert hold_snapshot["paper_state"]["positions"] == [{"symbol": "APP", "quantity": 3.0, "price": 21.0}]

    pending_store.enqueue(signals[2])
    close_empty_snapshot = runtime.drain_once()
    assert close_empty_snapshot["status"] == "ok"
    assert close_empty_snapshot["paper_state"]["processed_signal_count"] == 3
    assert close_empty_snapshot["paper_state"]["execution_event_count"] == 3
    assert close_empty_snapshot["paper_state"]["positions"] == [{"symbol": "APP", "quantity": 3.0, "price": 21.0}]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert len(noop_events) == 2
    fill_event = fill_events[0]
    hold_noop, cash_close_noop = noop_events
    assert fill_event["metadata"]["signal_id"] == "llm-app-entry-100"
    assert fill_event["metrics"]["fill_quantity"] == 3.0
    assert fill_event["metrics"]["fill_price"] == 20.0
    assert hold_noop["metadata"]["signal_id"] == "llm-app-hold-100"
    assert hold_noop["metadata"]["noop_reason"] == "hold_signal"
    assert hold_noop["metadata"]["market_price"] == 21.0
    assert cash_close_noop["metadata"]["signal_id"] == "llm-shop-cash-close-empty-100"
    assert cash_close_noop["metadata"]["noop_reason"] == "liquidate_without_position"
    assert cash_close_noop["metadata"]["quantity_type"] == "CASH_VALUE"
    assert cash_close_noop["metadata"]["requested_quantity"] == 500.0
    assert cash_close_noop["metadata"]["market_price"] == 50.0
    assert cash_close_noop["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 3.0
    assert pnl_event["metrics"]["processed_signal_count"] == 3
    assert pnl_event["metrics"]["execution_event_count"] == 3
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == pytest.approx(1 / 3)
    assert pnl_event["metrics"]["open_position_count"] == 1

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(fill_event, strategy_id=strategy_id, promotion_state="paper")
    stored_noops = [
        writer_adapter.ingest_telemetry_event(event, strategy_id=strategy_id, promotion_state="paper")
        for event in noop_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-100")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    assert records_by_id[stored_fill["event_id"]]["order_context"]["fill_quantity"] == 3.0
    assert records_by_id[stored_fill["event_id"]]["alpha_context"]["model_id"] == "gpt-alpha-e2e-100"
    assert records_by_id[stored_noops[0]["event_id"]]["order_context"]["noop_reason"] == "hold_signal"
    assert records_by_id[stored_noops[1]["event_id"]]["order_context"]["noop_reason"] == "liquidate_without_position"
    assert records_by_id[stored_noops[1]["event_id"]]["order_context"]["requested_quantity"] == 500.0
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_rate"] == pytest.approx(1 / 3)
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 3.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-broad-smoke-sponsor",
        contributing_persona_ids=["persona-llm-broad-smoke-ops"],
        summary=(
            "Final LLM broad smoke fetched APP and SHOP prices, filled an APP entry, emitted an APP HOLD no-op, "
            "then emitted a SHOP cash close no-position no-op. Recovered feedback preserved fill/noop lineage, "
            "1/3 fill-rate, open APP position, and 3.0 paper PnL in memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-broad-smoke-ops",
                "summary": "Broad smoke feedback preserved fill, multiple no-ops, adapter recovery, and PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_broad_smoke", "paper_noop", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
            signals[2]["signal_id"],
            stored_fill["event_id"],
            *(event["event_id"] for event in stored_noops),
            stored_pnl["event_id"],
        ],
    )
    for stored_event in [stored_fill, *stored_noops]:
        writeback_payload["runtime_telemetry_evidence"].append(
            {
                "ref_type": "telemetry_event",
                "ref_id": stored_event["event_id"],
                "event_type": stored_event["event_type"],
                "lineage": recovered_adapter.build_lineage_record(stored_event),
            }
        )
    writeback_payload["tags"].extend(["llm_broad_smoke", "paper_noop", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="final LLM broad smoke fill noops fill rate pnl",
        tags=["llm_broad_smoke", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    fill_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    noop_lineages = [item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated"]
    assert pnl_lineage["order_context"]["fill_rate"] == pytest.approx(1 / 3)
    assert pnl_lineage["order_context"]["pnl"] == 3.0
    assert fill_lineage["order_context"]["fill_quantity"] == 3.0
    assert {lineage["order_context"]["noop_reason"] for lineage in noop_lineages} == {
        "hold_signal",
        "liquidate_without_position",
    }

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-broad-smoke-ops",
        query="final broad smoke fill noops",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_pnl_lineage = next(item["lineage"] for item in persona_evidence if item["event_type"] == "pnl_snapshot")
    assert persona_pnl_lineage["strategy_id"] == strategy_id
    assert persona_pnl_lineage["order_context"]["open_position_count"] == 1


def _broad_smoke_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_key = {(row["metadata"]["symbol"], row["metadata"]["date"]): row for row in rows}
    common: dict[str, Any] = {
        "strategy_id": strategy_id,
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-broad-smoke-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.89,
        "order_type": "MARKET",
        "extra_metadata": LLM_REFS,
    }
    app_entry = signal_from_market_row(
        rows_by_key[("APP", "2026-06-09")],
        signal_id="llm-app-entry-100",
        symbol="APP.US",
        action="BUY",
        direction="LONG",
        quantity=3.0,
        alpha_source="llm_broad_smoke_entry",
        **common,
    )
    app_hold = signal_from_market_row(
        rows_by_key[("APP", "2026-06-10")],
        signal_id="llm-app-hold-100",
        symbol="APP.US",
        action="HOLD",
        direction="LONG",
        quantity=0.0,
        alpha_source="llm_broad_smoke_hold",
        **common,
    )
    shop_close_empty = signal_from_market_row(
        rows_by_key[("SHOP", "2026-06-10")],
        signal_id="llm-shop-cash-close-empty-100",
        symbol="SHOP.US",
        action="SELL",
        direction="LONG",
        quantity=500.0,
        alpha_source="llm_broad_smoke_cash_close_empty",
        **{**common, "quantity_type": "CASH_VALUE"},
    )
    return [app_entry, app_hold, shop_close_empty]
