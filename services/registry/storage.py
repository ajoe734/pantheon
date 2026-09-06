"""
BP5-SVC-002: In-memory storage backend for the registry service.

``RegistryStore`` is an explicit test double: unit tests construct it
directly to exercise ``RegistryService`` without a database. Production
selects the durable Postgres backend in ``services/registry/pg_store.py``
via :func:`build_registry_store` — see architecture-resumption-sa-sd.md §3.1.
Both backends expose the same call surface so ``RegistryService`` (split_api.py)
needs no changes to select between them.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Optional

from .models import (
    DeploymentStage,
    DeploymentSummary,
    DeploymentView,
    RegistryEntry,
    RegistryEntryCreate,
    utc_now_iso,
)


class RegistryUniqueViolationError(ValueError):
    """Raised when a create would collide on a composite ``unique_fields``
    identity (e.g. (strategy_id, version, artifact_type)) already held by a
    *different* registry_id. Mirrors the equivalent Postgres-backend error so
    ``RegistryService`` can catch one exception type regardless of backend."""

    def __init__(
        self,
        registry_id: str,
        unique_fields: tuple[str, ...],
        other_registry_id: str,
    ) -> None:
        super().__init__(
            f"Registry entry {registry_id} collides on {unique_fields} with the "
            f"already-registered registry_id={other_registry_id}."
        )
        self.registry_id = registry_id
        self.unique_fields = unique_fields
        self.other_registry_id = other_registry_id


class RegistryConcurrentUpdateError(ValueError):
    """Raised when a CAS-guarded update's base snapshot no longer matches the
    durable entry. Mirrors ``pg_store.RegistryConcurrentUpdateError`` so
    ``RegistryService`` can catch one exception type regardless of backend."""

    def __init__(self, registry_id: str) -> None:
        super().__init__(
            f"Registry entry {registry_id} was modified by another writer since it was read; "
            "re-read the current version before retrying."
        )
        self.registry_id = registry_id


class RegistryStore:
    """Thread-safe in-memory registry entry store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # registry_id -> RegistryEntry
        self._entries: dict[str, RegistryEntry] = {}
        # strategy_id -> list of registry_ids (for index)
        self._strategy_index: dict[str, list[str]] = {}
        # command_key -> {"new_metadata": ...} idempotent-replay ledger
        self._command_receipts: dict[str, dict] = {}
        # registry_id -> original entry snapshot at first successful creation
        # (reviewer finding 5 — see PostgresRegistryStore._store_creation_receipt)
        self._creation_receipts: dict[str, dict] = {}

    # -- Write operations -------------------------------------------------

    def _put_unlocked(self, entry: RegistryEntry) -> None:
        self._entries[entry.registry_id] = entry
        self._strategy_index.setdefault(entry.strategy_id, [])
        if entry.registry_id not in self._strategy_index[entry.strategy_id]:
            self._strategy_index[entry.strategy_id].append(entry.registry_id)

    @staticmethod
    def _new_entry(
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
    ) -> RegistryEntry:
        now = utc_now_iso()
        return RegistryEntry(
            registry_id=registry_id,
            artifact_type=payload.artifact_type,
            strategy_id=payload.strategy_id,
            version=payload.version,
            artifact_state=payload.artifact_state,
            lineage=payload.lineage,
            storage_ref=payload.storage_ref,
            checksum=payload.checksum,
            producer_run_id=payload.producer_run_id,
            evaluation_summary=payload.evaluation_summary,
            rollback_target=payload.rollback_target,
            metadata=payload.metadata,
            created_at=now,
            updated_at=now,
            last_actor=actor,
            owner_tenant=str((actor or {}).get("tenant") or "").strip() or None,
        )

    def put(self, entry: RegistryEntry) -> None:
        with self._lock:
            self._put_unlocked(entry)

    def create(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> RegistryEntry:
        """Create a new entry, atomically reserving ``unique_fields`` if given.

        Reviewer finding 3: the plain (non-``create_if_absent``) create path
        used to insert unconditionally with no composite-identity check at
        all, so two concurrent ``register()`` calls for the same
        (strategy_id, version, artifact_type) under two different
        caller-generated ``registry_id``s could both commit — this closes
        that race the same way ``create_if_absent`` does, under the same
        lock held for the whole check-then-insert sequence.
        """
        entry = self._new_entry(payload, registry_id, actor=actor)
        with self._lock:
            if unique_fields:
                for other in self._entries.values():
                    if other.registry_id == registry_id:
                        continue
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        raise RegistryUniqueViolationError(
                            registry_id, unique_fields, other.registry_id,
                        )
            self._put_unlocked(entry)
        return RegistryEntry.from_dict(entry.to_dict())

    def create_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create an entry, or return the existing entry unchanged.

        ``unique_fields`` (e.g. ``("strategy_id", "version", "artifact_type")``)
        reserves a composite identity in addition to ``registry_id`` — see
        architecture-resumption-sa-sd.md §3.2's "immutable revision identity"
        requirement: two different caller-supplied ``registry_id``s must not
        both succeed at the same (strategy_id, version, artifact_type) tuple.
        The whole scan-then-insert sequence runs under ``self._lock`` so this
        mirrors the table-locked semantics of
        ``PostgresJsonOwnerStore.insert_if_absent``.
        """
        with self._lock:
            existing = self._entries.get(registry_id)
            if existing is not None:
                # Returns the live current row; callers use
                # get_creation_receipt() for the immutable-content
                # comparison instead (reviewer finding 5) — a replayed
                # create is not a request to revert real progress (e.g. an
                # approval) back to its original draft state.
                return RegistryEntry.from_dict(existing.to_dict()), False
            if unique_fields:
                for other in self._entries.values():
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        return RegistryEntry.from_dict(other.to_dict()), False
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
            self._creation_receipts[registry_id] = entry.to_dict()
            return RegistryEntry.from_dict(entry.to_dict()), True

    def update(
        self,
        entry: RegistryEntry,
        *,
        expected: Optional[dict] = None,
        actor: Optional[dict] = None,
    ) -> RegistryEntry:
        """Commit a mutation, optionally CAS-guarded against the caller's base snapshot.

        ``expected`` must be the ``to_dict()`` snapshot the caller read via
        :meth:`get` before mutating ``entry`` — this mirrors the Postgres
        backend's CAS contract so both backends reject the same stale-write
        pattern the same way.
        """
        with self._lock:
            if expected is not None:
                current = self._entries.get(entry.registry_id)
                if current is None or current.to_dict() != expected:
                    raise RegistryConcurrentUpdateError(entry.registry_id)
            entry.updated_at = utc_now_iso()
            if actor is not None:
                entry.last_actor = actor
            self._put_unlocked(entry)
            return RegistryEntry.from_dict(entry.to_dict())

    # -- Read operations --------------------------------------------------

    def get(self, registry_id: str) -> Optional[RegistryEntry]:
        with self._lock:
            entry = self._entries.get(registry_id)
            return RegistryEntry.from_dict(entry.to_dict()) if entry is not None else None

    def list_by_strategy(self, strategy_id: str) -> list[RegistryEntry]:
        with self._lock:
            ids = self._strategy_index.get(strategy_id, [])
            return [
                RegistryEntry.from_dict(self._entries[rid].to_dict())
                for rid in ids
                if rid in self._entries
            ]

    def resolve_latest_approved(self, strategy_id: str) -> Optional[RegistryEntry]:
        """Return the newest approved entry for a strategy family (semver comparison)."""
        entries = self.list_by_strategy(strategy_id)
        approved = [e for e in entries if e.artifact_state.value == "approved"]
        if not approved:
            return None
        # Sort by semver descending
        def _parse_ver(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))
        approved.sort(key=lambda e: _parse_ver(e.version), reverse=True)
        return approved[0]

    # -- Deployment view projection ---------------------------------------

    def resolve_deployment_view(self, strategy_id: str) -> DeploymentView:
        """
        Composed read path that merges registry truth with deployment/runtime objects.

        latest_approved_* reflects the newest approved version (by semver).
        current_stage and related deployment fields come from the approved entry
        that is actually deployed (has a non-NONE deployment_summary.current_stage),
        which may be an older version than the latest approved.

        In v1 this derives from the deployment_summary cached on registry entries.
        When DEP-001 / RUN-001 land, this will query the actual DeploymentPlan
        and RuntimeBinding stores.
        """
        entries = self.list_by_strategy(strategy_id)
        approved = [e for e in entries if e.artifact_state.value == "approved"]
        view = DeploymentView(strategy_id=strategy_id)
        if not approved:
            return view

        def _parse_ver(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))

        approved_by_ver = sorted(approved, key=lambda e: _parse_ver(e.version), reverse=True)
        latest = approved_by_ver[0]
        view.latest_approved_registry_id = latest.registry_id
        view.latest_approved_version = latest.version

        # Find the approved entry that is actually deployed (non-NONE deployment stage).
        # If multiple exist, prefer the one most recently transitioned.
        deployed_candidates = [
            e for e in approved
            if e.deployment_summary is not None
            and e.deployment_summary.current_stage is not None
            and e.deployment_summary.current_stage != DeploymentStage.NONE
        ]
        if deployed_candidates:
            deployed = max(
                deployed_candidates,
                key=lambda e: e.deployment_summary.last_transition_at or "",
            )
            ds = deployed.deployment_summary
            view.current_stage = ds.current_stage
            view.deployment_plan_id = ds.deployment_plan_id
            view.runtime_binding_id = ds.runtime_binding_id
            view.last_transition_at = ds.last_transition_at

        return view

    def create_with_receipt(
        self,
        payload_factory: Callable[[], tuple[RegistryEntryCreate, str]],
        *,
        command_key: str,
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
        request_fingerprint: object = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create-or-replay a caller-scoped idempotent creation.

        Reviewer finding 4: the generic create routes (e.g. the name-only
        draft path) previously had no idempotency concept at all — a
        retried identical request under the same ``Idempotency-Key``
        synthesized a *fresh* random identity every time instead of
        returning the originally-created entry. ``payload_factory`` is only
        invoked when this is genuinely the first request under this
        tenant/actor-scoped key; a replay never generates a new identity,
        it returns the entry snapshot committed the first time.

        ``request_fingerprint`` is a JSON-serializable normalized
        representation of the caller's request (see
        ``PostgresRegistryStore.create_with_receipt`` for the reviewer
        finding 3 rationale): a same-key replay whose fingerprint differs
        from the one originally committed is a divergent request, not a
        replay, and must fail closed instead of silently returning the
        original entry under a different requested identity/content.
        """
        from .pg_store import DivergentCommandReplayError, PostgresRegistryStore, _request_digest

        scoped_key = PostgresRegistryStore.receipt_key(
            command_key, "register_entry", actor=actor, command_type="create",
        )
        request_digest = _request_digest({"request": request_fingerprint})
        with self._lock:
            receipt = self._command_receipts.get(scoped_key)
            if receipt is not None:
                if receipt.get("request_digest") != request_digest:
                    raise DivergentCommandReplayError(command_key)
                return RegistryEntry.from_dict(receipt["committed_entry"]), True

            payload, registry_id = payload_factory()
            if unique_fields:
                for other in self._entries.values():
                    if other.registry_id == registry_id:
                        continue
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        raise RegistryUniqueViolationError(
                            registry_id, unique_fields, other.registry_id,
                        )
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
            committed = entry.to_dict()
            self._command_receipts[scoped_key] = {
                "committed_entry": committed,
                "request_digest": request_digest,
            }
            return RegistryEntry.from_dict(committed), False

    def register_strategy_spec_revision(
        self,
        *,
        strategy_id: str,
        registry_id: str,
        payload: RegistryEntryCreate,
        validate_lineage: Callable[[list[RegistryEntry]], None],
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """In-memory mirror of ``PostgresRegistryStore.register_strategy_spec_revision``.

        ``self._lock`` already serializes the whole read-validate-write
        sequence for every caller against this process-local store (there is
        no separate advisory-lock primitive needed in-process), so this
        closes the same TOCTOU race the Postgres backend closes with a
        per-strategy_id advisory transaction lock.
        """
        with self._lock:
            existing_entries = [
                RegistryEntry.from_dict(entry.to_dict())
                for entry in self._entries.values()
                if entry.strategy_id == strategy_id
            ]
            validate_lineage(existing_entries)
            existing = self._entries.get(registry_id)
            if existing is not None:
                return RegistryEntry.from_dict(existing.to_dict()), False
            if unique_fields:
                for other in self._entries.values():
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        return RegistryEntry.from_dict(other.to_dict()), False
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
            self._creation_receipts[registry_id] = entry.to_dict()
            return RegistryEntry.from_dict(entry.to_dict()), True

    def get_creation_receipt(self, registry_id: str) -> Optional[RegistryEntry]:
        """In-memory mirror of ``PostgresRegistryStore.get_creation_receipt``."""
        with self._lock:
            original = self._creation_receipts.get(registry_id)
            return RegistryEntry.from_dict(original) if original is not None else None

    # -- Deployment summary update (called by deployment service) -----------

    def update_deployment_summary(
        self,
        registry_id: str,
        *,
        current_stage: DeploymentStage,
        deployment_plan_id: Optional[str] = None,
        runtime_binding_id: Optional[str] = None,
        actor: Optional[dict] = None,
    ) -> Optional[RegistryEntry]:
        """
        Updates the derived deployment_summary on a registry entry.

        This is a read-model projection — the authoritative deployment stage
        lives in DeploymentPlan / RuntimeBinding, not in the registry entry.
        """
        with self._lock:
            entry = self._entries.get(registry_id)
            if entry is None:
                return None

            entry.deployment_summary = DeploymentSummary(
                current_stage=current_stage,
                deployment_plan_id=deployment_plan_id,
                runtime_binding_id=runtime_binding_id,
                last_transition_at=utc_now_iso(),
            )
            entry.updated_at = utc_now_iso()
            if actor is not None:
                entry.last_actor = actor
            return RegistryEntry.from_dict(entry.to_dict())

    # -- Artifact-state CAS update (parity with PostgresRegistryStore) -----

    def commit_artifact_state_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict,
        target_state: Any,
        approved_at: Optional[str] = None,
        approver: Optional[str] = None,
        approval_decision_id: Optional[str] = None,
        command_key: Optional[str] = None,
        actor: Optional[dict] = None,
        validate: Optional[Callable[[RegistryEntry], None]] = None,
        expected_artifact_state: Optional[str] = None,
        expected_version: Optional[str] = None,
        expected_updated_at: Optional[str] = None,
    ) -> tuple[RegistryEntry, bool]:
        """In-memory mirror of ``PostgresRegistryStore.commit_artifact_state_cas``
        — same CAS + idempotent-replay + divergent-key-rejection contract as
        ``commit_metadata_cas``, applied to the artifact_state transition
        (reviewer finding 5). ``validate`` runs only on the non-replay path —
        see the Postgres backend's docstring for why re-validating a
        transition against a replay's already-post-transition current state
        would always (and wrongly) fail.

        ``expected_artifact_state``/``expected_version``/``expected_updated_at``
        mirror the Postgres backend's caller-bound-base digest fields
        (reviewer finding 6) — ``base_snapshot`` itself already carries the
        caller's claimed base merged in by the caller
        (``RegistryService.advance_artifact_state``), so the actual CAS
        enforcement is the ``current.to_dict() != base_snapshot`` check
        below; these are threaded through only so a same command_key
        resubmitted with a genuinely different claimed base is detected as
        divergent rather than silently replayed.
        """
        from .pg_store import PostgresRegistryStore

        scoped_key = (
            PostgresRegistryStore.receipt_key(
                command_key, registry_id, actor=actor, command_type="advance",
            )
            if command_key
            else None
        )
        target_value = target_state.value if hasattr(target_state, "value") else target_state
        with self._lock:
            if scoped_key:
                receipt = self._command_receipts.get(scoped_key)
                if receipt is not None:
                    # Deliberately excludes the entry's current
                    # artifact_state from the comparison — see the Postgres
                    # backend's docstring: there is no separate "expected
                    # base state" precondition here, and the base
                    # legitimately differs between the original call and a
                    # later replay issued after that call already committed.
                    if (
                        receipt.get("target_state") != target_value
                        or receipt.get("approver") != approver
                        or receipt.get("approval_decision_id") != approval_decision_id
                        or receipt.get("expected_artifact_state") != expected_artifact_state
                        or receipt.get("expected_version") != expected_version
                        or receipt.get("expected_updated_at") != expected_updated_at
                    ):
                        from .pg_store import DivergentCommandReplayError

                        raise DivergentCommandReplayError(command_key)
                    return RegistryEntry.from_dict(receipt["committed_entry"]), True

            current = self._entries.get(registry_id)
            if current is None or current.to_dict() != base_snapshot:
                raise RegistryConcurrentUpdateError(registry_id)
            if validate is not None:
                validate(RegistryEntry.from_dict(base_snapshot))
            entry = RegistryEntry.from_dict(base_snapshot)
            entry.artifact_state = target_state
            entry.updated_at = utc_now_iso()
            if approved_at is not None:
                entry.approved_at = approved_at
            if approver:
                entry.approver = approver
            if approval_decision_id:
                entry.approval_decision_id = approval_decision_id
            if actor is not None:
                entry.last_actor = actor
            self._put_unlocked(entry)
            committed = entry.to_dict()
            if scoped_key:
                self._command_receipts[scoped_key] = {
                    "target_state": target_value,
                    "approver": approver,
                    "approval_decision_id": approval_decision_id,
                    "expected_artifact_state": expected_artifact_state,
                    "expected_version": expected_version,
                    "expected_updated_at": expected_updated_at,
                    "committed_entry": committed,
                }
            return RegistryEntry.from_dict(committed), False

    # -- Metadata CAS update (parity with PostgresRegistryStore) -----------

    def commit_metadata_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict,
        new_metadata: Optional[dict],
        command_key: Optional[str] = None,
        actor: Optional[dict] = None,
    ) -> tuple[RegistryEntry, bool]:
        """In-memory mirror of ``PostgresRegistryStore.commit_metadata_cas``.

        The receipt ledger here is process-local (not durable across
        restarts), which is why this backend is a test double only — the
        contract this method proves (CAS + idempotent replay + divergent-key
        rejection + returning the *originally* committed entry snapshot on
        replay, never whatever the row has become since) is identical to the
        Postgres backend so ``RegistryService`` can be tested against either.
        The receipt key is scoped by tenant/actor/registry_id/command_key
        (mirroring ``PostgresRegistryStore.receipt_key``) so the same
        client-chosen ``command_key`` cannot collide across tenants/actors/
        aggregates.
        """
        from .pg_store import PostgresRegistryStore

        scoped_key = (
            PostgresRegistryStore.receipt_key(
                command_key, registry_id, actor=actor, command_type="metadata",
            )
            if command_key
            else None
        )
        with self._lock:
            if scoped_key:
                receipt = self._command_receipts.get(scoped_key)
                if receipt is not None:
                    # Compare the full normalized request — including the
                    # caller's precondition (expected_metadata) — not just
                    # the target metadata, so a same-key retry with a
                    # *changed* precondition is treated as divergent even
                    # when the target metadata happens to match (reviewer
                    # finding 6; mirrors PostgresRegistryStore).
                    if (
                        receipt["new_metadata"] != new_metadata
                        or receipt.get("expected_metadata") != base_snapshot.get("metadata")
                    ):
                        from .pg_store import DivergentCommandReplayError

                        raise DivergentCommandReplayError(command_key)
                    # Return the entry snapshot as it was at the moment this
                    # command_key originally committed — not a fresh read of
                    # the (possibly since-mutated-under-a-different-key) row.
                    return RegistryEntry.from_dict(receipt["committed_entry"]), True

            current = self._entries.get(registry_id)
            if current is None or current.to_dict() != base_snapshot:
                raise RegistryConcurrentUpdateError(registry_id)
            entry = RegistryEntry.from_dict(base_snapshot)
            entry.metadata = new_metadata
            entry.updated_at = utc_now_iso()
            if actor is not None:
                entry.last_actor = actor
            self._put_unlocked(entry)
            committed = entry.to_dict()
            if scoped_key:
                self._command_receipts[scoped_key] = {
                    "new_metadata": new_metadata,
                    "expected_metadata": base_snapshot.get("metadata"),
                    "committed_entry": committed,
                }
            return RegistryEntry.from_dict(committed), False


