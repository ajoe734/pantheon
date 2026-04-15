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
from .storage import RegistryStore

logger = logging.getLogger(__name__)


class RegistryError(ValueError):
    """Raised when a registry operation violates governance rules."""


class RegistryNotFoundError(RegistryError):
    """Raised when a registry entry does not exist (maps to HTTP 404)."""


class RegistryService:
    def __init__(self, store: RegistryStore):
        self.store = store

    # -- §8 operations ----------------------------------------------------

    def register(self, payload: RegistryEntryCreate, registry_id: str) -> RegistryEntryView:
        """Create a new draft or candidate entry."""
        if payload.artifact_state in (ArtifactState.APPROVED, ArtifactState.RETIRED):
            raise RegistryError(
                f"register() cannot create an entry in state '{payload.artifact_state.value}'. "
                "Only 'draft' or 'candidate' are allowed at creation time. "
                "Use advance_artifact_state() to transition through the governed state machine."
            )
        entry = self.store.create(payload, registry_id)
        logger.info("Registered %s (state=%s)", entry.registry_id, entry.artifact_state.value)
        return self._to_view(entry)

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
    ) -> RegistryEntryView:
        """
        Transition an entry through governed artifact-state checks.

        Registry owns artifact_state; deployment_stage is NOT touched here.
        """
        entry = self.store.get(registry_id)
        if entry is None:
            raise RegistryNotFoundError(f"Registry entry not found: {registry_id}")

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

        self.store.update(entry)
        logger.info(
            "Advanced %s artifact_state: %s -> %s",
            registry_id, current.value, target_state.value,
        )
        return self._to_view(entry)

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
        entry = self.store.update_deployment_summary(
            registry_id,
            current_stage=current_stage,
            deployment_plan_id=deployment_plan_id,
            runtime_binding_id=runtime_binding_id,
        )
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
