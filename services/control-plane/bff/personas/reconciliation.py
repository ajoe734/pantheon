"""Persona provisioning reconciliation writes.

The provisioning reconciler reads through ``ReadSurfacePorts`` but its
reconciled projection must be persisted through the authoritative Persona
mutation owner.
Keeping this narrow command port separate makes that read/write boundary
explicit and prevents a compatibility mutation method from reappearing on the
read surface.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

try:
    from ports.persona_capital_runtime import PersonaMutationPort
except ImportError:
    from services.control_plane.bff.ports.persona_capital_runtime import (  # type: ignore[no-redef]
        PersonaMutationPort,
    )


_RECONCILABLE_PROVISIONING_STATES = frozenset({
    "provisioning",
    "paper_running",
    "provisioning_failed",
})


class PersonaReconciliationMutationError(RuntimeError):
    """A reconciled Persona projection could not be persisted by its owner."""


class PersonaProvisioningReconciliationMutationPort:
    """Typed provisioning-projection command port over the Persona write owner."""

    def __init__(self, *, persona_mutation_port: PersonaMutationPort) -> None:
        self._persona_mutation_port = persona_mutation_port

    def persist_terminal_transition(
        self,
        persona_id: str,
        *,
        lifecycle_state: str,
        metadata: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Persist one provisioning reconciliation decision through the write owner.

        The authoritative Persona lifecycle and the provisioning runtime
        projection are intentionally distinct.  The terminal projection is
        therefore recorded in owner metadata as well as passed to the typed
        owner command, so a future readback can recover it without mutating a
        BFF read surface.
        """
        clean_persona_id = str(persona_id or "").strip()
        if not clean_persona_id:
            raise PersonaReconciliationMutationError("Persona id is required")
        if lifecycle_state not in _RECONCILABLE_PROVISIONING_STATES:
            raise PersonaReconciliationMutationError(
                f"Unsupported provisioning reconciliation state: {lifecycle_state!r}"
            )

        persisted_metadata = dict(metadata)
        persisted_metadata["provisioning_reconciliation_state"] = lifecycle_state
        updated = self._persona_mutation_port.update_persona(
            clean_persona_id,
            lifecycle_state=lifecycle_state,
            metadata=persisted_metadata,
        )
        if updated is None:
            raise PersonaReconciliationMutationError(
                f"Persona {clean_persona_id!r} was not found by the mutation owner"
            )
        return dict(updated)


__all__ = [
    "PersonaProvisioningReconciliationMutationPort",
    "PersonaReconciliationMutationError",
]
