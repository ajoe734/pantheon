"""Regression coverage for Persona provisioning reconciliation writes."""
from __future__ import annotations

import inspect
import os
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main
from personas.reconciliation import PersonaProvisioningReconciliationMutationPort
from ports import create_in_memory_read_surface_ports


PERSONA_ID = "persona-reconciliation-port"


class _RecordingPersonaMutationPort:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update_persona(
        self,
        persona_id: str,
        *,
        lifecycle_state: str | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        update = {
            "persona_id": persona_id,
            "lifecycle_state": lifecycle_state,
            "metadata": deepcopy(metadata or {}),
        }
        self.updates.append(update)
        return update


def _terminal_checkpoint(lifecycle_state: str) -> dict[str, Any]:
    if lifecycle_state == "paper_running":
        return {
            "committed": True,
            "ledger_state": "succeeded",
            "references": {
                "runtime_binding_id": "runtime-binding-reconciliation-port",
                "runtime_id": "runtime-reconciliation-port",
                "authoritative_readback": {"owner": "runtime-manager"},
            },
            "result": {
                "status": "paper_running",
                "paper_running": True,
            },
        }
    return {
        "committed": True,
        "ledger_state": "failed",
        "references": {},
        "result": {
            "status": "provisioning_failed",
            "paper_running": False,
        },
        "failure_reason": "deployment-owner-rejected",
    }


@pytest.mark.parametrize("expected_state", ["paper_running", "provisioning_failed"])
def test_terminal_reconciliation_uses_authoritative_mutation_port_not_read_surface(
    monkeypatch: pytest.MonkeyPatch,
    expected_state: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    read_surface = create_in_memory_read_surface_ports()
    mutation_owner = _RecordingPersonaMutationPort()
    monkeypatch.setattr(bff_main, "read_store", read_surface)
    monkeypatch.setattr(bff_main, "_PERSONA_BFF_OVERLAY", {})
    monkeypatch.setattr(
        bff_main,
        "_PERSONA_PROVISIONING_STORE",
        SimpleNamespace(
            get=lambda *_args: SimpleNamespace(
                state="succeeded" if expected_state == "paper_running" else "failed",
                references={},
                error={"terminal_reason": "deployment-owner-rejected"},
            )
        ),
    )
    monkeypatch.setattr(
        bff_main,
        "persona_reconciliation_mutation_port",
        PersonaProvisioningReconciliationMutationPort(
            persona_mutation_port=mutation_owner,
        ),
    )
    monkeypatch.setattr(
        bff_main,
        "_checkpoint_persona_provisioning_readback",
        lambda **_kwargs: _terminal_checkpoint(expected_state),
    )
    monkeypatch.setattr(
        bff_main,
        "_reconcile_persona_provisioning_compensation",
        lambda _metadata: None,
    )

    raw = {
        "persona_id": PERSONA_ID,
        "lifecycle_state": "provisioning",
        "metadata": {
            "tenant_id": "tenant-reconciliation-port",
            "provisioning_idempotency_key": "reconciliation-port-001",
        },
    }

    state = bff_main._materialize_terminal_persona_provisioning_ledger(
        PERSONA_ID,
        raw,
    )

    assert state == expected_state
    assert raw["lifecycle_state"] == expected_state
    assert len(mutation_owner.updates) == 1
    persisted = mutation_owner.updates[0]
    assert persisted["persona_id"] == PERSONA_ID
    assert persisted["lifecycle_state"] == expected_state
    assert persisted["metadata"]["provisioning_reconciliation_state"] == expected_state
    assert not callable(getattr(read_surface, "update_persona", None))
    assert "ReadSurfacePorts object has no attribute 'update_persona'" not in caplog.text


def test_persona_reconciliation_code_has_no_read_surface_mutation_delegation() -> None:
    source = inspect.getsource(bff_main)
    reconciliation_source = (
        Path(__file__).resolve().parents[1] / "personas" / "reconciliation.py"
    ).read_text(encoding="utf-8")

    assert "read_store.update_persona" not in source
    assert "read_store.create_persona" not in source
    assert "read_store.update_persona" not in reconciliation_source
    assert "read_store.create_persona" not in reconciliation_source
