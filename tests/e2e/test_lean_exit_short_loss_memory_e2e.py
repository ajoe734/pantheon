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


def test_exit_short_loss_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "065"
    strategy_id = "strategy-exit-short-loss"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-065-us-prices",
                provider="E2E Loop 065 Static US Prices",
                dataset="us_exit_short_loss_price_daily",
                feature_target="features/quant_exit_short_loss_inputs",
                schema_hash="us_exit_short_loss_price_daily.e2e_loop_065.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-065-okta-short",
                        dataset="us_exit_short_loss_price_daily",
                        symbol="OKTA",
                        trade_date="2026-06-08",
                        close=44.0,
                        volume=810000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-065-okta-cover",
                        dataset="us_exit_short_loss_price_daily",
                        symbol="OKTA",
                        trade_date="2026-06-09",
                        close=48.0,
                        volume=850000,
                    ),
                ],
                "next_watermark": "2026-06-09T21:05:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-065-us-prices",
            "trace_id": "trace-e2e-loop-065-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-08", 44.0),
        ("2026-06-09", 48.0),
    ]

    signals = _exit_short_loss_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-exit-short-loss",
        artifact_version="6.8.0",
        plan_id="plan-paper-exit-short-loss",
        persona_capital_binding_id="pcb-paper-exit-short-loss",
        default_strategy_id="paper-runtime-exit-short-loss",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-exit-short-loss",
            artifact_version="6.8.0",
            plan_id="plan-paper-exit-short-loss",
            persona_capital_binding_id="pcb-paper-exit-short-loss",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    short_snapshot = runtime.drain_once()
    assert short_snapshot["status"] == "ok"
    assert short_snapshot["paper_state"]["processed_signal_count"] == 1
    assert short_snapshot["paper_state"]["execution_event_count"] == 1
    assert short_snapshot["paper_state"]["positions"] == [
        {"symbol": "OKTA", "quantity": -4.0, "price": 44.0}
    ]

    pending_store.enqueue(signals[1])
    cover_snapshot = runtime.drain_once()

    assert cover_snapshot["status"] == "ok"
    assert cover_snapshot["paper_state"]["processed_signal_count"] == 2
    assert cover_snapshot["paper_state"]["execution_event_count"] == 2
    assert cover_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-okta-short-065",
        "quant-okta-exit-short-loss-065",
    ]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [-4.0, 4.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [44.0, 48.0]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "market_order"]

    short_fill, cover_fill = fill_events
    assert short_fill["metadata"]["alpha_source"] == "pure_quant_short_loss_entry"
    assert short_fill["metadata"]["requested_quantity"] == 4.0
    assert cover_fill["metadata"]["alpha_source"] == "pure_quant_exit_short_loss_cover"
    assert cover_fill["metadata"]["quantity_type"] == "SHARES"
    assert cover_fill["metadata"]["order_type"] == "MARKET"
    assert cover_fill["metadata"]["requested_quantity"] == 0.0
    assert cover_fill["metadata"]["market_price"] == 48.0
    assert cover_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == -16.0
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-065")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_cover_context = records_by_id[stored_fills[1]["event_id"]]["order_context"]
    assert recovered_cover_context["fill_quantity"] == 4.0
    assert recovered_cover_context["fill_price"] == 48.0
    assert recovered_cover_context["quantity_type"] == "SHARES"
    assert recovered_cover_context["order_type"] == "MARKET"
    assert recovered_cover_context["market_price"] == 48.0
    assert recovered_cover_context["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["pnl"] == -16.0
    assert recovered_pnl_context["fill_event_count"] == 2
    assert recovered_pnl_context["open_position_count"] == 0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-exit-short-loss-sponsor",
        contributing_persona_ids=["persona-exit-short-loss-ops"],
        summary=(
            "OKTA fetched prices opened 4 short shares at 44.0, then EXIT/SHORT covered them "
            "at 48.0 for -16.0 paper PnL, recovered adapter feedback, and wrote loss evidence "
            "into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-exit-short-loss-ops",
                "summary": "EXIT/SHORT loss feedback preserved buy-to-cover fill evidence and negative PnL.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["exit_short_loss", "paper_fill", "paper_performance"],
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
    writeback_payload["tags"].extend(["exit_short_loss", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="OKTA EXIT SHORT loss negative PnL",
        tags=["exit_short_loss", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    cover_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert pnl_lineage["order_context"]["pnl"] == -16.0
    assert cover_lineage["alpha_context"]["signal_id"] == "quant-okta-exit-short-loss-065"
    assert cover_lineage["order_context"]["fill_quantity"] == 4.0
    assert cover_lineage["order_context"]["fill_price"] == 48.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-exit-short-loss-ops",
        query="short cover loss negative pnl",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_pnl_lineage = next(item["lineage"] for item in persona_evidence if item["event_type"] == "pnl_snapshot")
    assert persona_pnl_lineage["strategy_id"] == strategy_id
    assert persona_pnl_lineage["order_context"]["pnl"] == -16.0


def _exit_short_loss_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common = {
        "strategy_id": strategy_id,
        "symbol": "OKTA.US",
        "quantity_type": "SHARES",
        "source_worker": "mock-exit-short-loss-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.88,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-08"],
            signal_id="quant-okta-short-065",
            action="SELL",
            direction="SHORT",
            quantity=4.0,
            alpha_source="pure_quant_short_loss_entry",
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-09"],
            signal_id="quant-okta-exit-short-loss-065",
            action="EXIT",
            direction="SHORT",
            quantity=0.0,
            alpha_source="pure_quant_exit_short_loss_cover",
            **common,
        ),
    ]
