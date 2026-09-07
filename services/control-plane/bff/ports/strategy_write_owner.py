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
            from services.registry.models import ArtifactState, ArtifactType, RegistryEntryCreate
            raw_state = str(record.get("lifecycle_state") or record.get("state") or "draft").lower()
            try:
                art_state = ArtifactState(raw_state)
            except ValueError:
                art_state = ArtifactState.DRAFT
            existing_entries = target.list_by_strategy(sid)
            if existing_entries:
                entry = existing_entries[-1]
                entry.artifact_state = art_state
                entry.metadata = dict(record)
                if hasattr(target, "update"):
                    target.update(entry)
                return record
            else:
                payload = RegistryEntryCreate(
                    artifact_type=ArtifactType.STRATEGY_SPEC,
                    strategy_id=sid,
                    version="1.0.0",
                    artifact_state=art_state,
                    metadata=dict(record),
                )
                reg_id = f"reg-{sid}"
                entry, created = target.create_if_absent(payload, reg_id)
                if not created and hasattr(target, "update"):
                    entry.artifact_state = art_state
                    entry.metadata = dict(record)
                    target.update(entry)
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
            entries = target.list_by_strategy(strategy_id)
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
