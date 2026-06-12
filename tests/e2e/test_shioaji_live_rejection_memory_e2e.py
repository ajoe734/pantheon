from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.broker.shioaji.adapter import ShioajiBrokerAdapter
from services.broker.shioaji.facade import PROOF_BOUNDARY, ShioajiSandboxFacade
from services.execution.lean_runtime.symbol_parser import SymbolParseError, parse
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from tests.e2e.test_lean_market_data_order_memory_e2e import _read_jsonl, _source_ingest_client


def test_shioaji_live_disabled_rejection_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _tw_market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_tw_market_record()],
                "next_watermark": "2026-06-12T22:15:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-014-tw-prices",
            "trace_id": "trace-e2e-loop-014-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    assert ingest_body["run"]["status"] == "completed"
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "2317"
    assert row["metadata"]["close"] == 111.5

    signal = _tw_live_probe_signal(
        row,
        normalized_ref=normalized_ref,
        ingest_run_id=ingest_body["run"]["ingest_run_id"],
    )
    with pytest.raises(SymbolParseError):
        parse(signal["symbol"])

    api = _make_mock_shioaji_api()
    facade = ShioajiSandboxFacade(
        ShioajiBrokerAdapter(
            sandbox_enabled=True,
            _api=api,
            submit_spacing_seconds=0.0,
        )
    )
    lifecycle = facade.run_lifecycle(
        capital_pool_id="pool-paper-tw-live-disabled",
        strategy_id=signal["strategy_id"],
        symbol=row["metadata"]["symbol"],
        qty=signal["quantity"],
        side="buy",
        order_type="limit",
        limit_price=row["metadata"]["close"],
        account_kind="stock",
    )

    assert lifecycle["status"] == "passed"
    assert lifecycle["place_result"]["status"] == "submitted"
    assert lifecycle["cancel_result"]["status"] == "cancelled"
    assert lifecycle["readback_result"]["status"] == "cancelled"
    assert lifecycle["live_disabled_result"]["status"] == "rejected"
    live_response = lifecycle["live_disabled_result"]["response"]
    assert live_response["error_code"] == "SHIOAJI_LIVE_DISABLED"
    assert live_response["status_code"] == 403
    assert lifecycle["production_live_enabled"] is False
    assert lifecycle["capital_binding_enabled"] is False
    assert lifecycle["human_gate_required"] is True
    assert api.place_order.call_count == 1

    feedback_path = tmp_path / "feedback-store.jsonl"
    writer_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    rejection_event = _adapter_rejection_event(
        signal=signal,
        normalized_ref=normalized_ref,
        lifecycle=lifecycle,
        rejection_response=live_response,
    )
    stored_rejection = writer_adapter.ingest_telemetry_event(
        rejection_event,
        strategy_id=signal["strategy_id"],
        promotion_state="sandbox",
    )

    recovered_adapter = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_rejections = recovered_adapter.query_telemetry(
        strategy_id=signal["strategy_id"],
        event_type="order_rejection",
        promotion_state="sandbox",
        limit=3,
    )
    assert [event["event_id"] for event in recovered_rejections] == [stored_rejection["event_id"]]

    writeback_payload = recovered_adapter.build_learn_feedback_writeback_payload(
        recovered_rejections[0],
        sponsor_persona_id="persona-live-guard-sponsor",
        contributing_persona_ids=["persona-live-guard-ops"],
        summary=(
            "TW 2317 price data produced an LLM live-route probe; the TW symbol was routed away from "
            "LEAN to Shioaji, the adapter returned a live-disabled rejection without submitting real "
            "capital, and the rejection feedback was recovered before memory writeback."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-live-guard-ops",
                "summary": "Adapter rejection feedback preserved the SHIOAJI_LIVE_DISABLED response.",
                "proposal_ids": [signal["signal_id"]],
                "tags": ["adapter_rejection", "shioaji_live_disabled", "order_rejection"],
            }
        ],
        proposal_ids=[signal["signal_id"], stored_rejection["event_id"]],
    )
    writeback_payload["tags"].extend(["adapter_rejection", "shioaji_live_disabled"])

    institutional_path = tmp_path / "institutional-memory.json"
    persona_path = tmp_path / "persona-memory.json"
    writeback = write_learn_feedback(
        writeback_payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )
    assert writeback["created"] is True

    institutional_hits = InstitutionalMemoryStore(path=institutional_path).retrieve(
        query="Shioaji live disabled adapter rejection 2317",
        tags=["adapter_rejection", "shioaji_live_disabled"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    evidence = institutional_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    lineage = evidence["lineage"]
    alpha_context = lineage["alpha_context"]
    order_context = lineage["order_context"]
    assert alpha_context["signal_id"] == "llm-live-guard-2317-014"
    assert alpha_context["llm_decision_id"] == "llm-decision-e2e-loop-014"
    assert order_context["adapter"] == "shioaji_sandbox"
    assert order_context["order_status"] == "rejected"
    assert order_context["broker_submission_status"] == "rejected_before_broker"
    assert order_context["adapter_response_status"] == "rejected"
    assert order_context["adapter_error_code"] == "SHIOAJI_LIVE_DISABLED"
    assert order_context["adapter_status_code"] == 403
    assert order_context["requested_execution_mode"] == "live"
    assert order_context["blocked_execution_mode"] == "live"
    assert order_context["submitted_to_broker"] is False
    assert order_context["is_real_order"] is False
    assert order_context["is_real_capital"] is False
    assert order_context["proof_boundary"] == PROOF_BOUNDARY

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-live-guard-ops",
        query="SHIOAJI_LIVE_DISABLED response",
        tags=["order_rejection"],
        limit=3,
    )
    assert persona_hits
    persona_order_context = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0][
        "lineage"
    ]["order_context"]
    assert persona_order_context["adapter_error_message"] == live_response["message"]
    assert persona_order_context["production_live_enabled"] is False
    assert persona_order_context["capital_binding_enabled"] is False
    assert persona_order_context["human_gate_required"] is True


