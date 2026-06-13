from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_llm_cash_value_market_short_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    market_ingest = _configure_and_run_ingest(
        source_client,
        connector=_market_connector(),
        records=[_market_record()],
        connector_id="conn-e2e-loop-054-us-prices",
        trace_id="trace-e2e-loop-054-market-fetch",
        next_watermark="2026-06-12T20:54:00Z",
    )
    research_ingest = _configure_and_run_ingest(
        source_client,
        connector=_llm_research_connector(),
        records=[_llm_research_record()],
        connector_id="conn-e2e-loop-054-llm-research",
        trace_id="trace-e2e-loop-054-research-fetch",
        next_watermark="2026-06-12T20:55:00Z",
    )

    market_ref = market_ingest["storage_refs"]["normalized_refs"][0]
    research_ref = research_ingest["storage_refs"]["normalized_refs"][0]
    market_row = _read_jsonl(Path(market_ref["uri"]))[0]
    research_row = _read_jsonl(Path(research_ref["uri"]))[0]
    assert market_row["metadata"]["symbol"] == "CRM"
    assert market_row["metadata"]["close"] == 250.0
    assert research_row["metadata"]["llm_decision_id"] == "decision-e2e-loop-054-crm-short"

    signal = _llm_cash_market_short_signal(
        market_row,
        research_row,
        market_ref=market_ref,
        research_ref=research_ref,
        market_ingest_run_id=market_ingest["run"]["ingest_run_id"],
        research_ingest_run_id=research_ingest["run"]["ingest_run_id"],
    )
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    assert snapshot["paper_state"]["execution_event_count"] == 1
    assert snapshot["paper_state"]["positions"] == [
        {"symbol": "CRM", "quantity": -5.0, "price": 250.0}
    ]

    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "market_order"
    assert fill_event["metrics"]["fill_quantity"] == -5.0
    assert fill_event["metrics"]["fill_price"] == 250.0
    assert fill_event["metadata"]["signal_id"] == "llm-crm-cash-market-short-054"
    assert fill_event["metadata"]["alpha_source"] == "llm_event_cash_short_agent"
    assert fill_event["metadata"]["model_id"] == "gpt-research-short-v2"
    assert fill_event["metadata"]["prompt_bundle_id"] == "prompt-bundle-short-alpha-v1"
    assert fill_event["metadata"]["llm_prompt_id"] == "prompt-e2e-loop-054-crm-short"
    assert fill_event["metadata"]["llm_response_id"] == "resp-e2e-loop-054-crm-margin"
    assert fill_event["metadata"]["llm_decision_id"] == "decision-e2e-loop-054-crm-short"
    assert fill_event["metadata"]["quantity_type"] == "CASH_VALUE"
    assert fill_event["metadata"]["order_type"] == "MARKET"
    assert fill_event["metadata"]["requested_quantity"] == 1250.0
    assert fill_event["metadata"]["market_price"] == 250.0
    assert fill_event["metadata"]["source_evidence_refs"] == [
        {
            "dataset": "us_llm_cash_short_price_daily",
            "ingest_run_id": market_ingest["run"]["ingest_run_id"],
            "ref_type": "normalized_rows",
            "uri": market_ref["uri"],
        },
        {
            "dataset": "llm_cash_short_research_note",
            "ingest_run_id": research_ingest["run"]["ingest_run_id"],
            "ref_type": "normalized_rows",
            "uri": research_ref["uri"],
        },
    ]
    assert fill_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["open_bracket_order_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_fill = writer_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )
    stored_pnl = writer_adapter.ingest_telemetry_event(
        pnl_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_records = recovered_adapter.query_lineage_records(
        "runtime_binding",
        "binding-e2e-loop-054",
    )
    records_by_id = {record["record_id"]: record for record in recovered_records}
    recovered_fill = records_by_id[stored_fill["event_id"]]
    assert recovered_fill["alpha_context"]["llm_response_id"] == "resp-e2e-loop-054-crm-margin"
    assert recovered_fill["alpha_context"]["research_data_ref"] == research_ref["uri"]
    assert recovered_fill["order_context"]["quantity_type"] == "CASH_VALUE"
    assert recovered_fill["order_context"]["order_type"] == "MARKET"
    assert recovered_fill["order_context"]["requested_quantity"] == 1250.0
    assert recovered_fill["order_context"]["fill_quantity"] == -5.0
    assert recovered_fill["order_context"]["fill_price"] == 250.0
    assert recovered_fill["order_context"]["market_price"] == 250.0
    assert recovered_fill["order_context"]["submitted_to_broker"] is False
    recovered_pnl_context = records_by_id[stored_pnl["event_id"]]["order_context"]
    assert recovered_pnl_context["fill_event_count"] == 1
    assert recovered_pnl_context["fill_rate"] == 1.0
    assert recovered_pnl_context["open_position_count"] == 1

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-llm-cash-short-sponsor",
        contributing_persona_ids=["persona-llm-short-researcher"],
        summary=(
            "CRM market data and an LLM margin-pressure research note produced a SELL/SHORT "
            "CASH_VALUE market order; LEAN sized it to a -5 share paper fill, recovered the "
            "LLM/order feedback, and wrote the short CASH_VALUE context into memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-short-researcher",
                "summary": "LLM cash short feedback preserved prompt lineage, source evidence, and negative fill.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["llm_cash_short", "paper_fill", "source_evidence"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["tags"].extend(["llm_cash_short", "paper_fill", "source_evidence"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="CRM LLM cash value market short source evidence",
        tags=["llm_cash_short", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    institutional_evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = institutional_evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "llm-crm-cash-market-short-054"
    assert alpha_context["llm_decision_id"] == "decision-e2e-loop-054-crm-short"
    assert alpha_context["source_evidence_refs"][1]["dataset"] == "llm_cash_short_research_note"
    assert alpha_context["research_data_ref"] == research_ref["uri"]
    assert order_context["quantity_type"] == "CASH_VALUE"
    assert order_context["order_type"] == "MARKET"
    assert order_context["fill_quantity"] == -5.0
    assert order_context["requested_quantity"] == 1250.0
    assert order_context["submitted_to_broker"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-short-researcher",
        query="LLM cash short negative fill",
        tags=["source_evidence"],
        limit=3,
    )
    assert persona_hits
    persona_lineage = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]
    assert persona_lineage["strategy_id"] == "strategy-llm-cash-market-short"
    assert persona_lineage["alpha_context"]["model_id"] == "gpt-research-short-v2"
    assert persona_lineage["order_context"]["fill_quantity"] == -5.0


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
    assert body["run"]["status"] == "completed", body
    return body


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-054-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 054 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_llm_cash_short_price_daily",
            "feature_targets": ["features/llm_cash_short_inputs"],
            "schema_hash": "us_llm_cash_short_price_daily.e2e_loop_054.v1",
        },
    }


def _llm_research_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-054-llm-research",
        "source_type": "news",
        "provider": "E2E Loop 054 Static LLM Short Research",
        "license_scope": "internal",
        "metadata": {
            "dataset": "llm_cash_short_research_note",
            "access_scope": ["research", "audit_evidence"],
            "entitlement_tags": ["llm-cash-short-internal-research"],
            "feature_targets": ["features/llm_cash_short_inputs"],
            "schema_hash": "llm_cash_short_research_note.e2e_loop_054.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-054-crm-price",
        "title": "CRM daily close for E2E loop 054",
        "content_ref": "market://us_llm_cash_short_price_daily/CRM/2026-06-12",
        "metadata": {
            "dataset": "us_llm_cash_short_price_daily",
            "date": "2026-06-12",
            "symbol": "CRM",
            "open": 252.0,
            "high": 253.0,
            "low": 247.0,
            "close": 250.0,
            "volume": 1750000,
        },
    }


def _llm_research_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-054-crm-llm-note",
        "title": "CRM margin-pressure LLM short note for E2E loop 054",
        "content_ref": "llm-note://cash-short/CRM/2026-06-12/margin-pressure",
        "metadata": {
            "dataset": "llm_cash_short_research_note",
            "date": "2026-06-12",
            "event_time": "2026-06-12T20:40:00Z",
            "available_time": "2026-06-12T20:41:00Z",
            "published_at": "2026-06-12T20:40:00Z",
            "publisher": "Pantheon Internal LLM Research",
            "symbol": "CRM",
            "symbols": ["CRM"],
            "event_type": "margin_pressure_commentary",
            "model_id": "gpt-research-short-v2",
            "prompt_bundle_id": "prompt-bundle-short-alpha-v1",
            "llm_prompt_id": "prompt-e2e-loop-054-crm-short",
            "llm_response_id": "resp-e2e-loop-054-crm-margin",
            "llm_decision_id": "decision-e2e-loop-054-crm-short",
            "sentiment_score": -0.68,
            "body": "CRM margin-pressure note supports a small short exposure.",
            "rationale_summary": "Margin pressure and guidance tone support a small short signal.",
        },
    }


