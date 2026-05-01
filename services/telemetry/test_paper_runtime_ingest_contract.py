"""P0-TEL-001 paper runtime TelemetryEvent ingest contract tests."""

from __future__ import annotations

import asyncio
import types
import unittest
import uuid
from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import RuntimeTelemetryEmitter
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.telemetry.ingest_svc import TelemetryIngestService


SCHEMA_PATH = str(Path(__file__).with_name("telemetry_event.schema.json"))
KNOWN_BINDING_ID = str(uuid.uuid4())


def _known_binding() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        binding_id=KNOWN_BINDING_ID,
        runtime_id="rt-paper-contract",
        capital_pool_id="pool-paper-contract",
        artifact_id="artifact-paper-contract",
        artifact_version="1.0.0",
        plan_id="plan-paper-contract",
        persona_capital_binding_id="pcb-paper-contract",
        deployment_mode="paper",
        effective_at="2026-01-01T00:00:00Z",
        retired_at=None,
    )


class _StubBindingStore:
    def get_binding(self, binding_id: str):
        if binding_id == KNOWN_BINDING_ID:
            return _known_binding()
        return None


class _FakeBindingResolver:
    def resolve(self) -> dict[str, Any]:
        binding = _known_binding()
        return {
            "binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
            "capital_pool_id": binding.capital_pool_id,
            "artifact_id": binding.artifact_id,
            "artifact_version": binding.artifact_version,
            "plan_id": binding.plan_id,
            "persona_capital_binding_id": binding.persona_capital_binding_id,
            "deployment_mode": binding.deployment_mode,
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
            "context_source": "launch_manifest",
        }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "paper",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "rt-paper-contract",
            "PANTHEON_WORKSPACE_REF": "workspace-paper-contract",
            "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper-contract",
            "PANTHEON_PERSONA_ID": "persona-paper-contract",
            "PANTHEON_SESSION_ID": "session-paper-contract",
            "PANTHEON_TRACE_ID": str(uuid.uuid4()),
            "PANTHEON_REQUEST_ID": "request-paper-contract",
        }
    )


def _paper_event(
    *,
    event_type: str = "heartbeat",
    event_id: str | None = None,
    deployment_stage: str = "paper",
    binding_id: str | None = KNOWN_BINDING_ID,
    metrics: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    binding = _known_binding()
    event = {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "created_at": "2026-05-01T00:00:00Z",
        "execution_mode": "paper" if deployment_stage == "paper" else "live",
        "environment": deployment_stage,
        "deployment_stage": deployment_stage,
        "binding_id": binding_id,
        "runtime_id": binding.runtime_id,
        "capital_pool_id": binding.capital_pool_id,
        "artifact_id": binding.artifact_id,
        "artifact_version": binding.artifact_version,
        "plan_id": binding.plan_id,
        "persona_capital_binding_id": binding.persona_capital_binding_id,
        "target": {
            "strategy_id": "strategy-paper-contract",
            "artifact_type": "execution_bundle",
            "artifact_version": binding.artifact_version,
            "lineage_ref": "lineage-paper-contract",
        },
        "metrics": metrics or {"heartbeat": 1},
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_role": "paper",
            "context_source": "launch_manifest",
            **(metadata or {}),
        },
    }
    if binding_id is None:
        event.pop("binding_id")
    return event


class PaperRuntimeTelemetryIngestContractTest(unittest.TestCase):
    def test_paper_heartbeat_validates_and_dedupes_by_event_id(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            emitter = RuntimeTelemetryEmitter(_runtime_identity(), _FakeBindingResolver())
            event = emitter.build_event(
                "heartbeat",
                {"heartbeat": 1},
                metadata={"runtime_package": "paper_execution_runtime"},
                event_id=str(uuid.uuid4()),
                created_at="2026-05-01T00:00:00Z",
            )
            self.assertIsNotNone(event)
            first = await svc.ingest(event)
            duplicate = await svc.ingest(dict(event))
            await asyncio.sleep(0.05)
            await svc.stop(graceful=True)
            stats = svc.stats()
            return first, duplicate, stats

        first, duplicate, stats = asyncio.run(scenario())

        self.assertTrue(first)
        self.assertTrue(duplicate)
        self.assertEqual(stats["service"]["total_ingested"], 1)
        self.assertEqual(stats["service"]["total_duplicates"], 1)
        self.assertEqual(stats["service"]["total_rejected"], 0)
        self.assertEqual(stats["writer"]["total_written"], 1)

    def test_stage_mismatch_rejected_against_runtime_binding(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
            )
            await svc.start()
            ok = await svc.ingest(_paper_event(deployment_stage="live"))
            stats = svc.stats()
            dlq = svc.get_dlq_entries()
            await svc.stop(graceful=True)
            return ok, stats, dlq

        ok, stats, dlq = asyncio.run(scenario())

        self.assertFalse(ok)
        self.assertEqual(stats["service"]["total_rejected"], 1)
        self.assertIn("deployment_stage", dlq[0]["reason"])

    def test_missing_binding_rejected_by_schema_or_evidence_contract(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
            )
            await svc.start()
            ok = await svc.ingest(_paper_event(binding_id=None))
            stats = svc.stats()
            dlq = svc.get_dlq_entries()
            await svc.stop(graceful=True)
            return ok, stats, dlq

        ok, stats, dlq = asyncio.run(scenario())

        self.assertFalse(ok)
        self.assertEqual(stats["service"]["total_rejected"], 1)
        self.assertIn("binding", dlq[0]["reason"].lower())

    def test_bracket_order_logged_validates_as_logged_only_not_broker_submission(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            event = _paper_event(
                event_type="bracket_order_logged",
                metrics={"action": "bracket_logged_only"},
                metadata={
                    "broker_submission_status": "logged_only",
                    "submitted_to_broker": False,
                    "is_real_order": False,
                    "is_real_capital": False,
                },
            )
            ok = await svc.ingest(event)
            await asyncio.sleep(0.05)
            await svc.stop(graceful=True)
            stats = svc.stats()
            return ok, stats

        ok, stats = asyncio.run(scenario())

        self.assertTrue(ok)
        self.assertEqual(stats["service"]["total_ingested"], 1)
        self.assertEqual(stats["writer"]["total_written"], 1)


if __name__ == "__main__":
    unittest.main()
