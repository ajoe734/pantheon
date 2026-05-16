"""TEL-001-RB TelemetryEvent canonical schema rebaseline tests."""

from __future__ import annotations

import asyncio
import json
import types
import unittest
import uuid
from pathlib import Path
from typing import Any

from services.telemetry.ingest_svc import TelemetryIngestService

try:
    import jsonschema
except ImportError:  # pragma: no cover - existing telemetry tests require this too.
    jsonschema = None


SCHEMA_PATH = Path(__file__).with_name("telemetry_event.schema.json")
BINDING_ID = "d9e2b35f-8333-46f4-89df-e1b6f2249e10"

REBASELINE_REQUIRED_EVENT_TYPES = {
    "heartbeat",
    "runtime_health",
    "deploy_started",
    "deploy_completed",
    "rollback_started",
    "rollback_completed",
    "pause_triggered",
    "liquidate_triggered",
    "paper_order_simulated",
    "paper_fill_simulated",
    "bracket_order_logged",
    "order_rejection_simulated",
    "governance_decision",
    "approval_action",
    "manual_override",
    "kill_switch_action",
    "telemetry_mirror_mismatch",
}


class _BindingStore:
    def get_binding(self, binding_id: str):
        if binding_id != BINDING_ID:
            return None
        return types.SimpleNamespace(
            binding_id=BINDING_ID,
            runtime_id="rt-tel001-rb",
            capital_pool_id="pool-tel001-rb",
            artifact_id="artifact-tel001-rb",
            artifact_version="1.0.0",
            plan_id="plan-tel001-rb",
            persona_capital_binding_id="pcb-tel001-rb",
            deployment_mode="paper",
            effective_at="2026-05-16T00:00:00Z",
            retired_at=None,
        )


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _event_types(schema: dict[str, Any]) -> list[str]:
    return list(schema["properties"]["event_type"]["enum"])


def _metrics_for(event_type: str) -> dict[str, Any]:
    if event_type == "pnl_snapshot":
        return {"pnl": 12.5}
    if event_type == "drawdown_snapshot":
        return {"drawdown_pct": 0.4}
    if event_type == "slippage_observation":
        return {"slippage_bps": 1.2}
    if event_type in {"fill_observation", "paper_fill_simulated", "fill_received"}:
        return {"fill_quantity": 10.0, "fill_price": 101.25}
    if event_type in {"order_rejection", "order_rejection_simulated"}:
        return {"reject_reason": "simulated_reject"}
    if event_type in {"position_snapshot", "position_snapshot_received", "broker_position_snapshot"}:
        return {"position_qty": 10.0}
    if event_type == "heartbeat":
        return {"heartbeat": 1}
    if event_type == "runtime_health":
        return {"heartbeat": 1, "health_status": "ok"}
    if event_type == "telemetry_mirror_mismatch":
        return {"action": "telemetry_mirror_mismatch", "mismatch_count": 1}
    if event_type == "bracket_order_logged":
        return {"action": "bracket_logged_only"}
    return {"action": event_type}


def _event(event_type: str) -> dict[str, Any]:
    event = {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"pantheon:tel001-rb:{event_type}")),
        "event_type": event_type,
        "created_at": "2026-05-16T00:10:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": BINDING_ID,
        "runtime_id": "rt-tel001-rb",
        "capital_pool_id": "pool-tel001-rb",
        "artifact_id": "artifact-tel001-rb",
        "artifact_version": "1.0.0",
        "plan_id": "plan-tel001-rb",
        "persona_capital_binding_id": "pcb-tel001-rb",
        "authority_refs": {
            "write_owner": "runtime-manager",
            "authority_source": "runtime_binding",
            "runtime_role": "paper",
            "runtime_mode": "paper",
        },
        "target": {
            "registry_id": "strategy-tel001-rb",
            "strategy_id": "strategy-tel001-rb",
            "artifact_version": "1.0.0",
            "artifact_type": "execution_bundle",
            "promotion_state": "paper",
            "lineage_ref": "lineage-tel001-rb",
        },
        "metrics": _metrics_for(event_type),
        "metadata": {
            "context_source": "schema_rebaseline_test",
            "runtime_role": "paper",
        },
    }
    if event_type.startswith("rollback_"):
        event["rollback_parent"] = "42e84802-9493-4d4f-9c6d-e19f39905cd3"
        event["rollback_action_type"] = "pause_then_replace"
    if "order" in event_type or "fill" in event_type:
        event["order_id"] = f"order-{event_type}"
        event["symbol"] = "SPY"
        event["quantity"] = 10.0
        event["price"] = 101.25
    if "position" in event_type:
        event["symbol"] = "SPY"
        event["position_qty"] = 10.0
        event["quantity"] = 10.0
        event["price"] = 101.25
    return event


class TelemetryEventRebaselineSchemaTest(unittest.TestCase):
    def test_schema_declares_rebaseline_runtime_action_event_surface(self) -> None:
        schema = _schema()

        if jsonschema is not None:
            jsonschema.Draft7Validator.check_schema(schema)

        self.assertEqual(schema["title"], "TelemetryEvent")
        self.assertEqual(schema["$id"], "https://pantheon/telemetry-event/v2")
        for field in (
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "deployment_stage",
            "plan_id",
            "persona_capital_binding_id",
        ):
            self.assertIn(field, schema["required"])

        declared = set(_event_types(schema))
        missing = REBASELINE_REQUIRED_EVENT_TYPES - declared
        self.assertEqual(missing, set())

    def test_every_declared_event_type_ingests_with_runtime_binding_evidence(self) -> None:
        async def scenario() -> tuple[dict[str, int], list[dict[str, Any]]]:
            svc = TelemetryIngestService(
                schema_path=str(SCHEMA_PATH),
                binding_store=_BindingStore(),
                batch_size=50,
                batch_interval=0.01,
            )
            await svc.start()
            rejected: list[dict[str, Any]] = []
            for event_type in _event_types(_schema()):
                event = _event(event_type)
                if not await svc.ingest(event):
                    rejected.append(event)
            await asyncio.sleep(0.05)
            stats = svc.stats()["service"]
            await svc.stop(graceful=True)
            return stats, rejected

        stats, rejected = asyncio.run(scenario())

        self.assertEqual(rejected, [])
        self.assertEqual(stats["total_rejected"], 0)
        self.assertEqual(stats["total_ingested"], len(_event_types(_schema())))


if __name__ == "__main__":
    unittest.main()
