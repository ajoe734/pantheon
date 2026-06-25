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


def test_lean_llm_hold_noop_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    market_ingest = _configure_and_run_ingest(
        source_client,
        connector=_market_connector(),
        records=[_market_record()],
        connector_id="conn-e2e-loop-020-us-riskoff-prices",
        trace_id="trace-e2e-loop-020-market-fetch",
        next_watermark="2026-06-12T23:50:00Z",
    )
    risk_note_ingest = _configure_and_run_ingest(
        source_client,
        connector=_llm_risk_note_connector(),
        records=[_llm_risk_note_record()],
        connector_id="conn-e2e-loop-020-llm-risk-notes",
        trace_id="trace-e2e-loop-020-risk-note-fetch",
        next_watermark="2026-06-12T23:51:00Z",
    )

    market_ref = market_ingest["storage_refs"]["normalized_refs"][0]
    risk_note_ref = risk_note_ingest["storage_refs"]["normalized_refs"][0]
    market_row = _read_jsonl(Path(market_ref["uri"]))[0]
    risk_note_row = _read_jsonl(Path(risk_note_ref["uri"]))[0]
    assert market_row["metadata"]["symbol"] == "MSFT"
    assert market_row["metadata"]["close"] == 420.0
    assert risk_note_row["metadata"]["llm_decision_id"] == "decision-e2e-loop-020-msft-hold"

    signal = _llm_hold_signal(
        market_row,
        risk_note_row,
        market_ref=market_ref,
        risk_note_ref=risk_note_ref,
        market_ingest_run_id=market_ingest["run"]["ingest_run_id"],
        risk_note_ingest_run_id=risk_note_ingest["run"]["ingest_run_id"],
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
    assert snapshot["paper_state"]["positions"] == []
    assert snapshot["paper_state"]["recent_order_events"][0]["event_type"] == "paper_order_simulated"
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    assert len(noop_events) == 1
    assert fill_events == []
    noop_event = noop_events[0]
    assert noop_event["metrics"]["action"] == "hold_signal_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["signal_id"] == "llm-hold-msft-riskoff-020"
    assert noop_event["metadata"]["alpha_source"] == "llm_riskoff_agent"
    assert noop_event["metadata"]["model_id"] == "gpt-4.1-risk"
    assert noop_event["metadata"]["llm_prompt_id"] == "prompt-e2e-loop-020-msft-risk"
    assert noop_event["metadata"]["llm_response_id"] == "resp-e2e-loop-020-msft-risk"
    assert noop_event["metadata"]["llm_decision_id"] == "decision-e2e-loop-020-msft-hold"
    assert noop_event["metadata"]["noop_reason"] == "hold_signal"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["price"] == 420.0
    assert noop_event["metadata"]["market_price"] == 420.0
    assert noop_event["metadata"]["submitted_to_broker"] is False
    assert noop_event["metadata"]["source_evidence_refs"] == [
        {
            "dataset": "us_riskoff_price_daily",
            "ingest_run_id": market_ingest["run"]["ingest_run_id"],
            "ref_type": "normalized_rows",
            "uri": market_ref["uri"],
        },
        {
            "dataset": "llm_riskoff_hold_note",
            "ingest_run_id": risk_note_ingest["run"]["ingest_run_id"],
            "ref_type": "normalized_rows",
            "uri": risk_note_ref["uri"],
        },
    ]

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 0
    assert pnl_event["metrics"]["fill_rate"] == 0.0
    assert pnl_event["metrics"]["open_position_count"] == 0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored_noop = writer_adapter.ingest_telemetry_event(
        noop_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )
    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_noops = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="paper_order_simulated",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_noops] == [stored_noop["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_noops[0],
        sponsor_persona_id="persona-hold-sponsor",
        contributing_persona_ids=["persona-llm-riskoff"],
        summary=(
            "MSFT market data and an LLM risk-off note produced a HOLD alpha decision; LEAN paper "
            "runtime acknowledged the no-order decision, recovered it from the feedback store, "
            "and wrote the no-op execution evidence into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-llm-riskoff",
                "summary": "LLM HOLD feedback preserved no-order status, source evidence, and broker non-submission.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["llm_hold_noop", "no_order_decision", "source_evidence"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_noop["event_id"]],
    )
    writeback_payload["tags"].extend(["llm_hold_noop", "no_order_decision", "source_evidence"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="MSFT LLM HOLD no order decision source evidence",
        tags=["llm_hold_noop", "no_order_decision"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "llm-hold-msft-riskoff-020"
    assert alpha_context["alpha_source"] == "llm_riskoff_agent"
    assert alpha_context["model_id"] == "gpt-4.1-risk"
    assert alpha_context["llm_prompt_id"] == "prompt-e2e-loop-020-msft-risk"
    assert alpha_context["llm_response_id"] == "resp-e2e-loop-020-msft-risk"
    assert alpha_context["llm_decision_id"] == "decision-e2e-loop-020-msft-hold"
    assert alpha_context["market_price"] == 420.0
    assert alpha_context["source_evidence_refs"][1]["dataset"] == "llm_riskoff_hold_note"
    assert order_context["noop_reason"] == "hold_signal"
    assert order_context["decision_status"] == "no_order"
    assert order_context["order_status"] == "not_submitted"
    assert order_context["quantity_type"] == "SHARES"
    assert order_context["requested_quantity"] == 0.0
    assert order_context["price"] == 420.0
    assert order_context["fill_rate"] == 0.0
    assert order_context["noop_count"] == 1
    assert order_context["broker_submission_status"] == "not_submitted_signal_noop"
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-llm-riskoff",
        query="HOLD no order source evidence",
        tags=["source_evidence"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    assert persona_evidence["lineage"]["alpha_context"]["research_data_ref"] == risk_note_ref["uri"]
    assert persona_evidence["lineage"]["order_context"]["decision_status"] == "no_order"


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
        "connector_id": "conn-e2e-loop-020-us-riskoff-prices",
        "source_type": "market",
        "provider": "E2E Loop 020 Static US Risk-Off Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_riskoff_price_daily",
            "feature_targets": ["features/llm_hold_noop_inputs"],
            "schema_hash": "us_riskoff_price_daily.e2e_loop_020.v1",
        },
    }


def _llm_risk_note_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-020-llm-risk-notes",
        "source_type": "news",
        "provider": "E2E Loop 020 Static LLM Risk Notes",
        "license_scope": "internal",
        "metadata": {
            "dataset": "llm_riskoff_hold_note",
            "access_scope": ["research", "audit_evidence"],
            "entitlement_tags": ["llm-riskoff-hold-internal"],
            "feature_targets": ["features/llm_hold_noop_inputs"],
            "schema_hash": "llm_riskoff_hold_note.e2e_loop_020.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-020-msft-price",
        "title": "MSFT daily close for E2E loop 020",
        "content_ref": "market://us_riskoff_price_daily/MSFT/2026-06-12",
        "metadata": {
            "dataset": "us_riskoff_price_daily",
            "date": "2026-06-12",
            "symbol": "MSFT",
            "open": 422.0,
            "high": 424.0,
            "low": 417.0,
            "close": 420.0,
            "volume": 2100000,
        },
    }


def _llm_risk_note_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-020-msft-llm-risk-note",
        "title": "MSFT LLM risk-off HOLD note for E2E loop 020",
        "content_ref": "llm-note://riskoff/MSFT/2026-06-12/hold",
        "metadata": {
            "dataset": "llm_riskoff_hold_note",
            "date": "2026-06-12",
            "event_time": "2026-06-12T23:48:00Z",
            "available_time": "2026-06-12T23:49:00Z",
            "published_at": "2026-06-12T23:48:00Z",
            "publisher": "Pantheon Internal LLM Risk",
            "symbol": "MSFT",
            "symbols": ["MSFT"],
            "event_type": "macro_risk_hold",
            "model_id": "gpt-4.1-risk",
            "prompt_bundle_id": "prompt-bundle-riskoff-hold-v1",
            "llm_prompt_id": "prompt-e2e-loop-020-msft-risk",
            "llm_response_id": "resp-e2e-loop-020-msft-risk",
            "llm_decision_id": "decision-e2e-loop-020-msft-hold",
            "risk_score": 0.87,
            "body": "LLM risk note recommends HOLD because macro event risk outweighs fresh long entry.",
            "rationale_summary": "Avoid new exposure until event risk clears.",
        },
    }


def _llm_hold_signal(
    market_row: dict[str, Any],
    risk_note_row: dict[str, Any],
    *,
    market_ref: dict[str, Any],
    risk_note_ref: dict[str, Any],
    market_ingest_run_id: str,
    risk_note_ingest_run_id: str,
) -> dict[str, Any]:
    market = market_row["metadata"]
    note = risk_note_row["metadata"]
    source_evidence_refs = [
        {
            "ref_type": "normalized_rows",
            "dataset": market_ref["dataset"],
            "uri": market_ref["uri"],
            "ingest_run_id": market_ingest_run_id,
        },
        {
            "ref_type": "normalized_rows",
            "dataset": risk_note_ref["dataset"],
            "uri": risk_note_ref["uri"],
            "ingest_run_id": risk_note_ingest_run_id,
        },
    ]
    return {
        "signal_id": "llm-hold-msft-riskoff-020",
        "version": "1.0",
        "strategy_id": "strategy-llm-hold-noop",
        "timestamp": _iso_now(),
        "symbol": "MSFT.US",
        "action": "HOLD",
        "direction": "LONG",
        "quantity": 0.0,
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-riskoff-normalizer",
        "metadata": {
            "alpha_source": "llm_riskoff_agent",
            "confidence_score": 0.91,
            "model_id": note["model_id"],
            "prompt_bundle_id": note["prompt_bundle_id"],
            "llm_prompt_id": note["llm_prompt_id"],
            "llm_response_id": note["llm_response_id"],
            "llm_decision_id": note["llm_decision_id"],
            "research_note_ref": risk_note_row["content_ref"],
            "llm_note_ref": risk_note_ref["uri"],
            "market_data_ref": market_ref["uri"],
            "research_data_ref": risk_note_ref["uri"],
            "news_data_ref": risk_note_row["content_ref"],
            "source_evidence_refs": source_evidence_refs,
            "market_data": {
                "dataset": market["dataset"],
                "symbol": market["symbol"],
                "date": market["date"],
                "close": market["close"],
                "content_ref": market_row["content_ref"],
            },
            "research_data": {
                "dataset": note["dataset"],
                "symbol": note["symbol"],
                "event_time": note["event_time"],
                "event_type": note["event_type"],
                "content_ref": risk_note_row["content_ref"],
            },
            "normalized_data_ref": market_ref["uri"],
            "source_dataset_ref": "us_riskoff_price_daily+llm_riskoff_hold_note",
            "ingest_run_id": market_ingest_run_id,
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-020",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-020-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-020",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-020",
                "runtime_id": "paper-runtime-020",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-llm-hold",
                "artifact_version": "20.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-llm-hold",
                "persona_capital_binding_id": "pcb-paper-llm-hold",
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
            "event_id": f"e2e-loop-020-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-020",
            "runtime_id": "paper-runtime-020",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-llm-hold",
            "artifact_version": "20.0.0",
            "plan_id": "plan-paper-llm-hold",
            "persona_capital_binding_id": "pcb-paper-llm-hold",
            "target": {
                "registry_id": "artifact-paper-llm-hold",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-llm-hold",
                "artifact_version": "20.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-020-runtime",
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
