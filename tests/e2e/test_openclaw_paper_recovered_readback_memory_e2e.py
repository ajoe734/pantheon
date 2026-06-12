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


def test_openclaw_paper_recovered_readback_feedback_memory_e2e(tmp_path, monkeypatch) -> None:
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
            "connector_id": "conn-e2e-loop-038-openclaw-recovered-prices",
            "trace_id": "trace-e2e-loop-038-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "TSLA"
    assert row["metadata"]["close"] == 178.25

    signal = _paper_readback_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    order_store: dict[str, dict[str, Any]] = {}
    audit_path = tmp_path / "paper-broker-recovered-audit.jsonl"
    submit_adapter = PaperBrokerAdapter(
        enabled=True,
        broker_url="http://paper-broker-sidecar:8102",
        binding_resolver=_binding_resolver(signal["strategy_id"]),
        audit_log=PaperBrokerAuditLog(path=str(audit_path)),
        trace_id_factory=lambda: "trace-e2e-loop-038-adapter-submit",
    )

    def fake_sidecar(method: str, path: str, payload=None, params=None) -> dict[str, Any]:
        if method == "POST" and path == "/api/broker/paper/orders":
            assert payload["symbol"] == "TSLA"
            assert payload["qty"] == 8.0
            assert payload["limit_price"] == 178.25
            submitted_order = _submitted_order(signal, row)
            order_store[submitted_order["order_id"]] = submitted_order
            return {"status": "ok", "order": submitted_order}
        if method == "GET" and path.startswith("/api/broker/paper/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            return {"status": "ok", "order": order_store[order_id]}
        if method == "GET" and path == "/api/broker/paper/orders":
            orders = list(order_store.values())
            if params and params.get("capital_pool_id"):
                orders = [order for order in orders if order["capital_pool_id"] == params["capital_pool_id"]]
            if params and params.get("strategy_id"):
                orders = [order for order in orders if order["strategy_id"] == params["strategy_id"]]
            return {"status": "ok", "orders": orders[: int((params or {}).get("limit") or 100)]}
        raise AssertionError(f"unexpected sidecar call: {method} {path}")

    monkeypatch.setattr(submit_adapter, "_call_sidecar", fake_sidecar)

    submit_result = submit_adapter.submit_paper_order(
        capital_pool_id="pool-openclaw-recovered-readback",
        strategy_id=signal["strategy_id"],
        symbol=row["metadata"]["symbol"],
        qty=signal["quantity"],
        side="buy",
        order_type="limit",
        limit_price=row["metadata"]["close"],
        operator_id="operator-e2e-loop-038",
        trace_id="trace-e2e-loop-038-submit",
    )
    submitted_order = submit_result["order"]
    assert submitted_order["status"] == "submitted"
    assert submitted_order["fill_qty"] == 0.0
    assert submit_adapter.read_audit(operator_id="operator-e2e-loop-038")[-1]["status"] == "submitted"

    order_store[submitted_order["order_id"]] = _filled_order(submitted_order)
    recovered_adapter = PaperBrokerAdapter(
        enabled=True,
        broker_url="http://paper-broker-sidecar:8102",
        binding_resolver=_binding_resolver(signal["strategy_id"]),
        audit_log=PaperBrokerAuditLog(path=str(audit_path)),
        trace_id_factory=lambda: "trace-e2e-loop-038-adapter-recovered",
    )
    monkeypatch.setattr(recovered_adapter, "_call_sidecar", fake_sidecar)

    readback_result = recovered_adapter.get_paper_order(submitted_order["order_id"])
    list_result = recovered_adapter.list_paper_orders(
        capital_pool_id="pool-openclaw-recovered-readback",
        strategy_id=signal["strategy_id"],
        limit=5,
    )
    audit_readback = recovered_adapter.read_audit(operator_id="operator-e2e-loop-038")

    assert readback_result["order"]["status"] == "filled"
    assert readback_result["order"]["fill_qty"] == 8.0
    assert readback_result["order"]["remaining_qty"] == 0.0
    assert list_result["orders"] == [readback_result["order"]]
    assert [(entry["event"], entry["outcome"]) for entry in audit_readback] == [
        ("paper_order_intent", "pending"),
        ("paper_order_intent", "ok"),
    ]

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    submitted_event = _order_event(
        "order_submitted",
        signal=signal,
        normalized_ref=normalized_ref,
        order=submitted_order,
    )
    filled_event = _order_event(
        "order_filled",
        signal=signal,
        normalized_ref=normalized_ref,
        order=readback_result["order"],
    )
    stored_submitted = writer_adapter.ingest_telemetry_event(
        submitted_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )
    stored_filled = writer_adapter.ingest_telemetry_event(
        filled_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    summary_store = RuntimeSummaryProjectionStore(path=tmp_path / "runtime-summary.json")
    projected = summary_store.project_event(stored_filled)
    assert projected is not None
    assert projected["runtime_id"] == "openclaw-paper-runtime-038"
    assert projected["fill_rate"] == 1.0
    assert projected["avg_slippage_bps"] == 0.0
    assert projected["total_trades"] == 1
    assert projected["pnl"] == 0.0

    feedback_recovered = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_submits = feedback_recovered.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_submitted",
        promotion_state="paper",
        limit=3,
    )
    recovered_fills = feedback_recovered.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_filled",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_submits] == [stored_submitted["event_id"]]
    assert [event["event_id"] for event in recovered_fills] == [stored_filled["event_id"]]
    assert recovered_fills[0]["metrics"]["fill_quantity"] == 8.0
    assert recovered_fills[0]["metrics"]["fill_rate"] == 1.0

    writeback_payload = feedback_recovered.build_learn_feedback_writeback_payload(
        recovered_fills[0],
        sponsor_persona_id="persona-openclaw-recovery-sponsor",
        contributing_persona_ids=["persona-openclaw-readback-ops"],
        summary=(
            "TSLA market data produced an OpenClaw paper limit order; a fresh adapter instance recovered "
            "the sidecar filled readback, feedback-store recovery preserved the fill, and Learn memory "
            "captured the adapter readback recovery path."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-openclaw-readback-ops",
                "summary": "Recovered readback feedback preserved filled quantity, order id, and performance metrics.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["openclaw_recovered_readback", "adapter_recovery", "paper_fill"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_submitted["event_id"], stored_filled["event_id"]],
    )
    writeback_payload["tags"].extend(["openclaw_recovered_readback", "adapter_recovery", "paper_fill"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="OpenClaw recovered readback TSLA filled",
        tags=["openclaw_recovered_readback", "adapter_recovery"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert evidence["event_type"] == "order_filled"
    assert alpha_context["signal_id"] == "paper-readback-tsla-038"
    assert alpha_context["alpha_source"] == "paper_adapter_recovered_readback_quant"
    assert alpha_context["market_data_ref"] == normalized_ref["uri"]
    assert order_context["adapter"] == "openclaw_paper_broker"
    assert order_context["order_id"] == submitted_order["order_id"]
    assert order_context["adapter_order_id"] == submitted_order["order_id"]
    assert order_context["order_status"] == "filled"
    assert order_context["readback_status"] == "filled"
    assert order_context["fill_status"] == "filled"
    assert order_context["requested_quantity"] == 8.0
    assert order_context["fill_quantity"] == 8.0
    assert order_context["filled_quantity"] == 8.0
    assert order_context["remaining_quantity"] == 0.0
    assert order_context["fill_price"] == 178.25
    assert order_context["fill_rate"] == 1.0
    assert order_context["total_trades"] == 1
    assert order_context["submitted_to_broker"] is True
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-openclaw-readback-ops",
        query="recovered adapter filled quantity",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["avg_slippage_bps"] == 0.0
    assert persona_order_context["pnl"] == 0.0
    assert persona_order_context["total_trades"] == 1


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-038-openclaw-recovered-prices",
        "source_type": "market",
        "provider": "E2E Loop 038 Static OpenClaw Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_recovered_readback_price_daily",
            "feature_targets": ["features/openclaw_recovered_readback_inputs"],
            "schema_hash": "us_recovered_readback_price_daily.e2e_loop_038.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-038-tsla",
        "title": "TSLA daily close for E2E loop 038",
        "content_ref": "market://us_recovered_readback_price_daily/TSLA/2026-06-12",
        "metadata": {
            "dataset": "us_recovered_readback_price_daily",
            "date": "2026-06-12",
            "symbol": "TSLA",
            "open": 177.5,
            "high": 181.0,
            "low": 176.75,
            "close": 178.25,
            "volume": 48000000,
        },
    }


def _paper_readback_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "paper-readback-tsla-038",
        "version": "1.0",
        "strategy_id": "strategy-openclaw-recovered-readback",
        "timestamp": _iso_now(),
        "symbol": "TSLA.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 8,
        "quantity_type": "SHARES",
        "source_worker": "mock-openclaw-readback-normalizer",
        "metadata": {
            "alpha_source": "paper_adapter_recovered_readback_quant",
            "confidence_score": 0.87,
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
            "order_adapter": "openclaw_paper_broker",
        },
    }


def _submitted_order(signal: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": "paper-order-readback-038",
        "capital_pool_id": "pool-openclaw-recovered-readback",
        "strategy_id": signal["strategy_id"],
        "symbol": row["metadata"]["symbol"],
        "qty": float(signal["quantity"]),
        "side": "buy",
        "order_type": "limit",
        "limit_price": float(row["metadata"]["close"]),
        "created_at": _iso_now(),
        "filled_at": None,
        "fill_price": None,
        "fill_qty": 0.0,
        "remaining_qty": float(signal["quantity"]),
        "status": "submitted",
        "sim_fill_flag": True,
        "is_real_order": False,
        "is_real_capital": False,
        "deployment_stage": "paper",
    }


def _filled_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        **order,
        "filled_at": _iso_now(),
        "fill_price": order["limit_price"],
        "fill_qty": order["qty"],
        "remaining_qty": 0.0,
        "status": "filled",
        "fill_status": "filled",
    }


def _order_event(
    event_type: str,
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    order: dict[str, Any],
) -> dict[str, Any]:
    requested_quantity = float(order["qty"])
    fill_quantity = float(order.get("fill_qty") or 0.0)
    remaining_quantity = float(order.get("remaining_qty") or max(requested_quantity - fill_quantity, 0.0))
    fill_price = float(order.get("fill_price") or order.get("limit_price") or 0.0)
    fill_rate = 1.0 if fill_quantity and remaining_quantity == 0.0 else 0.0
    metadata = {
        "signal_id": signal["signal_id"],
        "strategy_id": signal["strategy_id"],
        "source_worker": signal["source_worker"],
        "alpha_source": signal["metadata"]["alpha_source"],
        "confidence_score": signal["metadata"]["confidence_score"],
        "market_data_ref": normalized_ref["uri"],
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": signal["metadata"]["ingest_run_id"],
        "adapter": "openclaw_paper_broker",
        "broker": "openclaw",
        "provider": "OpenClaw Paper Broker",
        "order_id": order["order_id"],
        "adapter_order_id": order["order_id"],
        "order_quantity": requested_quantity,
        "requested_quantity": requested_quantity,
        "computed_quantity": requested_quantity,
        "remaining_quantity": remaining_quantity,
        "remaining_qty": remaining_quantity,
        "fill_status": order.get("fill_status") or order["status"],
        "fill_quantity": fill_quantity,
        "filled_quantity": fill_quantity,
        "fill_price": fill_price,
        "last_fill_quantity": fill_quantity,
        "last_fill_price": fill_price,
        "avg_fill_price": fill_price,
        "order_status": order["status"],
        "readback_status": order["status"],
        "side": order["side"],
        "order_type": order["order_type"],
        "limit_price": order["limit_price"],
        "broker_submission_status": order["status"],
        "submitted_to_broker": True,
        "is_real_order": order["is_real_order"],
        "is_real_capital": order["is_real_capital"],
        "deployment_stage": order["deployment_stage"],
    }
    return {
        "event_id": f"e2e-loop-038-{event_type}",
        "event_type": event_type,
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-e2e-loop-038",
        "runtime_id": "openclaw-paper-runtime-038",
        "capital_pool_id": order["capital_pool_id"],
        "artifact_id": "artifact-openclaw-recovered-readback",
        "artifact_version": "38.0.0",
        "plan_id": "plan-openclaw-recovered-readback",
        "persona_capital_binding_id": "pcb-openclaw-recovered-readback",
        "target": {
            "registry_id": "artifact-openclaw-recovered-readback",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "38.0.0",
            "artifact_type": "paper_broker_adapter",
            "promotion_state": "paper",
        },
        "metrics": {
            "order_quantity": requested_quantity,
            "requested_quantity": requested_quantity,
            "computed_quantity": requested_quantity,
            "remaining_quantity": remaining_quantity,
            "fill_quantity": fill_quantity,
            "filled_quantity": fill_quantity,
            "fill_price": fill_price,
            "last_fill_quantity": fill_quantity,
            "last_fill_price": fill_price,
            "avg_fill_price": fill_price,
            "fill_rate": fill_rate,
            "avg_slippage_bps": 0.0,
            "pnl": 0.0,
            "total_trades": 1 if fill_quantity else 0,
            "submitted_to_broker": True,
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-038-adapter-readback",
    }


def _binding_resolver(strategy_id: str):
    def resolve(capital_pool_id: str) -> dict[str, Any] | None:
        if capital_pool_id != "pool-openclaw-recovered-readback":
            return None
        return {
            "binding_id": "binding-e2e-loop-038",
            "capital_pool_id": capital_pool_id,
            "status": "active",
            "deployment_mode": "paper",
            "strategy_id": strategy_id,
            "artifact_id": "artifact-openclaw-recovered-readback",
            "persona_capital_binding_id": "pcb-openclaw-recovered-readback",
        }

    return resolve


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
