"""
BP5-SVC-002: Core registry operations using the split artifact_state / deployment_stage model.

Implements the §8 operations from contract.md:
- register(entry)
- get(registry_id)
- list_by_strategy(strategy_id)
- advance_artifact_state(registry_id, target_state)
- resolve_latest_approved(strategy_id)
- resolve_deployment_view(strategy_id)
"""
from __future__ import annotations

import logging
from typing import Optional

from .models import (
    ALLOWED_ARTIFACT_TRANSITIONS,
    ArtifactState,
    DeploymentStage,
    DeploymentView,
    RegistryEntry,
    RegistryEntryCreate,
    RegistryEntryView,
    utc_now_iso,
)
from .storage import RegistryConcurrentUpdateError, RegistryStore

logger = logging.getLogger(__name__)


class RegistryError(ValueError):
    """Raised when a registry operation violates governance rules."""


class RegistryNotFoundError(RegistryError):
    """Raised when a registry entry does not exist (maps to HTTP 404)."""


class RegistryConflictError(RegistryError):
    """Raised when a CAS-guarded mutation's base snapshot is stale (maps to HTTP 409)."""


class RegistryService:
    def __init__(self, store: RegistryStore):
        self.store = store

    # -- §8 operations ----------------------------------------------------

    def register(self, payload: RegistryEntryCreate, registry_id: str) -> RegistryEntryView:
        """Create a new draft or candidate entry."""
        self._validate_registration_state(payload)
        entry = self.store.create(payload, registry_id)
        logger.info("Registered %s (state=%s)", entry.registry_id, entry.artifact_state.value)
        return self._to_view(entry)

    def register_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
    ) -> tuple[RegistryEntryView, bool]:
        """Atomically register an id, returning the existing view on collision."""
        self._validate_registration_state(payload)
        entry, created = self.store.create_if_absent(payload, registry_id)
        if created:
            logger.info(
                "Registered %s (state=%s)",
                entry.registry_id,
                entry.artifact_state.value,
            )
        return self._to_view(entry), created

    @staticmethod
    def _validate_registration_state(payload: RegistryEntryCreate) -> None:
        if payload.artifact_state in (ArtifactState.APPROVED, ArtifactState.RETIRED):
            raise RegistryError(
                f"register() cannot create an entry in state '{payload.artifact_state.value}'. "
                "Only 'draft' or 'candidate' are allowed at creation time. "
                "Use advance_artifact_state() to transition through the governed state machine."
            )

    def get(self, registry_id: str) -> RegistryEntryView:
        """Read one entry with derived deployment_stage."""
        entry = self.store.get(registry_id)
        if entry is None:
            raise RegistryNotFoundError(f"Registry entry not found: {registry_id}")
        return self._to_view(entry)

    def list_by_strategy(self, strategy_id: str) -> list[RegistryEntryView]:
        """Enumerate versions within a strategy family."""
        entries = self.store.list_by_strategy(strategy_id)
        return [self._to_view(e) for e in entries]

    def advance_artifact_state(
        self,
        registry_id: str,
        target_state: ArtifactState,
        approver: Optional[str] = None,
        approval_decision_id: Optional[str] = None,
    ) -> RegistryEntryView:
        """
        Transition an entry through governed artifact-state checks.

        Registry owns artifact_state; deployment_stage is NOT touched here.
        """
        entry = self.store.get(registry_id)
        if entry is None:
            raise RegistryNotFoundError(f"Registry entry not found: {registry_id}")

        base_snapshot = entry.to_dict()
        current = entry.artifact_state
        allowed = ALLOWED_ARTIFACT_TRANSITIONS.get(current, [])
        if target_state not in allowed:
            raise RegistryError(
                f"Forbidden artifact-state transition: {current.value} -> {target_state.value}. "
                f"Allowed: {[a.value for a in allowed]}"
            )

        if target_state == ArtifactState.APPROVED and entry.lineage.is_empty():
            raise RegistryError(
                "Cannot approve artifact without lineage. "
                "Approved artifacts must carry source runs, parent entries, or source dataset/spec refs."
            )

        entry.artifact_state = target_state

        if target_state == ArtifactState.APPROVED:
            entry.approved_at = utc_now_iso()
            if approver:
                entry.approver = approver
            if approval_decision_id:
                entry.approval_decision_id = approval_decision_id

        try:
            entry = self.store.update(entry, expected=base_snapshot)
        except RegistryConcurrentUpdateError as exc:
            raise RegistryConflictError(str(exc)) from exc
        logger.info(
            "Advanced %s artifact_state: %s -> %s",
            registry_id, current.value, target_state.value,
        )
        return self._to_view(entry)

    def update_metadata(
        self,
        registry_id: str,
        *,
        expected_metadata: Optional[dict],
        new_metadata: Optional[dict],
        command_key: Optional[str] = None,
    ) -> tuple[RegistryEntryView, bool]:
        """Allowed metadata update with CAS — architecture-resumption-sa-sd.md §3.2.

        This mutates only the operator-facing ``metadata`` record kind; it can
        never fabricate or upgrade a validated StrategySpec/artifact_state.
        ``expected_metadata`` must match the entry's current durable metadata
        or the call fails with :class:`RegistryConflictError`. ``command_key``
        makes a retried identical request an idempotent no-op replay instead
        of a second mutation; a replay with different ``new_metadata`` under
        the same key fails instead of silently accepting a divergent write.
        """
        entry = self.store.get(registry_id)
        if entry is None:
            raise RegistryNotFoundError(f"Registry entry not found: {registry_id}")
        # The CAS binds the caller's claimed base (their expected_metadata
        # against the entry's other fields as just re-read) rather than a
        # bare "is expected_metadata == current metadata" check performed
        # here in Python: that would reject a valid idempotent replay, since
        # a replay's expected_metadata reflects the *original* request, not
        # whatever the row has become since. commit_metadata_cas() skips the
        # CAS entirely once command_key identifies an exact replay; for a
        # genuine (non-replay) call, a stale expected_metadata makes
        # claimed_base_snapshot disagree with the durable row and the CAS
        # fails closed the same way a stale full-entry snapshot would.
        claimed_base_snapshot = entry.to_dict()
        claimed_base_snapshot["metadata"] = expected_metadata
        base_snapshot = claimed_base_snapshot
        try:
            updated, replayed = self.store.commit_metadata_cas(
                registry_id=registry_id,
                base_snapshot=base_snapshot,
                new_metadata=new_metadata,
                command_key=command_key,
            )
        except RegistryConcurrentUpdateError as exc:
            raise RegistryConflictError(str(exc)) from exc
        return self._to_view(updated), replayed

    def resolve_latest_approved(self, strategy_id: str) -> Optional[RegistryEntryView]:
        """Return the newest approved entry for a strategy family."""
        entry = self.store.resolve_latest_approved(strategy_id)
        if entry is None:
            return None
        return self._to_view(entry)

    def resolve_deployment_view(self, strategy_id: str) -> DeploymentView:
        """
        Return the derived deployment-stage view from deployment/runtime objects.

        This is a composed read path, not a registry-only write authority.
        """
        return self.store.resolve_deployment_view(strategy_id)

    # -- Deployment summary projection (called by deployment service) -------

    def update_deployment_summary(
        self,
        registry_id: str,
        *,
        current_stage: DeploymentStage,
        deployment_plan_id: Optional[str] = None,
        runtime_binding_id: Optional[str] = None,
    ) -> RegistryEntryView:
        """
        Update the derived deployment_summary on a registry entry.

        Authoritative deployment stage lives outside the registry.
        """
        entry = self.store.get(registry_id)
        if entry is None:
            raise RegistryNotFoundError(f"Registry entry not found: {registry_id}")
        if current_stage != DeploymentStage.NONE and entry.artifact_state != ArtifactState.APPROVED:
            raise RegistryError(
                "Cannot project a non-'none' deployment stage onto an artifact that is not approved."
            )
        try:
            entry = self.store.update_deployment_summary(
                registry_id,
                current_stage=current_stage,
                deployment_plan_id=deployment_plan_id,
                runtime_binding_id=runtime_binding_id,
            )
        except RegistryConcurrentUpdateError as exc:
            raise RegistryConflictError(str(exc)) from exc
        if entry is None:
            raise RegistryNotFoundError(f"Registry entry not found: {registry_id}")
        return self._to_view(entry)

    # -- Internal helpers -------------------------------------------------

    @staticmethod
    def _to_view(entry: RegistryEntry) -> RegistryEntryView:
        stage = DeploymentStage.NONE
        if entry.deployment_summary and entry.deployment_summary.current_stage:
            stage = entry.deployment_summary.current_stage
        return RegistryEntryView(entry=entry, deployment_stage=stage)