def _tw_market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-014-tw-prices",
        "source_type": "market",
        "provider": "E2E Loop 014 Static TW Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "tw_live_guard_price_daily",
            "feature_targets": ["features/tw_live_guard_adapter_inputs"],
            "schema_hash": "tw_live_guard_price_daily.e2e_loop_014.v1",
        },
    }


def _tw_market_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-014-2317",
        "title": "2317 TW daily close for E2E loop 014",
        "content_ref": "market://tw_live_guard_price_daily/2317/2026-06-12",
        "metadata": {
            "dataset": "tw_live_guard_price_daily",
            "date": "2026-06-12",
            "symbol": "2317",
            "venue": "TWSE",
            "open": 109.0,
            "high": 112.0,
            "low": 108.5,
            "close": 111.5,
            "volume": 30000000,
        },
    }


def _tw_live_probe_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    metadata = row["metadata"]
    return {
        "signal_id": "llm-live-guard-2317-014",
        "version": "1.0",
        "strategy_id": "strategy-shioaji-live-guard",
        "timestamp": _iso_now(),
        "symbol": "2317.TWSE",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 2,
        "quantity_type": "SHARES",
        "source_worker": "mock-llm-live-guard-normalizer",
        "metadata": {
            "alpha_source": "llm_live_route_probe",
            "confidence_score": 0.86,
            "model_id": "gpt-risk-probe-e2e-loop-014",
            "prompt_bundle_id": "prompt-bundle-e2e-loop-014",
            "llm_prompt_id": "llm-prompt-e2e-loop-014",
            "llm_response_id": "llm-response-e2e-loop-014",
            "llm_decision_id": "llm-decision-e2e-loop-014",
            "requested_execution_mode": "live",
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "venue": metadata["venue"],
                "date": metadata["date"],
                "close": metadata["close"],
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref["uri"],
            "source_dataset_ref": normalized_ref["dataset"],
            "ingest_run_id": ingest_run_id,
            "order_adapter": "shioaji_sandbox",
            "adapter_route_reason": "TWSE symbols are routed to Shioaji; live mode must fail closed.",
        },
    }


