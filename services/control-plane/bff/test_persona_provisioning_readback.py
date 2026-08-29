"""Fail-closed tests for Persona provisioning authoritative readback."""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import main as bff_main
from ports import ReadSurfacePorts, create_read_surface_ports


PERSONA_ID = "persona-dynamic-alpha"
PERSONA_CAPITAL_BINDING_ID = "pcb-dynamic-alpha"
RUNTIME_BINDING_ID = "rb-authoritative-alpha"
RUNTIME_ID = "runtime-dynamic-alpha"
PLAN_ID = "plan-dynamic-alpha"
FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"


def _now(offset_seconds: int = 0) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raw_persona(**metadata_overrides: Any) -> dict[str, Any]:
    metadata = {
        "tenant_id": "tenant-alpha",
        "provisioning_idempotency_key": "persona-create-alpha",
        "persona_capital_binding_id": PERSONA_CAPITAL_BINDING_ID,
        "binding_id": PERSONA_CAPITAL_BINDING_ID,
        "deployment_plan_id": PLAN_ID,
        "deployment_saga_id": "saga-dynamic-alpha",
        "internal_paper_capital_pool_id": "pool-dynamic-alpha",
        "runtime_binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
        "provisioning_readback_started_at": _now(),
    }
    metadata.update(metadata_overrides)
    return {
        "persona_id": PERSONA_ID,
        "name": "Dynamic Alpha",
        "lifecycle_state": "provisioning",
        "created_at": _now(),
        "metadata": metadata,
    }


def _runtime_binding(**overrides: Any) -> dict[str, Any]:
    binding = {
        "binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
        "plan_id": PLAN_ID,
        "capital_pool_id": "pool-dynamic-alpha",
        "persona_capital_binding_id": PERSONA_CAPITAL_BINDING_ID,
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"persona_id": PERSONA_ID, "tenant_id": "tenant-alpha"},
    }
    binding.update(overrides)
    return binding


