from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
)
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_llm_conflict_loser_quant_winner_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "089"
    strategy_id = "strategy-llm-conflict-loser"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-089-us-prices",
                provider="E2E Loop 089 Static US Prices",
                dataset="us_llm_conflict_loser_price_daily",
                feature_target="features/llm_conflict_loser_inputs",
                schema_hash="us_llm_conflict_loser_price_daily.e2e_loop_089.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-089-wday",
                        dataset="us_llm_conflict_loser_price_daily",
                        symbol="WDAY",
                        trade_date="2026-06-10",
                        close=210.0,
                        volume=1_430_000,
                    )
                ],
                "next_watermark": "2026-06-10T21:21:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-089-us-prices",
            "trace_id": "trace-e2e-loop-089-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "WDAY"
    assert row["metadata"]["close"] == 210.0

    llm_loser, quant_winner = _conflicting_signals(
        row,
        strategy_id=strategy_id,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-conflict-loser",
        artifact_version="9.1.0",
        plan_id="plan-paper-llm-conflict-loser",
        persona_capital_binding_id="pcb-paper-llm-conflict-loser",
        default_strategy_id="paper-runtime-llm-conflict-loser",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([llm_loser, quant_winner]),
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-conflict-loser",
            artifact_version="9.1.0",
            plan_id="plan-paper-llm-conflict-loser",
            persona_capital_binding_id="pcb-paper-llm-conflict-loser",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    assert snapshot["paper_state"]["positions"] == [
        {"symbol": "WDAY", "quantity": 4.0, "price": 210.0}
    ]
    assert [event["event_type"] for event in snapshot["paper_state"]["recent_order_events"]] == [
        "paper_order_simulated",
        "paper_fill_simulated",
    ]

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    noop_event = next(event for event in telemetry.events if event["event_type"] == "paper_order_simulated")
    assert fill_event["metadata"]["signal_id"] == "quant-wday-conflict-winner-089"
    assert fill_event["metadata"]["alpha_source"] == "pure_quant_conflict_winner"
    assert fill_event["metrics"]["fill_quantity"] == 4.0
    assert fill_event["metrics"]["fill_price"] == 210.0

    assert noop_event["metadata"]["signal_id"] == "llm-wday-conflict-loser-089"
    assert noop_event["metadata"]["alpha_source"] == "llm_conflict_resolution_loser"
    assert noop_event["metadata"]["model_id"] == "gpt-alpha-e2e-089"
    assert noop_event["metrics"]["action"] == "signal_conflict_loser_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 8.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["noop_reason"] == "signal_conflict_loser"
    assert noop_event["metadata"]["filter_reason"] == "signal_conflict_loser"
    assert noop_event["metadata"]["conflict_winner_signal_id"] == "quant-wday-conflict-winner-089"
    assert noop_event["metadata"]["conflict_loser_signal_id"] == "llm-wday-conflict-loser-089"
    assert noop_event["metadata"]["conflict_symbol"] == "WDAY.US"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["broker_submission_status"] == "not_submitted_signal_filtered"
    assert noop_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-089")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill = records_by_id[stored_fill["event_id"]]
    recovered_noop = records_by_id[stored_noop["event_id"]]
    assert recovered_fill["order_context"]["fill_quantity"] == 4.0
    assert recovered_noop["alpha_context"]["model_id"] == "gpt-alpha-e2e-089"
    assert recovered_noop["alpha_context"]["llm_response_id"] == "response-e2e-089"
    assert recovered_noop["order_context"]["noop_reason"] == "signal_conflict_loser"
    assert recovered_noop["order_context"]["conflict_winner_signal_id"] == "quant-wday-conflict-winner-089"
    assert recovered_noop["order_context"]["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_rate"] == 0.5

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_noop,
        sponsor_persona_id="persona-llm-conflict-loser-sponsor",
        contributing_persona_ids=["persona-conflict-arbiter"],
        summary=(
            "WDAY market data produced an older LLM short alpha and a newer pure-quant long alpha; "
            "LEAN suppressed the LLM loser, filled only the quant winner, recovered both feedback records, "
            "and wrote conflict lineage into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-conflict-arbiter",
                "summary": "LLM conflict-loser feedback preserved winner/loser signal IDs, non-submission, and fill-rate context.",
                "proposal_ids": [llm_loser["signal_id"], quant_winner["signal_id"]],
                "tags": ["llm_conflict_loser", "signal_conflict", "paper_noop"],
            }
        ],
        proposal_ids=[
            llm_loser["signal_id"],
            quant_winner["signal_id"],
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
    writeback_payload["tags"].extend(["llm_conflict_loser", "signal_conflict", "paper_noop"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="WDAY LLM conflict loser quant winner",
        tags=["llm_conflict_loser", "signal_conflict"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    fill_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert noop_lineage["alpha_context"]["model_id"] == "gpt-alpha-e2e-089"
    assert noop_lineage["order_context"]["conflict_winner_signal_id"] == "quant-wday-conflict-winner-089"
    assert noop_lineage["order_context"]["submitted_to_broker"] is False
    assert fill_lineage["alpha_context"]["alpha_source"] == "pure_quant_conflict_winner"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-conflict-arbiter",
        query="llm conflict loser winner signal ids",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == strategy_id
    assert persona_lineage["order_context"]["noop_reason"] == "signal_conflict_loser"


def _conflicting_signals(
    row: dict[str, Any],
    *,
    strategy_id: str,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    llm_loser = _signal(
        row,
        strategy_id=strategy_id,
        signal_id="llm-wday-conflict-loser-089",
        timestamp=base_time - timedelta(minutes=3),
        action="SELL",
        direction="SHORT",
        quantity=8.0,
        source_worker="mock-llm-conflict-loser-normalizer",
        alpha_source="llm_conflict_resolution_loser",
        confidence_score=0.88,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_run_id,
        llm_metadata={
            "model_id": "gpt-alpha-e2e-089",
            "prompt_bundle_id": "prompt-bundle-e2e-089",
            "llm_prompt_id": "prompt-e2e-089",
            "llm_response_id": "response-e2e-089",
            "llm_decision_id": "decision-e2e-089",
            "research_note_ref": "memory://research/e2e-089/wday-short-note",
        },
    )
    quant_winner = _signal(
        row,
        strategy_id=strategy_id,
        signal_id="quant-wday-conflict-winner-089",
        timestamp=base_time,
        action="BUY",
        direction="LONG",
        quantity=4.0,
        source_worker="mock-quant-conflict-winner-normalizer",
        alpha_source="pure_quant_conflict_winner",
        confidence_score=0.93,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_run_id,
    )
    return llm_loser, quant_winner


def _signal(
    row: dict[str, Any],
    *,
    strategy_id: str,
    signal_id: str,
    timestamp: datetime,
    action: str,
    direction: str,
    quantity: float,
    source_worker: str,
    alpha_source: str,
    confidence_score: float,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
    llm_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = row["metadata"]
    signal_metadata = {
        "alpha_source": alpha_source,
        "confidence_score": confidence_score,
        "market_data_ref": normalized_ref["uri"],
        "market_data": {
            "dataset": metadata["dataset"],
            "symbol": metadata["symbol"],
            "date": metadata["date"],
            "close": metadata["close"],
            "content_ref": row["content_ref"],
        },
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": ingest_run_id,
    }
    signal_metadata.update(llm_metadata or {})
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": strategy_id,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": "WDAY.US",
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": "SHARES",
        "source_worker": source_worker,
        "order_type": "MARKET",
        "metadata": signal_metadata,
    }
