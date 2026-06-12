from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_llm_event_alpha_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    market_ingest = _configure_and_run_ingest(
        source_client,
        connector=_market_connector(),
        records=[_market_record()],
        connector_id="conn-e2e-loop-007-us-event-prices",
        trace_id="trace-e2e-loop-007-market-fetch",
        next_watermark="2026-06-12T20:15:00Z",
    )
    research_ingest = _configure_and_run_ingest(
        source_client,
        connector=_llm_research_connector(),
        records=[_llm_research_record()],
        connector_id="conn-e2e-loop-007-llm-research",
        trace_id="trace-e2e-loop-007-research-fetch",
        next_watermark="2026-06-12T20:20:00Z",
    )

    market_ref = market_ingest["storage_refs"]["normalized_refs"][0]
    research_ref = research_ingest["storage_refs"]["normalized_refs"][0]
    market_row = _read_jsonl(Path(market_ref["uri"]))[0]
    research_row = _read_jsonl(Path(research_ref["uri"]))[0]
    assert market_row["metadata"]["symbol"] == "MSFT"
    assert market_row["metadata"]["close"] == 410.0
    assert research_row["metadata"]["llm_response_id"] == "resp-e2e-loop-007-msft-capacity"

    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            [
                _llm_event_signal(
                    market_row,
                    research_row,
                    market_ref=market_ref,
                    research_ref=research_ref,
                    market_ingest_run_id=market_ingest["run"]["ingest_run_id"],
                    research_ingest_run_id=research_ingest["run"]["ingest_run_id"],
                )
            ]
        ),
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 1
    position = snapshot["paper_state"]["positions"][0]
    assert position["symbol"] == "MSFT"
    assert position["price"] == 410.0
    assert position["quantity"] == pytest.approx(43.902439, rel=1e-6)
    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "set_holdings"
    assert fill_event["metrics"]["fill_quantity"] == pytest.approx(43.902439, rel=1e-6)
    assert fill_event["metrics"]["fill_price"] == 410.0
    assert fill_event["metadata"]["alpha_source"] == "llm_event_research_agent"
    assert fill_event["metadata"]["model_id"] == "gpt-4.1-research"
    assert fill_event["metadata"]["prompt_bundle_id"] == "prompt-bundle-event-alpha-v2"
    assert fill_event["metadata"]["llm_prompt_id"] == "prompt-e2e-loop-007-msft-event"
    assert fill_event["metadata"]["llm_response_id"] == "resp-e2e-loop-007-msft-capacity"
    assert fill_event["metadata"]["llm_decision_id"] == "decision-e2e-loop-007-msft-long"
    assert fill_event["metadata"]["source_evidence_refs"] == [
        {
            "dataset": "us_event_price_daily",
            "ingest_run_id": market_ingest["run"]["ingest_run_id"],
            "ref_type": "normalized_rows",
            "uri": market_ref["uri"],
        },
        {
            "dataset": "llm_event_research_note",
            "ingest_run_id": research_ingest["run"]["ingest_run_id"],
            "ref_type": "normalized_rows",
            "uri": research_ref["uri"],
        },
    ]

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-llm-event-alpha",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-event-sponsor",
        contributing_persona_ids=["persona-llm-researcher"],
        summary=(
            "MSFT event alpha combined fetched market close=410.0 with an LLM research note, "
            "then placed a paper percent-portfolio order and received a fill."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-researcher",
                "summary": "LLM event alpha feedback preserved prompt, response, and source evidence refs.",
                "proposal_ids": ["llm-event-alpha-msft-007"],
                "tags": ["llm_event_alpha", "paper_fill", "source_evidence"],
            }
        ],
        proposal_ids=["llm-event-alpha-msft-007"],
    )

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )

    assert writeback["created"] is True
    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="MSFT LLM event alpha prompt response evidence",
        tags=["llm_event_alpha", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    institutional_evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    alpha_context = institutional_evidence["lineage"]["alpha_context"]
    assert alpha_context["signal_id"] == "llm-event-alpha-msft-007"
    assert alpha_context["llm_prompt_id"] == "prompt-e2e-loop-007-msft-event"
    assert alpha_context["llm_response_id"] == "resp-e2e-loop-007-msft-capacity"
    assert alpha_context["source_evidence_refs"][1]["dataset"] == "llm_event_research_note"

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-researcher",
        query="LLM event alpha source evidence",
        tags=["source_evidence"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    assert persona_evidence["lineage"]["alpha_context"]["llm_decision_id"] == "decision-e2e-loop-007-msft-long"
    assert persona_evidence["lineage"]["alpha_context"]["research_data_ref"] == research_ref["uri"]


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
        "connector_id": "conn-e2e-loop-007-us-event-prices",
        "source_type": "market",
        "provider": "E2E Loop 007 Static US Event Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_event_price_daily",
            "feature_targets": ["features/llm_event_alpha_inputs"],
            "schema_hash": "us_event_price_daily.e2e_loop_007.v1",
        },
    }


