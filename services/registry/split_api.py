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
    BUILTIN_TENANT,
    ArtifactState,
    DeploymentStage,
    DeploymentView,
    RegistryEntry,
    RegistryEntryCreate,
    RegistryEntryView,
    utc_now_iso,
)
from .pg_store import DivergentCommandReplayError
from .storage import RegistryConcurrentUpdateError, RegistryStore

# ``register_if_absent`` is called from two kinds of places: real API routes
# (service.py), which always pass a verified caller's ``actor`` explicitly,
# and the registry's own checked-in bootstrap code
# (strategy_artifact.py ``ensure_builtin_strategy_artifacts``), which never
# has a caller to authenticate and does not pass ``actor`` at all. An absent
# actor here can therefore only mean "the registry's own bootstrap is
# registering a built-in", so it is bound to the reserved ``BUILTIN_TENANT``
# identity rather than left untenanted — architecture-resumption-sa-sd.md
# §3.1/§3.3. No HTTP-reachable caller can trigger this default: every route
# that calls into RegistryService constructs and passes a real ``actor``.
_BOOTSTRAP_ACTOR: dict[str, object] = {
    "actor_id": "registry-bootstrap",
    "roles": ["system"],
    "tenant": BUILTIN_TENANT,
    "token_kind": "system",
}

# Composite identity a "next revision" create-if-absent call must not
# collide on across two different caller-supplied registry_ids — see
# architecture-resumption-sa-sd.md §3.2 "immutable revision identity".
_REVISION_UNIQUE_FIELDS: tuple[str, ...] = ("strategy_id", "version", "artifact_type")

logger = logging.getLogger(__name__)


class RegistryError(ValueError):
    """Raised when a registry operation violates governance rules."""


class RegistryNotFoundError(RegistryError):
    """Raised when a registry entry does not exist (maps to HTTP 404)."""


class RegistryConflictError(RegistryError):
    """Raised when a CAS-guarded mutation's base snapshot is stale (maps to HTTP 409)."""


# Metadata keys that hold an immutable, validated payload (StrategySpec,
# StrategyArtifact, AllocationPolicyArtifact) or an immutable identity link
# (source_seed_id). The generic metadata-replace path (update_metadata) is an
# operator draft-note record kind — architecture-resumption-sa-sd.md §3.2 — it
# must never be usable to silently overwrite or delete one of these once set,
# which would corrupt an approved artifact while keeping its old
# checksum/version untouched.
_IMMUTABLE_METADATA_KEYS = (
    "strategy_spec",
    "strategy_artifact",
    "allocation_policy_artifact",
    "source_seed_id",
)


def _reject_immutable_metadata_mutation(
    registry_id: str,
    current_metadata: Optional[dict],
    new_metadata: Optional[dict],
) -> None:
    """Fail closed if a metadata PATCH would set, change, or remove a reserved key.

    Prior defect (reviewer finding 3): this only rejected *changing* or
    *removing* a reserved key that was already present, so an entry that had
    never carried e.g. ``strategy_spec`` could have one smuggled in for the
    first time through this generic operator-metadata path — bypassing the
    dedicated ``POST /api/registry/strategy-specs`` schema/checksum
    validation entirely while keeping the entry's original (now-stale)
    checksum. Any value change for a reserved key — including introducing it
    where none existed — is rejected; these keys are only ever set through
    their dedicated registration path.
    """
    current = current_metadata or {}
    new = new_metadata or {}
    for key in _IMMUTABLE_METADATA_KEYS:
        if new.get(key) != current.get(key):
            raise RegistryConflictError(
                f"Registry entry {registry_id!r} metadata key {key!r} carries an immutable "
                "artifact payload/identity link and can only be set through its dedicated "
                "registration path, never via the generic metadata PATCH."
            )


class RegistryService:
    def __init__(self, store: RegistryStore):
        self.store = store

    # -- §8 operations ----------------------------------------------------

    def register(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
    ) -> RegistryEntryView:
        """Create a new draft or candidate entry."""
        self._validate_registration_state(payload)
        self._reject_version_collision(payload, exclude_registry_id=registry_id)
        entry = self.store.create(payload, registry_id, actor=actor)
        logger.info("Registered %s (state=%s)", entry.registry_id, entry.artifact_state.value)
        return self._to_view(entry)

    def register_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
    ) -> tuple[RegistryEntryView, bool]:
        """Atomically register an id, returning the existing view on collision.

        ``store.create_if_absent`` is called with ``_REVISION_UNIQUE_FIELDS``
        so a *different* caller-supplied ``registry_id`` at the same
        (strategy_id, version, artifact_type) atomically loses the race
        instead of both committing divergent content under the same version
        — architecture-resumption-sa-sd.md §3.2. The returned ``created=False``
        view may therefore have a different ``registry_id`` than requested;
        callers (see service.py's strategy-spec/strategy-artifact facades)
        must check that before treating it as an exact-key replay.
        """
        self._validate_registration_state(payload)
        entry, created = self.store.create_if_absent(
            payload,
            registry_id,
            actor=actor or _BOOTSTRAP_ACTOR,
            unique_fields=_REVISION_UNIQUE_FIELDS,
        )
        if created:
            logger.info(
                "Registered %s (state=%s)",
                entry.registry_id,
                entry.artifact_state.value,
            )
        return self._to_view(entry), created

    def _reject_version_collision(
        self, payload: RegistryEntryCreate, *, exclude_registry_id: str,
    ) -> None:
        """Best-effort (non-atomic) guard for the plain ``register()`` path.

        ``register()`` always creates a fresh, randomly-suffixed
        ``registry_id`` (see service.py's ``register_entry``/
        ``register_allocation_policy_artifact`` routes), so it has no natural
        create-if-absent identity to CAS against. This check narrows — but,
        under true concurrent races, does not fully close — the same
        (strategy_id, version, artifact_type) collision window that
        ``register_if_absent``'s ``unique_fields`` closes atomically at the
        store layer.
        """
        for view in self.list_by_strategy(payload.strategy_id):
            entry = view.entry
            if (
                entry.registry_id != exclude_registry_id
                and entry.artifact_type == payload.artifact_type
                and entry.version == payload.version
            ):
                raise RegistryConflictError(
                    f"strategy_id={payload.strategy_id!r} version={payload.version!r} "
                    f"artifact_type={payload.artifact_type.value!r} is already registered "
                    f"as registry_id={entry.registry_id!r}; immutable revision identity "
                    "forbids a second registry_id at the same (strategy_id, version, "
                    "artifact_type)."
                )

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
        *,
        actor: Optional[dict] = None,
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
            entry = self.store.update(entry, expected=base_snapshot, actor=actor)
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
        actor: Optional[dict] = None,
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
        # Reserved keys carrying an immutable validated payload (StrategySpec/
        # StrategyArtifact/AllocationPolicyArtifact) or identity link
        # (source_seed_id) must never be reachable through this generic
        # operator metadata-replace path once set — checked against the
        # actual durable entry, not the caller's claimed base, so a stale or
        # forged expected_metadata cannot be used to smuggle a change past
        # this guard.
        _reject_immutable_metadata_mutation(registry_id, entry.metadata, new_metadata)
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
                actor=actor,
            )
        except RegistryConcurrentUpdateError as exc:
            raise RegistryConflictError(str(exc)) from exc
        except DivergentCommandReplayError as exc:
            # A same command_key reused with a genuinely different request
            # (different precondition and/or target metadata) is a caller
            # bug, not a 500 — map it to the same 409 conflict semantics as a
            # stale CAS (reviewer finding 6).
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
        actor: Optional[dict] = None,
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
                actor=actor,
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