def _deployment_projection(
    binding: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    authoritative = deepcopy(binding if binding is not None else _runtime_binding())
    projection = {
        "plan_id": PLAN_ID,
        "deployment_saga_id": "saga-dynamic-alpha",
        "deployment_saga_status": "completed",
        "deployment_saga_progress": {"progress_status": "completed"},
        "runtime_binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
        "runtime_binding": authoritative,
    }
    projection.update(overrides)
    return projection


def _worker_session(**overrides: Any) -> dict[str, Any]:
    session = {
        "session_id": "paper-worker-alpha-1",
        "runtime_id": RUNTIME_ID,
        "binding_id": RUNTIME_BINDING_ID,
        "capital_pool_id": "pool-dynamic-alpha",
        "status": "running",
        "active": True,
        "last_heartbeat_at": _now(),
    }
    session.update(overrides)
    return session


def _schedule_readback(
    persona_id: str = PERSONA_ID,
    *,
    runtime_id: str = RUNTIME_ID,
    runtime_binding_id: str = RUNTIME_BINDING_ID,
    capital_pool_id: str = "pool-dynamic-alpha",
    persona_capital_binding_id: str = PERSONA_CAPITAL_BINDING_ID,
) -> dict[str, Any]:
    return {
        "persona_id": persona_id,
        "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "capital_pool_id": capital_pool_id,
        "persona_capital_binding_id": persona_capital_binding_id,
        "registered": True,
        "job_id": "job-persona-first-evaluation-alpha",
        "job_name": "pantheon-pantheon-persona-first-evaluation-persona-dynamic-alpha",
        "request_id": (
            f"persona-provisioning:{persona_id}:{FIRST_EVALUATION_WORKFLOW_ID}"
        ),
        "schedule": {"kind": "cron", "expr": "*/15 * * * *"},
        "session_target": persona_id,
        "observed_at": _now(),
    }


def _first_evaluation_job_fixture(
    *,
    persona_id: str = PERSONA_ID,
    runtime_id: str = RUNTIME_ID,
    runtime_binding_id: str = RUNTIME_BINDING_ID,
    capital_pool_id: str = "pool-dynamic-alpha",
    persona_capital_binding_id: str = PERSONA_CAPITAL_BINDING_ID,
) -> dict[str, Any]:
    event = {
        "kind": "pantheon.workflow.dispatch",
        "persona_id": persona_id,
        "policy_id": "oc002.cron.persona-first-evaluation",
        "request_id": f"persona-provisioning:{persona_id}:{FIRST_EVALUATION_WORKFLOW_ID}",
        "upstream_entrypoint": "evaluation.persona.first",
        "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "capital_pool_id": capital_pool_id,
        "persona_capital_binding_id": persona_capital_binding_id,
    }
    return {
        "id": "job-first-evaluation-delayed",
        "name": "pantheon-pantheon-persona-first-evaluation-persona-dynamic-alpha",
        "enabled": True,
        "deleteAfterRun": False,
        "schedule": {"kind": "cron", "expr": "*/15 * * * *"},
        "sessionTarget": "main",
        "wakeMode": "next-heartbeat",
        "payload": {"kind": "systemEvent", "text": json.dumps(event)},
    }


@dataclass
class _FakeReadStore:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        return deepcopy(self.sessions)

    def list_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        raise AssertionError("local/snapshot monitoring sessions are not lifecycle evidence")

    @staticmethod
    def _paper_runtime_monitoring_session_active(session: dict[str, Any]) -> bool:
        if session.get("ended_at") not in (None, ""):
            return False
        status = str(session.get("status") or "").strip().lower()
        if status in {"failed", "ended", "error", "stale"}:
            return False
        staleness = session.get("staleness")
        if isinstance(staleness, dict) and (
            str(staleness.get("status") or "").lower() == "stale"
            or staleness.get("reason")
        ):
            return False
        return bool(session.get("active", True))

    def update_persona(
        self,
        persona_id: str,
        *,
        lifecycle_state: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        update = {
            "persona_id": persona_id,
            "lifecycle_state": lifecycle_state,
            "metadata": deepcopy(metadata),
        }
        self.updates.append(update)
        return update


@dataclass
class _FakeProvisioningStore:
    acquired: list[tuple[str, str, str]] = field(default_factory=list)
    released: list[Any] = field(default_factory=list)
    busy: bool = False
    state: str = "provisioning"
    current_step: str = "authoritative_readback"
    references: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def _record(self) -> Any:
        return SimpleNamespace(
            references=deepcopy(self.references),
            state=self.state,
            current_step=self.current_step,
            result=deepcopy(self.result),
            error=deepcopy(self.error),
        )

    def get(self, tenant_id: str, idempotency_key: str) -> Any:
        assert tenant_id == "tenant-alpha"
        assert idempotency_key == "persona-create-alpha"
        return self._record()

    def acquire(
        self,
        tenant_id: str,
        idempotency_key: str,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> Any:
        assert lease_seconds > 0
        self.acquired.append((tenant_id, idempotency_key, lease_owner))
        if self.busy:
            return None
        return self._record()

    def release(self, record: Any, *, lease_owner: str) -> Any:
        assert self.acquired[-1][2] == lease_owner
        self.released.append(deepcopy(record))
        self.state = record.state
        self.current_step = record.current_step
        self.references = deepcopy(record.references)
        self.result = deepcopy(record.result)
        self.error = deepcopy(record.error)
        return record


class _RuntimeClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requested_binding_ids: list[str] = []
        self.requested_plan_ids: list[str] = []

    def get(self, binding_id: str) -> dict[str, Any] | None:
        self.requested_binding_ids.append(binding_id)
        if self.error is not None:
            raise self.error
        return None

    def list_by_plan(self, plan_id: str) -> list[dict[str, Any]]:
        self.requested_plan_ids.append(plan_id)
        if self.error is not None:
            raise self.error
        return []


@dataclass
class _Harness:
    read_store: _FakeReadStore
    provisioning_store: _FakeProvisioningStore
    projection: dict[str, Any]
    runtime_client: _RuntimeClient


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    read_store = _FakeReadStore(sessions=[_worker_session()])
    provisioning_store = _FakeProvisioningStore()
    projection = _deployment_projection()
    runtime_client = _RuntimeClient()

    monkeypatch.setattr(bff_main, "read_store", read_store)
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", provisioning_store)
    monkeypatch.setattr(bff_main, "_PERSONA_BFF_OVERLAY", {})
    monkeypatch.setattr(bff_main, "_get_json", lambda *_args, **_kwargs: deepcopy(projection))
    monkeypatch.setattr(bff_main, "_runtime_manager_client", lambda: runtime_client)
    monkeypatch.setattr(
        bff_main,
        "_register_persona_cron_required",
        lambda persona_id, capital_pool_id, persona_capital_binding_id, **kwargs: {
            "authoritative_readback": _schedule_readback(
                persona_id,
                runtime_id=str(kwargs.get("runtime_id") or ""),
                runtime_binding_id=str(kwargs.get("runtime_binding_id") or ""),
                capital_pool_id=capital_pool_id,
                persona_capital_binding_id=persona_capital_binding_id,
            )
        },
    )
    monkeypatch.setattr(
        bff_main,
        "_remove_persona_cron_required",
        lambda persona_id: {"persona_id": persona_id, "registered": False, "removed_ids": []},
    )
    monkeypatch.setattr(
        bff_main,
        "_reconcile_persona_provisioning_compensation",
        lambda _metadata: {"status": "completed"},
    )
    monkeypatch.setenv("PANTHEON_PERSONA_HEARTBEAT_MAX_AGE_SECONDS", "90")
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS", "600")
    return _Harness(read_store, provisioning_store, projection, runtime_client)


def test_required_cron_registration_polls_until_authoritative_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = object()
    attempts: list[dict[str, Any]] = []
    sleeps: list[float] = []

    class _RegistrationResult:
        def to_dict(self) -> dict[str, Any]:
            return {"mode": "gateway_rpc", "failed": [], "registered": []}

    class _DelayedReadbackRegistrar:
        def register_for_persona(
            self,
            persona_id: str,
            capital_pool_id: str | None = None,
            binding_id: str | None = None,
            *,
            workflow_ids: list[str] | None = None,
            runtime_id: str | None = None,
            runtime_binding_id: str | None = None,
            persona_capital_binding_id: str | None = None,
        ) -> _RegistrationResult:
            assert persona_id == PERSONA_ID
            assert capital_pool_id == "pool-dynamic-alpha"
            assert binding_id is None
            assert workflow_ids == [FIRST_EVALUATION_WORKFLOW_ID]
            assert runtime_id == RUNTIME_ID
            assert runtime_binding_id == RUNTIME_BINDING_ID
            assert persona_capital_binding_id == PERSONA_CAPITAL_BINDING_ID
            return _RegistrationResult()

        def _get_runtime(self) -> object:
            return runtime

        def get_first_evaluation_registration(
            self,
            persona_id: str,
            *,
            runtime: object | None = None,
            runtime_id: str | None = None,
            runtime_binding_id: str | None = None,
            capital_pool_id: str | None = None,
            persona_capital_binding_id: str | None = None,
        ) -> dict[str, Any] | None:
            attempts.append(
                {
                    "persona_id": persona_id,
                    "runtime": runtime,
                    "runtime_id": runtime_id,
                    "runtime_binding_id": runtime_binding_id,
                    "capital_pool_id": capital_pool_id,
                    "persona_capital_binding_id": persona_capital_binding_id,
                }
            )
            if len(attempts) < 3:
                return None
            return _first_evaluation_job_fixture()

        @staticmethod
        def _decode_job_event(job: dict[str, Any]) -> dict[str, Any] | None:
            return json.loads(str(job.get("payload", {}).get("text") or "{}"))

    monkeypatch.setitem(
        sys.modules,
        "persona_cron_registrar",
        SimpleNamespace(PersonaCronRegistrar=_DelayedReadbackRegistrar),
    )
    monkeypatch.setenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_TIMEOUT_SECONDS",
        "5",
    )
    monkeypatch.setenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_POLL_SECONDS",
        "0.1",
    )
    monkeypatch.setattr(bff_main.time, "sleep", lambda seconds: sleeps.append(seconds))

    receipt = bff_main._register_persona_cron_required(
        PERSONA_ID,
        "pool-dynamic-alpha",
        PERSONA_CAPITAL_BINDING_ID,
        runtime_id=RUNTIME_ID,
        runtime_binding_id=RUNTIME_BINDING_ID,
    )

    assert len(attempts) == 3
    assert len(sleeps) == 2
    assert all(call["runtime"] is runtime for call in attempts)
    assert receipt["authoritative_readback"]["registered"] is True
    assert receipt["authoritative_readback"]["job_id"] == "job-first-evaluation-delayed"
    assert receipt["authoritative_readback"]["readback_attempts"] == 3


def test_required_cron_registration_remains_fail_closed_after_bounded_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RegistrationResult:
        def to_dict(self) -> dict[str, Any]:
            return {"mode": "gateway_rpc", "failed": [], "registered": []}

    class _MissingReadbackRegistrar:
        def register_for_persona(self, *_args: Any, **_kwargs: Any) -> _RegistrationResult:
            return _RegistrationResult()

        def _get_runtime(self) -> object:
            return object()

        def get_first_evaluation_registration(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "persona_cron_registrar",
        SimpleNamespace(PersonaCronRegistrar=_MissingReadbackRegistrar),
    )
    monkeypatch.setenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_TIMEOUT_SECONDS",
        "0",
    )

    with pytest.raises(RuntimeError, match="after 1 attempts"):
        bff_main._register_persona_cron_required(
            PERSONA_ID,
            "pool-dynamic-alpha",
            PERSONA_CAPITAL_BINDING_ID,
            runtime_id=RUNTIME_ID,
            runtime_binding_id=RUNTIME_BINDING_ID,
        )


