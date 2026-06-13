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


def test_rebalance_partial_timeout_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T23:59:59Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-039-partial-rebalance-prices",
            "trace_id": "trace-e2e-loop-039-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "IWM"
    assert row["metadata"]["close"] == 200.0

    signal = _partial_rebalance_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
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

    first = runtime.drain_once()
    second = runtime.drain_once()
    third = runtime.drain_once()

    assert first["paper_state"]["processed_signal_count"] == 0
    assert first["paper_state"]["execution_event_count"] == 0
    assert second["paper_state"]["processed_signal_count"] == 0
    assert second["paper_state"]["execution_event_count"] == 0
    assert third["status"] == "ok"
    assert third["paper_state"]["processed_signal_count"] == 1
    assert third["paper_state"]["execution_event_count"] == 1
    assert third["paper_state"]["positions"] == [
        {"symbol": "IWM", "quantity": 50.0, "price": 200.0}
    ]

    fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
    assert noop_events == []
    assert len(fill_events) == 1
    fill_event = fill_events[0]
    assert fill_event["metrics"]["action"] == "set_holdings"
    assert fill_event["metrics"]["fill_quantity"] == 50.0
    assert fill_event["metrics"]["fill_price"] == 200.0
    assert fill_event["metadata"]["signal_id"] == "rebalance-partial-iwm-039"
    assert fill_event["metadata"]["run_id"] == "rebalance-run-partial-039"
    assert fill_event["metadata"]["alpha_source"] == "finrl_partial_rebalance_timeout"
    assert fill_event["metadata"]["quantity_type"] == "PERCENT_PORTFOLIO"
    assert fill_event["metadata"]["requested_quantity"] == 0.1
    assert fill_event["metadata"]["market_price"] == 200.0
    assert fill_event["metadata"]["submitted_to_broker"] is False

    pnl_event = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"][-1]
    assert pnl_event["metrics"]["pnl"] == 0.0
    assert pnl_event["metrics"]["processed_signal_count"] == 1
    assert pnl_event["metrics"]["execution_event_count"] == 1
    assert pnl_event["metrics"]["fill_event_count"] == 1
    assert pnl_event["metrics"]["fill_rate"] == 1.0
    assert pnl_event["metrics"]["open_position_count"] == 1
    assert pnl_event["metrics"]["avg_slippage_bps"] == 0.0

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
    recovered_fills = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="paper_fill_simulated",
        promotion_state="paper",
        limit=3,
    )
    recovered_pnls = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="pnl_snapshot",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_fills] == [stored_fill["event_id"]]
    assert [event["event_id"] for event in recovered_pnls] == [stored_pnl["event_id"]]
    assert recovered_pnls[0]["metrics"]["fill_rate"] == 1.0

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_fills[0],
        sponsor_persona_id="persona-partial-rebalance-sponsor",
        contributing_persona_ids=["persona-finrl-rebalance-ops"],
        summary=(
            "FinRL run_id rebalance-run-partial-039 delivered only the IWM target; LEAN buffered it "
            "until timeout, executed the partial batch as a 10 percent paper rebalance, recovered fill "
            "and PnL feedback through the adapter, and wrote the timeout outcome into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-finrl-rebalance-ops",
                "summary": "Partial rebalance timeout feedback preserved run_id, fill quantity, and fill-rate metrics.",
                "proposal_ids": [signal["signal_id"], "rebalance-run-partial-039"],
                "tags": ["partial_rebalance_timeout", "run_id_timeout", "paper_fill"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_fill["event_id"], stored_pnl["event_id"]],
    )
    writeback_payload["runtime_telemetry_evidence"].append(
        {
            "ref_type": "telemetry_event",
            "ref_id": stored_pnl["event_id"],
            "event_type": stored_pnl["event_type"],
            "lineage": recovered_adapter.build_lineage_record(stored_pnl),
        }
    )
    writeback_payload["tags"].extend(["partial_rebalance_timeout", "run_id_timeout", "paper_fill"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="FinRL partial rebalance timeout IWM fill rate",
        tags=["partial_rebalance_timeout", "run_id_timeout"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert len(evidence) == 2
    fill_lineage = evidence[0]["lineage"]
    alpha_context = fill_lineage["alpha_context"]
    order_context = fill_lineage["order_context"]
    assert alpha_context["signal_id"] == "rebalance-partial-iwm-039"
    assert alpha_context["run_id"] == "rebalance-run-partial-039"
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["quantity_type"] == "PERCENT_PORTFOLIO"
    assert order_context["requested_quantity"] == 0.1
    assert order_context["fill_quantity"] == 50.0
    assert order_context["fill_price"] == 200.0
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False
    pnl_lineage = evidence[1]["lineage"]
    assert pnl_lineage["order_context"]["fill_rate"] == 1.0
    assert pnl_lineage["order_context"]["open_position_count"] == 1
    assert pnl_lineage["order_context"]["pnl"] == 0.0

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-finrl-rebalance-ops",
        query="partial run timeout fill",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"]
    assert persona_evidence[0]["lineage"]["alpha_context"]["run_id"] == "rebalance-run-partial-039"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-039-partial-rebalance-prices",
        "source_type": "market",
        "provider": "E2E Loop 039 Static Partial Rebalance Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_partial_rebalance_price_daily",
            "feature_targets": ["features/finrl_partial_rebalance_timeout_inputs"],
            "schema_hash": "us_partial_rebalance_price_daily.e2e_loop_039.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-039-iwm",
        "title": "IWM daily close for E2E loop 039",
        "content_ref": "market://us_partial_rebalance_price_daily/IWM/2026-06-12",
        "metadata": {
            "dataset": "us_partial_rebalance_price_daily",
            "date": "2026-06-12",
            "symbol": "IWM",
            "open": 199.0,
            "high": 201.5,
            "low": 198.0,
            "close": 200.0,
            "volume": 22000000,
        },
    }


def _partial_rebalance_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "rebalance-partial-iwm-039",
        "version": "1.0",
        "strategy_id": "strategy-finrl-partial-rebalance",
        "run_id": "rebalance-run-partial-039",
        "timestamp": _iso_now(),
        "symbol": "IWM.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 0.10,
        "quantity_type": "PERCENT_PORTFOLIO",
        "source_worker": "mock-finrl-partial-rebalance-normalizer",
        "metadata": {
            "alpha_source": "finrl_partial_rebalance_timeout",
            "confidence_score": 1.0,
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
        },
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "paper-runtime-039",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-039-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-039",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-039",
                "runtime_id": "paper-runtime-039",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-partial-rebalance",
                "artifact_version": "39.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-partial-rebalance",
                "persona_capital_binding_id": "pcb-paper-partial-rebalance",
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
            "event_id": f"e2e-loop-039-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-039",
            "runtime_id": "paper-runtime-039",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-partial-rebalance",
            "artifact_version": "39.0.0",
            "plan_id": "plan-paper-partial-rebalance",
            "persona_capital_binding_id": "pcb-paper-partial-rebalance",
            "target": {
                "registry_id": "artifact-paper-partial-rebalance",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-partial-rebalance",
                "artifact_version": "39.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-039-runtime",
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
