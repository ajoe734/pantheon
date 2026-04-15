"""
BP5-SVC-002: In-memory storage backend for the registry service.

Provides a thread-safe in-memory store with projection support for
resolve_deployment_view(). This is a v1 implementation; a persistent
backend can be swapped in later without changing the API layer.
"""
from __future__ import annotations

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


class RegistryStore:
    """Thread-safe in-memory registry entry store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # registry_id -> RegistryEntry
        self._entries: dict[str, RegistryEntry] = {}
        # strategy_id -> list of registry_ids (for index)
        self._strategy_index: dict[str, list[str]] = {}

    # -- Write operations -------------------------------------------------

    def put(self, entry: RegistryEntry) -> None:
        with self._lock:
            self._entries[entry.registry_id] = entry
            self._strategy_index.setdefault(entry.strategy_id, [])
            if entry.registry_id not in self._strategy_index[entry.strategy_id]:
                self._strategy_index[entry.strategy_id].append(entry.registry_id)

    def create(self, payload: RegistryEntryCreate, registry_id: str) -> RegistryEntry:
        now = utc_now_iso()
        entry = RegistryEntry(
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
        )
        self.put(entry)
        return entry

    def update(self, entry: RegistryEntry) -> None:
        entry.updated_at = utc_now_iso()
        self.put(entry)

    # -- Read operations --------------------------------------------------

    def get(self, registry_id: str) -> Optional[RegistryEntry]:
        with self._lock:
            return self._entries.get(registry_id)

    def list_by_strategy(self, strategy_id: str) -> list[RegistryEntry]:
        with self._lock:
            ids = self._strategy_index.get(strategy_id, [])
            return [self._entries[rid] for rid in ids if rid in self._entries]

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
            return entry


# Module-level singleton for API layer to share
_default_store: Optional[RegistryStore] = None
_store_lock = threading.Lock()


def get_store() -> RegistryStore:
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = RegistryStore()
        return _default_store


def reset_store() -> None:
    """Reset the singleton — useful in tests."""
    global _default_store
    with _store_lock:
        _default_store = None