def _evaluate(
    *,
    raw: dict[str, Any] | None = None,
    bindings: dict[str, dict[str, Any]] | None = None,
    cron_registrations: set[tuple[str, str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    persona = raw if raw is not None else _raw_persona()
    state = bff_main._evaluate_persona_provisioning_status(
        PERSONA_ID,
        persona,
        all_bindings=bindings,
        all_cron_registrations=cron_registrations,
    )
    return state, persona


def test_exact_authoritative_identity_fresh_single_worker_and_first_eval_succeeds(
    harness: _Harness,
) -> None:
    binding = _runtime_binding()
    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: binding},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "paper_running"
    assert RUNTIME_BINDING_ID.startswith("rb-")
    assert RUNTIME_BINDING_ID != PERSONA_CAPITAL_BINDING_ID
    assert raw["metadata"]["runtime_binding_id"] == RUNTIME_BINDING_ID
    assert raw["metadata"]["runtime_id"] == RUNTIME_ID
    assert harness.read_store.updates[-1]["lifecycle_state"] == "paper_running"
    assert harness.provisioning_store.released[-1].state == "succeeded"
    references = harness.provisioning_store.released[-1].references
    assert references["runtime_binding_id"] == RUNTIME_BINDING_ID
    assert references["runtime_id"] == RUNTIME_ID
    proof = references["authoritative_readback"]
    assert proof["runtime_binding"] == binding
    assert proof["paper_worker"]["session_id"] == "paper-worker-alpha-1"
    assert proof["first_evaluation_schedule"]["job_id"]
    assert proof["first_evaluation_schedule"]["request_id"] == (
        f"persona-provisioning:{PERSONA_ID}:{FIRST_EVALUATION_WORKFLOW_ID}"
    )
    assert harness.provisioning_store.released[-1].result["status"] == "paper_running"
    assert harness.provisioning_store.released[-1].result["paper_running"] is True


def test_conflicting_optional_worker_persona_identity_fails_closed(
    harness: _Harness,
) -> None:
    harness.read_store.sessions = [_worker_session(persona_id="persona-other")]

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert harness.provisioning_store.released[-1].state == "failed"


@pytest.mark.parametrize(
    ("binding_update", "expected_reason"),
    [
        ({"plan_id": "plan-other"}, "runtime_binding_failed_or_mismatched"),
        ({"plan_id": None}, "runtime_binding_failed_or_mismatched"),
        (
            {"metadata": {"persona_id": "persona-other"}},
            "runtime_binding_failed_or_mismatched",
        ),
        ({"metadata": {}}, "runtime_binding_failed_or_mismatched"),
    ],
    ids=["wrong-plan", "missing-plan", "wrong-persona", "missing-persona"],
)
def test_runtime_binding_requires_exact_plan_and_persona_identity(
    harness: _Harness,
    binding_update: dict[str, Any],
    expected_reason: str,
) -> None:
    binding = _runtime_binding(**binding_update)
    harness.projection.clear()
    harness.projection.update(_deployment_projection(binding))

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: binding},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert expected_reason in raw["metadata"]["provisioning_failure_reason"]


def test_arbitrary_cron_registration_does_not_complete_provisioning(
    harness: _Harness,
) -> None:
    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, "pantheon.review")},
    )

    assert state == "provisioning"
    assert harness.provisioning_store.released == []