# Module-level singleton for API layer to share
_default_store: Optional["RegistryStore"] = None
_store_lock = threading.Lock()


def build_registry_store():
    """Select the registry storage backend from environment configuration.

    ``REGISTRY_STORE_BACKEND=postgres`` selects the durable production owner
    store (``pg_store.PostgresRegistryStore``). ``REGISTRY_STORE_BACKEND=memory``
    is an explicit, documented test/local-dev opt-in into the in-memory test
    double (see ``conftest.py``, which sets it for this whole package's unit
    test run) — never a silent fallback when nothing was configured at all.

    Reviewer finding 7 (gen-8 review): an earlier revision of this function
    defaulted an unset ``REGISTRY_STORE_BACKEND`` to memory in "dev posture"
    (no enforced ``PANTHEON_ENV``/``PANTHEON_PERSISTENCE_POSTURE``) — but that
    let the actual mounted app silently serve real writes against an
    in-memory store with zero configuration at all, which is exactly the
    "missing-config fallback" architecture-resumption-sa-sd.md §3.1 forbids:
    "memory is explicitly injected test-only, never missing-config/
    connection/schema fallback". ``REGISTRY_STORE_BACKEND`` must now always
    be explicitly set to ``memory`` or ``postgres`` — dev/test callers opt in
    to memory the same explicit way ``conftest.py`` already does, they do not
    rely on an implicit default.

    Backend selection additionally fails closed the same way
    ``services.foundation.persistence_posture`` already does for every other
    service: in an enforced posture (``PANTHEON_ENV``/``PANTHEON_PERSISTENCE_POSTURE``
    in {stage, staging, prod, production, ...}), ``REGISTRY_STORE_BACKEND``
    must resolve to ``postgres`` or this raises ``RuntimeError`` before a
    request is ever served — an explicit ``postgres`` selection with no
    reachable database then fails closed a second time inside
    ``build_postgres_registry_store()`` instead of silently downgrading here.
    """
    from services.foundation.persistence_posture import require_persistence_posture

    require_persistence_posture(
        "registry",
        backend_env_vars={"REGISTRY_STORE_BACKEND": "memory"},
        require_object_store=False,
    )

    backend_raw = os.getenv("REGISTRY_STORE_BACKEND")
    if backend_raw is None or not backend_raw.strip():
        raise RuntimeError(
            "REGISTRY_STORE_BACKEND is not set. The registry never silently defaults to "
            "the in-memory test double — set REGISTRY_STORE_BACKEND=memory to explicitly "
            "opt into it (tests/local dev only; see conftest.py) or "
            "REGISTRY_STORE_BACKEND=postgres for a durable deployment."
        )
    backend = backend_raw.strip().lower()
    if backend == "memory":
        # A configured Postgres DSN is a strong signal the deployer intended
        # the durable backend; silently returning the in-memory test double
        # in that case would mean every write vanishes on process exit while
        # looking identical to a working deployment. Fail closed instead of
        # guessing (architecture-resumption-sa-sd.md §3.1).
        if os.getenv("REGISTRY_STORE_DSN") or os.getenv("DATABASE_URL"):
            raise RuntimeError(
                "REGISTRY_STORE_DSN/DATABASE_URL is configured but REGISTRY_STORE_BACKEND="
                "memory; refusing to silently select the in-memory test double over an "
                "apparently-intended Postgres connection. Set REGISTRY_STORE_BACKEND=postgres."
            )
        return RegistryStore()
    if backend != "postgres":
        raise ValueError("REGISTRY_STORE_BACKEND must be memory or postgres")
    from .pg_store import build_postgres_registry_store

    return build_postgres_registry_store()


def get_store():
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = build_registry_store()
        return _default_store


def reset_store() -> None:
    """Reset the singleton — useful in tests."""
    global _default_store
    with _store_lock:
        _default_store = None
