from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.governance.deployment_plan import (
    RollbackRef,
    StagePlanner,
)
from services.control_plane.bff.management_read_models.service import (
    _page_slice,
    _project_operator_runtime_state_row,
    _snapshot_meta,
)
from services.control_plane.bff.runtime.router import create_runtime_router

_BFF_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BFF_DIR.parents[2]
_RUNTIME_MANAGER_SERVICE = _REPO_ROOT / "services" / "runtime-manager" / "service.py"
_EXEC_RUNTIME_MANAGER_DIR = _REPO_ROOT / "services" / "execution" / "runtime-manager"

os.environ.setdefault("PANTHEON_EXEC_RUNTIME_MANAGER_DIR", str(_EXEC_RUNTIME_MANAGER_DIR))

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    materialize_runtime_bootstrap_request,
)
from services.execution.lean_runtime.paper_runtime import (  # noqa: E402
    PaperRuntimeService,
    RuntimeBindingResolver,
    RuntimeTelemetryEmitter,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore  # noqa: E402
from services.execution.lean_runtime.runtime_context import (  # noqa: E402
    PantheonRuntimeContext,
    RuntimeContextSource,
)
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity  # noqa: E402
from services.telemetry.ingest_svc import TelemetryIngestService  # noqa: E402
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore  # noqa: E402


_SCHEMA_PATH = str(_REPO_ROOT / "services" / "telemetry" / "telemetry_event.schema.json")
_BRIDGE_COMMIT = "abc1234-p0-loop"
_OPERATOR_TOKEN = "Bearer p0-loop-operator:operator"


def _load_runtime_manager_service_type():
    spec = importlib.util.spec_from_file_location(
        "_p0_loop_runtime_manager_service",
        _RUNTIME_MANAGER_SERVICE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("runtime-manager service module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.RuntimeManagerService


def _registry_entry() -> dict[str, Any]:
    return {
        "registry_id": "artifact-p0-loop-paper-001",
        "artifact_type": "model_artifact",
        "strategy_id": "strategy-p0-loop-paper",
        "version": "1.0.0",
        "artifact_state": "approved",
        "checksum": "sha256:p0loop001",
        "approval_decision_id": "approval-p0-loop-001",
        "approved_at": "2026-05-01T00:00:00Z",
        "lineage": {"source_run_ids": ["experiment-p0-loop-001"]},
        "deployment_summary": {"current_stage": "none"},
    }


def _approval_decision() -> dict[str, Any]:
    return {
        "decision_id": "approval-p0-loop-001",
        "target_id": "artifact-p0-loop-paper-001",
        "target_version": "1.0.0",
        "decision_state": "decided",
        "decision": "approved",
        "capital_pool_id": "pool-p0-loop-paper",
        "persona_id": "persona-p0-loop-ops",
    }


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


class _RuntimeManagerClientAdapter:
    def __init__(self, runtime_manager: Any) -> None:
        self._runtime_manager = runtime_manager

    def list_all(self) -> list[dict[str, Any]]:
        return [binding.to_dict() for binding in self._runtime_manager.list_all()]


class _BindingStoreAdapter:
    def __init__(self, runtime_manager: Any) -> None:
        self._runtime_manager = runtime_manager

    def get_binding(self, binding_id: str) -> Any:
        return self._runtime_manager.get(binding_id)


class _LoopbackTelemetryEmitter:
    def __init__(
        self,
        *,
        identity: RuntimeIdentity,
        binding_resolver: RuntimeBindingResolver,
        ingest_service: TelemetryIngestService,
        runtime_context: PantheonRuntimeContext,
    ) -> None:
        self._emitter = RuntimeTelemetryEmitter(
            identity,
            binding_resolver,
            runtime_context=runtime_context,
        )
        self._ingest_service = ingest_service
        self.events: list[dict[str, Any]] = []
        self._sent = 0
        self._failed = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return True

    def emit(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        event = self._emitter.build_event(event_type, metrics, metadata)
        if event is None:
            self._failed += 1
            self._last_error = self._emitter.snapshot()["last_error"]
            return False

        self.events.append(event)
        ok = asyncio.run(self._ingest_service.ingest(event))
        if ok:
            self._sent += 1
            self._last_error = None
        else:
            self._failed += 1
            self._last_error = "telemetry ingest rejected event"
        return ok

    def emit_deploy_started(self) -> bool:
        return self.emit(
            "deploy_started",
            {"action": "deploy_started"},
            metadata={"runtime_package": "paper_execution_runtime"},
        )

    def emit_deploy_completed(self) -> bool:
        return self.emit(
            "deploy_completed",
            {"action": "deploy_completed"},
            metadata={"runtime_package": "paper_execution_runtime"},
        )

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata=metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata=metadata)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "url": "loopback://telemetry-ingest",
            "sent": self._sent,
            "failed": self._failed,
            "last_error": self._last_error,
        }


class _BffRuntimeStateStore:
    def __init__(
        self,
        *,
        runtime_manager: Any,
        runtime_summary_store: RuntimeSummaryProjectionStore,
    ) -> None:
        self._runtime_manager = runtime_manager
        self._runtime_summary_store = runtime_summary_store

    def list_runtime_bindings(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for binding in self._runtime_manager.list_all():
            payload = binding.to_dict()
            payload["id"] = payload["binding_id"]
            payload["runtime_binding_id"] = payload["binding_id"]
            payload["deployment_stage"] = payload["deployment_mode"]
            rows.append(payload)
        return rows

    def get_telemetry_summary(self, runtime_id: str) -> dict[str, Any] | None:
        return self._runtime_summary_store.get(runtime_id)

    def get_paper_runtime_monitoring_session(
        self,
        *,
        runtime_id: str,
        binding_id: str,
    ) -> dict[str, Any] | None:
        summary = self._runtime_summary_store.get(runtime_id)
        if not summary:
            return None
        return {
            "session_id": f"paper-session-{runtime_id}",
            "session_type": "paper_runtime",
            "binding_id": binding_id,
            "runtime_binding_id": binding_id,
            "runtime_id": runtime_id,
            "deployment_stage": summary.get("deployment_stage", "paper"),
            "status": "active",
            "active": True,
            "started_at": summary.get("first_heartbeat_at") or summary.get("last_heartbeat_at"),
            "last_heartbeat_at": summary.get("last_heartbeat_at"),
            "heartbeat_status": "ok",
            "stale_after_seconds": 60,
            "restart_count": 0,
        }

    def get_rollbacks(self, runtime_id: str) -> list[dict[str, Any]]:
        return []

    def dataset_source(self, dataset: str, **_: Any) -> str:
        return {
            "runtime_bindings": "runtime_manager",
            "telemetry_summaries": "telemetry_ingest_projection",
            "paper_runtime_monitoring_sessions": "paper_runtime",
            "rollbacks": "runtime_manager",
        }.get(dataset, "missing")


class MinimumPaperOperatingLoopSmokeTest(unittest.TestCase):
    def test_deployment_plan_to_runtime_binding_to_bff_runtime_status(self) -> None:
        RuntimeManagerService = _load_runtime_manager_service_type()
        registry_entry = _registry_entry()
        planner = StagePlanner()
        plan = planner.create_plan(
            plan_id="plan-p0-loop-paper-001",
            approval_decision_id="approval-p0-loop-001",
            approval_decision=_approval_decision(),
            registry_entry=registry_entry,
            capital_pool_id="pool-p0-loop-paper",
            target_stage="paper",
            sponsor_persona_id="persona-p0-loop-ops",
            runtime_config_ref="/workspace/lean/Launcher/config.json",
            rollback=RollbackRef(
                target_artifact_id="artifact-p0-loop-paper-000",
                target_version="0.9.0",
                action_type="replace",
            ),
            metadata={
                "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
                "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
                "engine_bridge_commit": _BRIDGE_COMMIT,
                "runtime_adapter_version": "0.1.0",
                "runtime_role": "pantheon-lean-paper-runtime",
            },
        )
        projection = planner.build_execution_projection(plan, registry_entry)

        orig_outbox = os.environ.get("PANTHEON_LIFECYCLE_OUTBOX_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="p0_loop_smoke_") as temp_dir:
                os.environ["PANTHEON_LIFECYCLE_OUTBOX_PATH"] = str(Path(temp_dir) / "lifecycle-outbox.json")
                runtime_manager = RuntimeManagerService(
                    store_path=Path(temp_dir) / "runtime_bindings.json",
                    single_runtime_enforced=True,
                )
            binding = runtime_manager.deploy(
                {
                    "plan_id": plan.plan_id,
                    "plan_status": _enum_value(plan.status),
                    "target_stage": _enum_value(plan.target_stage),
                    "artifact_id": plan.artifact_id,
                    "artifact_version": plan.artifact_version,
                    "capital_pool_id": plan.capital_pool_id,
                    "persona_capital_binding_id": "pcb-p0-loop-paper",
                    "persona_capital_binding_status": "active",
                    "allowed_deployment_scope": "paper",
                    "loader_checks_passed": True,
                    "runtime_id": "runtime-p0-loop-paper",
                    "strategy_id": plan.strategy_id,
                    "metadata": {
                        "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
                        "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
                        "engine_bridge_commit": _BRIDGE_COMMIT,
                        "runtime_adapter_version": "0.1.0",
                        "artifact_checksum": projection.metadata["checksum"],
                    },
                }
            )

            plan_payload = plan.to_dict()
            plan_payload["artifact_state"] = "approved"
            plan_payload["artifact_checksum"] = projection.metadata["checksum"]
            plan_payload["runtime_role"] = "pantheon-lean-paper-runtime"
            plan_payload["runtime_config_status"] = "approved"
            plan_payload["risk_policy_ref"] = "risk-policy-p0-loop-paper"
            plan_payload["risk_policy_evaluation"] = {
                "risk_policy_id": "risk-policy-p0-loop-paper",
                "risk_policy_version": "v1",
                "capital_pool_id": plan.capital_pool_id,
                "target_type": "runtime_launch",
                "target_id": plan.plan_id,
                "decision": "allowed",
                "checks": [],
                "blocking_reasons": [],
                "warnings": [],
                "evaluated_at": "2026-06-09T00:00:00Z",
                "trace_id": "trace-risk-policy-p0-loop-paper",
            }
            bootstrap_request = materialize_runtime_bootstrap_request(
                deployment_plan=plan_payload,
                runtime_binding=binding.to_dict(),
                request_id="rbr-p0-loop-paper-001",
                trace_id=str(uuid.uuid4()),
            )
            runtime_context = PantheonRuntimeContext.from_mapping(
                bootstrap_request.to_dict(),
                source=RuntimeContextSource.LAUNCH_MANIFEST,
                expected_stage="paper",
            )

            summary_store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
            ingest_service = TelemetryIngestService(
                schema_path=_SCHEMA_PATH,
                binding_store=_BindingStoreAdapter(runtime_manager),
                runtime_summary_store=summary_store,
                batch_size=10,
                batch_interval=0.01,
            )
            runtime_client = _RuntimeManagerClientAdapter(runtime_manager)
            identity_env = bootstrap_request.to_runtime_env()
            identity_env.update(
                {
                    "PANTHEON_RUNTIME_MANAGER_URL": "memory://runtime-manager",
                    "PANTHEON_TELEMETRY_URL": "loopback://telemetry-ingest",
                    "PANTHEON_WORKSPACE_REF": "workspace-p0-loop-paper",
                    "PANTHEON_AUTH_PROFILE_REF": "auth-profile-p0-loop-paper",
                    "PANTHEON_PERSONA_ID": "persona-p0-loop-ops",
                    "PANTHEON_SESSION_ID": "session-p0-loop-paper",
                }
            )
            identity = RuntimeIdentity.from_env(identity_env)
            loopback_emitter = _LoopbackTelemetryEmitter(
                identity=identity,
                binding_resolver=RuntimeBindingResolver(
                    runtime_client,
                    identity.runtime_id,
                    runtime_context=runtime_context,
                ),
                ingest_service=ingest_service,
                runtime_context=runtime_context,
            )
            paper_runtime = PaperRuntimeService(
                store=InMemoryPendingSignalStore(),
                identity=identity,
                runtime_manager_client=runtime_client,
                telemetry_emitter=loopback_emitter,
                runtime_context=runtime_context,
                poll_interval_seconds=3600,
            )

            runtime_snapshot = paper_runtime.drain_once()
            runtime_summary = summary_store.get(runtime_context.runtime_id)

            self.assertEqual(plan.artifact_id, registry_entry["registry_id"])
            self.assertEqual(binding.plan_id, plan.plan_id)
            self.assertEqual(binding.deployment_mode, "paper")
            self.assertEqual(bootstrap_request.bridge.remote, PANTHEON_LEAN_REMOTE)
            self.assertEqual(bootstrap_request.bridge.source_path, PANTHEON_LEAN_SOURCE_PATH)
            self.assertTrue(bootstrap_request.runtime_config.paper_mode)
            self.assertFalse(bootstrap_request.runtime_config.live_broker_enabled)
            self.assertFalse(runtime_snapshot["stub_mode"])
            self.assertEqual(runtime_snapshot["paper_state"]["processed_signal_count"], 0)
            self.assertIsNotNone(runtime_snapshot["paper_state"]["last_heartbeat_at"])
            self.assertIsNotNone(runtime_summary)
            assert runtime_summary is not None
            self.assertEqual(runtime_summary["runtime_binding_id"], binding.binding_id)
            self.assertEqual(runtime_summary["deployment_stage"], "paper")
            self.assertEqual(runtime_summary["last_heartbeat_at"], runtime_snapshot["paper_state"]["last_heartbeat_at"])
            self.assertEqual(runtime_summary["engine_bridge_repo"], PANTHEON_LEAN_REMOTE)
            self.assertEqual(runtime_summary["engine_bridge_path"], PANTHEON_LEAN_SOURCE_PATH)
            self.assertEqual(runtime_summary["health_summary"]["broker"], "not_applicable")
            self.assertTrue(loopback_emitter.events)
            self.assertTrue(
                all(event["execution_mode"] == "paper" for event in loopback_emitter.events)
            )
            self.assertTrue(
                all(event["deployment_stage"] == "paper" for event in loopback_emitter.events)
            )
            self.assertNotIn(
                "lean-platform",
                " ".join(
                    str(event.get("metadata", {}).get("engine_bridge_repo", ""))
                    for event in loopback_emitter.events
                ),
            )

            store = _BffRuntimeStateStore(
                runtime_manager=runtime_manager,
                runtime_summary_store=summary_store,
            )

            def _utc_now() -> str:
                return "2026-06-09T00:00:00Z"

            def _dataset_surface_status(
                dataset: str,
                *,
                snapshot_at: str | None = None,
                has_data: bool | None = None,
                missing_message: str | None = None,
                source: str | None = None,
            ) -> dict[str, Any]:
                source = source or store.dataset_source(dataset)
                surface: dict[str, Any] = {"status": "ok", "source": source}
                now = snapshot_at or _utc_now()
                if source == "local_snapshot":
                    surface["status"] = "degraded"
                    surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
                    surface["staleness"] = {
                        "served_from": "local_snapshot",
                        "last_known_at": now,
                    }
                elif source == "missing":
                    surface["status"] = "unavailable"
                    surface["staleness"] = {
                        "served_from": "unverifiable",
                        "last_known_at": now,
                    }
                elif source in {"canonical", "service_client"}:
                    surface["status"] = "ok"

                if has_data is False:
                    surface["status"] = "unavailable"
                    if missing_message:
                        surface["message"] = missing_message
                    surface["staleness"] = {
                        "served_from": "unverifiable",
                        "last_known_at": now,
                    }
                return surface

            def _composed_surface_status(
                *,
                snapshot_at: str | None = None,
                available: bool = True,
                missing_message: str | None = None,
            ) -> dict[str, Any]:
                now = snapshot_at or _utc_now()
                surface: dict[str, Any] = {"status": "ok", "source": "bff_composed"}
                if not available:
                    surface["status"] = "degraded"
                    if missing_message:
                        surface["message"] = missing_message
                    surface["staleness"] = {
                        "served_from": "unverifiable",
                        "last_known_at": now,
                    }
                return surface

            def _split_csv_query(value: str | None) -> list[str]:
                if not value:
                    return []
                return [part.strip() for part in str(value).split(",") if part.strip()]

            router = create_runtime_router(
                read_surface=store,
                dependencies={
                    "_extract_identity": lambda auth, **kw: auth_policy.extract_identity_stub(auth),
                    "_require_read_role": auth_policy.require_read_role,
                    "_bff_error": auth_policy.bff_error,
                    "utc_now": _utc_now,
                    "_split_csv_query": _split_csv_query,
                    "_project_operator_runtime_state_row": lambda binding: _project_operator_runtime_state_row(store, binding),
                    "_dataset_surface_status": _dataset_surface_status,
                    "_composed_surface_status": _composed_surface_status,
                    "_page_slice": _page_slice,
                    "_snapshot_meta": _snapshot_meta,
                },
            )
            app = FastAPI()
            app.include_router(router)
            response = TestClient(app).get(
                "/api/v1/operator/runtime-state",
                headers={"Authorization": _OPERATOR_TOKEN},
            )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["meta"]["surfaces"]["runtime_state"]["status"], "ok")
            runtime_row = payload["runtimes"][0]
            self.assertEqual(runtime_row["runtime_id"], runtime_context.runtime_id)
            self.assertEqual(runtime_row["runtime_binding_id"], binding.binding_id)
            self.assertEqual(runtime_row["deployment_stage"], "paper")
            self.assertEqual(
                runtime_row["telemetry_summary"]["last_heartbeat_at"],
                runtime_summary["last_heartbeat_at"],
            )
            self.assertEqual(
                runtime_row["telemetry_summary"]["engine_bridge_repo"],
                PANTHEON_LEAN_REMOTE,
            )
            self.assertEqual(
                runtime_row["telemetry_summary"]["health_summary"]["broker"],
                "not_applicable",
            )
        finally:
            if orig_outbox is None:
                os.environ.pop("PANTHEON_LIFECYCLE_OUTBOX_PATH", None)
            else:
                os.environ["PANTHEON_LIFECYCLE_OUTBOX_PATH"] = orig_outbox


if __name__ == "__main__":
    unittest.main()
