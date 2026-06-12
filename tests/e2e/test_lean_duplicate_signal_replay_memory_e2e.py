from __future__ import annotations

from pathlib import Path

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


def test_duplicate_signal_replay_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "070"
    strategy_id = "strategy-quant-duplicate-signal-id"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-070-us-prices",
                provider="E2E Loop 070 Static US Prices",
                dataset="us_duplicate_signal_price_daily",
                feature_target="features/duplicate_signal_replay_inputs",
                schema_hash="us_duplicate_signal_price_daily.e2e_loop_070.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-070-adbe",
                        dataset="us_duplicate_signal_price_daily",
                        symbol="ADBE",
                        trade_date="2026-06-08",
                        close=510.0,
                        volume=1_740_000,
                    )
                ],
                "next_watermark": "2026-06-08T21:10:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-070-us-prices",
            "trace_id": "trace-e2e-loop-070-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("ADBE", "2026-06-08", 510.0)
    ]

    signal = signal_from_market_row(
        normalized_rows[0],
        signal_id="quant-adbe-entry-070",
        strategy_id=strategy_id,
        symbol="ADBE.US",
        action="BUY",
        direction="LONG",
        quantity=2.0,
        quantity_type="SHARES",
        source_worker="mock-quant-duplicate-signal-normalizer",
        alpha_source="quant_breakout_replay_guard",
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.94,
        extra_metadata={
            "replay_window_id": "replay-window-e2e-070",
            "source_evidence_refs": ["market://us_duplicate_signal_price_daily/ADBE/2026-06-08"],
        },
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-duplicate-signal",
        artifact_version="7.3.0",
        plan_id="plan-paper-duplicate-signal",
        persona_capital_binding_id="pcb-paper-duplicate-signal",
        default_strategy_id="paper-runtime-duplicate-signal",
    )
    store = InMemoryPendingSignalStore([signal])
    runtime = PaperRuntimeService(
        store=store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-duplicate-signal",
            artifact_version="7.3.0",
            plan_id="plan-paper-duplicate-signal",
            persona_capital_binding_id="pcb-paper-duplicate-signal",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first_snapshot = runtime.drain_once()
    assert first_snapshot["status"] == "ok"
    assert first_snapshot["paper_state"]["processed_signal_count"] == 1
    assert first_snapshot["paper_state"]["execution_event_count"] == 1
    assert first_snapshot["paper_state"]["positions"] == [
        {"symbol": "ADBE", "quantity": 2.0, "price": 510.0}
    ]

    store.enqueue(dict(signal))
    replay_snapshot = runtime.drain_once()

    assert replay_snapshot["status"] == "ok"
    assert replay_snapshot["paper_state"]["processed_signal_count"] == 1
    assert replay_snapshot["paper_state"]["execution_event_count"] == 2
    assert replay_snapshot["paper_state"]["positions"] == [
        {"symbol": "ADBE", "quantity": 2.0, "price": 510.0}
    ]
    assert [event["event_type"] for event in replay_snapshot["paper_state"]["recent_order_events"]] == [
        "paper_fill_simulated",
        "paper_order_simulated",
    ]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert len(noop_events) == 1
    fill_event = fill_events[0]
    assert fill_event["metadata"]["signal_id"] == "quant-adbe-entry-070"
    assert fill_event["metadata"]["alpha_source"] == "quant_breakout_replay_guard"
    assert fill_event["metadata"]["market_price"] == 510.0
    assert fill_event["metrics"]["fill_quantity"] == 2.0
    assert fill_event["metrics"]["fill_price"] == 510.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    noop_event = noop_events[0]
    assert noop_event["metadata"]["signal_id"] == "quant-adbe-entry-070"
    assert noop_event["metrics"]["action"] == "duplicate_signal_id_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 2.0
    assert noop_event["metrics"]["computed_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["noop_reason"] == "duplicate_signal_id"
    assert noop_event["metadata"]["filter_reason"] == "duplicate_signal_id"
    assert noop_event["metadata"]["duplicate_signal_id"] == "quant-adbe-entry-070"
    assert noop_event["metadata"]["idempotent_replay"] is True
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["price"] == 510.0
    assert noop_event["metadata"]["market_price"] == 510.0
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_filtered"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    stored_noop = writer_adapter.ingest_telemetry_event(
        noop_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=strategy_id,
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-070")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill = records_by_id[stored_fill["event_id"]]
    recovered_noop = records_by_id[stored_noop["event_id"]]
    assert recovered_fill["alpha_context"]["signal_id"] == "quant-adbe-entry-070"
    assert recovered_fill["order_context"]["fill_quantity"] == 2.0
    assert recovered_noop["order_context"]["noop_reason"] == "duplicate_signal_id"
    assert recovered_noop["order_context"]["duplicate_signal_id"] == "quant-adbe-entry-070"
    assert recovered_noop["order_context"]["idempotent_replay"] is True
    assert recovered_noop["order_context"]["fill_rate"] == 0.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-duplicate-signal-sponsor",
        contributing_persona_ids=["persona-quant-replay-ops"],
        summary=(
            "ADBE market data produced a BUY signal that filled once, then the identical signal ID "
            "was replayed. LEAN preserved the original position, emitted idempotent duplicate "
            "no-order feedback, recovered both fill and duplicate feedback after adapter restart, "
            "and wrote the replay outcome into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-quant-replay-ops",
                "summary": "Duplicate signal replay preserved the original fill and emitted terminal idempotent no-order feedback.",
                "proposal_ids": [signal["signal_id"], stored_noop["event_id"]],
                "tags": ["duplicate_signal_id", "idempotent_replay", "paper_performance"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_noop["event_id"], stored_pnl["event_id"]],
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
                "ref_id": stored_noop["event_id"],
                "event_type": stored_noop["event_type"],
                "lineage": recovered_adapter.build_lineage_record(stored_noop),
            },
        ]
    )
    writeback_payload["tags"].extend(["duplicate_signal_id", "idempotent_replay", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ADBE duplicate signal replay idempotent original fill",
        tags=["duplicate_signal_id", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    fill_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    assert pnl_lineage["order_context"]["execution_event_count"] == 2
    assert pnl_lineage["order_context"]["fill_rate"] == 1.0
    assert fill_lineage["order_context"]["fill_quantity"] == 2.0
    assert noop_lineage["order_context"]["noop_reason"] == "duplicate_signal_id"
    assert noop_lineage["order_context"]["idempotent_replay"] is True
    assert noop_lineage["order_context"]["submitted_to_broker"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-quant-replay-ops",
        query="duplicate signal idempotent no order",
        tags=["idempotent_replay"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_noop_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_order_simulated"
    )
    assert persona_noop_lineage["strategy_id"] == strategy_id
    assert persona_noop_lineage["order_context"]["duplicate_signal_id"] == "quant-adbe-entry-070"
    assert persona_noop_lineage["order_context"]["computed_quantity"] == pytest.approx(0.0)