@pytest.mark.parametrize(
    "session_update",
    [
        {"status": "failed", "active": True},
        {"status": "running", "active": False},
    ],
    ids=["failed", "inactive"],
)
def test_terminal_or_inactive_worker_fails_even_with_fresh_heartbeat(
    harness: _Harness,
    session_update: dict[str, Any],
) -> None:
    harness.read_store.sessions = [_worker_session(**session_update)]

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "paper_worker_failed_stale_or_duplicated" in raw["metadata"][
        "provisioning_failure_reason"
    ]


def test_stale_worker_heartbeat_fails_closed(harness: _Harness) -> None:
    harness.read_store.sessions = [_worker_session(last_heartbeat_at=_now(-120))]

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "paper_worker_failed_stale_or_duplicated" in raw["metadata"][
        "provisioning_failure_reason"
    ]


def test_duplicate_live_workers_fail_closed(harness: _Harness) -> None:
    harness.read_store.sessions = [
        _worker_session(session_id="paper-worker-alpha-1"),
        _worker_session(session_id="paper-worker-alpha-2"),
    ]

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "paper_worker_failed_stale_or_duplicated" in raw["metadata"][
        "provisioning_failure_reason"
    ]


def test_one_fresh_replacement_ignores_historical_ended_worker(harness: _Harness) -> None:
    harness.read_store.sessions = [
        _worker_session(session_id="paper-worker-alpha-current"),
        _worker_session(
            session_id="paper-worker-alpha-old",
            status="ended",
            active=False,
            ended_at=_now(-30),
            last_heartbeat_at=_now(-120),
        ),
    ]

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "paper_running"


