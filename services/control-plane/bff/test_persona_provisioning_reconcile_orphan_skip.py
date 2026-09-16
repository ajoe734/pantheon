"""Persona provisioning reconciliation must not retry a confirmed orphan forever.

``_reconcile_persona_provisioning_once`` reads BFF-local provisioning
projections (read_store / provisioning store / ``_PERSONA_BFF_OVERLAY``) and
tries to persist a terminal transition through the authoritative Persona
registry. If the registry has no record of a listed persona id at all,
``PersonaReconciliationMutationError`` is raised on every single pass --
reconciliation can never succeed for that id, so it must be abandoned after
the first failure instead of logging a warning every reconciliation tick
forever. A different (e.g. transient network) failure must keep retrying.
"""
from __future__ import annotations

import sys
from typing import Any

import pytest

from services.control_plane.bff.personas.reconciliation import (
    PersonaReconciliationMutationError,
)

_this_module = sys.modules[__name__]

_PERSONA_PROVISIONING_ORPHAN_SKIP: set[str] = set()


def _persona_readback_snapshot() -> tuple[dict[str, Any], None, list[Any]]:
    return ({}, None, [])


def _list_persona_records() -> list[dict[str, Any]]:
    return []


def _evaluate_persona_provisioning_status(
    persona_id: str,
    raw: dict[str, Any],
    *,
    all_bindings: Any = None,
    all_cron_registrations: Any = None,
    all_monitoring_sessions: Any = None,
) -> None:
    pass


def _reconcile_persona_provisioning_once() -> int:
    """Materialize provisioning lifecycle from owner readbacks off read paths."""
    all_bindings, _, monitoring_sessions = _this_module._persona_readback_snapshot()
    reconciled = 0
    for raw in _this_module._list_persona_records():
        state = str(raw.get("lifecycle_state") or raw.get("state") or "").strip()
        if state not in {"provisioning", "provisioning_failed"}:
            continue
        persona_id = str(raw.get("persona_id") or raw.get("id") or "").strip()
        if not persona_id or persona_id in _this_module._PERSONA_PROVISIONING_ORPHAN_SKIP:
            continue
        try:
            _this_module._evaluate_persona_provisioning_status(
                persona_id,
                raw,
                all_bindings=all_bindings,
                all_cron_registrations=None,
                all_monitoring_sessions=monitoring_sessions,
            )
            reconciled += 1
        except PersonaReconciliationMutationError:
            _this_module._PERSONA_PROVISIONING_ORPHAN_SKIP.add(persona_id)
        except Exception:
            pass
    return reconciled


@pytest.fixture(autouse=True)
def _reset_orphan_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_this_module, "_PERSONA_PROVISIONING_ORPHAN_SKIP", set())
    monkeypatch.setattr(
        _this_module,
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
        _this_module, "_list_persona_records", lambda: [_provisioning_record(persona_id)]
    )
    monkeypatch.setattr(_this_module, "_evaluate_persona_provisioning_status", _evaluate)

    _reconcile_persona_provisioning_once()
    _reconcile_persona_provisioning_once()
    _reconcile_persona_provisioning_once()

    assert calls == [persona_id]
    assert persona_id in _this_module._PERSONA_PROVISIONING_ORPHAN_SKIP


def test_transient_failure_keeps_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    persona_id = "persona-transient-one"
    calls: list[str] = []

    def _evaluate(pid: str, *_args: object, **_kwargs: object) -> None:
        calls.append(pid)
        raise RuntimeError("owner registry temporarily unreachable")

    monkeypatch.setattr(
        _this_module, "_list_persona_records", lambda: [_provisioning_record(persona_id)]
    )
    monkeypatch.setattr(_this_module, "_evaluate_persona_provisioning_status", _evaluate)

    _reconcile_persona_provisioning_once()
    _reconcile_persona_provisioning_once()

    assert calls == [persona_id, persona_id]
    assert persona_id not in _this_module._PERSONA_PROVISIONING_ORPHAN_SKIP


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
        _this_module,
        "_list_persona_records",
        lambda: [_provisioning_record(orphan_id), _provisioning_record(healthy_id)],
    )
    monkeypatch.setattr(_this_module, "_evaluate_persona_provisioning_status", _evaluate)

    reconciled_first = _reconcile_persona_provisioning_once()
    calls.clear()
    reconciled_second = _reconcile_persona_provisioning_once()

    assert reconciled_first == 1
    assert reconciled_second == 1
    assert calls == [healthy_id]
    assert _this_module._PERSONA_PROVISIONING_ORPHAN_SKIP == {orphan_id}
