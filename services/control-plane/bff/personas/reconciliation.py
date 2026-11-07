"""Persona provisioning reconciliation writes.

The provisioning reconciler reads through ``ReadSurfacePorts`` but its
reconciled projection must be persisted through the authoritative Persona
mutation owner (via ``build_provisioning_router`` / ``PersonaProvisioningReconciliationMutationPort``).
Keeping this narrow command port separate makes that read/write boundary
explicit and prevents a compatibility mutation method from reappearing on the
read surface.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from services.control_plane.bff.ports.persona_capital_runtime import PersonaMutationPort


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


PAPER_ADVISORY_CAPABILITIES = ("persona_opinion",)
PAPER_ADVISORY_ENVIRONMENT_CEILING = "paper"
PAPER_ADVISORY_ACTOR = "persona_paper_advisory_reconcile"
_PAPER_ADVISORY_LIFECYCLES = frozenset({"paper_running", "paper_only", "paper_owner"})
_PAPER_ADVISORY_EXCLUDED_STATUSES = frozenset({"suspended", "archived", "retired", "frozen"})


class PersonaAdvisoryAdmissionError(RuntimeError):
    """The Persona is not eligible for, or the owner refused, advisory admission."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def paper_advisory_snapshot_id(persona_id: str) -> str:
    return f"cap-advisory-{persona_id}"


def paper_advisory_ineligibility(raw: Mapping[str, Any]) -> str:
    """Return a reason the Persona may not be admitted, or '' when eligible."""

    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    lifecycle = str(raw.get("lifecycle_state") or "").strip().lower()
    status = str(raw.get("status") or "").strip().lower()
    if lifecycle in {"retired", "suspended", "archived", "frozen", "draft"} or status in _PAPER_ADVISORY_EXCLUDED_STATUSES:
        return "persona_not_active"
    recon = str(metadata.get("provisioning_reconciliation_state") or "").strip().lower()
    # The owner stores a running Paper Persona as research_only plus the
    # reconciled projection in metadata; nothing else is Paper-eligible.
    if lifecycle in _PAPER_ADVISORY_LIFECYCLES or (lifecycle == "research_only" and recon == "paper_running"):
        return ""
    return "persona_not_paper_running"


class PersonaAdvisoryAdmissionPort:
    """Owner-backed, idempotent advisory (persona_opinion only) admission.

    Writes one deterministic capability snapshot receipt and an explicit
    ``paper`` environment ceiling through the Persona owner.  Grants no order,
    capital or runtime authority and never raises a ceiling above ``paper``.
    """

    def __init__(self, *, persona_mutation_port: Any) -> None:
        self._port = persona_mutation_port

    def ensure(self, persona_id: str, raw: Mapping[str, Any], *, generated_at: str) -> Dict[str, Any]:
        reason = paper_advisory_ineligibility(raw)
        if reason:
            raise PersonaAdvisoryAdmissionError(reason)
        upsert = getattr(self._port, "upsert_persona_capability_snapshot", None)
        if not callable(upsert):
            raise PersonaAdvisoryAdmissionError("capability_owner_unavailable")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        existing_ceiling = str(metadata.get("environment_ceiling") or "").strip().lower()
        if existing_ceiling not in ("", PAPER_ADVISORY_ENVIRONMENT_CEILING, "research", "shadow"):
            raise PersonaAdvisoryAdmissionError("environment_ceiling_exceeds_paper_advisory")
        snapshot_id = paper_advisory_snapshot_id(persona_id)
        try:
            snapshot = upsert(
                snapshot_id=snapshot_id,
                persona_id=persona_id,
                capabilities=list(PAPER_ADVISORY_CAPABILITIES),
                generated_at=generated_at,
                source_refs=["persona_paper_advisory_reconcile"],
                metadata={"scope": "paper_advisory"},
                actor_id=PAPER_ADVISORY_ACTOR,
            )
        except Exception as exc:  # owner conflicts/unavailability surface as precondition
            raise PersonaAdvisoryAdmissionError(f"capability_owner_rejected: {exc}") from exc
        if existing_ceiling != PAPER_ADVISORY_ENVIRONMENT_CEILING or metadata.get("capability_snapshot_id") != snapshot_id:
            updated = self._port.update_persona(
                persona_id,
                metadata={
                    "capability_snapshot_id": snapshot_id,
                    "environment_ceiling": PAPER_ADVISORY_ENVIRONMENT_CEILING,
                },
            )
            if updated is None:
                raise PersonaAdvisoryAdmissionError("persona_not_found_by_owner")
        return {
            "persona_id": persona_id,
            "capability_snapshot_id": snapshot_id,
            "capabilities": list(snapshot.get("capabilities") or PAPER_ADVISORY_CAPABILITIES),
            "environment_ceiling": PAPER_ADVISORY_ENVIRONMENT_CEILING,
        }


__all__ = [
    "PAPER_ADVISORY_CAPABILITIES",
    "PersonaAdvisoryAdmissionError",
    "PersonaAdvisoryAdmissionPort",
    "paper_advisory_ineligibility",
    "paper_advisory_snapshot_id",
    "PersonaProvisioningReconciliationMutationPort",
    "PersonaReconciliationMutationError",
]
