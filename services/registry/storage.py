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
from typing import Optional

from .models import (
    DeploymentStage,
    DeploymentSummary,
    DeploymentView,
    RegistryEntry,
    RegistryEntryCreate,
    utc_now_iso,
)


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
    ) -> RegistryEntry:
        entry = self._new_entry(payload, registry_id, actor=actor)
        self.put(entry)
        return RegistryEntry.from_dict(entry.to_dict())

    def create_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create an entry, or return the existing entry unchanged."""
        with self._lock:
            existing = self._entries.get(registry_id)
            if existing is not None:
                return RegistryEntry.from_dict(existing.to_dict()), False
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
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
            PostgresRegistryStore.receipt_key(command_key, registry_id, actor=actor)
            if command_key
            else None
        )
        with self._lock:
            if scoped_key:
                receipt = self._command_receipts.get(scoped_key)
                if receipt is not None:
                    if receipt["new_metadata"] != new_metadata:
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
    (or unset, in dev posture) is an explicit, documented test/local-dev
    opt-in into the in-memory test double — never a silent fallback for a
    staging/production deployment that failed to configure Postgres.

    Backend selection fails closed the same way
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

    backend = os.getenv("REGISTRY_STORE_BACKEND", "memory").strip().lower()
    if backend in ("", "memory"):
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
