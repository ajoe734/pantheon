from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.execution.lean_runtime.paper_runtime import (
    PaperRuntimeService,
    RuntimeTelemetryEmitter,
)
from services.execution.lean_runtime.pending_signal_store import (
    InMemoryPendingSignalStore,
)
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.lean_runtime.signal_producer import build_decision_signals
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.trade_journey.lifecycle_projector import LifecycleProjector, RelationalLifecycleProjector


class _RecordingRelationalStore:
    def __init__(self) -> None:
        self.controller = None
        self.receipts = {}
        self.stage_events = {}
        self.mutations = []

    def get_controller_state(self, *_args):
        return self.controller

    def get_receipt(self, event_id: str):
        return self.receipts.get(event_id)

    def get_receipts(self, event_ids):
        return {
            event_id: self.receipts[event_id]
            for event_id in event_ids
            if event_id in self.receipts
        }

    def load_journey_stage_events_bulk(self, keys):
        return {
            key: [dict(event) for event in self.stage_events.get(key, [])]
            for key in keys
        }

    def execute_batch_transaction(self, controller_id, tenant_scope, environment_scope, mutation):
        self.mutations.append(mutation)
        for receipt in mutation.receipts:
            self.receipts[receipt.event_id] = receipt
        for stage in mutation.stages:
            key = (stage.tenant_id, stage.environment, stage.journey_id)
            events = self.stage_events.setdefault(key, [])
            if not any(event.get("event_id") == stage.contract_fields.get("event_id") for event in events):
                events.append(dict(stage.contract_fields))
        prior = self.controller
        checkpoint = 0 if prior is None else prior.checkpoint_seq
        while any(receipt.ingested_seq == checkpoint + 1 for receipt in self.receipts.values()):
            checkpoint += 1
        revision = (0 if prior is None else prior.projection_revision) + bool(mutation.receipts)
        source_high = max(
            0 if prior is None else prior.source_high_watermark,
            mutation.source_high_watermark,
            checkpoint,
        )
        backlog = max(0, source_high - checkpoint)
        accepted_live = (
            mutation.mode == "live"
            and backlog == 0
            and len(mutation.quarantines) == 0
            and not mutation.error_message
        )
        from services.trade_journey.projection_store import ControllerStateRow
        self.controller = ControllerStateRow(
            controller_id=controller_id,
            tenant_scope=tenant_scope,
            environment_scope=environment_scope,
            checkpoint_seq=checkpoint,
            source_high_watermark=source_high,
            backlog_count=backlog,
            projection_revision=revision,
            deployment_sha=mutation.deployment_sha,
            mode=mutation.mode,
            status="ready" if accepted_live else mutation.status,
            accepted_live=accepted_live,
            unresolved_quarantine_count=len(mutation.quarantines),
        )
        return self.controller


class _RuntimeManagerClient:
    def __init__(self, binding: dict) -> None:
        self._binding = dict(binding)

    def list_all(self) -> list[dict]:
        return [dict(self._binding)]


class _BindingResolver:
    def __init__(self, binding: dict) -> None:
        self._binding = dict(binding)

    def resolve(self) -> dict:
        return dict(self._binding)