def test_projection_requires_exact_durable_saga_identity(harness: _Harness) -> None:
    harness.projection["deployment_saga_id"] = "saga-other"

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "deployment_projection_identity_mismatched" in raw["metadata"][
        "provisioning_failure_reason"
    ]


def test_timeout_starts_at_durable_readback_checkpoint_not_persona_creation(
    harness: _Harness,
) -> None:
    old_persona = _raw_persona()
    old_persona["created_at"] = _now(-3600)
    old_persona["metadata"]["provisioning_readback_started_at"] = _now()

    state, _ = _evaluate(
        raw=old_persona,
        bindings={},
        cron_registrations=set(),
    )

    assert state == "provisioning"

    old_persona["metadata"]["provisioning_readback_started_at"] = _now(-601)
    state, raw = _evaluate(
        raw=old_persona,
        bindings={},
        cron_registrations=set(),
    )
    assert state == "provisioning_failed"
    assert "provisioning_timeout" in raw["metadata"]["provisioning_failure_reason"]


@pytest.mark.parametrize("saga_state", ["failed", "compensating", "compensated"])
def test_terminal_saga_precedes_other_success_signals(
    harness: _Harness,
    saga_state: str,
) -> None:
    harness.projection["deployment_saga_status"] = saga_state

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "deployment_saga_failed" in raw["metadata"]["provisioning_failure_reason"]
    assert harness.provisioning_store.released[-1].state == "failed"


