"""Typed write port for Strategy specification and aggregate records.

Matches DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md and SD §5.1.
Eliminates fake write methods on ReadSurfacePorts.
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Optional, Protocol, Union, runtime_checkable
import logging

log = logging.getLogger(__name__)


@runtime_checkable
class StrategyWriteOwnerPort(Protocol):
    """The canonical BFF-side write port for durable strategy mutations."""

    def upsert_strategy(self, strategy: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert strategy record in authoritative storage."""
        ...

    def create_strategy_spec(self, strategy: Dict[str, Any]) -> Dict[str, Any]:
        """Create strategy spec record in authoritative storage."""
        ...

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Get strategy record if available."""
        ...


class CanonicalStrategyWriteOwner:
    """Production and in-memory strategy write owner.

    Persists strategy specs to the canonical durable Strategy store (RegistryStore /
    PostgresRegistryStore), ensuring that create and patch operations are durably
    written to the authoritative domain store without local caches or private
    read-surface projection writes.
    """

    def __init__(self, store: Optional[Union[Any, Callable[[], Any]]] = None) -> None:
        self._store = store

    def _resolve_target(self) -> Any:
        target = self._store() if callable(self._store) else self._store
        if target is None:
            from services.registry.storage import get_store
            target = get_store()
        return target

    def upsert_strategy(self, strategy: Dict[str, Any]) -> Dict[str, Any]:
        sid = str(strategy.get("id") or strategy.get("strategy_id") or "").strip()
        if not sid:
            raise ValueError("Strategy record missing strategy_id / id")
        raw_state_check = str(strategy.get("lifecycle_state") or strategy.get("state") or strategy.get("status") or strategy.get("artifact_state") or "").lower()
        if raw_state_check == "approved" and not strategy.get("approval_decision_id"):
            raise ValueError(
                "Direct transition to 'approved' state is forbidden without a verified "
                "governance approval_decision_id. Strategies must be reviewed and advanced "
                "through the governed state machine."
            )
        record = dict(strategy)
        if "strategy_id" not in record:
            record["strategy_id"] = sid
        if "id" not in record:
            record["id"] = sid
        if "title" not in record and "name" in record:
            record["title"] = record["name"]
        if "name" not in record and "title" in record:
            record["name"] = record["title"]
        if "versions" not in record:
            record["versions"] = [
                {
                    "spec_version_id": "v1",
                    "spec_version": "v1",
                    "title": record.get("title") or record.get("name"),
                    "name": record.get("name") or record.get("title"),
                    "risk": record.get("risk"),
                    "lifecycle_state": record.get("lifecycle_state") or record.get("status") or record.get("state") or "draft",
                    "persona_ids": [record["persona_id"]] if record.get("persona_id") else (record.get("personaIds") or []),
                }
            ]
            record["current_spec_version_id"] = "v1"

        target = self._resolve_target()
        if target is None:
            raise RuntimeError("Canonical strategy store target is unavailable")

        # Canonical RegistryStore / PostgresRegistryStore
        if hasattr(target, "create_if_absent") and hasattr(target, "list_by_strategy"):
            import re
            from services.registry.models import ArtifactState, ArtifactType, Lineage, RegistryEntryCreate
            raw_state = str(record.get("lifecycle_state") or record.get("state") or record.get("status") or "draft").lower()
            try:
                art_state = ArtifactState(raw_state)
            except ValueError:
                art_state = ArtifactState.DRAFT

            from fastapi import HTTPException
            from services.runtime_auth_inbound import AuthContext
            from services.registry.service import _authorize_write
            from services.registry.split_api import RegistryService, BUILTIN_TENANT

            # The transport must supply its verified principal. Product fields
            # such as owner/tenant_id never grant authority to write.
            actor = record.get("actor")
            if not isinstance(actor, dict) or not all(
                str(actor.get(key) or "").strip()
                for key in ("actor_id", "tenant", "token_kind")
            ) or not isinstance(actor.get("roles"), (list, tuple, set, frozenset)):
                raise HTTPException(403, "Verified strategy write principal required")
            actor = dict(actor)
            actor["tenant"] = str(actor["tenant"]).strip()
            if actor["tenant"] == BUILTIN_TENANT or not set(actor["roles"]).intersection(
                {"operator", "registry-writer", "admin"}
            ):
                raise HTTPException(403, "Registry write role and caller tenant required")
            for tenant_key in ("tenant_id", "tenantId"):
                if record.get(tenant_key) and str(record[tenant_key]).strip() != actor["tenant"]:
                    raise HTTPException(403, "Strategy tenant differs from verified principal")
            auth = AuthContext(
                actor_id=actor["actor_id"], roles=frozenset(actor["roles"]),
                claims={"tenant": actor["tenant"]}, token_kind=actor["token_kind"],
            )
            reg_service = RegistryService(target)

            def authorize(entry: Any) -> None:
                _authorize_write(auth, reg_service.get(entry.registry_id))

            def update_entry(entry: Any) -> None:
                # Recheck after create_if_absent too: a concurrent creator may
                # have reserved this identity for another tenant.
                authorize(entry)
                if entry.artifact_state != art_state:
                    raise ValueError("Lifecycle changes require the governed state transition command")
                command_key = record.get("command_key") or record.get("idempotency_key")
                if command_key:
                    # The canonical owner's committed receipt carries
                    # committed_entry (the entry as it was actually written),
                    # never an "expected_metadata" precondition field — that
                    # field only ever existed on the retired in-memory-only
                    # migration fixture and must not be read here. Detect a
                    # replay of this exact command_key against the frozen
                    # committed_entry so a later, unrelated mutation under a
                    # different command_key cannot change the answer.
                    receipt = reg_service.get_command_receipt(
                        entry.registry_id, command_key, actor=actor,
                    )
                    if receipt is not None:
                        committed_metadata = dict(
                            (receipt.get("committed_entry") or {}).get("metadata") or {}
                        )
                        probe = dict(committed_metadata)
                        probe.update(record)
                        if probe != committed_metadata:
                            from services.registry.split_api import RegistryConflictError
                            raise RegistryConflictError(
                                f"command_key={command_key!r} was already committed with "
                                "different metadata for this strategy"
                            )
                        return
                # First application of this command (or no idempotency key at
                # all): the caller-bound CAS precondition is the entry's
                # current durable metadata, read once here.
                expected_metadata = entry.metadata
                merged_meta = dict(expected_metadata or {})
                merged_meta.update(record)
                reg_service.update_metadata(
                    entry.registry_id, expected_metadata=expected_metadata,
                    new_metadata=merged_meta, actor=actor,
                    command_key=command_key,
                )

            def _next_ver(ver: str) -> str:
                if re.match(r"^\d+\.\d+\.\d+$", str(ver)):
                    parts = [int(p) for p in str(ver).split(".")]
                    parts[-1] += 1
                    return ".".join(str(p) for p in parts)
                return "1.0.1"

            # Strictly filter for STRATEGY_SPEC artifacts — never clobber evaluation_result or other artifacts!
            strategy_spec_entries = [
                e for e in target.list_by_strategy(sid)
                if getattr(e, "artifact_type", None) in (
                    ArtifactType.STRATEGY_SPEC,
                    ArtifactType.STRATEGY_SPEC.value,
                    "strategy_spec",
                )
            ]

            for existing in strategy_spec_entries:
                authorize(existing)

            if art_state == ArtifactState.APPROVED:
                from services.registry.service import RegistryService
                reg_service = RegistryService(target)
                approval_decision_id = str(record["approval_decision_id"]).strip()
                command_key = str(record.get("command_key") or record.get("idempotency_key") or f"cmd-approve-{sid}").strip()
                if not strategy_spec_entries:
                    raise ValueError(f"Cannot approve strategy {sid}: no existing strategy spec found to advance.")
                entry = strategy_spec_entries[-1]
                if getattr(entry, "artifact_state", None) in (ArtifactState.DRAFT, "draft"):
                    cand_cmd_key = f"cmd-cand-{sid}-{entry.version}"
                    reg_service.advance_artifact_state(
                        entry.registry_id,
                        ArtifactState.CANDIDATE,
                        command_key=cand_cmd_key,
                        actor=actor,
                        expected_artifact_state=ArtifactState.DRAFT,
                        expected_version=entry.version,
                        expected_updated_at=entry.updated_at,
                    )
                    entry = target.get(entry.registry_id)
                reg_service.advance_artifact_state(
                    entry.registry_id,
                    ArtifactState.APPROVED,
                    approval_decision_id=approval_decision_id,
                    command_key=command_key,
                    actor=actor,
                    expected_artifact_state=entry.artifact_state,
                    expected_version=entry.version,
                    expected_updated_at=entry.updated_at,
                )
                return record

            if strategy_spec_entries:
                draft_entries = [
                    e for e in strategy_spec_entries
                    if getattr(e, "artifact_state", None) in (
                        ArtifactState.DRAFT,
                        ArtifactState.CANDIDATE,
                        "draft",
                        "candidate",
                    )
                ]
                if draft_entries:
                    entry = draft_entries[-1]
                    update_entry(entry)
                    return record
                else:
                    # All existing strategy specs are approved/retired: create a new revision rather than clobbering approved state
                    latest_entry = strategy_spec_entries[-1]
                    target_ver = record.get("current_spec_version") or record.get("version")
                    if not target_ver or any(getattr(e, "version", None) == target_ver for e in strategy_spec_entries):
                        target_ver = _next_ver(getattr(latest_entry, "version", "1.0.0"))
                    payload = RegistryEntryCreate(
                        artifact_type=ArtifactType.STRATEGY_SPEC,
                        strategy_id=sid,
                        version=target_ver,
                        artifact_state=art_state,
                        metadata=dict(record),
                        lineage=Lineage(source_strategy_spec_id=latest_entry.registry_id),
                    )
                    reg_id = f"reg-strategy-spec-{sid}-{target_ver}"
                    entry, created = target.create_if_absent(payload, reg_id, actor=actor)
                    if not created:
                        update_entry(entry)
                    return record
            else:
                target_ver = record.get("current_spec_version") or record.get("version") or "1.0.0"
                payload = RegistryEntryCreate(
                    artifact_type=ArtifactType.STRATEGY_SPEC,
                    strategy_id=sid,
                    version=target_ver,
                    artifact_state=art_state,
                    metadata=dict(record),
                )
                reg_id = f"reg-strategy-spec-{sid}-{target_ver}"
                entry, created = target.create_if_absent(payload, reg_id, actor=actor)
                if not created:
                    update_entry(entry)
                return record

        # Direct domain store methods
        if hasattr(target, "upsert_strategy"):
            res = target.upsert_strategy(record)
            if res is False:
                raise RuntimeError("Target store rejected strategy upsert")
            return res if isinstance(res, dict) else record
        if hasattr(target, "create_strategy_spec"):
            res = target.create_strategy_spec(record)
            if res is False:
                raise RuntimeError("Target store rejected strategy spec creation")
            return res if isinstance(res, dict) else record
        if hasattr(target, "save"):
            res = target.save(record)
            if res is False:
                raise RuntimeError("Target store rejected strategy save")
            return res if isinstance(res, dict) else record
        if hasattr(target, "insert"):
            res = target.insert(record)
            if res is False:
                raise RuntimeError("Target store rejected strategy insert")
            return res if isinstance(res, dict) else record
        if isinstance(target, dict):
            target[sid] = record
            return record

        raise RuntimeError(f"Target store {type(target)} does not implement strategy persistence")

    def create_strategy_spec(self, strategy: Dict[str, Any]) -> Dict[str, Any]:
        return self.upsert_strategy(strategy)

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        target = self._resolve_target()
        if target is None:
            return None
        if hasattr(target, "list_by_strategy"):
            from services.registry.models import ArtifactType
            entries = [
                e for e in target.list_by_strategy(strategy_id)
                if getattr(e, "artifact_type", None) in (
                    ArtifactType.STRATEGY_SPEC,
                    ArtifactType.STRATEGY_SPEC.value,
                    "strategy_spec",
                )
            ]
            if entries:
                entry = entries[-1]
                if isinstance(entry.metadata, dict) and entry.metadata:
                    return dict(entry.metadata)
                return entry.to_dict()
        if hasattr(target, "get_strategy"):
            return target.get_strategy(strategy_id)
        if isinstance(target, dict):
            return target.get(strategy_id)
        if hasattr(target, "get"):
            item = target.get(strategy_id)
            if isinstance(item, dict):
                return item
            if hasattr(item, "metadata") and isinstance(item.metadata, dict):
                return dict(item.metadata)
            if hasattr(item, "to_dict"):
                return item.to_dict()
            return None
        return None


def create_strategy_write_owner(store: Optional[Any] = None) -> StrategyWriteOwnerPort:
    """Build a StrategyWriteOwnerPort instance."""
    return CanonicalStrategyWriteOwner(store=store)


__all__ = [
    "StrategyWriteOwnerPort",
    "CanonicalStrategyWriteOwner",
    "create_strategy_write_owner",
]
