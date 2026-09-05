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
    """Adapts the durable Decision Journal owner to Agora's read-store shape.

    Wraps an existing read-store object (any ``ReadSurfacePorts`` instance or
    compatible double) and proxies every attribute lookup to it unchanged,
    except for the three Decision Journal methods this adapter itself
    implements against the canonical governance owner:

    - ``list_decision_journal_entries``
    - ``create_decision_journal_entry``
    - ``patch_decision_journal_entry``
    """

    def __init__(self, inner: Any, stores: DecisionJournalStores) -> None:
        self._inner = inner
        self._stores = stores

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def list_decision_journal_entries(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return list_entries(self._stores)

    def create_decision_journal_entry(
        self,
        *,
        title: str,
        body: str,
        actor_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        created_at: str,
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        payload = payload or {}
        entry_id = str(
            payload.get("id") or payload.get("entryId") or f"dje-{uuid.uuid4().hex[:10]}"
        )
        return create_entry(
            self._stores,
            entry_id=entry_id,
            title=title,
            body=body,
            actor_id=actor_id or "",
            created_at=created_at,
            tags=payload.get("tags"),
            linked_strategy_ids=payload.get("linkedStrategyIds"),
            linked_persona_ids=payload.get("linkedPersonaIds"),
            visibility=str(payload.get("visibility") or "private"),
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
        )


def build_decision_journal_owner_adapter(
    inner: Any, *, data_dir: Optional[str] = None
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
