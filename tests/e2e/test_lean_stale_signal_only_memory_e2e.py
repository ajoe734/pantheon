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


def test_stale_signal_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "071"
    strategy_id = "strategy-quant-stale-signal-guard"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-071-us-prices",
                provider="E2E Loop 071 Static US Prices",
                dataset="us_stale_signal_price_daily",
                feature_target="features/stale_signal_guard_inputs",
                schema_hash="us_stale_signal_price_daily.e2e_loop_071.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-071-intu",
                        dataset="us_stale_signal_price_daily",
                        symbol="INTU",
                        trade_date="2026-06-09",
                        close=650.0,
                        volume=820_000,
                    )
                ],
                "next_watermark": "2026-06-09T21:11:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-071-us-prices",
            "trace_id": "trace-e2e-loop-071-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["symbol"], row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("INTU", "2026-06-09", 650.0)
    ]

    signal = signal_from_market_row(
        normalized_rows[0],
        signal_id="quant-intu-stale-entry-071",
        strategy_id=strategy_id,
        symbol="INTU.US",
        action="BUY",
        direction="LONG",
        quantity=0.25,
        quantity_type="PERCENT_PORTFOLIO",
        source_worker="mock-quant-stale-signal-normalizer",
        alpha_source="quant_momentum_stale_guard",
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
        confidence_score=0.82,
        extra_metadata={
            "source_evidence_refs": ["market://us_stale_signal_price_daily/INTU/2026-06-09"],
            "stale_guard_window_hours": 24,
        },
    )
    signal["timestamp"] = "2024-01-01T00:00:00Z"

    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-stale-signal",
        artifact_version="7.4.0",
        plan_id="plan-paper-stale-signal",
        persona_capital_binding_id="pcb-paper-stale-signal",
        default_strategy_id="paper-runtime-stale-signal",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-stale-signal",
            artifact_version="7.4.0",
            plan_id="plan-paper-stale-signal",
            persona_capital_binding_id="pcb-paper-stale-signal",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    assert snapshot["paper_state"]["execution_event_count"] == 1
    assert snapshot["paper_state"]["positions"] == []
    assert snapshot["paper_state"]["recent_order_events"][0]["event_type"] == "paper_order_simulated"
    assert snapshot["paper_state"]["recent_order_events"][0]["action"] == "stale_signal_noop"

    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(noop_events) == 1
    assert fill_events == []
    noop_event = noop_events[0]
    assert noop_event["metrics"]["action"] == "stale_signal_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 0.25
    assert noop_event["metrics"]["computed_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metrics"]["submitted_to_broker"] is False
    assert noop_event["metadata"]["signal_id"] == "quant-intu-stale-entry-071"
    assert noop_event["metadata"]["alpha_source"] == "quant_momentum_stale_guard"
    assert noop_event["metadata"]["noop_reason"] == "stale_signal"
    assert noop_event["metadata"]["filter_reason"] == "stale_signal"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert noop_event["metadata"]["requested_quantity"] == 0.25
    assert noop_event["metadata"]["computed_quantity"] == 0.0
    assert noop_event["metadata"]["price"] == 650.0
    assert noop_event["metadata"]["market_price"] == 650.0
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_filtered"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 0
    assert pnl_event["metrics"]["fill_rate"] == 0.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
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
    recovered_noops = recovered_adapter.query_telemetry(
        strategy_id=strategy_id,
        event_type="paper_order_simulated",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_noops] == [stored_noop["event_id"]]
    recovered_lineage = recovered_adapter.build_lineage_record(recovered_noops[0])
    assert recovered_lineage["alpha_context"]["signal_id"] == "quant-intu-stale-entry-071"
    assert recovered_lineage["alpha_context"]["alpha_source"] == "quant_momentum_stale_guard"
    assert recovered_lineage["alpha_context"]["market_data_ref"] == [normalized_refs[0]["uri"]]
    assert recovered_lineage["order_context"]["noop_reason"] == "stale_signal"
    assert recovered_lineage["order_context"]["filter_reason"] == "stale_signal"
    assert recovered_lineage["order_context"]["order_status"] == "not_submitted"
    assert recovered_lineage["order_context"]["computed_quantity"] == 0.0
    assert recovered_lineage["order_context"]["fill_rate"] == 0.0
    assert recovered_lineage["order_context"]["broker_submission_status"] == "not_submitted_signal_filtered"

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-stale-signal-sponsor",
        contributing_persona_ids=["persona-quant-stale-guard"],
        summary=(
            "INTU market data produced a percent-portfolio BUY signal, but its alpha timestamp "
            "was older than the runtime stale threshold. LEAN emitted stale-signal no-order "
            "feedback, submitted nothing to a broker, recovered that feedback after adapter "
            "restart, and wrote zero-fill performance into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-quant-stale-guard",
                "summary": "Stale-signal feedback preserved market input lineage, filter reason, and broker non-submission.",
                "proposal_ids": [signal["signal_id"], stored_noop["event_id"]],
                "tags": ["stale_signal", "paper_noop", "paper_performance"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_noop["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_noop["event_id"],
            "event_type": stored_noop["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_noop),
        }
    )
    writeback_payload["tags"].extend(["stale_signal", "paper_noop", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="INTU stale signal no order zero fill",
        tags=["stale_signal", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    assert pnl_lineage["order_context"]["fill_event_count"] == 0
    assert pnl_lineage["order_context"]["fill_rate"] == 0.0
    assert noop_lineage["alpha_context"]["signal_id"] == "quant-intu-stale-entry-071"
    assert noop_lineage["order_context"]["noop_reason"] == "stale_signal"
    assert noop_lineage["order_context"]["submitted_to_broker"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-quant-stale-guard",
        query="stale signal filter no broker submission",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_noop_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_order_simulated"
    )
    assert persona_noop_lineage["strategy_id"] == strategy_id
    assert persona_noop_lineage["order_context"]["filter_reason"] == "stale_signal"
    assert persona_noop_lineage["order_context"]["requested_quantity"] == 0.25