def _llm_cash_market_short_signal(
    market_row: dict[str, Any],
    research_row: dict[str, Any],
    *,
    market_ref: dict[str, Any],
    research_ref: dict[str, Any],
    market_ingest_run_id: str,
    research_ingest_run_id: str,
) -> dict[str, Any]:
    market = market_row["metadata"]
    research = research_row["metadata"]
    source_evidence_refs = [
        {
            "ref_type": "normalized_rows",
            "dataset": market_ref["dataset"],
            "uri": market_ref["uri"],
            "ingest_run_id": market_ingest_run_id,
        },
        {
            "ref_type": "normalized_rows",
            "dataset": research_ref["dataset"],
            "uri": research_ref["uri"],
            "ingest_run_id": research_ingest_run_id,
        },
    ]
    return {
        "signal_id": "llm-crm-cash-market-short-054",
        "version": "1.0",
        "strategy_id": "strategy-llm-cash-market-short",
        "timestamp": _iso_now(),
        "symbol": "CRM.US",
        "action": "SELL",
        "direction": "SHORT",
        "quantity": 1250.0,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-llm-cash-short-normalizer",
        "metadata": {
            "alpha_source": "llm_event_cash_short_agent",
            "confidence_score": 0.79,
            "model_id": research["model_id"],
            "prompt_bundle_id": research["prompt_bundle_id"],
            "llm_prompt_id": research["llm_prompt_id"],
            "llm_response_id": research["llm_response_id"],
            "llm_decision_id": research["llm_decision_id"],
            "research_note_ref": research_row["content_ref"],
            "llm_note_ref": research_ref["uri"],
            "market_data_ref": market_ref["uri"],
            "research_data_ref": research_ref["uri"],
            "news_data_ref": research_row["content_ref"],
            "source_evidence_refs": source_evidence_refs,
            "market_data": {
                "dataset": market["dataset"],
                "symbol": market["symbol"],
                "date": market["date"],
                "close": market["close"],
                "content_ref": market_row["content_ref"],
            },
            "research_data": {
                "dataset": research["dataset"],
                "symbol": research["symbol"],
                "event_time": research["event_time"],
                "event_type": research["event_type"],
                "content_ref": research_row["content_ref"],
            },
            "normalized_data_ref": market_ref["uri"],
            "source_dataset_ref": "us_llm_cash_short_price_daily+llm_cash_short_research_note",
            "ingest_run_id": market_ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-054",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-054-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-054",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-054",
                "runtime_id": "paper-runtime-054",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-llm-cash-short",
                "artifact_version": "7.1.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-llm-cash-short",
                "persona_capital_binding_id": "pcb-paper-llm-cash-short",
                "status": "active",
            }
        ]


class _CanonicalTelemetryRecorder:
    enabled = True

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        metadata = dict(metadata or {})
        index = len(self.events) + 1
        event = {
            "event_id": f"e2e-loop-054-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-054",
            "runtime_id": "paper-runtime-054",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-llm-cash-short",
            "artifact_version": "7.1.0",
            "plan_id": "plan-paper-llm-cash-short",
            "persona_capital_binding_id": "pcb-paper-llm-cash-short",
            "target": {
                "registry_id": "artifact-paper-llm-cash-short",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-llm-cash-short",
                "artifact_version": "7.1.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-054-runtime",
        }
        self.events.append(event)
        return True

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "url": "memory://telemetry",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