@pytest.mark.parametrize(
    ("saga_status", "progress_status"),
    [
        ("accepted", "pending"),
        ("completed", "pending"),
        ("running", "completed"),
    ],
)
def test_incomplete_deployment_projection_remains_pending(
    harness: _Harness,
    saga_status: str,
    progress_status: str,
) -> None:
    harness.projection["deployment_saga_status"] = saga_status
    harness.projection["deployment_saga_progress"] = {
        "progress_status": progress_status
    }

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert harness.provisioning_store.released == []


def test_compensating_progress_is_terminal_even_before_saga_status_changes(
    harness: _Harness,
) -> None:
    harness.projection["deployment_saga_status"] = "running"
    harness.projection["deployment_saga_progress"] = {
        "progress_status": "compensating"
    }

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "deployment_saga_failed" in raw["metadata"]["provisioning_failure_reason"]


def test_projection_without_saga_identity_is_incomplete_not_mismatched(
    harness: _Harness,
) -> None:
    harness.projection.pop("deployment_saga_id")

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert raw["lifecycle_state"] == "provisioning"
    assert harness.provisioning_store.released == []


def test_deployment_degraded_cannot_reuse_local_ids_as_success(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("deployment unavailable")

    monkeypatch.setattr(bff_main, "_get_json", unavailable)

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert harness.provisioning_store.released == []


def test_runtime_manager_degraded_does_not_raise_or_fake_success(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.projection.pop("runtime_binding", None)
    degraded_client = _RuntimeClient(error=RuntimeError("runtime manager unavailable"))
    monkeypatch.setattr(bff_main, "_runtime_manager_client", lambda: degraded_client)

    state, _ = _evaluate(
        bindings=None,
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert degraded_client.requested_plan_ids == [PLAN_ID]
    assert harness.provisioning_store.released == []


def test_cron_degraded_does_not_raise_or_fake_success(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bff_main,
        "_register_persona_cron_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cron authority unavailable")
        ),
    )

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations=None,
    )

    assert state == "provisioning"
    assert harness.provisioning_store.released == []


def test_runtime_identity_is_forwarded_to_exact_schedule_reconciliation(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def reconcile(
        persona_id: str,
        capital_pool_id: str,
        persona_capital_binding_id: str,
        *,
        runtime_id: str | None = None,
        runtime_binding_id: str | None = None,
    ) -> dict[str, Any]:
        calls.append(
            {
                "persona_id": persona_id,
                "capital_pool_id": capital_pool_id,
                "persona_capital_binding_id": persona_capital_binding_id,
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
            }
        )
        return {
            "authoritative_readback": {
                **_schedule_readback(
                    persona_id,
                    runtime_id=str(runtime_id or ""),
                    runtime_binding_id=str(runtime_binding_id or ""),
                    capital_pool_id=capital_pool_id,
                    persona_capital_binding_id=persona_capital_binding_id,
                ),
                **calls[-1],
            }
        }

    monkeypatch.setattr(bff_main, "_register_persona_cron_required", reconcile)

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations=None,
    )

    assert state == "paper_running"
    assert calls == [
        {
            "persona_id": PERSONA_ID,
            "capital_pool_id": "pool-dynamic-alpha",
            "persona_capital_binding_id": PERSONA_CAPITAL_BINDING_ID,
            "runtime_id": RUNTIME_ID,
            "runtime_binding_id": RUNTIME_BINDING_ID,
        }
    ]


def test_embedded_projection_binding_is_not_runtime_manager_authority(
    harness: _Harness,
) -> None:
    state, _ = _evaluate(
        bindings={},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert harness.read_store.updates == []


def test_multiple_active_bindings_for_plan_fail_closed(harness: _Harness) -> None:
    duplicate = _runtime_binding(
        binding_id="rb-authoritative-alpha-duplicate",
        runtime_id="runtime-dynamic-alpha-duplicate",
    )
    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding(), duplicate["binding_id"]: duplicate},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "runtime_binding_failed_or_mismatched" in raw["metadata"][
        "provisioning_failure_reason"
    ]


