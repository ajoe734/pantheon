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
    "model_id": "gpt-alpha-e2e-087",
    "prompt_bundle_id": "prompt-bundle-e2e-087",
    "llm_prompt_id": "prompt-e2e-087",
    "llm_response_id": "response-e2e-087",
    "llm_decision_id": "decision-e2e-087",
    "research_note_ref": "memory://research/e2e-087/roku-limit-cap-note",
    "llm_note_ref": "memory://llm/e2e-087/roku-cash-limit-cap",
    "research_data_ref": ["research://roku/e2e-087/ad-spend-reset"],
}


def test_llm_cash_value_limit_close_caps_position_feedback_memory_readback_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    loop_id = "087"
    strategy_id = "strategy-llm-cash-limit-close-cap"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-087-us-prices",
                provider="E2E Loop 087 Static US Prices",
                dataset="us_llm_cash_limit_close_cap_price_daily",
                feature_target="features/llm_cash_limit_close_cap_inputs",
                schema_hash="us_llm_cash_limit_close_cap_price_daily.e2e_loop_087.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-087-roku-entry",
                        dataset="us_llm_cash_limit_close_cap_price_daily",
                        symbol="ROKU",
                        trade_date="2026-06-09",
                        close=60.0,
                        volume=1_020_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-087-roku-close",
                        dataset="us_llm_cash_limit_close_cap_price_daily",
                        symbol="ROKU",
                        trade_date="2026-06-10",
                        close=72.0,
                        volume=1_180_000,
                    ),
                ],
                "next_watermark": "2026-06-10T21:19:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-087-us-prices",
            "trace_id": "trace-e2e-loop-087-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-09", 60.0),
        ("2026-06-10", 72.0),
    ]

    signals = _llm_cash_limit_cap_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-cash-limit-cap",
        artifact_version="8.9.0",
        plan_id="plan-paper-llm-cash-limit-cap",
        persona_capital_binding_id="pcb-paper-llm-cash-limit-cap",
        default_strategy_id="paper-runtime-llm-cash-limit-cap",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-cash-limit-cap",
            artifact_version="8.9.0",
            plan_id="plan-paper-llm-cash-limit-cap",
            persona_capital_binding_id="pcb-paper-llm-cash-limit-cap",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "ROKU", "quantity": 3.0, "price": 60.0}
    ]

    pending_store.enqueue(signals[1])
    close_snapshot = runtime.drain_once()

    assert close_snapshot["status"] == "ok"
    assert close_snapshot["paper_state"]["processed_signal_count"] == 2
    assert close_snapshot["paper_state"]["execution_event_count"] == 2
    assert close_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "llm-roku-entry-087",
        "llm-roku-cash-limit-close-cap-087",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "limit_order"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [3.0, -3.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [60.0, 72.0]

    entry_fill, close_fill = fill_events
    assert entry_fill["metadata"]["model_id"] == "gpt-alpha-e2e-087"
    assert close_fill["metadata"]["alpha_source"] == "llm_research_cash_limit_close_cap"
    assert close_fill["metadata"]["quantity_type"] == "CASH_VALUE"
    assert close_fill["metadata"]["order_type"] == "LIMIT"
    assert close_fill["metadata"]["limit_price"] == 72.0
    assert close_fill["metadata"]["requested_quantity"] == 10_000.0
    assert close_fill["metadata"]["market_price"] == 72.0
    assert close_fill["metadata"]["llm_decision_id"] == "decision-e2e-087"
    assert close_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 36.0
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-087")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_close = records_by_id[stored_fills[1]["event_id"]]
    assert recovered_close["alpha_context"]["model_id"] == "gpt-alpha-e2e-087"
    assert recovered_close["alpha_context"]["llm_response_id"] == "response-e2e-087"
    assert recovered_close["order_context"]["quantity_type"] == "CASH_VALUE"
    assert recovered_close["order_context"]["requested_quantity"] == 10_000.0
    assert recovered_close["order_context"]["limit_price"] == 72.0
    assert recovered_close["order_context"]["fill_quantity"] == -3.0
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 36.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-cash-limit-cap-sponsor",
        contributing_persona_ids=["persona-llm-cash-limit-cap-ops"],
        summary=(
            "ROKU LLM close requested 10000.0 cash through a 72.0 limit, but LEAN capped the close "
            "to the open 3 long shares, recovered fill and PnL feedback, and wrote capped limit evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-cash-limit-cap-ops",
                "summary": "LLM cash limit cap feedback preserved requested cash, actual capped fill, and flat position PnL.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_cash_limit_close_cap", "paper_fill", "paper_performance"],
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
    writeback_payload["tags"].extend(["llm_cash_limit_close_cap", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="ROKU LLM cash limit close cap PnL",
        tags=["llm_cash_limit_close_cap", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    close_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert pnl_lineage["order_context"]["pnl"] == 36.0
    assert close_lineage["alpha_context"]["llm_decision_id"] == "decision-e2e-087"
    assert close_lineage["order_context"]["requested_quantity"] == 10_000.0
    assert close_lineage["order_context"]["fill_quantity"] == -3.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-cash-limit-cap-ops",
        query="cash limit close capped fill",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == strategy_id
    assert persona_lineage["order_context"]["pnl"] == 36.0


def _llm_cash_limit_cap_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common = {
        "strategy_id": strategy_id,
        "symbol": "ROKU.US",
        "source_worker": "mock-llm-cash-limit-cap-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.83,
        "extra_metadata": LLM_REFS,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-09"],
            signal_id="llm-roku-entry-087",
            action="BUY",
            direction="LONG",
            quantity=3.0,
            quantity_type="SHARES",
            alpha_source="llm_research_cash_limit_entry",
            order_type="MARKET",
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-10"],
            signal_id="llm-roku-cash-limit-close-cap-087",
            action="SELL",
            direction="LONG",
            quantity=10_000.0,
            quantity_type="CASH_VALUE",
            alpha_source="llm_research_cash_limit_close_cap",
            order_type="LIMIT",
            limit_price=72.0,
            **common,
        ),
    ]
