"""Fail-closed tests for Persona provisioning authoritative readback."""
from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import main as bff_main


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
        "runtime_binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
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
        "persona_capital_binding_id": PERSONA_CAPITAL_BINDING_ID,
        "status": "active",
        "metadata": {"persona_id": PERSONA_ID},
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
        "status": "running",
        "active": True,
        "last_heartbeat_at": _now(),
    }
    session.update(overrides)
    return session


@dataclass
class _FakeReadStore:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)

    def list_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        return deepcopy(self.sessions)

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
        return SimpleNamespace(
            references={},
            state="provisioning",
            current_step="authoritative_readback",
            error=None,
        )

    def release(self, record: Any, *, lease_owner: str) -> Any:
        assert self.acquired[-1][2] == lease_owner
        self.released.append(deepcopy(record))
        return record


class _RuntimeClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requested_binding_ids: list[str] = []

    def get(self, binding_id: str) -> dict[str, Any] | None:
        self.requested_binding_ids.append(binding_id)
        if self.error is not None:
            raise self.error
        return None


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
    monkeypatch.setenv("PANTHEON_PERSONA_HEARTBEAT_MAX_AGE_SECONDS", "90")
    monkeypatch.setenv("PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS", "600")
    return _Harness(read_store, provisioning_store, projection, runtime_client)


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
    assert harness.provisioning_store.released[-1].references == {
        "runtime_binding_id": RUNTIME_BINDING_ID,
        "runtime_id": RUNTIME_ID,
    }


def test_conflicting_optional_worker_persona_identity_fails_closed(
    harness: _Harness,
) -> None:
    harness.read_store.sessions = [_worker_session(persona_id="persona-other")]

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations={(PERSONA_ID, FIRST_EVALUATION_WORKFLOW_ID)},
    )

    assert state == "provisioning"
    assert harness.provisioning_store.released == []


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


@pytest.mark.parametrize("saga_state", ["failed", "compensated"])
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
    assert degraded_client.requested_binding_ids == [RUNTIME_BINDING_ID]
    assert harness.provisioning_store.released == []


def test_cron_degraded_does_not_raise_or_fake_success(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("persona_cron_registrar")

    class DegradedRegistrar:
        def _get_runtime(self) -> Any:
            raise RuntimeError("cron authority unavailable")

    module.PersonaCronRegistrar = DegradedRegistrar  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "persona_cron_registrar", module)

    state, _ = _evaluate(
        bindings={RUNTIME_BINDING_ID: _runtime_binding()},
        cron_registrations=None,
    )

    assert state == "provisioning"
    assert harness.provisioning_store.released == []
