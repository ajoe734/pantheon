"""Typed adapter binding Agora's Decision Journal routes to the durable owner.

JOURNAL-OWNER-001 selected ``services.governance.decision_journal`` as the
single durable Decision Journal owner (transactional compare-and-set,
append-only audit, idempotent patch, JSON/Postgres restart-safe backend). The
BFF's shared ``ReadSurfacePorts`` deliberately does not expose
``create_decision_journal_entry``/``patch_decision_journal_entry`` -- see
``RETAINED_WRITES_DEFERRED_FROM_READ_SURFACE`` in
``tests/test_read_surface_caller_migration.py`` -- because canonical journal
write ownership lives outside the BFF.

This module supplies the missing capability as a narrow, typed adapter
composed only at the Agora router boundary (``agora/router.py``). It does not
add a second store: every read, create, and patch call is delegated straight
through to :mod:`services.governance.decision_journal`, which owns the only
schema and the only write path.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.governance.decision_journal import (
    DecisionJournalStores,
    build_decision_journal_stores,
    create_entry,
    get_entry,
    list_entries,
    patch_entry,
)


def resolve_decision_journal_data_dir() -> str:
    """Resolve the durable Decision Journal data directory.

    Follows the same direct-store convention already used by other BFF-side
    consumers of governance-owned durable state (see ``services/capital`` and
    ``services/deployment``): a domain-specific override first, then the
    shared governance data directory, then a dev-only fallback.
    """

    return (
        os.getenv("PANTHEON_DECISION_JOURNAL_DATA_DIR")
        or os.getenv("PANTHEON_GOVERNANCE_DATA_DIR")
        or os.getenv("GOVERNANCE_DATA_DIR")
        or "/tmp/pantheon/governance"
    )


class DecisionJournalOwnerAdapter:
    """Typed adapter binding Decision Journal routes to the durable owner.

    Provides explicit, typed query and command operations against the canonical
    governance Decision Journal owner without arbitrary dynamic proxying (__getattr__).
    """

    def __init__(self, inner: Optional[Any] = None, stores: Optional[DecisionJournalStores] = None) -> None:
        self._inner = inner
        self._stores = stores or build_decision_journal_stores(resolve_decision_journal_data_dir())

    @property
    def stores(self) -> DecisionJournalStores:
        return self._stores

    def list_decision_journal_entries(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        include_unscoped_legacy: bool = False,
        **_kwargs: Any,
    ) -> List[Dict[str, Any]]:
        return list_entries(
            self._stores,
            tenant_id=tenant_id,
            user_id=user_id,
            include_unscoped_legacy=include_unscoped_legacy,
        )

    def get_decision_journal_entry(
        self,
        entry_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        return get_entry(
            self._stores,
            entry_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    def create_decision_journal_entry(
        self,
        *,
        title: str,
        body: str,
        entry_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        created_at: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        payload = payload or {}
        resolved_entry_id = str(
            entry_id
            or _kwargs.get("entry_id")
            or payload.get("id")
            or payload.get("entryId")
            or f"dje-{uuid.uuid4().hex[:10]}"
        )
        resolved_tenant = tenant_id or payload.get("tenant_id") or payload.get("tenantId")
        resolved_user = user_id or payload.get("user_id") or payload.get("userId") or actor_id or ""
        return create_entry(
            self._stores,
            entry_id=resolved_entry_id,
            title=title,
            body=body,
            actor_id=actor_id or "",
            created_at=created_at,
            tags=payload.get("tags"),
            linked_strategy_ids=payload.get("linkedStrategyIds"),
            linked_persona_ids=payload.get("linkedPersonaIds"),
            visibility=str(payload.get("visibility") or "private"),
            tenant_id=resolved_tenant,
            user_id=resolved_user,
        )

    def patch_decision_journal_entry(
        self,
        entry_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        idempotency_key: str,
        request_hash: str,
        patched_at: str,
        correlation_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        return patch_entry(
            self._stores,
            entry_id,
            patch=patch,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            patched_at=patched_at,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    def check_create_idempotency(
        self,
        *,
        scoped_key: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        record = self._stores.idempotency.get(scoped_key)
        if record is None:
            return None
        if record.get("request_hash") != request_hash:
            return {"conflict": True, "record": record}
        return {"conflict": False, "result": record.get("result")}

    def record_create_idempotency(
        self,
        *,
        scoped_key: str,
        raw_key: str,
        tenant_id: str,
        user_id: str,
        request_hash: str,
        result: Dict[str, Any],
        created_at: str,
    ) -> None:
        self._stores.idempotency.put({
            "idempotency_key": scoped_key,
            "raw_idempotency_key": raw_key,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "actor_id": user_id,
            "request_hash": request_hash,
            "status": "succeeded",
            "result": result,
            "created_at": created_at,
        })


DecisionJournalWriteOwner = DecisionJournalOwnerAdapter


def build_decision_journal_write_owner(
    *, data_dir: Optional[str] = None
) -> DecisionJournalWriteOwner:
    stores = build_decision_journal_stores(data_dir or resolve_decision_journal_data_dir())
    return DecisionJournalWriteOwner(None, stores)


def build_decision_journal_owner_adapter(
    inner: Optional[Any] = None, *, data_dir: Optional[str] = None
) -> DecisionJournalOwnerAdapter:
    stores = build_decision_journal_stores(data_dir or resolve_decision_journal_data_dir())
    return DecisionJournalOwnerAdapter(inner, stores)


def wrap_get_read_store_with_decision_journal_owner(
    get_read_store: Callable[[], Any], *, data_dir: Optional[str] = None
) -> Callable[[], Any]:
    """Return a ``get_read_store`` callable augmented with the journal owner.

    The durable stores are built once (module-scoped closure) so every call
    shares the same owner posture a real process restart would see: a fresh
    adapter still reads and writes through the same on-disk/Postgres owner
    store, never a process-local dict.
    """

    stores = build_decision_journal_stores(data_dir or resolve_decision_journal_data_dir())

    def _wrapped() -> Any:
        inner = get_read_store()
        if inner is None:
            return None
        return DecisionJournalOwnerAdapter(inner, stores)

    return _wrapped