def _adapter_rejection_event(
    *,
    signal: dict[str, Any],
    normalized_ref: dict[str, Any],
    lifecycle: dict[str, Any],
    rejection_response: dict[str, Any],
) -> dict[str, Any]:
    metadata = {
        "signal_id": signal["signal_id"],
        "strategy_id": signal["strategy_id"],
        "source_worker": signal["source_worker"],
        "alpha_source": signal["metadata"]["alpha_source"],
        "confidence_score": signal["metadata"]["confidence_score"],
        "model_id": signal["metadata"]["model_id"],
        "prompt_bundle_id": signal["metadata"]["prompt_bundle_id"],
        "llm_prompt_id": signal["metadata"]["llm_prompt_id"],
        "llm_response_id": signal["metadata"]["llm_response_id"],
        "llm_decision_id": signal["metadata"]["llm_decision_id"],
        "normalized_data_ref": normalized_ref["uri"],
        "source_dataset_ref": normalized_ref["dataset"],
        "ingest_run_id": signal["metadata"]["ingest_run_id"],
        "adapter": "shioaji_sandbox",
        "broker": "shioaji",
        "provider": "Shioaji",
        "order_id": "client-order-live-disabled-014",
        "adapter_order_id": "client-order-live-disabled-014",
        "account_kind": "stock",
        "order_status": "rejected",
        "broker_submission_status": "rejected_before_broker",
        "submitted_to_broker": False,
        "adapter_response_status": lifecycle["live_disabled_result"]["status"],
        "adapter_error_code": rejection_response["error_code"],
        "adapter_error_message": rejection_response["message"],
        "adapter_status_code": rejection_response["status_code"],
        "reject_reason": rejection_response["message"],
        "rejection_status": rejection_response["status"],
        "requested_execution_mode": "live",
        "blocked_execution_mode": "live",
        "is_real_order": False,
        "is_real_capital": False,
        "deployment_stage": "sandbox",
        "production_live_enabled": lifecycle["production_live_enabled"],
        "capital_binding_enabled": lifecycle["capital_binding_enabled"],
        "human_gate_required": lifecycle["human_gate_required"],
        "proof_boundary": lifecycle["proof_boundary"],
    }
    return {
        "event_id": "e2e-loop-014-order-rejection-live-disabled",
        "event_type": "order_rejection",
        "created_at": _iso_now(),
        "execution_mode": "sandbox",
        "environment": "sandbox",
        "deployment_stage": "sandbox",
        "binding_id": "binding-e2e-loop-014",
        "runtime_id": "adapter-runtime-014",
        "capital_pool_id": "pool-paper-tw-live-disabled",
        "artifact_id": "artifact-shioaji-live-guard",
        "artifact_version": "14.0.0",
        "plan_id": "plan-shioaji-live-guard",
        "persona_capital_binding_id": "pcb-shioaji-live-guard",
        "target": {
            "registry_id": "artifact-shioaji-live-guard",
            "strategy_id": signal["strategy_id"],
            "artifact_version": "14.0.0",
            "artifact_type": "broker_adapter_rejection",
            "promotion_state": "sandbox",
        },
        "metrics": {
            "requested_quantity": signal["quantity"],
            "limit_price": signal["metadata"]["market_data"]["close"],
            "rejected_order_count": 1,
            "submitted_to_broker": 0,
            "is_real_order": 0,
            "is_real_capital": 0,
        },
        "metadata": metadata,
        "trace_id": "trace-e2e-loop-014-adapter",
    }


def _make_mock_shioaji_api() -> MagicMock:
    mock_trade = MagicMock()
    mock_trade.trade_id = "mock-e2e-loop-014-trade"
    mock_trade.status = SimpleNamespace(
        id="mock-e2e-loop-014-trade",
        status="Submitted",
        status_code="0",
        msg="accepted by sandbox",
    )

    api = MagicMock()
    api.Contracts.Stocks.__getitem__.return_value = MagicMock()
    api.Order.return_value = MagicMock()
    api.place_order.return_value = mock_trade
    api.cancel_order.return_value = None
    api.update_status.return_value = None
    api.stock_account = SimpleNamespace(
        account_type="stock",
        broker_id="9A95",
        account_id="stock-e2e-loop-014",
        person_id="person-e2e-loop-014",
        signed=True,
    )
    api.futopt_account = SimpleNamespace(
        account_type="futures",
        broker_id="F002000",
        account_id="future-e2e-loop-014",
        person_id="person-e2e-loop-014",
        signed=True,
    )
    return api


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
