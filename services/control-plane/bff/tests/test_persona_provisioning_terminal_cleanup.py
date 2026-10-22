"""Focused regression tests for terminal Persona provisioning cleanup and cron snapshot reuse.

Task: PERSONA-PROVISIONING-FAILED-TERMINAL-20261020
Acceptance criteria:
1. Once first-evaluation schedule cleanup for a provisioning_failed persona has an
   authoritative readback showing no registration, the reconciler records that outcome
   durably and later passes make zero cron RPCs and zero persona record writes for that
   persona.
2. A failed cleanup is retried with bounded backoff instead of every reconciler pass.
3. Each reconciler pass reads cron registrations at most once through a single cron.list
   snapshot and passes it as all_cron_registrations so a provisioning persona whose
   schedule already matches is not re-registered.
4. Persona lifecycle states and provisioning readback responses are unchanged for operators.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    deterministic_provisioning_ids,
)
from services.control_plane.bff.personas import service as _persona_service_module
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.tests.test_persona_provisioning_reconcile import (
    _RecordingPersonaMutationPort,
    _provisioning_read_surface_double,
)
from services.control_plane.cron.persona_cron_registrar import (
    PersonaCronRegistrar,
    _job_name,
)
from services.control_plane.cron.workflows import (
    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
    WORKFLOW_CATALOG,
)


class _RecordingCronGatewayRuntime:
    """In-memory CronGatewayRuntime recording every RPC call."""

    def __init__(self, jobs: Optional[List[Dict[str, Any]]] = None) -> None:
        self.jobs: List[Dict[str, Any]] = deepcopy(jobs or [])
        self.calls: List[tuple[str, Optional[Dict[str, Any]]]] = []
        self.fail_error: Optional[Exception] = None

    def gateway_call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        self.calls.append((method, deepcopy(params)))
        if self.fail_error is not None:
            raise self.fail_error
        if method == "cron.list":
            return {"jobs": deepcopy(self.jobs)}
        if method == "cron.remove":
            job_id = str((params or {}).get("id") or "")
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if str(j.get("id") or "") != job_id]
            return {"ok": True, "removed": len(self.jobs) < before}
        if method == "cron.add":
            job = deepcopy(params or {})
            job.setdefault("id", f"job-{len(self.jobs) + 1}")
            self.jobs.append(job)
            return {"job": deepcopy(job)}
        if method == "cron.update":
            job_id = str((params or {}).get("id") or "")
            patch = deepcopy((params or {}).get("patch") or {})
            for job in self.jobs:
                if str(job.get("id") or "") == job_id:
                    job.update(patch)
                    return {"job": deepcopy(job)}
            raise RuntimeError(f"Unknown job id {job_id}")
        raise AssertionError(f"Unexpected cron RPC method: {method}")


def _build_matching_cron_job(
    *,
    persona_id: str,
    runtime_id: str,
    runtime_binding_id: str,
    capital_pool_id: str,
    persona_capital_binding_id: str,
    job_id: str = "job-canonical-1",
) -> Dict[str, Any]:
    workflow = WORKFLOW_CATALOG[PERSONA_FIRST_EVALUATION_WORKFLOW_ID]
    event = {
        "kind": "pantheon.workflow.dispatch",
        "persona_id": persona_id,
        "policy_id": workflow.policy_id,
        "request_id": (
            f"persona-provisioning:{persona_id}:{PERSONA_FIRST_EVALUATION_WORKFLOW_ID}"
        ),
        "upstream_entrypoint": workflow.upstream_entrypoint,
        "workflow_id": PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "capital_pool_id": capital_pool_id,
        "persona_capital_binding_id": persona_capital_binding_id,
    }
    return {
        "id": job_id,
        "name": _job_name(PERSONA_FIRST_EVALUATION_WORKFLOW_ID, persona_id),
        "enabled": True,
        "deleteAfterRun": False,
        "schedule": {"kind": "cron", "expr": workflow.schedule},
        "sessionTarget": "main",
        "wakeMode": "next-heartbeat",
        "payload": {
            "kind": "systemEvent",
            "text": json.dumps(event),
        },
    }


@pytest.fixture()
def terminal_cleanup_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> tuple[Any, _RecordingPersonaMutationPort, _RecordingCronGatewayRuntime]:
    monkeypatch.setenv(
        "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner"
    )
    read_surface = _provisioning_read_surface_double()
    mutation_port = _RecordingPersonaMutationPort(read_surface)
    cmd_store = CommandStore(os.path.join(str(tmp_path), "commands.jsonl"))
    persona_svc = PersonaService(
        write_owner=mutation_port,
        read_store=read_surface,
        ranking_write_owner=object(),
        command_store=cmd_store,
    )
    persona_svc._write_owner = mutation_port
    persona_svc._read_store = read_surface
    monkeypatch.setattr(
        _persona_service_module, "_composed_persona_service", persona_svc
    )
    monkeypatch.setattr(
        _persona_service_module,
        "_PERSONA_PROVISIONING_STORE",
        MemoryPersonaProvisioningStore(),
    )
    getattr(_persona_service_module, "_PERSONA_CLEANUP_BACKOFF", {}).clear()

    cron_runtime = _RecordingCronGatewayRuntime()
    monkeypatch.setattr(
        PersonaCronRegistrar, "_get_runtime", lambda self, *_a, **_kw: cron_runtime
    )
    return read_surface, mutation_port, cron_runtime


def test_provisioning_failed_with_cleanup_completed_makes_zero_cron_rpcs_and_zero_writes_across_ten_passes(
    terminal_cleanup_harness: Any,
) -> None:
    read_surface, mutation_port, cron_runtime = terminal_cleanup_harness
    persona_id = "persona-terminal-clean-001"
    cron_runtime.jobs.append(
        _build_matching_cron_job(
            persona_id=persona_id,
            runtime_id="rt-001",
            runtime_binding_id="rb-001",
            capital_pool_id="pool-001",
            persona_capital_binding_id="pcb-001",
            job_id="job-stale-001",
        )
    )
    read_surface.create_persona(
        persona_id=persona_id,
        name="Terminal Failed Persona",
        actor_id="op-2",
        lifecycle_state="provisioning_failed",
        metadata={
            "tenant_id": "tenant-a",
            "provisioning_idempotency_key": "idem-terminal-clean-001",
            "provisioning_failure_reason": "deployment_saga_failed",
            "provisioning_compensation": {
                "status": "completed",
                "reason": "deployment_saga_failed",
            },
        },
    )
    mutation_port.updates.clear()
    cron_runtime.calls.clear()

    # Pass 0 performs the authoritative schedule removal and records registered=False durably.
    _persona_service_module._reconcile_persona_provisioning_once()
    persisted = read_surface.get_persona(persona_id)
    assert persisted is not None
    assert persisted["lifecycle_state"] == "provisioning_failed"
    cleanup_receipt = persisted["metadata"].get("first_evaluation_schedule_cleanup")
    assert isinstance(cleanup_receipt, dict)
    assert cleanup_receipt.get("registered") is False
    assert cleanup_receipt.get("removed_ids") == ["job-stale-001"]

    # Clear counters and run 10 consecutive reconciler passes.
    cron_runtime.calls.clear()
    mutation_port.updates.clear()
    for _ in range(10):
        _persona_service_module._reconcile_persona_provisioning_once()

    assert cron_runtime.calls == []
    assert mutation_port.updates == []

    # Operator DTO and provisioning metadata projection preserve lifecycle state and cleanup receipt.
    dto_before = _persona_service_module._project_persona_dto(persisted)
    assert dto_before["lifecycleStatus"] == "provisioning_failed"
    ledger_record = ProvisioningRecord(
        tenant_id="tenant-a",
        idempotency_key="idem-terminal-clean-001",
        request_hash="hash-001",
        persona_id=persona_id,
        normalized_name="terminal-failed-persona",
        request_payload={"name": "Terminal Failed Persona"},
        state="failed",
        current_step="failed",
        references={"first_evaluation_schedule_cleanup": deepcopy(cleanup_receipt)},
    )
    ids = deterministic_provisioning_ids(ledger_record)
    projected_meta = _persona_service_module._persona_provisioning_metadata(
        ledger_record,
        ids=ids,
        payload={"name": "Terminal Failed Persona"},
        owner="op-2",
        archetype="generalist",
        risk="low",
        mandate=None,
        strategy_family=None,
        traits=None,
        lifecycle_state="provisioning_failed",
    )
    assert projected_meta["first_evaluation_schedule_cleanup"]["registered"] is False
    after_ten_passes = read_surface.get_persona(persona_id)
    assert after_ten_passes["lifecycle_state"] == "provisioning_failed"
    assert (
        after_ten_passes["metadata"]["first_evaluation_schedule_cleanup"]["registered"]
        is False
    )
    assert (
        _persona_service_module._project_persona_dto(after_ten_passes)[
            "lifecycleStatus"
        ]
        == "provisioning_failed"
    )


def test_failed_schedule_cleanup_retries_with_bounded_backoff(
    terminal_cleanup_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_surface, mutation_port, cron_runtime = terminal_cleanup_harness
    persona_id = "persona-cleanup-backoff-001"
    read_surface.create_persona(
        persona_id=persona_id,
        name="Backoff Failed Persona",
        actor_id="op-2",
        lifecycle_state="provisioning_failed",
        metadata={
            "tenant_id": "tenant-a",
            "provisioning_idempotency_key": "idem-backoff-001",
            "provisioning_failure_reason": "deployment_saga_failed",
            "provisioning_compensation": {
                "status": "completed",
                "reason": "deployment_saga_failed",
            },
        },
    )
    mutation_port.updates.clear()
    cron_runtime.calls.clear()

    now_mono = [1000.0]
    monkeypatch.setattr(_persona_service_module.time, "monotonic", lambda: now_mono[0])
    cron_runtime.fail_error = RuntimeError("cron gateway unavailable")

    # Pass 1 at t=1000s: attempts cleanup, fails, records pending receipt, arms 10s backoff.
    _persona_service_module._reconcile_persona_provisioning_once()
    assert len(cron_runtime.calls) == 1
    assert cron_runtime.calls[0][0] == "cron.list"
    assert len(mutation_port.updates) == 1
    assert (
        mutation_port.updates[0]["metadata"]["first_evaluation_schedule_cleanup"][
            "status"
        ]
        == "pending"
    )

    # Passes 2 & 3 within backoff window (t=1005s, t=1009s): 0 cron RPCs, 0 writes.
    cron_runtime.calls.clear()
    mutation_port.updates.clear()
    for step_time in (1005.0, 1009.0):
        now_mono[0] = step_time
        _persona_service_module._reconcile_persona_provisioning_once()
    assert cron_runtime.calls == []
    assert mutation_port.updates == []

    # Direct evaluation during backoff still reports "persona_cron" diagnostic.
    diagnostics: List[str] = []
    _persona_service_module._evaluate_persona_provisioning_status(
        persona_id,
        read_surface.get_persona(persona_id),
        diagnostics=diagnostics,
    )
    assert diagnostics == ["persona_cron"]
    assert cron_runtime.calls == []
    assert mutation_port.updates == []

    # Pass 4 after first backoff expires (t=1011s): retries once, fails again, arms 20s backoff.
    now_mono[0] = 1011.0
    _persona_service_module._reconcile_persona_provisioning_once()
    assert len(cron_runtime.calls) == 1
    assert cron_runtime.calls[0][0] == "cron.list"

    # Pass 5 within 20s backoff window (t=1025s): 0 cron RPCs, 0 writes.
    cron_runtime.calls.clear()
    mutation_port.updates.clear()
    now_mono[0] = 1025.0
    _persona_service_module._reconcile_persona_provisioning_once()
    assert cron_runtime.calls == []
    assert mutation_port.updates == []

    # Pass 6 after 20s backoff expires (t=1032s) with healthy cron gateway: succeeds and settles.
    cron_runtime.fail_error = None
    now_mono[0] = 1032.0
    _persona_service_module._reconcile_persona_provisioning_once()
    assert [c[0] for c in cron_runtime.calls] == ["cron.list"]
    persisted = read_surface.get_persona(persona_id)
    assert (
        persisted["metadata"]["first_evaluation_schedule_cleanup"]["registered"]
        is False
    )

    # Subsequent pass (t=1035s): 0 cron RPCs and 0 writes.
    cron_runtime.calls.clear()
    mutation_port.updates.clear()
    now_mono[0] = 1035.0
    _persona_service_module._reconcile_persona_provisioning_once()
    assert cron_runtime.calls == []
    assert mutation_port.updates == []


def test_reconciler_pass_reads_cron_once_and_skips_reregistering_matching_provisioning_persona(
    terminal_cleanup_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_surface, mutation_port, cron_runtime = terminal_cleanup_harness
    persona_id = "persona-provisioning-match-001"
    ledger_store = _persona_service_module._persona_provisioning_store()
    seeded_record, _ = ledger_store.reserve(
        tenant_id="tenant-a",
        idempotency_key="idem-match-001",
        request_hash="hash-match-001",
        normalized_name="provisioning-match-persona",
        persona_id=persona_id,
        request_payload={"name": "Provisioning Match Persona"},
    )
    ids = deterministic_provisioning_ids(seeded_record)
    plan_id = ids.deployment_plan_id
    saga_id = ids.deployment_saga_id
    runtime_id = "rt-match-001"
    runtime_binding_id = "rb-match-001"
    capital_pool_id = ids.capital_pool_id
    persona_capital_binding_id = ids.persona_capital_binding_id

    matching_job = _build_matching_cron_job(
        persona_id=persona_id,
        runtime_id=runtime_id,
        runtime_binding_id=runtime_binding_id,
        capital_pool_id=capital_pool_id,
        persona_capital_binding_id=persona_capital_binding_id,
        job_id="job-match-001",
    )
    cron_runtime.jobs.append(matching_job)

    now_iso = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    seeded_record.references["provisioning_readback_started_at"] = now_iso
    read_surface.create_persona(
        persona_id=persona_id,
        name="Provisioning Match Persona",
        actor_id="op-2",
        lifecycle_state="provisioning",
        metadata={
            "tenant_id": "tenant-a",
            "provisioning_idempotency_key": "idem-match-001",
            "deployment_plan_id": plan_id,
            "deployment_saga_id": saga_id,
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
            "internal_paper_capital_pool_id": capital_pool_id,
            "persona_capital_binding_id": persona_capital_binding_id,
            "provisioning_readback_started_at": now_iso,
        },
    )

    authoritative_binding = {
        "binding_id": runtime_binding_id,
        "runtime_id": runtime_id,
        "plan_id": plan_id,
        "capital_pool_id": capital_pool_id,
        "persona_capital_binding_id": persona_capital_binding_id,
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"persona_id": persona_id, "tenant_id": "tenant-a"},
    }
    projection = {
        "plan_id": plan_id,
        "deployment_saga_id": saga_id,
        "deployment_saga_status": "completed",
        "deployment_saga_progress": {"progress_status": "completed"},
        "runtime_binding_id": runtime_binding_id,
        "runtime_id": runtime_id,
        "runtime_binding": authoritative_binding,
    }

    class _ProjectionTransport:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def get(self, owner: str, path: str) -> Any:
            if owner == "deployment":
                return deepcopy(projection)
            raise AssertionError(f"Unexpected transport GET {owner} {path}")

    class _RuntimeManagerClient:
        def get(self, bid: str) -> Any:
            return deepcopy(authoritative_binding) if bid == runtime_binding_id else None

        def list_by_plan(self, pid: str) -> Any:
            return [deepcopy(authoritative_binding)] if pid == plan_id else []

        def list_all(self) -> Any:
            return [deepcopy(authoritative_binding)]

    monkeypatch.setattr(
        _persona_service_module, "_PersonaOwnerHttpTransport", _ProjectionTransport
    )
    monkeypatch.setattr(
        _persona_service_module, "_runtime_manager_client", lambda: _RuntimeManagerClient()
    )
    # Worker startup session without heartbeat yet keeps persona in `provisioning`.
    read_surface.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
        {
            "session_id": f"session-{persona_id}",
            "runtime_id": runtime_id,
            "binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "status": "starting",
            "active": True,
            "last_heartbeat_at": None,
        }
    ]

    register_calls: List[str] = []
    orig_register = _persona_service_module._register_persona_cron_required

    def _spy_register(pid: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        register_calls.append(pid)
        return orig_register(pid, *args, **kwargs)

    monkeypatch.setattr(
        _persona_service_module, "_register_persona_cron_required", _spy_register
    )
    cron_runtime.calls.clear()
    mutation_port.updates.clear()

    # Pass 1: persona remains `provisioning` waiting for first heartbeat,
    # snapshot reads cron.list once and does NOT re-register the matching job.
    _persona_service_module._reconcile_persona_provisioning_once()
    assert [c[0] for c in cron_runtime.calls] == ["cron.list"]
    assert register_calls == []
    assert read_surface.get_persona(persona_id)["lifecycle_state"] == "provisioning"

    # Pass 2: fresh heartbeat arrives; persona transitions to `paper_running`
    # using the single cron.list snapshot without calling _register_persona_cron_required.
    fresh_hb = (
        (datetime.now(timezone.utc) - timedelta(seconds=3))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    read_surface.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
        {
            "session_id": f"session-{persona_id}",
            "runtime_id": runtime_id,
            "binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "status": "running",
            "active": True,
            "last_heartbeat_at": fresh_hb,
        }
    ]
    cron_runtime.calls.clear()
    _persona_service_module._reconcile_persona_provisioning_once()
    assert [c[0] for c in cron_runtime.calls] == ["cron.list"]
    assert register_calls == []
    settled = read_surface.get_persona(persona_id)
    assert settled["lifecycle_state"] == "paper_running"
    assert (
        settled["metadata"]["provisioning_authoritative_readback"][
            "first_evaluation_schedule"
        ]["job_id"]
        == "job-match-001"
    )