@pytest.mark.parametrize(
    "binding_update",
    [
        {"capital_pool_id": "pool-other"},
        {"metadata": {"persona_id": PERSONA_ID, "tenant_id": "tenant-other"}},
        {"deployment_mode": "live"},
    ],
    ids=["wrong-pool", "wrong-tenant", "wrong-mode"],
)
def test_runtime_binding_requires_exact_scope_identity(
    harness: _Harness,
    binding_update: dict[str, Any],
) -> None:
    binding = _runtime_binding(**binding_update)
    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: binding},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "runtime_binding_failed_or_mismatched" in raw["metadata"][
        "provisioning_failure_reason"
    ]


@pytest.mark.parametrize(
    "session_update",
    [
        {"status": ""},
        {"session_id": "", "id": ""},
        {"capital_pool_id": "pool-other"},
    ],
    ids=["missing-status", "missing-session-id", "wrong-pool"],
)
def test_worker_requires_exact_running_owner_record(
    harness: _Harness,
    session_update: dict[str, Any],
) -> None:
    harness.read_store.sessions = [_worker_session(**session_update)]
    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "paper_worker_failed_stale_or_duplicated" in raw["metadata"][
        "provisioning_failure_reason"
    ]


@pytest.mark.parametrize(
    "status",
    ["accepted", "initializing", "pending", "queued", "starting"],
)
def test_single_starting_worker_without_heartbeat_remains_pending(
    harness: _Harness,
    status: str,
) -> None:
    harness.read_store.sessions = [
        _worker_session(status=status, last_heartbeat_at=None)
    ]

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert raw["lifecycle_state"] == "provisioning"
    assert harness.provisioning_store.released == []


def test_worker_staleness_marker_overrides_fresh_heartbeat(
    harness: _Harness,
) -> None:
    harness.read_store.sessions = [
        _worker_session(staleness={"status": "stale", "reason": "owner timeout"})
    ]

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert "paper_worker_failed_stale_or_duplicated" in raw["metadata"][
        "provisioning_failure_reason"
    ]


def test_running_worker_before_first_heartbeat_remains_pending(
    harness: _Harness,
) -> None:
    harness.read_store.sessions = [_worker_session(last_heartbeat_at=None)]

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert raw["lifecycle_state"] == "provisioning"
    assert harness.provisioning_store.released == []


def test_terminal_state_waits_for_durable_ledger_lease(harness: _Harness) -> None:
    harness.provisioning_store.busy = True
    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert raw["lifecycle_state"] == "provisioning"
    assert harness.read_store.updates == []

    harness.provisioning_store.busy = False
    state, raw = _evaluate(
        raw=raw,
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )
    assert state == "paper_running"
    assert raw["lifecycle_state"] == "paper_running"


@pytest.mark.parametrize(
    "ledger_state",
    [
        "failed",
        "compensated",
    ],
)
def test_success_readback_cannot_reverse_failed_terminal_ledger(
    harness: _Harness,
    ledger_state: str,
) -> None:
    harness.provisioning_store.state = ledger_state

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert raw["lifecycle_state"] == "provisioning_failed"
    assert harness.provisioning_store.released[-1].state == ledger_state
    assert harness.provisioning_store.released[-1].references[
        "first_evaluation_schedule_cleanup"
    ]["registered"] is False


def test_failure_readback_preserves_compensated_terminal_ledger(
    harness: _Harness,
) -> None:
    harness.provisioning_store.state = "compensated"
    harness.projection["deployment_saga_status"] = "failed"

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning_failed"
    assert harness.provisioning_store.released[-1].state == "compensated"
    assert harness.provisioning_store.released[-1].references[
        "first_evaluation_schedule_cleanup"
    ]["registered"] is False


