"""Persona provisioning reconciliation must not retry a confirmed orphan forever
or churn lease writes on idle ticks with only succeeded and retired records.

``_reconcile_persona_provisioning_once`` reads BFF-local provisioning
projections (read_store / provisioning store / ``_PERSONA_BFF_OVERLAY``) and
tries to persist a terminal transition through the authoritative Persona
registry. If the registry has no record of a listed persona id at all,
``PersonaReconciliationMutationError`` is raised on every single pass --
reconciliation can never succeed for that id, so it must be abandoned after
the first failure instead of logging a warning every reconciliation tick
forever. A different (e.g. transient network) failure must keep retrying.

When all provisioning records are already ``succeeded`` or ``retired`` (or
confirmed orphans), steady-state ticks must perform 0 lease INSERT/UPDATE/DELETE
writes while still reconciling pending rows normally when actionable work exists.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    PostgresPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.reconciliation import (
    PersonaReconciliationMutationError,
)


@pytest.fixture(autouse=True)
def _reset_orphan_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_ORPHAN_SKIP", set())
    monkeypatch.setattr(
        personas_service,
        "_persona_readback_snapshot",
        lambda: ({}, None, []),
    )


def _provisioning_record(persona_id: str) -> dict[str, Any]:
    return {"persona_id": persona_id, "lifecycle_state": "provisioning"}


def test_confirmed_orphan_is_reconciled_once_then_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persona_id = "persona-orphan-one"
    calls: list[str] = []

    def _evaluate(pid: str, *_args: object, **_kwargs: object) -> None:
        calls.append(pid)
        raise PersonaReconciliationMutationError(
            f"Persona {pid!r} was not found by the mutation owner"
        )

    monkeypatch.setattr(
        personas_service, "_list_persona_records", lambda: [_provisioning_record(persona_id)]
    )
    monkeypatch.setattr(personas_service, "_evaluate_persona_provisioning_status", _evaluate)

    personas_service._reconcile_persona_provisioning_once()
    personas_service._reconcile_persona_provisioning_once()
    personas_service._reconcile_persona_provisioning_once()

    assert calls == [persona_id]
    assert persona_id in personas_service._PERSONA_PROVISIONING_ORPHAN_SKIP


def test_transient_failure_keeps_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    persona_id = "persona-transient-one"
    calls: list[str] = []

    def _evaluate(pid: str, *_args: object, **_kwargs: object) -> None:
        calls.append(pid)
        raise RuntimeError("owner registry temporarily unreachable")

    monkeypatch.setattr(
        personas_service, "_list_persona_records", lambda: [_provisioning_record(persona_id)]
    )
    monkeypatch.setattr(personas_service, "_evaluate_persona_provisioning_status", _evaluate)

    personas_service._reconcile_persona_provisioning_once()
    personas_service._reconcile_persona_provisioning_once()

    assert calls == [persona_id, persona_id]
    assert persona_id not in personas_service._PERSONA_PROVISIONING_ORPHAN_SKIP


def test_different_orphans_are_tracked_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan_id = "persona-orphan-two"
    healthy_id = "persona-healthy-two"
    calls: list[str] = []

    def _evaluate(pid: str, *_args: object, **_kwargs: object) -> None:
        calls.append(pid)
        if pid == orphan_id:
            raise PersonaReconciliationMutationError(
                f"Persona {pid!r} was not found by the mutation owner"
            )

    monkeypatch.setattr(
        personas_service,
        "_list_persona_records",
        lambda: [_provisioning_record(orphan_id), _provisioning_record(healthy_id)],
    )
    monkeypatch.setattr(personas_service, "_evaluate_persona_provisioning_status", _evaluate)

    reconciled_first = personas_service._reconcile_persona_provisioning_once()
    calls.clear()
    reconciled_second = personas_service._reconcile_persona_provisioning_once()

    assert reconciled_first == 1
    assert reconciled_second == 1
    assert calls == [healthy_id]
    assert personas_service._PERSONA_PROVISIONING_ORPHAN_SKIP == {orphan_id}


class _SqlTrackingConnect:
    """Records all SQL statements against PostgresPersonaProvisioningStore."""

    def __init__(self, rows_by_state: list[tuple[str, str]]) -> None:
        self.rows_by_state = list(rows_by_state)
        self.statements: list[tuple[str, Any]] = []

    def __call__(self, _dsn: str) -> "_SqlTrackingConnect":
        return self

    def __enter__(self) -> "_SqlTrackingConnect":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def cursor(self) -> "_SqlTrackingConnect":
        return self

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append((sql.strip(), params))

    def fetchall(self) -> list[tuple[str, str]]:
        if not self.statements:
            return []
        last_sql = self.statements[-1][0].upper()
        if "WHERE STATE NOT IN ('SUCCEEDED', 'RETIRED')" in last_sql:
            return [
                (pid, st)
                for pid, st in self.rows_by_state
                if st not in {"succeeded", "retired"}
            ]
        return []

    def fetchone(self) -> None:
        return None

    @property
    def write_statements(self) -> list[str]:
        writes: list[str] = []
        for sql, _ in self.statements:
            upper = sql.lstrip().upper()
            if upper.startswith(("INSERT", "UPDATE", "DELETE")):
                writes.append(sql)
        return writes


def test_postgres_steady_state_ticks_with_only_succeeded_and_retired_rows_perform_zero_lease_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        ("persona-live-1", "succeeded"),
        ("persona-live-2", "succeeded"),
        ("persona-live-3", "succeeded"),
        ("persona-live-4", "succeeded"),
        *[(f"persona-retired-{i}", "retired") for i in range(165)],
    ]
    tracker = _SqlTrackingConnect(rows)
    store = PostgresPersonaProvisioningStore(
        "postgresql://example/provisioning",
        connect=tracker,
    )
    # Clear bootstrap DDL statements before measuring steady-state reconcile ticks.
    tracker.statements.clear()

    snapshot_calls: list[int] = []
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", store)
    monkeypatch.setattr(
        personas_service,
        "_persona_readback_snapshot",
        lambda: (snapshot_calls.append(1) or ({}, None, [])),
    )

    for _ in range(5):
        assert personas_service._reconcile_persona_provisioning_once() == 0

    assert tracker.write_statements == []
    assert snapshot_calls == []
    assert len(tracker.statements) == 5
    assert all(
        "WHERE state NOT IN ('succeeded', 'retired')" in sql
        for sql, _ in tracker.statements
    )


def test_memory_steady_state_succeeded_and_retired_rows_perform_zero_lease_writes_while_pending_reconciles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryPersonaProvisioningStore()
    lease_writes: list[tuple[str, str]] = []

    orig_acquire = store.acquire
    orig_checkpoint = store.checkpoint
    orig_release = store.release

    def _recording_acquire(tenant_id: str, idempotency_key: str, **kwargs: Any) -> Any:
        lease_writes.append(("acquire", idempotency_key))
        return orig_acquire(tenant_id, idempotency_key, **kwargs)

    def _recording_checkpoint(record: ProvisioningRecord, **kwargs: Any) -> Any:
        lease_writes.append(("checkpoint", record.idempotency_key))
        return orig_checkpoint(record, **kwargs)

    def _recording_release(record: ProvisioningRecord, **kwargs: Any) -> Any:
        lease_writes.append(("release", record.idempotency_key))
        return orig_release(record, **kwargs)

    # Seed succeeded (both complete and legacy without authoritative_readback) and retired rows.
    for idx, state in enumerate(("succeeded", "succeeded", "retired", "retired"), start=1):
        key = f"idem-steady-{idx}"
        pid = f"persona-steady-{idx}"
        store.reserve(
            tenant_id="tenant-dev",
            idempotency_key=key,
            request_hash=f"hash-{idx}",
            normalized_name=f"steady {idx}",
            persona_id=pid,
            request_payload={"name": f"Steady {idx}", "requested_by": "op"},
        )
        rec = orig_acquire("tenant-dev", key, lease_owner="seed", lease_seconds=60)
        assert rec is not None
        rec.state = state
        if idx == 1:
            rec.references = {
                "runtime_binding_id": "rb-steady-1",
                "runtime_id": "rt-steady-1",
                "authoritative_readback": {"observed_at": "2026-11-25T00:00:00Z"},
            }
            rec.result = {"status": "paper_running", "paper_running": True}
        orig_release(rec, lease_owner="seed")

    class _EmptyReadStore:
        @staticmethod
        def list_personas(**_kwargs: Any) -> list[dict[str, Any]]:
            return []

        @staticmethod
        def get_persona(_pid: str) -> None:
            return None

    monkeypatch.setattr(store, "acquire", _recording_acquire)
    monkeypatch.setattr(store, "checkpoint", _recording_checkpoint)
    monkeypatch.setattr(store, "release", _recording_release)
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", store)
    monkeypatch.setattr(personas_service, "_get_active_read_store", lambda **_: _EmptyReadStore())
    monkeypatch.setattr(personas_service, "_get_active_write_owner", lambda **_: None)

    # Steady-state ticks with only succeeded and retired rows must do 0 lease writes.
    for _ in range(3):
        assert personas_service._reconcile_persona_provisioning_once() == 0
    assert lease_writes == []

    # Add a pending provisioning record and verify it reconciles normally.
    store.reserve(
        tenant_id="tenant-dev",
        idempotency_key="idem-pending-1",
        request_hash="hash-pending-1",
        normalized_name="pending 1",
        persona_id="persona-pending-1",
        request_payload={"name": "Pending 1", "requested_by": "op"},
    )
    evaluated: list[str] = []

    def _fake_evaluate(pid: str, raw: dict[str, Any], **_kwargs: Any) -> str:
        evaluated.append(pid)
        rec = orig_acquire("tenant-dev", "idem-pending-1", lease_owner="eval", lease_seconds=60)
        assert rec is not None
        rec.state = "succeeded"
        rec.references = {
            "runtime_binding_id": "rb-pending-1",
            "runtime_id": "rt-pending-1",
            "authoritative_readback": {"observed_at": "2026-11-25T00:01:00Z"},
        }
        rec.result = {"status": "paper_running", "paper_running": True}
        orig_release(rec, lease_owner="eval")
        raw.setdefault("metadata", {})["provisioning_reconciliation_state"] = "paper_running"
        return "paper_running"

    monkeypatch.setattr(
        personas_service, "_evaluate_persona_provisioning_status", _fake_evaluate
    )
    assert personas_service._reconcile_persona_provisioning_once() == 1
    assert evaluated == ["persona-pending-1"]
    assert {key for _, key in lease_writes} <= {"idem-pending-1"}

    # Once the pending row transitions to succeeded, subsequent ticks perform 0 work and 0 lease writes.
    evaluated.clear()
    lease_writes.clear()
    assert personas_service._reconcile_persona_provisioning_once() == 0
    assert evaluated == []
    assert lease_writes == []

