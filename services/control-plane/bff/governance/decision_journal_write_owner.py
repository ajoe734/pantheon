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
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.governance.decision_journal import (
    DecisionJournalStores,
    build_decision_journal_stores,
    create_entry,
    domain_creation_idempotency_key,
    get_entry,
    list_entries,
    patch_entry,
    _project,
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
        actor_id: Optional[str] = None,
        include_unscoped_legacy: bool = False,
        **_kwargs: Any,
    ) -> List[Dict[str, Any]]:
        resolved_actor = actor_id or _kwargs.get("actor_id")
        resolved_user = user_id or _kwargs.get("user_id") or resolved_actor
        return list_entries(
            self._stores,
            tenant_id=tenant_id,
            actor_id=resolved_actor,
            user_id=resolved_user,
            include_unscoped_legacy=include_unscoped_legacy,
        )

    def get_decision_journal_entry(
        self,
        entry_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        include_unscoped_legacy: bool = False,
        **_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        resolved_actor = actor_id or _kwargs.get("actor_id")
        resolved_user = user_id or _kwargs.get("user_id") or resolved_actor
        return get_entry(
            self._stores,
            entry_id,
            tenant_id=tenant_id,
            actor_id=resolved_actor,
            user_id=resolved_user,
            include_unscoped_legacy=include_unscoped_legacy,
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
            category=payload.get("category"),
            context_refs=payload.get("contextRefs") or payload.get("context_refs"),
            version=payload.get("version"),
            updated_at=payload.get("updatedAt") or payload.get("updated_at"),
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

    def _recover_committed_entry_result(
        self,
        record: Dict[str, Any],
        entry_id: Optional[str] = None,
        raw_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not hasattr(self, "_stores") or self._stores is None or not hasattr(self._stores, "entries"):
            return None

        # 1. Never recover while a bundle write lock is actively held
        if hasattr(self._stores, "is_bundle_locked") and self._stores.is_bundle_locked():
            return None

        target_id = entry_id or record.get("entry_id")
        if not target_id:
            all_entries = self._stores.entries.list_all()
            rec_tenant = str(record.get("tenant_id") or "").strip()
            rec_user = str(record.get("user_id") or record.get("actor_id") or "").strip()
            for ent in all_entries:
                if not isinstance(ent, dict):
                    continue
                e_tenant = str(ent.get("tenant_id") or ent.get("tenantId") or "").strip()
                e_user = str(ent.get("userId") or ent.get("user_id") or ent.get("createdBy") or "").strip()
                if (not rec_tenant or e_tenant == rec_tenant) and (not rec_user or e_user == rec_user):
                    target_id = ent.get("id")
                    break

        if not target_id:
            return None

        clean_target_id = str(target_id).strip()
        persisted = self._stores.entries.get(clean_target_id)
        if persisted is None or not isinstance(persisted, dict):
            return None

        # 2. Never promote a live provisional row: uncommitted creation outbox must be None
        if persisted.get("_creation_outbox") is not None:
            return None

        # 3. Tenant and actor/user scoping match
        rec_tenant = str(record.get("tenant_id") or "").strip()
        rec_user = str(record.get("user_id") or record.get("actor_id") or "").strip()
        p_tenant = str(persisted.get("tenant_id") or persisted.get("tenantId") or "").strip()
        p_user = str(persisted.get("userId") or persisted.get("user_id") or persisted.get("createdBy") or "").strip()
        p_actor = str(persisted.get("createdBy") or persisted.get("actor_id") or p_user).strip()

        req_tenant = str(tenant_id or "").strip()
        req_user = str(user_id or "").strip()

        if rec_tenant and p_tenant and rec_tenant != p_tenant:
            return None
        if rec_user and p_user and rec_user != p_user and rec_user != p_actor:
            return None
        if req_tenant and rec_tenant and req_tenant != rec_tenant:
            return None
        if req_user and rec_user and req_user != rec_user:
            return None
        if req_tenant and p_tenant and req_tenant != p_tenant:
            return None
        if req_user and p_user and req_user != p_user and req_user != p_actor:
            return None

        # 4. Scoped get_entry must succeed (fail-closed tenant/visibility/isolation check)
        scoped_entry = get_entry(
            self._stores,
            clean_target_id,
            tenant_id=req_tenant or rec_tenant or p_tenant,
            actor_id=req_user or rec_user or p_actor,
            user_id=req_user or rec_user or p_user,
        )
        if scoped_entry is None:
            return None

        # 5. Verify creation transaction commitment in journal idempotency store
        effective_tenant = rec_tenant or p_tenant
        effective_actor = p_actor or rec_user
        create_idem_key = domain_creation_idempotency_key(
            tenant_id=effective_tenant,
            actor_id=effective_actor,
            entry_id=clean_target_id,
        )
        if self._stores.idempotency is not None:
            idem_rec = self._stores.idempotency.get(create_idem_key)
            if idem_rec is None or idem_rec.get("status") != "succeeded":
                return None

        # 6. Verify successful secondary writes (creation event in outbox if outbox configured)
        if self._stores.outbox is not None:
            outbox_events = self._stores.outbox.list_all()
            has_created_event = any(
                isinstance(e, dict)
                and e.get("event_type") == "decision_journal.entry.created"
                and str(e.get("aggregate_id") or (e.get("data") or {}).get("id") or "").strip() == clean_target_id
                for e in outbox_events
            )
            if not has_created_event:
                return None

        resolved_raw_key = raw_key or record.get("raw_idempotency_key") or ""
        reconstructed = {
            "data": dict(scoped_entry),
            "meta": {
                "snapshot_at": str(scoped_entry.get("createdAt") or scoped_entry.get("updatedAt") or ""),
                "idempotency": {"idempotencyKey": resolved_raw_key, "replayed": True},
                "surfaces": {"agora_journal_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        record_succeeded = {
            **record,
            "status": "succeeded",
            "result": reconstructed,
            "entry_id": clean_target_id,
        }
        if self._stores.idempotency is not None:
            self._stores.idempotency.put(record_succeeded)
        return reconstructed

    def check_create_idempotency(
        self,
        *,
        scoped_key: str,
        request_hash: str,
        entry_id: Optional[str] = None,
        raw_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not hasattr(self, "_stores") or self._stores is None or not hasattr(self._stores, "idempotency"):
            return None
        reservation = {
            "idempotency_key": scoped_key,
            "raw_idempotency_key": raw_key,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "actor_id": user_id,
            "request_hash": request_hash,
            "entry_id": entry_id,
            "status": "pending",
            "created_pid": os.getpid(),
            "created_at": time.time(),
            "result": None,
        }
        reserved, existing = self._stores.idempotency.insert_if_absent(reservation)
        if reserved:
            return None

        # Scope validation on existing record
        rec_tenant = str(existing.get("tenant_id") or "").strip()
        rec_user = str(existing.get("user_id") or existing.get("actor_id") or "").strip()
        rec_entry = str(existing.get("entry_id") or "").strip()
        req_tenant = str(tenant_id or "").strip()
        req_user = str(user_id or "").strip()
        req_entry = str(entry_id or "").strip()

        if req_tenant and rec_tenant and req_tenant != rec_tenant:
            return {"conflict": True, "record": existing, "reason": "cross_tenant_scope_mismatch"}
        if req_user and rec_user and req_user != rec_user:
            return {"conflict": True, "record": existing, "reason": "cross_user_scope_mismatch"}
        if req_entry and rec_entry and req_entry != rec_entry:
            return {"conflict": True, "record": existing, "reason": "entry_id_scope_mismatch"}

        if existing.get("request_hash") != request_hash:
            return {"conflict": True, "record": existing}

        status = str(existing.get("status") or "")
        if status == "pending":
            created_pid = existing.get("created_pid")
            is_dead = False
            if created_pid and created_pid != os.getpid():
                try:
                    os.kill(created_pid, 0)
                except ProcessLookupError:
                    is_dead = True
                except PermissionError:
                    pass

            if is_dead:
                target_id = entry_id or existing.get("entry_id")
                if target_id:
                    recovered = self._recover_committed_entry_result(
                        existing,
                        entry_id=target_id,
                        raw_key=raw_key,
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    if recovered is not None:
                        return {"conflict": False, "result": recovered}
                # Previous creator crashed before durable entry was committed: reclaim reservation
                self._stores.idempotency.put(reservation)
                return None

            return {"conflict": False, "pending": True, "scoped_key": scoped_key}

        if status == "failed":
            self._stores.idempotency.put(reservation)
            return None

        if status == "succeeded":
            result = existing.get("result")
            if isinstance(result, dict) and isinstance(result.get("data"), dict):
                d = result["data"]
                d_tenant = str(d.get("tenant_id") or d.get("tenantId") or "").strip()
                d_user = str(d.get("userId") or d.get("user_id") or d.get("createdBy") or "").strip()
                d_id = str(d.get("id") or d.get("entryId") or "").strip()
                if req_tenant and d_tenant and req_tenant != d_tenant:
                    return {"conflict": True, "record": existing, "reason": "cross_tenant_scope_mismatch"}
                if req_user and d_user and req_user != d_user:
                    return {"conflict": True, "record": existing, "reason": "cross_user_scope_mismatch"}
                if req_entry and d_id and req_entry != d_id:
                    return {"conflict": True, "record": existing, "reason": "entry_id_scope_mismatch"}
            return {"conflict": False, "result": result}

        return {"conflict": False, "result": existing.get("result")}

    def await_create_idempotency(
        self,
        *,
        scoped_key: str,
        request_hash: str,
        entry_id: Optional[str] = None,
        raw_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        req_tenant = str(tenant_id or "").strip()
        req_user = str(user_id or "").strip()
        req_entry = str(entry_id or "").strip()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self._stores.idempotency.get(scoped_key)
            if record is not None:
                rec_tenant = str(record.get("tenant_id") or "").strip()
                rec_user = str(record.get("user_id") or record.get("actor_id") or "").strip()
                rec_entry = str(record.get("entry_id") or "").strip()
                if req_tenant and rec_tenant and req_tenant != rec_tenant:
                    return {"conflict": True, "record": record, "reason": "cross_tenant_scope_mismatch"}
                if req_user and rec_user and req_user != rec_user:
                    return {"conflict": True, "record": record, "reason": "cross_user_scope_mismatch"}
                if req_entry and rec_entry and req_entry != rec_entry:
                    return {"conflict": True, "record": record, "reason": "entry_id_scope_mismatch"}

                if record.get("request_hash") != request_hash:
                    return {"conflict": True, "record": record}
                status = str(record.get("status") or "")
                if status == "succeeded":
                    res = record.get("result")
                    if isinstance(res, dict) and isinstance(res.get("data"), dict):
                        d = res["data"]
                        d_tenant = str(d.get("tenant_id") or d.get("tenantId") or "").strip()
                        d_user = str(d.get("userId") or d.get("user_id") or d.get("createdBy") or "").strip()
                        d_id = str(d.get("id") or d.get("entryId") or "").strip()
                        if req_tenant and d_tenant and req_tenant != d_tenant:
                            return {"conflict": True, "record": record, "reason": "cross_tenant_scope_mismatch"}
                        if req_user and d_user and req_user != d_user:
                            return {"conflict": True, "record": record, "reason": "cross_user_scope_mismatch"}
                        if req_entry and d_id and req_entry != d_id:
                            return {"conflict": True, "record": record, "reason": "entry_id_scope_mismatch"}
                    return {"conflict": False, "result": res}
                if status == "failed":
                    return {"conflict": False, "failed": True}

                created_pid = record.get("created_pid")
                if created_pid and created_pid != os.getpid():
                    try:
                        os.kill(created_pid, 0)
                    except ProcessLookupError:
                        target_id = entry_id or record.get("entry_id")
                        if target_id:
                            recovered = self._recover_committed_entry_result(
                                record,
                                entry_id=target_id,
                                raw_key=raw_key,
                                tenant_id=tenant_id,
                                user_id=user_id,
                            )
                            if recovered is not None:
                                return {"conflict": False, "result": recovered}
                        return {"conflict": False, "failed": True}
                    except PermissionError:
                        pass
            time.sleep(0.005)

        # Final deadline check
        record = self._stores.idempotency.get(scoped_key)
        if record is not None:
            rec_tenant = str(record.get("tenant_id") or "").strip()
            rec_user = str(record.get("user_id") or record.get("actor_id") or "").strip()
            rec_entry = str(record.get("entry_id") or "").strip()
            if (req_tenant and rec_tenant and req_tenant != rec_tenant) or \
               (req_user and rec_user and req_user != rec_user) or \
               (req_entry and rec_entry and req_entry != rec_entry):
                return {"conflict": True, "record": record, "reason": "scope_mismatch"}

            if str(record.get("status") or "") == "succeeded":
                res = record.get("result")
                if isinstance(res, dict) and isinstance(res.get("data"), dict):
                    d = res["data"]
                    d_tenant = str(d.get("tenant_id") or d.get("tenantId") or "").strip()
                    d_user = str(d.get("userId") or d.get("user_id") or d.get("createdBy") or "").strip()
                    d_id = str(d.get("id") or d.get("entryId") or "").strip()
                    if (req_tenant and d_tenant and req_tenant != d_tenant) or \
                       (req_user and d_user and req_user != d_user) or \
                       (req_entry and d_id and req_entry != d_id):
                        return {"conflict": True, "record": record, "reason": "scope_mismatch"}
                return {"conflict": False, "result": res}
            target_id = entry_id or record.get("entry_id")
            if target_id:
                recovered = self._recover_committed_entry_result(
                    record,
                    entry_id=target_id,
                    raw_key=raw_key,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                if recovered is not None:
                    return {"conflict": False, "result": recovered}

        return {"conflict": True, "error": "timed out waiting for concurrent idempotency"}

    def fail_create_idempotency(
        self,
        *,
        scoped_key: str,
        request_hash: str,
    ) -> None:
        if hasattr(self, "_stores") and self._stores is not None and hasattr(self._stores, "idempotency"):
            self._stores.idempotency.put({
                "idempotency_key": scoped_key,
                "request_hash": request_hash,
                "status": "failed",
                "result": None,
            })

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
        entry_id: Optional[str] = None,
    ) -> None:
        rec_entry_id = entry_id or (result.get("data") or {}).get("id")
        self._stores.idempotency.put({
            "idempotency_key": scoped_key,
            "raw_idempotency_key": raw_key,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "actor_id": user_id,
            "request_hash": request_hash,
            "entry_id": rec_entry_id,
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