def test_succeeded_terminal_ledger_replays_paper_running_after_crash(
    harness: _Harness,
) -> None:
    durable_binding_id = "rb-durable-before-crash"
    durable_runtime_id = "runtime-durable-before-crash"
    durable_binding = _runtime_binding(
        binding_id=durable_binding_id,
        runtime_id=durable_runtime_id,
    )
    durable_worker = _worker_session(
        binding_id=durable_binding_id,
        runtime_id=durable_runtime_id,
    )
    durable_readback = {
        "observed_at": _now(-5),
        "deployment": {
            "plan_id": PLAN_ID,
            "saga_id": "saga-dynamic-alpha",
            "saga_status": "completed",
            "progress_status": "completed",
        },
        "runtime_binding": durable_binding,
        "paper_worker": durable_worker,
        "first_evaluation_schedule": _schedule_readback(
            runtime_id=durable_runtime_id,
            runtime_binding_id=durable_binding_id,
        ),
    }
    harness.provisioning_store.state = "succeeded"
    harness.provisioning_store.references = {
        "runtime_binding_id": durable_binding_id,
        "runtime_id": durable_runtime_id,
        "authoritative_readback": durable_readback,
    }
    harness.provisioning_store.result = {
        "status": "paper_running",
        "paper_running": True,
        "authoritative_readback": durable_readback,
        "recorded_at": _now(-5),
    }
    harness.projection["deployment_saga_status"] = "failed"

    state, raw = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "paper_running"
    assert raw["lifecycle_state"] == "paper_running"
    assert raw["metadata"]["runtime_binding_id"] == durable_binding_id
    assert raw["metadata"]["runtime_id"] == durable_runtime_id
    assert raw["metadata"]["provisioning_authoritative_readback"] == durable_readback
    assert harness.provisioning_store.released[-1].state == "succeeded"


def test_terminal_failure_ledger_materializes_before_owner_readback(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.provisioning_store.state = "failed"
    harness.provisioning_store.error = {
        "terminal_reason": "dispatch_owner_failed"
    }
    monkeypatch.setattr(
        bff_main,
        "_get_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal replay must precede Deployment readback")
        ),
    )

    state, raw = _evaluate(bindings={}, cron_registrations=set())

    assert state == "provisioning_failed"
    assert raw["metadata"]["provisioning_failure_reason"] == "dispatch_owner_failed"


def test_cleanup_outage_does_not_erase_new_terminal_failure(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.projection["deployment_saga_status"] = "failed"
    monkeypatch.setattr(
        bff_main,
        "_remove_persona_cron_required",
        lambda _persona_id: (_ for _ in ()).throw(RuntimeError("cron unavailable")),
    )
    diagnostics: list[str] = []
    raw = _raw_persona()

    state = bff_main._evaluate_persona_provisioning_status(
        PERSONA_ID,
        raw,
        all_bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        all_cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
        diagnostics=diagnostics,
    )

    assert state == "provisioning_failed"
    assert harness.provisioning_store.state == "failed"
    assert harness.provisioning_store.result["status"] == "provisioning_failed"
    assert harness.provisioning_store.result["paper_running"] is False
    assert raw["metadata"]["first_evaluation_schedule_cleanup"] == {
        "status": "pending",
        "registered": None,
        "terminal_reason": "cron unavailable",
    }


def test_stable_terminal_failure_reconciliation_does_not_write_churn(
    harness: _Harness,
) -> None:
    raw = _raw_persona(
        first_evaluation_schedule_cleanup={
            "persona_id": PERSONA_ID,
            "registered": False,
            "removed_ids": [],
        },
        provisioning_compensation={"status": "completed"},
    )
    raw["lifecycle_state"] = "provisioning_failed"

    state, _ = _evaluate(raw=raw)

    assert state == "provisioning_failed"
    assert harness.read_store.updates == []


def test_authoritative_worker_read_never_enables_snapshot_fallback(tmp_path) -> None:
    store = create_read_surface_ports()
    calls: list[bool] = []

    class Canonical:
        def list_records(
            self,
            dataset: str,
            *,
            include_snapshot_fallback: bool = True,
        ) -> tuple[bool, list[dict[str, Any]]]:
            assert dataset == "paper_runtime_monitoring_sessions"
            calls.append(include_snapshot_fallback)
            return False, [{"session_id": "snapshot-must-not-pass"}]

    store._canonical = Canonical()  # type: ignore[attr-defined]

    assert store.list_authoritative_paper_runtime_monitoring_sessions() == []
    assert calls == [False]
