from __future__ import annotations

from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e._lean_memory_e2e_helpers import (
    CanonicalTelemetryRecorder,
    RuntimeManagerClient,
    market_connector,
    market_record,
    signal_from_market_row,
)
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


LLM_REFS = {
    "model_id": "gpt-alpha-e2e-090",
    "prompt_bundle_id": "prompt-bundle-e2e-090",
    "llm_prompt_id": "prompt-e2e-090",
    "llm_response_id": "response-e2e-090",
    "llm_decision_id": "decision-e2e-090",
    "research_note_ref": "memory://research/e2e-090/binding-isolation",
}


def test_llm_binding_isolation_filters_misrouted_signal_feedback_memory_e2e(
    tmp_path,
    monkeypatch,
) -> None:
    loop_id = "090"
    strategy_id = "strategy-llm-binding-isolation"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": market_connector(
                connector_id="conn-e2e-loop-090-us-prices",
                provider="E2E Loop 090 Static US Prices",
                dataset="us_llm_binding_price_daily",
                feature_target="features/llm_binding_isolation_inputs",
                schema_hash="us_llm_binding_price_daily.e2e_loop_090.v1",
            ),
            "fetch": {
                "mode": "static_records",
                "records": [
                    market_record(
                        source_id="src-e2e-loop-090-aapl",
                        dataset="us_llm_binding_price_daily",
                        symbol="AAPL",
                        trade_date="2026-06-10",
                        close=190.0,
                        volume=2_300_000,
                    ),
                    market_record(
                        source_id="src-e2e-loop-090-nvda",
                        dataset="us_llm_binding_price_daily",
                        symbol="NVDA",
                        trade_date="2026-06-10",
                        close=135.0,
                        volume=3_100_000,
                    ),
                ],
                "next_watermark": "2026-06-10T21:22:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-090-us-prices",
            "trace_id": "trace-e2e-loop-090-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    rows = _read_jsonl(Path(normalized_ref["uri"]))
    assert {row["metadata"]["symbol"] for row in rows} == {"AAPL", "NVDA"}

    signals = _binding_signals(
        rows,
        strategy_id=strategy_id,
        normalized_ref_uri=normalized_ref["uri"],
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-binding",
        artifact_version="9.2.0",
        plan_id="plan-paper-llm-binding",
        persona_capital_binding_id="pcb-paper-llm-binding",
        default_strategy_id="paper-runtime-llm-binding",
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(signals),
        identity=_runtime_identity(),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-binding",
            artifact_version="9.2.0",
            plan_id="plan-paper-llm-binding",
            persona_capital_binding_id="pcb-paper-llm-binding",
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
        {"symbol": "AAPL", "quantity": 2.0, "price": 190.0}
    ]

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    noop_event = next(event for event in telemetry.events if event["event_type"] == "paper_order_simulated")
    assert fill_event["metadata"]["signal_id"] == "llm-aapl-binding-valid-090"
    assert fill_event["metadata"]["binding_id"] == "binding-e2e-loop-090"
    assert fill_event["metadata"]["alpha_source"] == "llm_binding_isolated_valid"
    assert fill_event["metadata"]["model_id"] == "gpt-alpha-e2e-090"
    assert fill_event["metrics"]["fill_quantity"] == 2.0
    assert "llm-nvda-binding-misrouted-090" not in {event["metadata"].get("signal_id") for event in [fill_event]}

    assert noop_event["metadata"]["signal_id"] == "llm-nvda-binding-misrouted-090"
    assert noop_event["metrics"]["action"] == "binding_mismatch_noop"
    assert noop_event["metadata"]["alpha_source"] == "llm_binding_misrouted"
    assert noop_event["metadata"]["noop_reason"] == "binding_mismatch"
    assert noop_event["metadata"]["expected_binding_id"] == "binding-e2e-loop-090"
    assert noop_event["metadata"]["signal_binding_id"] == "binding-other-runtime"
    assert noop_event["metadata"]["model_id"] == "gpt-alpha-e2e-090"
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-090")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    assert records_by_id[stored_fill["event_id"]]["order_context"]["fill_quantity"] == 2.0
    recovered_noop = records_by_id[stored_noop["event_id"]]
    assert recovered_noop["alpha_context"]["model_id"] == "gpt-alpha-e2e-090"
    assert recovered_noop["order_context"]["noop_reason"] == "binding_mismatch"
    assert recovered_noop["order_context"]["signal_binding_id"] == "binding-other-runtime"
    assert recovered_noop["order_context"]["submitted_to_broker"] is False
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["fill_rate"] == 0.5

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_noop,
        sponsor_persona_id="persona-llm-binding-sponsor",
        contributing_persona_ids=["persona-llm-binding-ops"],
        summary=(
            "LLM binding isolation consumed fetched AAPL/NVDA data, executed only the AAPL signal "
            "for binding-e2e-loop-090, filtered the NVDA signal routed to binding-other-runtime, "
            "recovered feedback, and wrote isolation evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-binding-ops",
                "summary": "LLM binding isolation feedback preserved valid fill, misroute no-op, and binding IDs.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_binding_isolation", "misroute_filtered", "paper_performance"],
            }
        ],
        proposal_ids=[
            signals[0]["signal_id"],
            signals[1]["signal_id"],
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
    writeback_payload["tags"].extend(["llm_binding_isolation", "misroute_filtered", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="LLM binding isolation misrouted filtered",
        tags=["llm_binding_isolation", "misroute_filtered"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    noop_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_order_simulated")
    fill_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert noop_lineage["order_context"]["noop_reason"] == "binding_mismatch"
    assert noop_lineage["order_context"]["signal_binding_id"] == "binding-other-runtime"
    assert fill_lineage["alpha_context"]["signal_id"] == "llm-aapl-binding-valid-090"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-binding-ops",
        query="misrouted binding filtered",
        tags=["misroute_filtered"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == strategy_id
    assert persona_lineage["order_context"]["noop_reason"] == "binding_mismatch"


def _binding_signals(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    normalized_ref_uri: str,
    ingest_run_id: str,
) -> list[dict[str, Any]]:
    by_symbol = {row["metadata"]["symbol"]: row for row in rows}
    misrouted = _signal(
        by_symbol["NVDA"],
        signal_id="llm-nvda-binding-misrouted-090",
        strategy_id=strategy_id,
        binding_id="binding-other-runtime",
        source_worker="mock-llm-binding-misrouted-normalizer",
        alpha_source="llm_binding_misrouted",
        quantity=5.0,
        normalized_ref_uri=normalized_ref_uri,
        ingest_run_id=ingest_run_id,
    )
    valid = _signal(
        by_symbol["AAPL"],
        signal_id="llm-aapl-binding-valid-090",
        strategy_id=strategy_id,
        binding_id="binding-e2e-loop-090",
        source_worker="mock-llm-binding-valid-normalizer",
        alpha_source="llm_binding_isolated_valid",
        quantity=2.0,
        normalized_ref_uri=normalized_ref_uri,
        ingest_run_id=ingest_run_id,
    )
    return [misrouted, valid]


def _signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    strategy_id: str,
    binding_id: str,
    source_worker: str,
    alpha_source: str,
    quantity: float,
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
        quantity_type="SHARES",
        source_worker=source_worker,
        alpha_source=alpha_source,
        normalized_ref_uris=[normalized_ref_uri],
        ingest_run_id=ingest_run_id,
        confidence_score=0.9,
        order_type="MARKET",
        extra_metadata={**LLM_REFS, "binding_id": binding_id},
    )
    signal["binding_id"] = binding_id
    return signal


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_BINDING_ID": "binding-e2e-loop-090",
            "PANTHEON_RUNTIME_ID": "paper-runtime-090",
            "PANTHEON_CAPITAL_POOL_ID": "pool-paper",
            "PANTHEON_ARTIFACT_ID": "artifact-paper-llm-binding",
            "PANTHEON_ARTIFACT_VERSION": "9.2.0",
            "PANTHEON_DEPLOYMENT_STAGE": "paper",
            "PANTHEON_DEPLOYMENT_PLAN_ID": "plan-paper-llm-binding",
            "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-paper-llm-binding",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-090-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-090",
        }
    )