def _llm_research_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-007-llm-research",
        "source_type": "news",
        "provider": "E2E Loop 007 Static LLM Research Notes",
        "license_scope": "internal",
        "metadata": {
            "dataset": "llm_event_research_note",
            "access_scope": ["research", "audit_evidence"],
            "entitlement_tags": ["llm-event-alpha-internal-research"],
            "feature_targets": ["features/llm_event_alpha_inputs"],
            "schema_hash": "llm_event_research_note.e2e_loop_007.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-007-msft-price",
        "title": "MSFT daily close for E2E loop 007",
        "content_ref": "market://us_event_price_daily/MSFT/2026-06-12",
        "metadata": {
            "dataset": "us_event_price_daily",
            "date": "2026-06-12",
            "symbol": "MSFT",
            "open": 405.0,
            "high": 415.0,
            "low": 402.0,
            "close": 410.0,
            "volume": 1800000,
        },
    }


def _llm_research_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-007-msft-llm-note",
        "title": "MSFT capacity event LLM research note for E2E loop 007",
        "content_ref": "llm-note://event-alpha/MSFT/2026-06-12/capacity",
        "metadata": {
            "dataset": "llm_event_research_note",
            "date": "2026-06-12",
            "event_time": "2026-06-12T19:45:00Z",
            "available_time": "2026-06-12T19:46:00Z",
            "published_at": "2026-06-12T19:45:00Z",
            "publisher": "Pantheon Internal LLM Research",
            "symbol": "MSFT",
            "symbols": ["MSFT"],
            "event_type": "cloud_capacity_expansion",
            "model_id": "gpt-4.1-research",
            "prompt_bundle_id": "prompt-bundle-event-alpha-v2",
            "llm_prompt_id": "prompt-e2e-loop-007-msft-event",
            "llm_response_id": "resp-e2e-loop-007-msft-capacity",
            "llm_decision_id": "decision-e2e-loop-007-msft-long",
            "sentiment_score": 0.74,
            "body": "MSFT capacity expansion note supports a short-horizon long signal.",
            "rationale_summary": "Capacity expansion note supports a short-horizon long signal.",
        },
    }


def _llm_event_signal(
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
        "signal_id": "llm-event-alpha-msft-007",
        "version": "1.0",
        "strategy_id": "strategy-llm-event-alpha",
        "timestamp": _iso_now(),
        "symbol": "MSFT.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.20,
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-llm-event-alpha-normalizer",
        "metadata": {
            "alpha_source": "llm_event_research_agent",
            "confidence_score": 0.90,
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
            "source_dataset_ref": "us_event_price_daily+llm_event_research_note",
            "ingest_run_id": market_ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-007",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-007-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-007",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-007",
                "runtime_id": "paper-runtime-007",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-llm-event",
                "artifact_version": "7.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-llm-event",
                "persona_capital_binding_id": "pcb-paper-llm-event",
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
            "event_id": f"e2e-loop-007-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-007",
            "runtime_id": "paper-runtime-007",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-llm-event",
            "artifact_version": "7.0.0",
            "plan_id": "plan-paper-llm-event",
            "persona_capital_binding_id": "pcb-paper-llm-event",
            "target": {
                "registry_id": "artifact-paper-llm-event",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-llm-event",
                "artifact_version": "7.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-007-runtime",
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
        metrics = {"pnl": pnl}
        if extra_metrics:
            metrics.update(extra_metrics)
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
