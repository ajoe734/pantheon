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


def test_crypto_cash_value_feedback_memory_readback_e2e(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)

    configured = source_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _market_connector(),
            "fetch": {
                "mode": "static_records",
                "records": [_crypto_record()],
                "next_watermark": "2026-06-12T22:30:00Z",
            },
        },
    )
    assert configured.status_code == 201, configured.text

    ingest = source_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-e2e-loop-006-crypto-prices",
            "trace_id": "trace-e2e-loop-006-data-fetch",
        },
    )
    assert ingest.status_code == 201, ingest.text
    ingest_body = ingest.json()
    normalized_ref = ingest_body["storage_refs"]["normalized_refs"][0]
    row = _read_jsonl(Path(normalized_ref["uri"]))[0]
    assert row["metadata"]["symbol"] == "BTCUSD"
    assert row["metadata"]["close"] == 30000.0

    telemetry = _CanonicalTelemetryRecorder()
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(
            [
                _crypto_signal(
                    row,
                    normalized_ref=normalized_ref,
                    ingest_run_id=ingest_body["run"]["ingest_run_id"],
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
    assert position["symbol"] == "BTCUSD"
    assert position["quantity"] == 2.0
    assert position["price"] == 30000.0
    fill_event = next(event for event in telemetry.events if event["event_type"] == "paper_fill_simulated")
    assert fill_event["metrics"]["action"] == "market_order"
    assert fill_event["metrics"]["fill_quantity"] == 2.0
    assert fill_event["metrics"]["fill_price"] == 30000.0
    assert fill_event["metadata"]["alpha_source"] == "crypto_momentum_quant"
    assert fill_event["metadata"]["market_price"] == 30000.0
    assert fill_event["metadata"]["source_dataset_ref"] == "crypto_price_daily"

    feedback_adapter = FeedbackStoreAdapter(feedback_store_path=str(tmp_path / "feedback-store.jsonl"))
    stored_fill = feedback_adapter.ingest_telemetry_event(
        fill_event,
        strategy_id="strategy-crypto-cash",
        promotion_state="paper",
    )
    writeback_payload = feedback_adapter.build_learn_feedback_writeback_payload(
        stored_fill,
        sponsor_persona_id="persona-crypto-sponsor",
        contributing_persona_ids=["persona-crypto-ops"],
        summary=(
            "BTCUSD.KRAKEN crypto alpha consumed fetched close=30000.0 and placed a "
            "paper CASH_VALUE order that filled 2 BTC."
        ),
        contributor_feedback=[
            {
                "persona_id": "persona-crypto-ops",
                "summary": "Crypto CASH_VALUE fill feedback confirmed non-equity LEAN symbol routing.",
                "proposal_ids": ["quant-btc-cash-006"],
                "tags": ["crypto_cash_value", "paper_fill", "market_data_fetch"],
            }
        ],
        proposal_ids=["quant-btc-cash-006"],
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
        query="BTCUSD crypto CASH_VALUE",
        tags=["crypto_cash_value", "paper_fill"],
        scope="system_wide",
        limit=3,
    )
    assert institutional_hits
    assert institutional_hits[0].entry.source_event_id == stored_fill["event_id"]

    persona_hits = PersonaMemoryStore(path=persona_path).retrieve(
        persona_id="persona-crypto-ops",
        query="crypto symbol routing",
        tags=["paper_fill"],
        limit=3,
    )
    assert persona_hits
    evidence = persona_hits[0].entry.content["structured_payload"]["runtime_telemetry_evidence"][0]
    assert evidence["lineage"]["strategy_id"] == "strategy-crypto-cash"


def _market_connector() -> dict[str, Any]:
    return {
        "connector_id": "conn-e2e-loop-006-crypto-prices",
        "source_type": "market",
        "provider": "E2E Loop 006 Static Crypto Prices",
        "license_scope": "internal",
        "metadata": {
            "dataset": "crypto_price_daily",
            "feature_targets": ["features/crypto_cash_inputs"],
            "schema_hash": "crypto_price_daily.e2e_loop_006.v1",
        },
    }


def _crypto_record() -> dict[str, Any]:
    return {
        "source_id": "src-e2e-loop-006-btcusd",
        "title": "BTCUSD daily close for E2E loop 006",
        "content_ref": "market://crypto_price_daily/BTCUSD/2026-06-12",
        "metadata": {
            "dataset": "crypto_price_daily",
            "date": "2026-06-12",
            "symbol": "BTCUSD",
            "venue": "KRAKEN",
            "open": 29900.0,
            "high": 30500.0,
            "low": 29500.0,
            "close": 30000.0,
            "volume": 4200,
        },
    }


def _crypto_signal(
    row: dict[str, Any],
    *,
    normalized_ref: dict[str, Any],
    ingest_run_id: str,
) -> dict[str, Any]:
    return {
        "signal_id": "quant-btc-cash-006",
        "version": "1.0",
        "strategy_id": "strategy-crypto-cash",
        "timestamp": _iso_now(),
        "symbol": "BTCUSD.KRAKEN",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 60000,
        "quantity_type": "CASH_VALUE",
        "source_worker": "mock-crypto-quant-normalizer",
        "metadata": {
            "alpha_source": "crypto_momentum_quant",
            "confidence_score": 0.84,
            "market_data": {
                "dataset": row["metadata"]["dataset"],
                "symbol": row["metadata"]["symbol"],
                "venue": row["metadata"]["venue"],
                "date": row["metadata"]["date"],
                "close": row["metadata"]["close"],
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
            "PANTHEON_RUNTIME_ID": "paper-runtime-006",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": "trace-e2e-loop-006-runtime",
            "PANTHEON_REQUEST_ID": "request-e2e-loop-006",
        }
    )


class _RuntimeManagerClient:
    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": "binding-e2e-loop-006",
                "runtime_id": "paper-runtime-006",
                "capital_pool_id": "pool-paper",
                "artifact_id": "artifact-paper-crypto",
                "artifact_version": "6.0.0",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": "plan-paper-crypto",
                "persona_capital_binding_id": "pcb-paper-crypto",
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
            "event_id": f"e2e-loop-006-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": "binding-e2e-loop-006",
            "runtime_id": "paper-runtime-006",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper-crypto",
            "artifact_version": "6.0.0",
            "plan_id": "plan-paper-crypto",
            "persona_capital_binding_id": "pcb-paper-crypto",
            "target": {
                "registry_id": "artifact-paper-crypto",
                "strategy_id": metadata.get("strategy_id") or "paper-runtime-crypto",
                "artifact_version": "6.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": "trace-e2e-loop-006-runtime",
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
