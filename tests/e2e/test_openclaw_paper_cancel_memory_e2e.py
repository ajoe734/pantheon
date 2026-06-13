from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client

_OPENCLAW_DIR = Path(__file__).resolve().parents[2] / "services" / "openclaw-gateway-adapter"
if str(_OPENCLAW_DIR) not in sys.path:
    sys.path.insert(0, str(_OPENCLAW_DIR))

from paper_broker_adapter import PaperBrokerAdapter, PaperBrokerAuditLog  # noqa: E402


def test_openclaw_paper_cancel_feedback_performance_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T23:00:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-016-us-prices",
            "trace_id": "trace-e2e-loop-016-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "IBM"
    assert row["metadata"]["close"] == 190.25

    signal = _paper_cancel_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    submitted_order = _submitted_order_response(signal, row)["order"]
    canceled_order = {**submitted_order, "status": "canceled", "cancel_status": "acknowledged"}
    sidecar_calls: list[dict[str, Any]] = []
    adapter = PaperBrokerAdapter(
        enabled=True,
        broker_url="http://paper-broker-sidecar:8102",
        binding_resolver=_binding_resolver(signal["strategy_id"]),
        audit_log=PaperBrokerAuditLog(path=str(tmp_path / "paper-broker-cancel-audit.jsonl")),
        trace_id_factory=lambda: "trace-e2e-loop-016-adapter",
    )

    def fake_sidecar(method: str, path: str, payload=None, params=None) -> dict[str, Any]:
        sidecar_calls.append({"method": method, "path": path, "payload": payload, "params": params})
        if method == "POST" and path == "/api/broker/paper/orders":
            assert payload["symbol"] == "IBM"
            assert payload["qty"] == 12.0
            assert payload["limit_price"] == 190.25
            return {"status": "ok", "order": submitted_order}
        if method == "POST" and path == f"/api/broker/paper/orders/{submitted_order['order_id']}/cancel":
            return {
                "status": "ok",
                "order": canceled_order,
                "cancel_status": "acknowledged",
                "cancel_request_id": "cancel-request-e2e-loop-016",
            }
        if method == "GET" and path == f"/api/broker/paper/orders/{submitted_order['order_id']}":
            return {"status": "ok", "order": canceled_order}
        if method == "GET" and path == "/api/broker/paper/orders":
            return {"status": "ok", "orders": [canceled_order]}
        raise AssertionError(f"unexpected sidecar call: {method} {path}")

    monkeypatch.setattr(adapter, "_call_sidecar", fake_sidecar)

    submit_result = adapter.submit_paper_order(
        capital_pool_id="pool-openclaw-cancel",
        strategy_id=signal["strategy_id"],
        symbol=row["metadata"]["symbol"],
        qty=signal["quantity"],
        side="buy",
        order_type="limit",
        limit_price=row["metadata"]["close"],
        operator_id="operator-e2e-loop-016",
        trace_id="trace-e2e-loop-016-submit",
    )
    cancel_result = adapter.cancel_paper_order(
        submitted_order["order_id"],
        operator_id="operator-e2e-loop-016",
        trace_id="trace-e2e-loop-016-cancel",
    )
    readback_result = adapter.get_paper_order(submitted_order["order_id"])
    list_result = adapter.list_paper_orders(
        capital_pool_id="pool-openclaw-cancel",
        strategy_id=signal["strategy_id"],
        limit=5,
    )

    assert submit_result["order"]["status"] == "submitted"
    assert submit_result["order"]["fill_qty"] == 0.0
    assert cancel_result["cancel_status"] == "acknowledged"
    assert cancel_result["order"]["status"] == "canceled"
    assert readback_result["order"]["status"] == "canceled"
    assert list_result["orders"][0]["order_id"] == submitted_order["order_id"]
    assert [call["method"] for call in sidecar_calls] == ["POST", "POST", "GET", "GET"]
    audit_entries = adapter.read_audit(operator_id="operator-e2e-loop-016")
    assert [(entry["event"], entry["outcome"]) for entry in audit_entries] == [
        ("paper_order_intent", "pending"),
        ("paper_order_intent", "ok"),
        ("paper_order_cancel_intent", "pending"),
        ("paper_order_cancel_intent", "ok"),
    ]
    assert audit_entries[1]["status"] == "submitted"

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    cancel_event = _cancel_event(
        signal=signal,
        normalized_ref=normalized_ref,
        order=cancel_result["order"],
    )
    stored_cancel = writer_adapter.ingest_telemetry_event(
        cancel_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    summary_store = RuntimeSummaryProjectionStore(path=tmp_path / "runtime-summary.json")
    projected = summary_store.project_event(stored_cancel)
    assert projected is not None
    assert projected["runtime_id"] == "openclaw-paper-runtime-016"
    assert projected["fill_rate"] == 0.0
    assert projected["avg_slippage_bps"] == 0.0
    assert projected["total_trades"] == 0
    assert projected["pnl"] == 0.0

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_cancels = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_canceled",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_cancels] == [stored_cancel["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_cancels[0],
        sponsor_persona_id="persona-cancel-sponsor",
        contributing_persona_ids=["persona-cancel-ops"],
        summary=(
            "IBM market data produced an OpenClaw paper limit order; the adapter accepted the order, "
            "operator risk controls canceled the unfilled order, performance projection kept fill rate at 0, "
            "and recovered feedback wrote the cancel acknowledgement into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-cancel-ops",
                "summary": "Cancel feedback preserved reason, operator, unfilled quantity, and cancel ack.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["paper_order_cancel", "cancel_ack", "performance_projection"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_cancel["event_id"]],
    )
    writeback_payload["tags"].extend(["paper_order_cancel", "cancel_ack", "performance_projection"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="IBM paper order canceled unfilled",
        tags=["paper_order_cancel", "cancel_ack"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "paper-cancel-ibm-016"
    assert alpha_context["source_worker"] == "mock-openclaw-cancel-normalizer"
    assert order_context["adapter"] == "openclaw_paper_broker"
    assert order_context["order_status"] == "canceled"
    assert order_context["cancel_status"] == "acknowledged"
    assert order_context["cancel_reason"] == "price_guard_invalidated"
    assert order_context["cancel_requested_by"] == "operator-e2e-loop-016"
    assert order_context["cancel_request_id"] == "cancel-request-e2e-loop-016"
    assert order_context["requested_quantity"] == 12.0
    assert order_context["unfilled_quantity"] == 12.0
    assert order_context["cancelled_quantity"] == 12.0
    assert order_context["fill_rate"] == 0.0
    assert order_context["submitted_to_broker"] is True
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-cancel-ops",
        query="cancel reason operator",
        tags=["cancel_ack"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["avg_slippage_bps"] == 0.0
    assert persona_order_context["pnl"] == 0.0
    assert persona_order_context["total_trades"] == 0


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-016-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 016 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_cancel_price_daily",
            "feature_targets": ["features/openclaw_paper_cancel_inputs"],
            "schema_hash": "us_cancel_price_daily.e2e_loop_016.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-016-ibm",
        "title": "IBM daily close for E2E loop 016",
        "content_ref": "market://us_cancel_price_daily/IBM/2026-06-12",
        "metadata": {
            "dataset": "us_cancel_price_daily",
            "date": "2026-06-12",
            "symbol": "IBM",
            "open": 189.25,
            "high": 191.0,
            "low": 188.75,
            "close": 190.25,
            "volume": 4100000,
        },
    }


def _paper_cancel_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "paper-cancel-ibm-016",
        "version": "1.0",
        "strategy_id": "strategy-openclaw-paper-cancel",
        "timestamp": _iso_now(),
        "symbol": "IBM.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 12.0,
        "quantity_type": "SHARES",
        "source_worker": "mock-openclaw-cancel-normalizer",
        "metadata": {
            "alpha_source": "paper_adapter_cancel_quant",
            "confidence_score": 0.84,
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
            "order_adapter": "openclaw_paper_broker",
        },
    }


def _submitted_order_response(signal: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    requested_quantity = float(signal["quantity"])
    return {
        "status": "ok",
        "order": {
            "order_id": "paper-order-cancel-016",
            "capital_pool_id": "pool-openclaw-cancel",
            "strategy_id": signal["strategy_id"],
            "symbol": row["metadata"]["symbol"],
            "qty": requested_quantity,
            "side": "buy",
            "order_type": "limit",
            "limit_price": row["metadata"]["close"],
            "status": "submitted",
            "fill_status": "unfilled",
            "fill_price": None,
            "fill_qty": 0.0,
            "filled_quantity": 0.0,
            "remaining_qty": requested_quantity,
            "remaining_quantity": requested_quantity,
            "is_real_order": False,
            "is_real_capital": False,
            "sim_fill_flag": True,
            "deployment_stage": "paper",
        },
    }


def _binding_resolver(strategy_id: str):
    def resolve(capital_pool_id: str) -> dict[str, Any] | None:
        if capital_pool_id != "pool-openclaw-cancel":
            return None
        return {
            "binding_id": "binding-e2e-loop-016",
            "runtime_id": "openclaw-paper-runtime-016",
            "capital_pool_id": "pool-openclaw-cancel",
            "artifact_id": "artifact-openclaw-paper-cancel",
            "artifact_version": "16.0.0",
            "deployment_mode": "paper",
            "deployment_stage": "paper",
            "plan_id": "plan-openclaw-paper-cancel",
            "persona_capital_binding_id": "pcb-openclaw-paper-cancel",
            "status": "active",
            "metadata": {"strategy_id": strategy_id},
        }

    return resolve


def _cancel_event(
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    order: dict[str, Any],
) -> dict[str, Any]:
    requested_quantity = float(order["qty"])
    unfilled_quantity = float(order["remaining_quantity"])
    metadata = {
        "signal_id": signal["signal_id"],
        "strategy_id": signal["strategy_id"],
        "source_worker": signal["source_worker"],
        "alpha_source": signal["metadata"]["alpha_source"],
        "confidence_score": signal["metadata"]["confidence_score"],
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": signal["metadata"]["ingest_run_id"],
        "adapter": "openclaw_paper_broker",
        "broker": "paper_broker",
        "provider": "Pantheon Paper Broker Sidecar",
        "order_id": order["order_id"],
        "adapter_order_id": order["order_id"],
        "order_quantity": requested_quantity,
        "requested_quantity": requested_quantity,
        "order_status": order["status"],
        "cancel_status": order["cancel_status"],
        "cancel_reason": "price_guard_invalidated",
        "cancel_requested_by": "operator-e2e-loop-016",
        "cancel_request_id": "cancel-request-e2e-loop-016",
        "cancel_ack_status": "acknowledged",
        "cancelled_quantity": unfilled_quantity,
        "unfilled_quantity": unfilled_quantity,
        "broker_submission_status": "canceled",
        "submitted_to_broker": True,
        "is_real_order": order["is_real_order"],
        "is_real_capital": order["is_real_capital"],
        "deployment_stage": "paper",
    }
    return {
        "event_id": "e2e-loop-016-order-canceled",
        "event_type": "order_canceled",
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-e2e-loop-016",
        "runtime_id": "openclaw-paper-runtime-016",
        "capital_pool_id": "pool-openclaw-cancel",
        "artifact_id": "artifact-openclaw-paper-cancel",
        "artifact_version": "16.0.0",
        "plan_id": "plan-openclaw-paper-cancel",
        "persona_capital_binding_id": "pcb-openclaw-paper-cancel",
        "target": {
            "registry_id": "artifact-openclaw-paper-cancel",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "16.0.0",
            "artifact_type": "paper_broker_adapter",
            "promotion_state": "paper",
        },
        "metrics": {
            "requested_quantity": requested_quantity,
            "fill_quantity": 0.0,
            "remaining_quantity": unfilled_quantity,
            "unfilled_quantity": unfilled_quantity,
            "cancelled_quantity": unfilled_quantity,
            "cancel_latency_ms": 42.0,
            "fill_rate": 0.0,
            "avg_slippage_bps": 0.0,
            "pnl": 0.0,
            "total_trades": 0,
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-016-cancel",
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
