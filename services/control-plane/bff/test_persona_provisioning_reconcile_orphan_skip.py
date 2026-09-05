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

import pytest

from services.control_plane.bff import main as bff_main
from personas.reconciliation import PersonaReconciliationMutationError


@pytest.fixture(autouse=True)
def _reset_orphan_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_ORPHAN_SKIP", set())
    monkeypatch.setattr(
        bff_main,
        "_persona_readback_snapshot",
        lambda: ({}, None, []),
    )


def _provisioning_record(persona_id: str) -> dict:
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
        bff_main, "_list_persona_records", lambda: [_provisioning_record(persona_id)]
    )
    monkeypatch.setattr(bff_main, "_evaluate_persona_provisioning_status", _evaluate)

    bff_main._reconcile_persona_provisioning_once()
    bff_main._reconcile_persona_provisioning_once()
    bff_main._reconcile_persona_provisioning_once()

    assert calls == [persona_id]
    assert persona_id in bff_main._PERSONA_PROVISIONING_ORPHAN_SKIP


def test_transient_failure_keeps_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    persona_id = "persona-transient-one"
    calls: list[str] = []

    def _evaluate(pid: str, *_args: object, **_kwargs: object) -> None:
        calls.append(pid)
        raise RuntimeError("owner registry temporarily unreachable")

    monkeypatch.setattr(
        bff_main, "_list_persona_records", lambda: [_provisioning_record(persona_id)]
    )
    monkeypatch.setattr(bff_main, "_evaluate_persona_provisioning_status", _evaluate)

    bff_main._reconcile_persona_provisioning_once()
    bff_main._reconcile_persona_provisioning_once()

    assert calls == [persona_id, persona_id]
    assert persona_id not in bff_main._PERSONA_PROVISIONING_ORPHAN_SKIP


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
        bff_main,
        "_list_persona_records",
        lambda: [_provisioning_record(orphan_id), _provisioning_record(healthy_id)],
    )
    monkeypatch.setattr(bff_main, "_evaluate_persona_provisioning_status", _evaluate)

    reconciled_first = bff_main._reconcile_persona_provisioning_once()
    calls.clear()
    reconciled_second = bff_main._reconcile_persona_provisioning_once()

    assert reconciled_first == 1
    assert reconciled_second == 1
    assert calls == [healthy_id]
    assert bff_main._PERSONA_PROVISIONING_ORPHAN_SKIP == {orphan_id}
