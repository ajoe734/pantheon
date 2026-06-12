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


def test_limit_roundtrip_feedback_performance_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "066"
    strategy_id = "strategy-limit-roundtrip"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-066-us-prices",
                provider="E2E Loop 066 Static US Prices",
                dataset="us_limit_roundtrip_price_daily",
                feature_target="features/quant_limit_roundtrip_inputs",
                schema_hash="us_limit_roundtrip_price_daily.e2e_loop_066.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-066-ashop-entry",
                        dataset="us_limit_roundtrip_price_daily",
                        symbol="SHOP",
                        trade_date="2026-06-05",
                        close=20.5,
                        volume=1_010_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-066-ashop-exit",
                        dataset="us_limit_roundtrip_price_daily",
                        symbol="SHOP",
                        trade_date="2026-06-08",
                        close=22.5,
                        volume=1_030_000,
                    ),
                ],
                "next_watermark": "2026-06-08T21:06:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-066-us-prices",
            "trace_id": "trace-e2e-loop-066-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_refs = ingest_body["storage_refs"]["normalized_refs"]
    normalized_rows = [row for ref in normalized_refs for row in _read_jsonl(Path(ref["uri"]))]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in normalized_rows] == [
        ("2026-06-05", 20.5),
        ("2026-06-08", 22.5),
    ]

    signals = _limit_roundtrip_signals(
        normalized_rows,
        strategy_id=strategy_id,
        normalized_ref_uris=[ref["uri"] for ref in normalized_refs],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-limit-roundtrip",
        artifact_version="6.9.0",
        plan_id="plan-paper-limit-roundtrip",
        persona_capital_binding_id="pcb-paper-limit-roundtrip",
        default_strategy_id="paper-runtime-limit-roundtrip",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-limit-roundtrip",
            artifact_version="6.9.0",
            plan_id="plan-paper-limit-roundtrip",
            persona_capital_binding_id="pcb-paper-limit-roundtrip",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["positions"] == [
        {"symbol": "SHOP", "quantity": 5.0, "price": 20.0}
    ]

    pending_store.enqueue(signals[1])
    exit_snapshot = runtime.drain_once()

    assert exit_snapshot["status"] == "ok"
    assert exit_snapshot["paper_state"]["processed_signal_count"] == 2
    assert exit_snapshot["paper_state"]["execution_event_count"] == 2
    assert exit_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "quant-shop-limit-entry-066",
        "quant-shop-limit-exit-066",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["limit_order", "limit_order"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [5.0, -5.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [20.0, 23.0]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metadata"]["order_type"] == "LIMIT"
    assert entry_fill["metadata"]["limit_price"] == 20.0
    assert entry_fill["metadata"]["market_price"] == 20.5
    assert exit_fill["metadata"]["order_type"] == "LIMIT"
    assert exit_fill["metadata"]["limit_price"] == 23.0
    assert exit_fill["metadata"]["market_price"] == 22.5
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 15.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-066")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_exit_context = records_by_id[stored_fills[1]["event_id"]]["order_context"]
    assert recovered_exit_context["fill_quantity"] == -5.0
    assert recovered_exit_context["fill_price"] == 23.0
    assert recovered_exit_context["order_type"] == "LIMIT"
    assert recovered_exit_context["limit_price"] == 23.0
    assert recovered_exit_context["market_price"] == 22.5
    assert recovered_exit_context["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 15.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-limit-roundtrip-sponsor",
        contributing_persona_ids=["persona-limit-roundtrip-ops"],
        summary=(
            "SHOP fetched prices opened 5 long shares with a 20.0 limit, closed them with a "
            "23.0 limit for 15.0 paper PnL, recovered adapter feedback, and wrote limit order "
            "evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-limit-roundtrip-ops",
                "summary": "LIMIT roundtrip feedback preserved limit prices, fills, and final PnL evidence.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["limit_roundtrip", "paper_fill", "paper_performance"],
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
    writeback_payload["tags"].extend(["limit_roundtrip", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="SHOP LIMIT roundtrip PnL",
        tags=["limit_roundtrip", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    exit_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert pnl_lineage["order_context"]["pnl"] == 15.0
    assert exit_lineage["alpha_context"]["signal_id"] == "quant-shop-limit-exit-066"
    assert exit_lineage["order_context"]["limit_price"] == 23.0
    assert exit_lineage["order_context"]["fill_price"] == 23.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-limit-roundtrip-ops",
        query="limit roundtrip fill pnl",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_pnl_lineage = next(item["lineage"] for item in persona_evidence if item["event_type"] == "pnl_snapshot")
    assert persona_pnl_lineage["strategy_id"] == strategy_id
    assert persona_pnl_lineage["order_context"]["pnl"] == 15.0


def _limit_roundtrip_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in rows}
    common = {
        "strategy_id": strategy_id,
        "symbol": "SHOP.US",
        "quantity_type": "SHARES",
        "source_worker": "mock-limit-roundtrip-normalizer",
        "normalized_ref_uris": normalized_ref_uris,
        "ingest_run_id": ingest_run_id,
        "confidence_score": 0.94,
        "order_type": "LIMIT",
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-05"],
            signal_id="quant-shop-limit-entry-066",
            action="BUY",
            direction="LONG",
            quantity=5.0,
            alpha_source="pure_quant_limit_entry",
            limit_price=20.0,
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-08"],
            signal_id="quant-shop-limit-exit-066",
            action="SELL",
            direction="LONG",
            quantity=5.0,
            alpha_source="pure_quant_limit_exit",
            limit_price=23.0,
            **common,
        ),
    ]
