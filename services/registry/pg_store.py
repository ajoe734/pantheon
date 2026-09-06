"""Durable Postgres-backed RegistryEntry owner store.

Selected via ``REGISTRY_STORE_BACKEND=postgres`` (see :func:`build_registry_store`
in ``services/registry/storage.py``). This is the single production write
authority for StrategySpec content, immutable versions, RegistryEntry
identities and artifact-state per architecture-resumption-sa-sd.md §3.1: no
independent full-spec/revision copy, no process-memory production fallback.

Every RegistryEntry lives as one JSONB row in ``registry.entries`` (via
``PostgresJsonOwnerStore``). Idempotent command receipts for the metadata CAS
update path live in a second table, ``registry.command_receipts``, committed
in the *same* Postgres transaction as the entry mutation it records (see
:meth:`PostgresRegistryStore.commit_metadata_cas`), so a crash between
"entry written" and "receipt written" cannot happen and a same command key
replayed later returns the original committed version instead of re-running
the mutation or fabricating a second one.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

from .models import (
    DeploymentStage,
    DeploymentSummary,
    DeploymentView,
    RegistryEntry,
    RegistryEntryCreate,
    utc_now_iso,
)
from .storage import RegistryConcurrentUpdateError


class DivergentCommandReplayError(ValueError):
    """A command key was reused with a different requested mutation than the
    one it originally committed. Idempotent replay only preserves the
    original committed version for the exact same request; a divergent
    request under the same key is a caller bug, not a silently accepted
    second version."""

    def __init__(self, command_key: str) -> None:
        super().__init__(
            f"command_key {command_key!r} was already committed with a different request payload; "
            "idempotent replay requires an identical request."
        )
        self.command_key = command_key


def _request_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _new_entry(
    payload: RegistryEntryCreate,
    registry_id: str,
    *,
    actor: Optional[dict[str, Any]] = None,
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


class PostgresRegistryStore:
    """Durable RegistryEntry owner store with the same call surface as the
    in-memory ``RegistryStore`` test double, so ``RegistryService`` (split_api.py)
    needs no changes to select this backend."""

    def __init__(
        self,
        *,
        dsn: str,
        entries_table: str = "registry.entries",
        receipts_table: str = "registry.command_receipts",
        bootstrap: bool = True,
    ) -> None:
        self._entries = PostgresJsonOwnerStore(
            dsn=dsn, table=entries_table, owner_service="registry-svc", bootstrap=bootstrap,
        )
        self._receipts = PostgresJsonOwnerStore(
            dsn=dsn, table=receipts_table, owner_service="registry-svc", bootstrap=bootstrap,
        )

    # -- Write operations ---------------------------------------------------

    def create(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
    ) -> RegistryEntry:
        entry, _created = self.create_if_absent(payload, registry_id, actor=actor)
        return entry

    def create_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create an entry, or return the durable existing entry unchanged.

        Uses ``insert_if_absent`` so two processes racing on the same
        ``registry_id`` (a same-key divergent-or-duplicate create request)
        commit exactly one row; the loser gets back the winner's durable
        entry instead of silently overwriting it. ``unique_fields`` (e.g.
        ``("strategy_id", "version", "artifact_type")``) additionally
        reserves a composite identity under a table-level lock held for the
        whole read/decide/write sequence, so two *different* registry_ids
        racing on the same (strategy_id, version, artifact_type) tuple also
        commit exactly one winner — architecture-resumption-sa-sd.md §3.2's
        "immutable revision identity" requirement.
        """
        entry = _new_entry(payload, registry_id, actor=actor)
        created, canonical = self._entries.insert_if_absent(
            registry_id, entry.to_dict(), unique_fields=unique_fields,
        )
        if created:
            return entry, True
        return RegistryEntry.from_dict(canonical), False

    def put(self, entry: RegistryEntry) -> None:
        """Unconditional overwrite — restricted to idempotent built-in bootstrap
        registration at startup, never a caller-facing mutation path."""
        self._entries.put(entry.registry_id, entry.to_dict())

    def update(
        self,
        entry: RegistryEntry,
        *,
        expected: Optional[dict[str, Any]] = None,
        actor: Optional[dict[str, Any]] = None,
    ) -> RegistryEntry:
        """Commit a mutation, optionally CAS-guarded against the caller's base snapshot.

        ``expected`` must be the exact ``to_dict()`` snapshot the caller read
        via :meth:`get` before mutating ``entry`` in place — this binds the
        write to the version the caller actually observed, not whatever is
        latest in the database at write time. Omitting ``expected`` performs
        an unconditional overwrite (used only by legacy internal call sites
        that predate CAS enforcement); new mutation paths must always pass it.
        """
        entry.updated_at = utc_now_iso()
        if actor is not None:
            entry.last_actor = actor
        new_payload = entry.to_dict()
        ok, canonical = self._entries.compare_and_set(entry.registry_id, expected, new_payload)
        if not ok:
            if expected is None:
                # No guard was requested but the row does not exist at all.
                raise RegistryConcurrentUpdateError(entry.registry_id)
            raise RegistryConcurrentUpdateError(entry.registry_id)
        return RegistryEntry.from_dict(canonical)

    @staticmethod
    def receipt_key(
        command_key: str,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
    ) -> str:
        """Scope an idempotent command receipt by tenant/actor/command/aggregate.

        A bare ``command_key`` is not a safe idempotency scope on its own: two
        different tenants or actors could submit the same client-chosen key
        against two different registry_ids and collide on one receipt row.
        Scoping by tenant + actor + registry_id (the aggregate) + command_key
        makes the receipt identity unambiguous.

        Prior defect (reviewer finding 6): a plain ``f"{tenant}:{actor_id}:..."``
        colon-join let ``tenant="a:b", actor="c"`` collide with
        ``tenant="a", actor="b:c"`` — the delimiter can appear inside either
        field. Each component is now length-prefixed before joining (an
        unambiguous framing regardless of what characters the field
        contains) and the whole framed string is hashed to keep the key a
        fixed, storage-friendly size.
        """
        tenant = str((actor or {}).get("tenant") or "unscoped").strip() or "unscoped"
        actor_id = str((actor or {}).get("actor_id") or "unscoped").strip() or "unscoped"
        parts = [tenant, actor_id, registry_id, command_key]
        framed = "|".join(f"{len(part)}:{part}" for part in parts)
        return hashlib.sha256(framed.encode("utf-8")).hexdigest()

    def commit_metadata_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict[str, Any],
        new_metadata: Optional[dict[str, Any]],
        command_key: Optional[str] = None,
        actor: Optional[dict[str, Any]] = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically CAS the metadata field and record an idempotent receipt.

        Both writes commit in one Postgres transaction, using a single shared
        connection (:meth:`PostgresJsonOwnerStore.transaction`) so the entry
        mutation and its receipt land together or not at all.

        The receipt row is reserved *before* the entry mutation via
        ``insert_if_absent`` (which holds a table-level lock for the rest of
        this transaction), and its ``created`` result is what decides the
        control flow — not an assumption: a fresh reservation (``created``)
        proceeds to CAS the entry and then fills in the receipt with the
        entry snapshot this transaction actually commits; an existing
        reservation (``not created``) is a replay (identical ``new_metadata``
        digest) or a divergent reuse (different digest) of the same
        tenant-scoped ``command_key``.

        A replay always returns the entry snapshot captured in that original
        receipt row (``idempotent_replay=True``) — never a fresh read of
        whatever the entry has become since, which could reflect a later,
        unrelated mutation under a *different* command_key. A replay with a
        different ``new_metadata`` under the same key raises
        :class:`DivergentCommandReplayError` instead of silently accepting a
        second version under one key.
        """
        entry = RegistryEntry.from_dict(base_snapshot)
        entry.metadata = new_metadata
        entry.updated_at = utc_now_iso()
        if actor is not None:
            entry.last_actor = actor
        new_payload = entry.to_dict()
        # The digest must cover the caller's precondition (expected_metadata,
        # carried in base_snapshot["metadata"]) as well as the target
        # metadata — reviewer finding 6: a same-key request with a *changed*
        # precondition but identical target metadata must not silently
        # report replay=true, since the caller's compare-and-swap intent
        # differs even though the end state looks the same.
        request_digest = _request_digest({
            "registry_id": registry_id,
            "expected_metadata": base_snapshot.get("metadata"),
            "metadata": new_metadata,
        })
        scoped_key = self.receipt_key(command_key, registry_id, actor=actor) if command_key else None

        with self._entries.transaction() as conn:
            reservation: Optional[dict[str, Any]] = None
            if scoped_key:
                reservation = {
                    "command_key": command_key,
                    "receipt_key": scoped_key,
                    "registry_id": registry_id,
                    "request_digest": request_digest,
                    "committed_entry": None,
                    "committed_at": None,
                }
                reserved, receipt_payload = self._receipts.insert_if_absent(
                    scoped_key, reservation, conn=conn,
                )
                if not reserved:
                    if receipt_payload.get("request_digest") != request_digest:
                        raise DivergentCommandReplayError(command_key)
                    committed_entry = receipt_payload.get("committed_entry")
                    if committed_entry is None:
                        # Another in-flight transaction reserved this key but
                        # has not yet committed the mutation it records —
                        # fail closed rather than return an incomplete replay.
                        raise RegistryConcurrentUpdateError(registry_id)
                    return RegistryEntry.from_dict(committed_entry), True

            ok, canonical = self._entries.compare_and_set(
                registry_id, base_snapshot, new_payload, conn=conn,
            )
            if not ok:
                raise RegistryConcurrentUpdateError(registry_id)

            if scoped_key:
                finalized = dict(reservation)
                finalized["committed_entry"] = canonical
                finalized["committed_at"] = utc_now_iso()
                filled, _ = self._receipts.compare_and_set(
                    scoped_key, reservation, finalized, conn=conn,
                )
                if not filled:
                    raise RegistryConcurrentUpdateError(registry_id)
        return RegistryEntry.from_dict(canonical), False

    def update_deployment_summary(
        self,
        registry_id: str,
        *,
        current_stage: DeploymentStage,
        deployment_plan_id: Optional[str] = None,
        runtime_binding_id: Optional[str] = None,
        actor: Optional[dict[str, Any]] = None,
    ) -> Optional[RegistryEntry]:
        raw = self._entries.get(registry_id)
        if raw is None:
            return None
        entry = RegistryEntry.from_dict(raw)
        entry.deployment_summary = DeploymentSummary(
            current_stage=current_stage,
            deployment_plan_id=deployment_plan_id,
            runtime_binding_id=runtime_binding_id,
            last_transition_at=utc_now_iso(),
        )
        try:
            return self.update(entry, expected=raw, actor=actor)
        except RegistryConcurrentUpdateError:
            # A concurrent projection update lost a race; report the current
            # durable entry rather than silently dropping the caller's write.
            raise

    # -- Read operations ------------------------------------------------

    def get(self, registry_id: str) -> Optional[RegistryEntry]:
        raw = self._entries.get(registry_id)
        return RegistryEntry.from_dict(raw) if raw is not None else None

    def list_by_strategy(self, strategy_id: str) -> list[RegistryEntry]:
        return [
            RegistryEntry.from_dict(raw)
            for raw in self._entries.list_all()
            if raw.get("strategy_id") == strategy_id
        ]

    def resolve_latest_approved(self, strategy_id: str) -> Optional[RegistryEntry]:
        entries = self.list_by_strategy(strategy_id)
        approved = [e for e in entries if e.artifact_state.value == "approved"]
        if not approved:
            return None

        def _parse_ver(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))

        approved.sort(key=lambda e: _parse_ver(e.version), reverse=True)
        return approved[0]

    def resolve_deployment_view(self, strategy_id: str) -> DeploymentView:
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


def _registry_backend() -> str:
    return os.getenv("REGISTRY_STORE_BACKEND", "memory").strip().lower()


def _registry_dsn() -> str:
    dsn = os.getenv("REGISTRY_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("REGISTRY_STORE_DSN or DATABASE_URL is required for Postgres registry store")
    return dsn


def _registry_bootstrap() -> bool:
    return os.getenv("REGISTRY_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")


def build_postgres_registry_store() -> PostgresRegistryStore:
    """Construct the production Postgres registry store from environment config.

    Raises with an explicit configuration error rather than silently falling
    back to an in-memory store when Postgres is selected but unreachable/
    unconfigured — memory is a test double injected directly by tests, never
    a missing-config fallback for the selected production backend.
    """
    return PostgresRegistryStore(
        dsn=_registry_dsn(),
        entries_table=os.getenv("REGISTRY_ENTRIES_TABLE", "registry.entries"),
        receipts_table=os.getenv("REGISTRY_RECEIPTS_TABLE", "registry.command_receipts"),
        bootstrap=_registry_bootstrap(),
    )