class _AcceptedResponse:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _load_reconciliation_service(data_dir: Path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    service_dir = repo_root / "services" / "reconciliation-drift"
    module_name = f"reconciliation_lifecycle_integration_{uuid.uuid4().hex}"
    previous_consumer = sys.modules.pop("consumer", None)
    previous_store = sys.modules.pop("store", None)
    monkeypatch.syspath_prepend(str(service_dir))
    monkeypatch.setenv("RECONCILIATION_DRIFT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("RECONCILIATION_DRIFT_STORE_BACKEND", "json")
    monkeypatch.setenv("PERSISTENCE_POSTURE", "lenient")
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "")
    spec = importlib.util.spec_from_file_location(module_name, service_dir / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("consumer", None)
        sys.modules.pop("store", None)
        if previous_consumer is not None:
            sys.modules["consumer"] = previous_consumer
        if previous_store is not None:
            sys.modules["store"] = previous_store
    return module


def test_real_paper_producer_runtime_reconciliation_projects_one_canonical_loop(
    tmp_path,
    monkeypatch,
):
    binding_id = "30000000-0000-4000-8000-000000000001"
    trace_id = "40000000-0000-4000-8000-000000000001"
    binding = {
        "binding_id": binding_id,
        "runtime_id": "runtime-paper-integration-001",
        "tenant_id": "tenant-paper-integration",
        "capital_pool_id": "pool-paper-integration",
        "artifact_id": "artifact-paper-integration",
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "plan_id": "plan-paper-integration",
        "persona_capital_binding_id": "pcb-paper-integration",
        "status": "active",
    }
    identity = RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": binding["runtime_id"],
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "test-runtime-token",
            "PANTHEON_TELEMETRY_URL": "http://telemetry:8083",
            "PANTHEON_WORKSPACE_REF": "workspace-paper-integration",
            "PANTHEON_AUTH_PROFILE_REF": "auth-paper-integration",
            "PANTHEON_PERSONA_ID": "persona-paper-integration",
            "PANTHEON_SESSION_ID": "session-paper-integration",
            "PANTHEON_TRACE_ID": trace_id,
            "PANTHEON_REQUEST_ID": "request-paper-integration",
        }
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_text = now.isoformat().replace("+00:00", "Z")
    [signal] = build_decision_signals(
        {
            "decision_id": "decision-paper-integration-001",
            "signal_id": "signal-paper-integration-001",
            "strategy_id": "strategy-paper-integration",
            "timestamp": now_text,
            "tenant_id": binding["tenant_id"],
            "environment": "paper",
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 2,
            "quantity_type": "SHARES",
            "run_id": "run-paper-integration-001",
        },
        binding_id=binding_id,
        runtime_id=binding["runtime_id"],
    )

    captured: list[dict] = []

    def accept_telemetry(request, timeout=None):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _AcceptedResponse()

    monkeypatch.setenv(
        "PANTHEON_PERFORMANCE_STATE_PATH",
        str(tmp_path / "paper-ledger.json"),
    )
    monkeypatch.setenv(
        "PANTHEON_LIFECYCLE_OUTBOX_PATH",
        str(tmp_path / "lifecycle-outbox.json"),
    )
    monkeypatch.setattr("urllib.request.urlopen", accept_telemetry)
    telemetry = RuntimeTelemetryEmitter(identity, _BindingResolver(binding))
    service = PaperRuntimeService(
        store=InMemoryPendingSignalStore([signal]),
        identity=identity,
        runtime_manager_client=_RuntimeManagerClient(binding),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    service.drain_once()
    service._consumer.flush_rebalance(signal["run_id"], service._algo)

    lifecycle_types = {
        "signal_generation",
        "trade_decision",
        "order_submitted",
        "paper_fill_simulated",
        "position_snapshot",
    }
    lifecycle = [event for event in captured if event["event_type"] in lifecycle_types]

    summary_store = RuntimeSummaryProjectionStore()
    summary = None
    for event in lifecycle:
        summary = summary_store.project_event(event)
    assert summary is not None

    reconciliation = _load_reconciliation_service(
        tmp_path / "reconciliation",
        monkeypatch,
    )
    reconciliation_event, reason = reconciliation._scheduled_lifecycle_event(
        summary=summary,
        evaluation={
            "evaluation_id": "evaluation-paper-integration-001",
            "tick_id": "tick-paper-integration-001",
            "binding_id": binding_id,
            "runtime_id": binding["runtime_id"],
            "status": "ok",
            "drift_checks": [],
            "reconciliation_checks": [{"check": "paper_ledger", "status": "ok"}],
        },
        timestamp=(now + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
    )

    committed = []
    for ingested_seq, payload in enumerate([*lifecycle, reconciliation_event], start=1):
        committed.append(
            {
                "ingested_seq": ingested_seq,
                "ingested_at": payload["created_at"],
                "event_id": payload["event_id"],
                "event_type": payload["event_type"],
                "created_at": payload["created_at"],
                "payload": payload,
            }
        )
    store = _RecordingRelationalStore()
    projector = RelationalLifecycleProjector(
        store,
        deployment_sha="integration-deadbeef",
    )
    result = projector.project_records(
        committed,
        mode="live",
        source_high_watermark=len(committed),
    )

    mutation = store.mutations[-1]
    assert [stage.stage_name for stage in mutation.stages] == [
        "signal_generation",
        "trade_decision",
        "order_submission",
        "fill_management",
        "ledger_booking",
        "reconciliation",
    ]
    assert {stage.journey_id for stage in mutation.stages} == {
        signal["journey_id"]
    }
    assert result.loop_run_count == 1
    loop = mutation.loop_runs[0]
    assert loop.loop_run_id == "lr-run-paper-integration-001"
    assert loop.status == "completed"
    assert loop.contract_payload["canonical_event_count"] == 6
    assert projector.controller["accepted_live"] is True
    assert projector.controller["status"] == "ready"
