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


def test_llm_news_research_market_roundtrip_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    loop_id = "093"
    strategy_id = "strategy-llm-news-research-market"
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    market_ingest = _configure_and_run_ingest(
        source_client,
        connector=market_connector(
            connector_id="conn-e2e-loop-093-market-prices",
            provider="E2E Loop 093 Static Market Prices",
            dataset="us_llm_news_research_price_daily",
            feature_target="features/llm_news_research_inputs",
            schema_hash="us_llm_news_research_price_daily.e2e_loop_093.v1",
        ),
        records=[
            market_record(
                source_id="src-e2e-loop-093-epam-entry",
                dataset="us_llm_news_research_price_daily",
                symbol="EPAM",
                trade_date="2026-06-09",
                close=100.0,
                volume=820_000,
            ),
            market_record(
                source_id="src-e2e-loop-093-epam-exit",
                dataset="us_llm_news_research_price_daily",
                symbol="EPAM",
                trade_date="2026-06-10",
                close=112.0,
                volume=930_000,
            ),
        ],
        connector_id="conn-e2e-loop-093-market-prices",
        trace_id="trace-e2e-loop-093-market-fetch",
        next_watermark="2026-06-10T21:30:00Z",
    )
    research_ingest = _configure_and_run_ingest(
        source_client,
        connector=_research_connector(),
        records=[_research_record()],
        connector_id="conn-e2e-loop-093-llm-research",
        trace_id="trace-e2e-loop-093-research-fetch",
        next_watermark="2026-06-10T21:35:00Z",
    )

    market_refs = market_ingest["storage_refs"]["normalized_refs"]
    market_rows = [row for ref in market_refs for row in _read_jsonl(Path(ref["uri"]))]
    research_ref = research_ingest["storage_refs"]["normalized_refs"][0]
    research_row = _read_jsonl(Path(research_ref["uri"]))[0]
    assert [(row["metadata"]["date"], row["metadata"]["close"]) for row in market_rows] == [
        ("2026-06-09", 100.0),
        ("2026-06-10", 112.0),
    ]
    assert research_row["metadata"]["llm_response_id"] == "response-e2e-093-epam-guidance"

    signals = _llm_news_research_signals(
        market_rows,
        research_row,
        strategy_id=strategy_id,
        market_ref_uris=[ref["uri"] for ref in market_refs],
        research_ref=research_ref,
        market_ingest_run_id=market_ingest["run"]["ingest_run_id"],
        research_ingest_run_id=research_ingest["run"]["ingest_run_id"],
    )
    telemetry = CanonicalTelemetryRecorder(
        loop_id=loop_id,
        artifact_id="artifact-paper-llm-news-research-market",
        artifact_version="9.5.0",
        plan_id="plan-paper-llm-news-research-market",
        persona_capital_binding_id="pcb-paper-llm-news-research-market",
        default_strategy_id="paper-runtime-llm-news-research-market",
    )
    pending_store = InMemoryPendingSignalStore([signals[0]])
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=runtime_identity(loop_id=loop_id),
        runtime_manager_client=RuntimeManagerClient(
            loop_id=loop_id,
            artifact_id="artifact-paper-llm-news-research-market",
            artifact_version="9.5.0",
            plan_id="plan-paper-llm-news-research-market",
            persona_capital_binding_id="pcb-paper-llm-news-research-market",
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    entry_snapshot = runtime.drain_once()
    assert entry_snapshot["status"] == "ok"
    assert entry_snapshot["paper_state"]["processed_signal_count"] == 1
    assert entry_snapshot["paper_state"]["execution_event_count"] == 1
    assert entry_snapshot["paper_state"]["positions"] == [{"symbol": "EPAM", "quantity": 3.0, "price": 100.0}]

    pending_store.enqueue(signals[1])
    exit_snapshot = runtime.drain_once()
    assert exit_snapshot["status"] == "ok"
    assert exit_snapshot["paper_state"]["processed_signal_count"] == 2
    assert exit_snapshot["paper_state"]["execution_event_count"] == 2
    assert exit_snapshot["paper_state"]["positions"] == []

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert [event["metadata"]["signal_id"] for event in fill_events] == [
        "llm-epam-news-entry-093",
        "llm-epam-news-exit-093",
    ]
    assert [event["metrics"]["action"] for event in fill_events] == ["market_order", "liquidate"]
    assert [event["metrics"]["fill_quantity"] for event in fill_events] == [3.0, -3.0]
    assert [event["metrics"]["fill_price"] for event in fill_events] == [100.0, 112.0]

    entry_fill, exit_fill = fill_events
    assert entry_fill["metadata"]["model_id"] == "gpt-alpha-e2e-093"
    assert entry_fill["metadata"]["research_data_ref"] == research_ref["uri"]
    assert entry_fill["metadata"]["news_data_ref"] == research_row["content_ref"]
    assert exit_fill["metadata"]["llm_decision_id"] == "decision-e2e-093-epam-exit"
    assert exit_fill["metadata"]["research_note_ref"] == research_row["content_ref"]
    assert exit_fill["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 36.0
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
    recovered_records = recovered_adapter.query_lineage_records("runtime_binding", "binding-e2e-loop-093")
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_entry = records_by_id[stored_fills[0]["event_id"]]
    recovered_exit = records_by_id[stored_fills[1]["event_id"]]
    assert recovered_entry["alpha_context"]["research_data_ref"] == research_ref["uri"]
    assert recovered_entry["alpha_context"]["news_data_ref"] == research_row["content_ref"]
    assert recovered_entry["alpha_context"]["source_evidence_refs"][1]["uri"] == research_ref["uri"]
    assert recovered_exit["alpha_context"]["llm_response_id"] == "response-e2e-093-epam-guidance"
    assert recovered_exit["alpha_context"]["llm_decision_id"] == "decision-e2e-093-epam-exit"
    assert recovered_exit["order_context"]["fill_quantity"] == -3.0
    assert records_by_id[stored_pnl["event_id"]]["order_context"]["pnl"] == 36.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_pnl,
        sponsor_persona_id="persona-llm-news-research-sponsor",
        contributing_persona_ids=["persona-llm-news-research-ops"],
        summary=(
            "EPAM LLM news/research roundtrip fetched market rows plus a research note, opened 3 shares at 100.0, "
            "closed at 112.0 for 36.0 paper PnL, recovered adapter feedback, and wrote the evidence into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-news-research-ops",
                "summary": "LLM feedback preserved market, news, research, prompt, fill, and PnL lineage.",
                "proposal_ids": [signal["signal_id"] for signal in signals],
                "tags": ["llm_news_research", "paper_fill", "paper_performance"],
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
    writeback_payload["tags"].extend(["llm_news_research", "paper_fill", "paper_performance"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="EPAM LLM news research market PnL",
        tags=["llm_news_research", "paper_performance"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence_items = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    pnl_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "pnl_snapshot")
    exit_lineage = next(item["lineage"] for item in evidence_items if item["event_type"] == "paper_fill_simulated")
    assert pnl_lineage["order_context"]["pnl"] == 36.0
    assert exit_lineage["alpha_context"]["research_data_ref"] == research_ref["uri"]
    assert exit_lineage["alpha_context"]["news_data_ref"] == research_row["content_ref"]
    assert exit_lineage["order_context"]["fill_price"] == 112.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-news-research-ops",
        query="news research fill pnl",
        tags=["paper_performance"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    persona_exit_lineage = next(
        item["lineage"] for item in persona_evidence if item["event_type"] == "paper_fill_simulated"
    )
    assert persona_exit_lineage["alpha_context"]["llm_prompt_id"] == "prompt-e2e-093-epam-news"


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


def _research_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-093-llm-research",
        "source_type": "news",
        "provider": "E2E Loop 093 Static LLM Research Notes",
        "license_scope": "internal",
        "metadata": {
            "dataset": "llm_news_research_note",
            "access_scope": ["research", "audit_evidence"],
            "entitlement_tags": ["llm-news-research-internal"],
            "feature_targets": ["features/llm_news_research_inputs"],
            "schema_hash": "llm_news_research_note.e2e_loop_093.v1",
        },
    }


def _research_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-093-epam-llm-note",
        "title": "EPAM guidance revision LLM research note for E2E loop 093",
        "content_ref": "news://llm-research/EPAM/2026-06-10/guidance",
        "metadata": {
            "dataset": "llm_news_research_note",
            "date": "2026-06-10",
            "event_time": "2026-06-10T20:05:00Z",
            "available_time": "2026-06-10T20:06:00Z",
            "published_at": "2026-06-10T20:05:00Z",
            "publisher": "Pantheon Internal LLM Research",
            "symbol": "EPAM",
            "symbols": ["EPAM"],
            "event_type": "guidance_revision",
            "model_id": "gpt-alpha-e2e-093",
            "prompt_bundle_id": "prompt-bundle-e2e-093",
            "llm_prompt_id": "prompt-e2e-093-epam-news",
            "llm_response_id": "response-e2e-093-epam-guidance",
            "entry_decision_id": "decision-e2e-093-epam-entry",
            "exit_decision_id": "decision-e2e-093-epam-exit",
            "sentiment_score": 0.67,
            "body": "EPAM guidance revision note supports a tactical long and a next-day exit.",
            "rationale_summary": "Guidance revision supports a tactical long and next-day exit.",
        },
    }


def _llm_news_research_signals(
    market_rows: list[dict[str, Any]],
    research_row: dict[str, Any],
    *,
    strategy_id: str,
    market_ref_uris: list[str],
    research_ref: dict[str, Any],
    market_ingest_run_id: str,
    research_ingest_run_id: str,
) -> list[dict[str, Any]]:
    rows_by_date = {row["metadata"]["date"]: row for row in market_rows}
    research = research_row["metadata"]
    source_evidence_refs = [
        {
            "ref_type": "normalized_rows",
            "dataset": "us_llm_news_research_price_daily",
            "uri": market_ref_uris[0],
            "ingest_run_id": market_ingest_run_id,
        },
        {
            "ref_type": "normalized_rows",
            "dataset": research_ref["dataset"],
            "uri": research_ref["uri"],
            "ingest_run_id": research_ingest_run_id,
        },
    ]
    common: dict[str, Any] = {
        "strategy_id": strategy_id,
        "symbol": "EPAM.US",
        "quantity": 3.0,
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-news-research-normalizer",
        "normalized_ref_uris": market_ref_uris,
        "ingest_run_id": market_ingest_run_id,
        "confidence_score": 0.84,
        "order_type": "MARKET",
    }
    common_metadata = {
        "model_id": research["model_id"],
        "prompt_bundle_id": research["prompt_bundle_id"],
        "llm_prompt_id": research["llm_prompt_id"],
        "llm_response_id": research["llm_response_id"],
        "research_note_ref": research_row["content_ref"],
        "llm_note_ref": research_ref["uri"],
        "research_data_ref": research_ref["uri"],
        "news_data_ref": research_row["content_ref"],
        "source_evidence_refs": source_evidence_refs,
    }
    entry = signal_from_market_row(
        rows_by_date["2026-06-09"],
        signal_id="llm-epam-news-entry-093",
        action="BUY",
        direction="LONG",
        alpha_source="llm_news_research_market_entry",
        extra_metadata={
            **common_metadata,
            "llm_decision_id": research["entry_decision_id"],
        },
        **common,
    )
    exit_signal = signal_from_market_row(
        rows_by_date["2026-06-10"],
        signal_id="llm-epam-news-exit-093",
        action="SELL",
        direction="LONG",
        alpha_source="llm_news_research_market_exit",
        extra_metadata={
            **common_metadata,
            "llm_decision_id": research["exit_decision_id"],
        },
        **common,
    )
    return [entry, exit_signal]
