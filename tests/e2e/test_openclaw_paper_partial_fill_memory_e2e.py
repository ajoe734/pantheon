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


def test_openclaw_paper_partial_fill_feedback_performance_memory_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_market_record()],
                "next_watermark": "2026-06-12T22:45:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-015-us-prices",
            "trace_id": "trace-e2e-loop-015-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "INTC"
    assert row["metadata"]["close"] == 31.25

    signal = _paper_partial_fill_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    partial_order = _partial_order_response(signal, row)["order"]
    sidecar_calls: list[dict[str, Any]] = []
    adapter = PaperBrokerAdapter(
        enabled=True,
        broker_url="http://paper-broker-sidecar:8102",
        binding_resolver=_binding_resolver(signal["strategy_id"]),
        audit_log=PaperBrokerAuditLog(path=str(tmp_path / "paper-broker-audit.jsonl")),
        trace_id_factory=lambda: "trace-e2e-loop-015-adapter-submit",
    )

    def fake_sidecar(method: str, path: str, payload=None, params=None) -> dict[str, Any]:
        sidecar_calls.append({"method": method, "path": path, "payload": payload, "params": params})
        if method == "POST" and path == "/api/broker/paper/orders":
            assert payload["symbol"] == "INTC"
            assert payload["qty"] == 50.0
            assert payload["limit_price"] == 31.25
            return {"status": "ok", "order": partial_order}
        if method == "GET" and path == "/api/broker/paper/orders":
            return {"status": "ok", "orders": [partial_order]}
        if method == "GET" and path == f"/api/broker/paper/orders/{partial_order['order_id']}":
            return {"status": "ok", "order": partial_order}
        raise AssertionError(f"unexpected sidecar call: {method} {path}")

    monkeypatch.setattr(adapter, "_call_sidecar", fake_sidecar)

    submit_result = adapter.submit_paper_order(
        capital_pool_id="pool-openclaw-partial-fill",
        strategy_id=signal["strategy_id"],
        symbol=row["metadata"]["symbol"],
        qty=signal["quantity"],
        side="buy",
        order_type="limit",
        limit_price=row["metadata"]["close"],
        operator_id="operator-e2e-loop-015",
        trace_id="trace-e2e-loop-015-adapter-submit",
    )
    list_result = adapter.list_paper_orders(
        capital_pool_id="pool-openclaw-partial-fill",
        strategy_id=signal["strategy_id"],
        limit=5,
    )
    readback_result = adapter.get_paper_order(partial_order["order_id"])

    assert submit_result["order"]["status"] == "partially_filled"
    assert submit_result["order"]["fill_qty"] == 20.0
    assert list_result["orders"][0]["order_id"] == partial_order["order_id"]
    assert readback_result["order"]["remaining_qty"] == 30.0
    assert [call["method"] for call in sidecar_calls] == ["POST", "GET", "GET"]
    audit_entries = adapter.read_audit(operator_id="operator-e2e-loop-015")
    assert [entry["outcome"] for entry in audit_entries] == ["pending", "ok"]
    assert audit_entries[-1]["status"] == "partially_filled"
    assert audit_entries[-1]["fill_qty"] == 20.0

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    partial_event = _partial_fill_event(
        signal=signal,
        normalized_ref=normalized_ref,
        order=submit_result["order"],
    )
    stored_partial = writer_adapter.ingest_telemetry_event(
        partial_event,
        strategy_id=signal["strategy_id"],
        promotion_state="paper",
    )

    summary_store = RuntimeSummaryProjectionStore(path=tmp_path / "runtime-summary.json")
    projected = summary_store.project_event(stored_partial)
    assert projected is not None
    assert projected["runtime_id"] == "openclaw-paper-runtime-015"
    assert projected["fill_rate"] == 0.4
    assert projected["avg_slippage_bps"] == 0.0
    assert projected["total_trades"] == 1
    assert projected["pnl"] == 0.0

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_partials = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_partially_filled",
        promotion_state="paper",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_partials] == [stored_partial["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_partials[0],
        sponsor_persona_id="persona-partial-fill-sponsor",
        contributing_persona_ids=["persona-partial-fill-ops"],
        summary=(
            "INTC market data produced a paper limit order through the OpenClaw paper broker adapter; "
            "the adapter returned a partial fill, performance projection calculated a 0.4 fill rate, "
            "and recovered feedback carried the partial-fill outcome into Learn memory."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-partial-fill-ops",
                "summary": "Partial-fill feedback preserved filled, remaining, ratio, and performance metrics.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["partial_fill", "paper_broker_adapter", "performance_projection"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_partial["event_id"]],
    )
    writeback_payload["tags"].extend(["partial_fill", "paper_broker_adapter", "performance_projection"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="INTC partial fill 0.4 fill rate",
        tags=["partial_fill", "performance_projection"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "paper-partial-intc-015"
    assert alpha_context["alpha_source"] == "paper_adapter_partial_fill_quant"
    assert order_context["adapter"] == "openclaw_paper_broker"
    assert order_context["order_status"] == "partially_filled"
    assert order_context["fill_status"] == "partially_filled"
    assert order_context["requested_quantity"] == 50.0
    assert order_context["fill_quantity"] == 20.0
    assert order_context["remaining_quantity"] == 30.0
    assert order_context["partial_fill_ratio"] == 0.4
    assert order_context["fill_rate"] == 0.4
    assert order_context["submitted_to_broker"] is True
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-partial-fill-ops",
        query="remaining partial quantity",
        tags=["paper_broker_adapter"],
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
        "connector_id": "conn-e2e-loop-015-us-prices",
        "source_type": "market",
        "provider": "E2E Loop 015 Static US Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "us_partial_fill_price_daily",
            "feature_targets": ["features/openclaw_paper_partial_fill_inputs"],
            "schema_hash": "us_partial_fill_price_daily.e2e_loop_015.v1",
        },
    }


def _market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-015-intc",
        "title": "INTC daily close for E2E loop 015",
        "content_ref": "market://us_partial_fill_price_daily/INTC/2026-06-12",
        "metadata": {
            "dataset": "us_partial_fill_price_daily",
            "date": "2026-06-12",
            "symbol": "INTC",
            "open": 30.75,
            "high": 31.5,
            "low": 30.5,
            "close": 31.25,
            "volume": 42000000,
        },
    }


def _paper_partial_fill_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "paper-partial-intc-015",
        "version": "1.0",
        "strategy_id": "strategy-openclaw-paper-partial-fill",
        "timestamp": _iso_now(),
        "symbol": "INTC.US",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 50.0,
        "quantity_type": "SHARES",
        "source_worker": "mock-openclaw-partial-fill-normalizer",
        "metadata": {
            "alpha_source": "paper_adapter_partial_fill_quant",
            "confidence_score": 0.88,
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


def _partial_order_response(signal: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    requested_quantity = float(signal["quantity"])
    filled_quantity = 20.0
    remaining_quantity = requested_quantity - filled_quantity
    partial_ratio = filled_quantity / requested_quantity
    return {
        "status": "ok",
        "order": {
            "order_id": "paper-order-partial-015",
            "capital_pool_id": "pool-openclaw-partial-fill",
            "strategy_id": signal["strategy_id"],
            "symbol": row["metadata"]["symbol"],
            "qty": requested_quantity,
            "side": "buy",
            "order_type": "limit",
            "limit_price": row["metadata"]["close"],
            "status": "partially_filled",
            "fill_status": "partially_filled",
            "fill_price": row["metadata"]["close"],
            "fill_qty": filled_quantity,
            "filled_quantity": filled_quantity,
            "remaining_qty": remaining_quantity,
            "remaining_quantity": remaining_quantity,
            "partial_fill_ratio": partial_ratio,
            "is_real_order": False,
            "is_real_capital": False,
            "sim_fill_flag": True,
            "deployment_stage": "paper",
        },
    }


def _binding_resolver(strategy_id: str):
    def resolve(capital_pool_id: str) -> dict[str, Any] | None:
        if capital_pool_id != "pool-openclaw-partial-fill":
            return None
        return {
            "binding_id": "binding-e2e-loop-015",
            "runtime_id": "openclaw-paper-runtime-015",
            "capital_pool_id": "pool-openclaw-partial-fill",
            "artifact_id": "artifact-openclaw-paper-partial",
            "artifact_version": "15.0.0",
            "deployment_mode": "paper",
            "deployment_stage": "paper",
            "plan_id": "plan-openclaw-paper-partial",
            "persona_capital_binding_id": "pcb-openclaw-paper-partial",
            "status": "active",
            "metadata": {"strategy_id": strategy_id},
        }

    return resolve


def _partial_fill_event(
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    order: dict[str, Any],
) -> dict[str, Any]:
    requested_quantity = float(order["qty"])
    filled_quantity = float(order["fill_qty"])
    remaining_quantity = float(order["remaining_quantity"])
    partial_ratio = round(filled_quantity / requested_quantity, 6)
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
        "fill_status": order["fill_status"],
        "filled_quantity": filled_quantity,
        "remaining_quantity": remaining_quantity,
        "partial_fill_ratio": partial_ratio,
        "broker_submission_status": "partially_filled",
        "submitted_to_broker": True,
        "is_real_order": order["is_real_order"],
        "is_real_capital": order["is_real_capital"],
        "deployment_stage": "paper",
    }
    return {
        "event_id": "e2e-loop-015-order-partially-filled",
        "event_type": "order_partially_filled",
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": "binding-e2e-loop-015",
        "runtime_id": "openclaw-paper-runtime-015",
        "capital_pool_id": "pool-openclaw-partial-fill",
        "artifact_id": "artifact-openclaw-paper-partial",
        "artifact_version": "15.0.0",
        "plan_id": "plan-openclaw-paper-partial",
        "persona_capital_binding_id": "pcb-openclaw-paper-partial",
        "target": {
            "registry_id": "artifact-openclaw-paper-partial",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "15.0.0",
            "artifact_type": "paper_broker_adapter",
            "promotion_state": "paper",
        },
        "metrics": {
            "requested_quantity": requested_quantity,
            "fill_quantity": filled_quantity,
            "fill_price": float(order["fill_price"]),
            "remaining_quantity": remaining_quantity,
            "partial_fill_ratio": partial_ratio,
            "fill_rate": partial_ratio,
            "avg_slippage_bps": 0.0,
            "pnl": 0.0,
            "total_trades": 1,
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-015-adapter-submit",
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
