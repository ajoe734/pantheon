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


def test_llm_multiref_limit_roundtrip_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "094"
    strategy_id = "strategy-llm-multiref-limit"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    market_ingest = _configure_and_run_ingest(
        source_client,
        connector=market_connector(
            connector_id="conn-e2e-loop-094-market-prices",
            provider="E2E Loop 094 Static Market Prices",
            dataset="us_llm_multiref_limit_price_daily",
            feature_target="features/llm_multiref_limit_inputs",
            schema_hash="us_llm_multiref_limit_price_daily.e2e_loop_094.v1",
        ),
        records=[
            market_record(
                source_id="src-e2e-loop-094-okta-entry",
                dataset="us_llm_multiref_limit_price_daily",
                symbol="OKTA",
                trade_date="2026-06-09",
                close=50.0,
                volume=610_000,
            ),
            market_record(
                source_id="src-e2e-loop-094-okta-exit",
                dataset="us_llm_multiref_limit_price_daily",
                symbol="OKTA",
                trade_date="2026-06-10",
                close=55.5,
                volume=690_000,
            ),
        ],
        connector_id="conn-e2e-loop-094-market-prices",
        trace_id="trace-e2e-loop-094-market-fetch",
        next_watermark="2026-06-10T21:40:00Z",
    )
    alt_ingest = _configure_and_run_ingest(
        source_client,
        connector=_alt_connector(),
        records=[_alt_record()],
        connector_id="conn-e2e-loop-094-alt-signal",
        trace_id="trace-e2e-loop-094-alt-fetch",
        next_watermark="2026-06-10T21:41:00Z",
    )

    market_refs = market_ingest["storage_refs"]["normalized_refs"]
    market_rows = [row for ref in market_refs for row in _read_jsonl(Path(ref["uri"]))]
    alt_ref = alt_ingest["storage_refs"]["normalized_refs"][0]
    alt_row = _read_jsonl(Path(alt_ref["uri"]))[0]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in market_rows] == [
        ("2026-06-09", 50.0),
        ("2026-06-10", 55.5),
    ]
    assert alt_row["metadata"]["dataset"] == "llm_multiref_alt_signal"
    assert alt_row["metadata"]["model_id"] == "gpt-alpha-e2e-094"

    signals = _llm_multiref_limit_signals(
        market_rows,
        alt_row,
        strategy_id=strategy_id,
        market_ref_uris=[ref["uri"] for ref in market_refs],
        alt_ref=alt_ref,
        market_ingest_run_id=market_ingest["run"]["ingest_run_id"],
        alt_ingest_run_id=alt_ingest["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-multiref-limit",
        artifact_version="9.6.0",
        plan_id="plan-paper-llm-multiref-limit",
        persona_capital_binding_id="pcb-paper-llm-multiref-limit",
        default_strategy_id="paper-runtime-llm-multiref-limit",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-multiref-limit",
            artifact_version="9.6.0",
            plan_id="plan-paper-llm-multiref-limit",
            persona_capital_binding_id="pcb-paper-llm-multiref-limit",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["processed_signal_count"] == 1
    assert entry_snapshot["paper_state"]["positions"] == [{"symbol": "OKTA", "quantity": 5.0, "price": 49.0}]

    pending_store.enqueue(signals[1])
    exit_snapshot = runtime.drain_once()
    assert exit_snapshot["status"] == "ok"
    assert exit_snapshot["paper_state"]["processed_signal_count"] == 2
    assert exit_snapshot["paper_state"]["execution_event_count"] == 2
    assert exit_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "llm-okta-multiref-limit-entry-094",
        "llm-okta-multiref-limit-exit-094",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["limit_order", "limit_order"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [5.0, -5.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [49.0, 56.0]
    assert fill_events[0]["metadata"]["normalized_data_ref"] == [market_refs[0]["uri"], alt_ref["uri"]]
    assert fill_events[0]["metadata"]["source_evidence_refs"][1]["uri"] == alt_ref["uri"]
    assert fill_events[1]["metadata"]["llm_decision_id"] == "decision-e2e-094-okta-exit"
    assert fill_events[1]["metadata"]["research_data_ref"] == alt_ref["uri"]

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 35.0
    assert pnl_event["metrics"]["processed_signal_count"] == 2
    assert pnl_event["metrics"]["execution_event_count"] == 2
    assert pnl_event["metrics"]["fill_event_count"] == 2
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fills = [
        writer_adapter.ingest_telemetry_event(event, strategy_id=strategy_id, promotion_state="paper")
        for event in fill_events
    ]
    stored_pnl = writer_adapter.ingest_telemetry_event(pnl_event, strategy_id=strategy_id, promotion_state="paper")

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-094")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_entry = records_by_id[stored_fills[0]["event_id"]]
    recovered_exit = records_by_id[stored_fills[1]["event_id"]]
    assert recovered_entry["alpha_context"]["normalized_data_ref"] == [market_refs[0]["uri"], alt_ref["uri"]]
    assert recovered_entry["alpha_context"]["source_evidence_refs"][1]["ingest_run_id"] == alt_ingest["run"][
        "ingest_run_id"
    ]
    assert recovered_entry["alpha_context"]["research_data_ref"] == alt_ref["uri"]
    assert recovered_exit["alpha_context"]["model_id"] == "gpt-alpha-e2e-094"
    assert recovered_exit["order_context"]["limit_price"] == 56.0
    assert recovered_exit["order_context"]["fill_quantity"] == -5.0
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 35.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-multiref-limit-sponsor",
        contributing_persona_ids=["persona-llm-multiref-limit-ops"],
        summary=(
            "OKTA LLM limit roundtrip used market and alternative normalized refs, opened 5 shares at 49.0, "
            "closed at 56.0 for 35.0 paper PnL, recovered adapter feedback, and wrote multi-ref evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-multiref-limit-ops",
                "summary": "Multi-ref LLM feedback preserved normalized refs, source evidence, limit fills, and PnL.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_multiref_limit", "source_evidence", "paper_performance"],
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
    writeback_payload["tags"].extend(["llm_multiref_limit", "source_evidence", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="OKTA LLM multi normalized refs limit PnL",
        tags=["llm_multiref_limit", "source_evidence"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    exit_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    assert exit_lineage["alpha_context"]["normalized_data_ref"] == [market_refs[0]["uri"], alt_ref["uri"]]
    assert exit_lineage["alpha_context"]["source_evidence_refs"][1]["uri"] == alt_ref["uri"]
    assert exit_lineage["order_context"]["fill_price"] == 56.0
    assert pnl_lineage["order_context"]["pnl"] == 35.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-multiref-limit-ops",
        query="multi normalized refs source evidence",
        tags=["source_evidence"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_exit_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_fill_simulated"
    )
    assert persona_exit_lineage["alpha_context"]["llm_response_id"] == "response-e2e-094-okta-alt"


def _configure_and_run_ingest(
    source_client: Any,
    *,
    connector: dict[str, Any],
    records: list[dict[str, Any]],
    connector_id: str,
    trace_id: str,
    next_watermark: str,
) -> dict[str, Any]:
    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": connector,
            "fetch": {
                "mode": "static_records",
                "records": records,
                "next_watermark": next_watermark,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": connector_id,
            "trace_id": trace_id,
        },
    )
    assert ingest.status_code == 201, ingest.text
    body = ingest.json()
    assert body["run"]["status"] == "completed"
    return body


def _alt_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-094-alt-signal",
        "source_type": "news",
        "provider": "E2E Loop 094 Static Alternative Signal",
        "license_scope": "internal",
        "metadata": {
            "dataset": "llm_multiref_alt_signal",
            "access_scope": ["research", "audit_evidence"],
            "entitlement_tags": ["llm-multiref-internal"],
            "feature_targets": ["features/llm_multiref_limit_inputs"],
            "schema_hash": "llm_multiref_alt_signal.e2e_loop_094.v1",
        },
    }


def _alt_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-094-okta-alt",
        "title": "OKTA alternative account expansion signal for E2E loop 094",
        "content_ref": "alt://llm-multiref/OKTA/2026-06-10/account-expansion",
        "metadata": {
            "dataset": "llm_multiref_alt_signal",
            "date": "2026-06-10",
            "event_time": "2026-06-10T20:22:00Z",
            "available_time": "2026-06-10T20:23:00Z",
            "published_at": "2026-06-10T20:22:00Z",
            "publisher": "Pantheon Internal Alt Signal",
            "symbol": "OKTA",
            "symbols": ["OKTA"],
            "model_id": "gpt-alpha-e2e-094",
            "prompt_bundle_id": "prompt-bundle-e2e-094",
            "llm_prompt_id": "prompt-e2e-094-okta-alt",
            "llm_response_id": "response-e2e-094-okta-alt",
            "entry_decision_id": "decision-e2e-094-okta-entry",
            "exit_decision_id": "decision-e2e-094-okta-exit",
            "alt_signal_score": 0.81,
            "body": "Alternative account expansion signal supports a limit-entry and limit-exit OKTA trade.",
        },
    }


def _llm_multiref_limit_signals(
    market_rows: list[dict[str, Any]],
    alt_row: dict[str, Any],
    *,
    strategy_id: str,
    market_ref_uris: list[str],
    alt_ref: dict[str, Any],
    market_ingest_run_id: str,
    alt_ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in market_rows}
    alt = alt_row["metadata"]
    normalized_refs = [market_ref_uris[0], alt_ref["uri"]]
    source_evidence_refs = [
        {
            "ref_type": "normalized_rows",
            "dataset": "us_llm_multiref_limit_price_daily",
            "uri": market_ref_uris[0],
            "ingest_run_id": market_ingest_run_id,
        },
        {
            "ref_type": "normalized_rows",
            "dataset": alt_ref["dataset"],
            "uri": alt_ref["uri"],
            "ingest_run_id": alt_ingest_run_id,
        },
    ]
    common = {
        "strategy_id": strategy_id,
        "symbol": "OKTA.US",
        "quantity": 5.0,
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-multiref-limit-normalizer",
        "normalized_ref_uris": market_ref_uris,
        "ingest_run_id": market_ingest_run_id,
        "confidence_score": 0.86,
        "order_type": "LIMIT",
    }
    common_metadata = {
        "model_id": alt["model_id"],
        "prompt_bundle_id": alt["prompt_bundle_id"],
        "llm_prompt_id": alt["llm_prompt_id"],
        "llm_response_id": alt["llm_response_id"],
        "research_note_ref": alt_row["content_ref"],
        "research_data_ref": alt_ref["uri"],
        "news_data_ref": alt_row["content_ref"],
        "normalized_data_ref": normalized_refs,
        "source_evidence_refs": source_evidence_refs,
    }
    return [
        signal_from_market_row(
            rows_by_date["2026-06-09"],
            signal_id="llm-okta-multiref-limit-entry-094",
            action="BUY",
            direction="LONG",
            alpha_source="llm_multiref_limit_entry",
            limit_price=49.0,
            extra_metadata={
                **common_metadata,
                "llm_decision_id": alt["entry_decision_id"],
            },
            **common,
        ),
        signal_from_market_row(
            rows_by_date["2026-06-10"],
            signal_id="llm-okta-multiref-limit-exit-094",
            action="SELL",
            direction="LONG",
            alpha_source="llm_multiref_limit_exit",
            limit_price=56.0,
            extra_metadata={
                **common_metadata,
                "llm_decision_id": alt["exit_decision_id"],
            },
            **common,
        ),
    ]
