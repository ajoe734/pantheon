from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def test_lean_signal_conflict_loser_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T23:59:58Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-029-us-conflict-loser-prices",
            "trace_id": "trace-e2e-loop-029-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "TEAM"
    assert row["metadata"]["close"] == 185.0

    pure_quant_loser, llm_winner = _conflicting_signals(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([pure_quant_loser, llm_winner]),
        identity=_runtime_identity(),
        runtime_manager_client=_RuntimeManagerClient(),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    snapshot = runtime.drain_once()

    assert snapshot["status"] == "ok"
    assert snapshot["paper_state"]["processed_signal_count"] == 2
    assert snapshot["paper_state"]["execution_event_count"] == 2
    position = snapshot["paper_state"]["positions"][0]
    assert position["symbol"] == "TEAM"
    assert position["quantity"] == 5.0
    assert position["price"] == 185.0
    assert [event["event_type"] for event in snapshot["paper_state"]["recent_order_events"]] == [
        "paper_order_simulated",
        "paper_fill_simulated",
    ]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert len(fill_events) == 1
    assert len(noop_events) == 1
    fill_event = fill_events[0]
    noop_event = noop_events[0]
    assert fill_event["metadata"]["signal_id"] == "llm-conflict-team-winner-029"
    assert fill_event["metadata"]["alpha_source"] == "llm_conflict_resolution_winner"
    assert fill_event["metadata"]["model_id"] == "gpt-signal-arb-e2e"
    assert fill_event["metrics"]["fill_quantity"] == 5.0
    assert fill_event["metrics"]["fill_price"] == 185.0

    assert noop_event["metadata"]["signal_id"] == "quant-conflict-team-loser-029"
    assert noop_event["metrics"]["action"] == "signal_conflict_loser_noop"
    assert noop_event["metrics"]["noop_count"] == 1
    assert noop_event["metrics"]["requested_quantity"] == 12.0
    assert noop_event["metrics"]["computed_quantity"] == 0.0
    assert noop_event["metrics"]["fill_quantity"] == 0.0
    assert noop_event["metrics"]["fill_rate"] == 0.0
    assert noop_event["metadata"]["alpha_source"] == "pure_quant_conflict_loser"
    assert noop_event["metadata"]["noop_reason"] == "signal_conflict_loser"
    assert noop_event["metadata"]["filter_reason"] == "signal_conflict_loser"
    assert noop_event["metadata"]["conflict_winner_signal_id"] == "llm-conflict-team-winner-029"
    assert noop_event["metadata"]["conflict_loser_signal_id"] == "quant-conflict-team-loser-029"
    assert noop_event["metadata"]["conflict_symbol"] == "TEAM.US"
    assert noop_event["metadata"]["conflict_resolution_rule"] == "last_write_wins_timestamp_then_confidence"
    assert noop_event["metadata"]["decision_status"] == "no_order"
    assert noop_event["metadata"]["order_status"] == "not_submitted"
    assert noop_event["metadata"]["quantity_type"] == "SHARES"
    assert noop_event["metadata"]["requested_quantity"] == 12.0
    assert noop_event["metadata"]["computed_quantity"] == 0.0
    assert noop_event["metadata"]["price"] == 185.0
    assert noop_event["metadata"]["market_price"] == 185.0
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
    stored_noop = writer_adapter.ingest_telemetry_event(
        noop_event,
        strategy_id=pure_quant_loser["strategy_id"],
        promotion_state="paper",
    )
    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_noops = recovered_adapter.query_telemetry(
        strategy_id=pure_quant_loser["strategy_id"],
        event_type="paper_order_simulated",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_noops] == [stored_noop["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_noops[0],
        sponsor_persona_id="persona-conflict-loser-sponsor",
        contributing_persona_ids=["persona-conflict-arbiter"],
        summary=(
            "TEAM market data produced a pure quant alpha and a newer LLM alpha for the same symbol; "
            "LEAN suppressed the older quant loser before order submission, filled only the LLM winner, "
            "recovered loser feedback through the adapter, and wrote the conflict decision into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-conflict-arbiter",
                "summary": "Conflict-loser feedback preserved winner and loser signal IDs plus broker non-submission.",
                "proposal_ids": [pure_quant_loser["signal_id"], llm_winner["signal_id"]],
                "tags": ["signal_conflict", "conflict_loser", "paper_noop"],
            }
        ],
        proposal_ids=[pure_quant_loser["signal_id"], llm_winner["signal_id"], stored_noop["event_id"]],
    )
    writeback_payload["tags"].extend(["signal_conflict", "conflict_loser", "paper_noop"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="TEAM signal conflict loser no order winner signal id",
        tags=["signal_conflict", "conflict_loser"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "quant-conflict-team-loser-029"
    assert alpha_context["alpha_source"] == "pure_quant_conflict_loser"
    assert alpha_context["market_price"] == 185.0
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["noop_reason"] == "signal_conflict_loser"
    assert order_context["filter_reason"] == "signal_conflict_loser"
    assert order_context["conflict_winner_signal_id"] == "llm-conflict-team-winner-029"
    assert order_context["conflict_loser_signal_id"] == "quant-conflict-team-loser-029"
    assert order_context["conflict_symbol"] == "TEAM.US"
    assert order_context["conflict_resolution_rule"] == "last_write_wins_timestamp_then_confidence"
    assert order_context["decision_status"] == "no_order"
    assert order_context["order_status"] == "not_submitted"
    assert order_context["quantity_type"] == "SHARES"
    assert order_context["requested_quantity"] == 12.0
    assert order_context["computed_quantity"] == 0.0
    assert order_context["price"] == 185.0
    assert order_context["fill_rate"] == 0.0
    assert order_context["noop_count"] == 1
    assert order_context["broker_submission_status"] == "not_submitted_signal_filtered"
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-conflict-arbiter",
        query="winner loser signal ids broker non submission",
        tags=["paper_noop"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["conflict_winner_signal_id"] == "llm-conflict-team-winner-029"
    assert persona_order_context["conflict_loser_signal_id"] == "quant-conflict-team-loser-029"
    assert persona_order_context["submitted_to_broker"] is False


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-029-us-conflict-loser-prices",
        "source_type": "market",
        "provider": "E2E Loop 029 Static Conflict Loser Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_conflict_loser_price_daily",
            "feature_targets": ["features/signal_conflict_loser_inputs"],
            "schema_hash": "us_conflict_loser_price_daily.e2e_loop_029.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-029-team",
        "title": "TEAM daily close for E2E loop 029",
        "content_ref": "market://us_conflict_loser_price_daily/TEAM/2026-06-12",
        "metadata": {
            "dataset": "us_conflict_loser_price_daily",
            "date": "2026-06-12",
            "symbol": "TEAM",
            "open": 183.0,
            "high": 188.0,
            "low": 181.0,
            "close": 185.0,
            "volume": 1400000,
        },
    }


def _conflicting_signals(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    loser = _signal(
        row,
        signal_id="quant-conflict-team-loser-029",
        timestamp=base_time - timedelta(minutes=4),
        quantity=12,
        quantity_type="SHARES",
        source_worker="mock-quant-conflict-loser-normalizer",
        alpha_source="pure_quant_conflict_loser",
        confidence_score=0.91,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_run_id,
    )
    winner = _signal(
        row,
        signal_id="llm-conflict-team-winner-029",
        timestamp=base_time,
        quantity=925,
        quantity_type="CASH_VALUE",
        source_worker="mock-llm-conflict-winner-normalizer",
        alpha_source="llm_conflict_resolution_winner",
        confidence_score=0.79,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_run_id,
        model_id="gpt-signal-arb-e2e",
        prompt_bundle_id="prompt-conflict-loser-029",
    )
    return loser, winner


def _signal(
    row: dict[str, Any],
    *,
    signal_id: str,
    timestamp: datetime,
    quantity: float,
    quantity_type: str,
    source_worker: str,
    alpha_source: str,
    confidence_score: float,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
    model_id: str | None = None,
    prompt_bundle_id: str | None = None,
) -> dict[str, Any]:
    metadata = row["metadata"]
    signal_metadata: dict[str, Any] = {
        "alpha_source": alpha_source,
        "confidence_score": confidence_score,
        "market_data_ref": normalized_ref["uri"],
        "source_evidence_refs": [
            {
                "ref_type": "normalized_rows",
                "dataset": normalized_ref["dataset"],
                "uri": normalized_ref["uri"],
                "ingest_run_id": ingest_run_id,
            }
        ],
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
    if model_id:
        signal_metadata["model_id"] = model_id
    if prompt_bundle_id:
        signal_metadata["prompt_bundle_id"] = prompt_bundle_id
    return {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": "strategy-conflict-loser-feedback",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": "TEAM.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": quantity,
        "quantity_type": quantity_type,
        "source_worker": source_worker,
        "metadata": signal_metadata,
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-029",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-029-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-029",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-029",
                "runtime_id": "paper-runtime-029",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-conflict-loser",
                "artifact_version": "29.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-conflict-loser",
                "persona_capital_binding_id": "pcb-paper-conflict-loser",
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
            "event_id": f"e2e-loop-029-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-029",
            "runtime_id": "paper-runtime-029",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-conflict-loser",
            "artifact_version": "29.0.0",
            "plan_id": "plan-paper-conflict-loser",
            "persona_capital_binding_id": "pcb-paper-conflict-loser",
            "target": {
                "registry_id": "artifact-paper-conflict-loser",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-conflict-loser",
                "artifact_version": "29.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-029-runtime",
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
