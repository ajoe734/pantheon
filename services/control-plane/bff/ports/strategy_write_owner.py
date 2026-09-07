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

    Persists strategy specs to the underlying strategy store or dictionary,
    ensuring that create and patch operations are durably written and
    immediately readable by canonical read surface ports.
    """

    def __init__(self, store: Optional[Union[Any, Callable[[], Any]]] = None) -> None:
        self._store = store if store is not None else {}
        self._records: Dict[str, Dict[str, Any]] = {}

    def _resolve_target(self) -> Any:
        target = self._store() if callable(self._store) else self._store
        if target is None:
            main_mod = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
            if main_mod is not None and hasattr(main_mod, "read_store"):
                target = main_mod.read_store
        if hasattr(target, "research_knowledge_source"):
            rks = getattr(target, "research_knowledge_source", None)
            if rks is not None and hasattr(rks, "_strategy_specs"):
                return rks._strategy_specs
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
                    "lifecycle_state": record.get("lifecycle_state") or record.get("status") or "draft",
                    "persona_ids": [record["persona_id"]] if record.get("persona_id") else [],
                }
            ]
            record["current_spec_version_id"] = "v1"

        self._records[sid] = dict(record)
        target = self._resolve_target()
        if isinstance(target, dict):
            target[sid] = record
        elif hasattr(target, "upsert_strategy"):
            return target.upsert_strategy(record)
        elif hasattr(target, "create_strategy_spec"):
            return target.create_strategy_spec(record)
        elif hasattr(target, "save"):
            target.save(record)
        elif hasattr(target, "insert"):
            target.insert(record)
        return record

    def create_strategy_spec(self, strategy: Dict[str, Any]) -> Dict[str, Any]:
        return self.upsert_strategy(strategy)

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        if strategy_id in self._records:
            return dict(self._records[strategy_id])
        target = self._resolve_target()
        if isinstance(target, dict):
            return target.get(strategy_id)
        if hasattr(target, "get_strategy"):
            return target.get_strategy(strategy_id)
        if hasattr(target, "get"):
            return target.get(strategy_id)
        return None


def create_strategy_write_owner(store: Optional[Any] = None) -> StrategyWriteOwnerPort:
    """Build a StrategyWriteOwnerPort instance."""
    return CanonicalStrategyWriteOwner(store=store)


__all__ = [
    "StrategyWriteOwnerPort",
    "CanonicalStrategyWriteOwner",
    "create_strategy_write_owner",
]
