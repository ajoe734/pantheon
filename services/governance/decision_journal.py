"""Independent persistent write owner for Decision Journal entries.

Decision Journal entries never had a real owner. The BFF's ``read_store.py``
degraded path (see
``docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-008-agora-journal-merge-patch.md``)
persisted them to a ``bff_local_dev_store`` overlay while asserting
``canonicalWriteAuthority=agora_journal_service`` -- a claim nothing actually
backed. This module is that real owner.

It reuses the same durable-store posture the rest of the governance service
already uses for freeze orders and rollbacks
(:mod:`services.governance.record_store`): a JSON file on disk in dev, a
Postgres-owned table in staging/production. There is no in-memory dict, no
local overlay, and no BFF/``read_store`` import anywhere in this module --
every entry, idempotency record, audit event, and outbox event is written
through and read back from a real owner store.

SD §5.3 scorecard requirements satisfied:
1. Transactional compare-and-set writes and version tracking.
2. Append-only audit history with diff calculation.
3. Durable outbox/event streaming.
4. Tenant and user isolation across create, list, detail, patch, audit, and idempotency.
5. Rejection of caller-supplied ID collisions across actors/tenants (never returns
   another principal's private record on insert-if-absent).
6. Controlled handling and migration of unscoped legacy rows.
7. Fresh-process restart parity.
"""
from __future__ import annotations

import copy
import fcntl
import os
import time
import uuid
from pathlib import Path
import threading
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

from .record_store import (
    GovernanceRecordStore,
    JsonGovernanceRecordStore,
    _record_id,
    build_governance_record_store,
)


class _FileLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def __enter__(self) -> _FileLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
        finally:
            self._fd = None


_ReadResult = TypeVar("_ReadResult")


def _read_only_filesystem(path: Path) -> bool:
    """Inspect mount posture, not chmod bits (root can bypass the latter)."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return bool(os.statvfs(path).f_flag & os.ST_RDONLY)


def _read_under_shared_lock(lock_path: Path, read: Callable[[], _ReadResult]) -> _ReadResult:
    """Use the writer's exact persistent lock inode without creating a file.

    A pre-lock legacy/empty store may have no lock yet. Its single-file reads
    are atomic replacements; if a first writer creates the permanent lock
    during the read, discard that result and retry under that same lock.
    The bundle caller applies this rule across all four store snapshots.
    Locks must never be deleted/replaced while the owner is serving traffic.
    """
    deadline = time.monotonic() + 1.0
    for _ in range(4):
        try:
            fd = os.open(str(lock_path), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            result = read()
            if not lock_path.exists():
                return result
            continue
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise DecisionJournalConcurrencyError("journal readonly snapshot lock timeout")
                    time.sleep(0.005)
            before = os.fstat(fd)
            result = read()
            try:
                after = lock_path.stat()
            except FileNotFoundError:
                continue
            if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
                return result
        finally:
            os.close(fd)
    raise DecisionJournalConcurrencyError("journal readonly snapshot lock changed")


_HELD_BUNDLE_LOCKS = threading.local()


class _BundleFileLock:
    """Re-entrant cross-process and cross-thread bundle file lock."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path.resolve()

    def __enter__(self) -> _BundleFileLock:
        held = getattr(_HELD_BUNDLE_LOCKS, "held", None)
        if held is None:
            held = {}
            _HELD_BUNDLE_LOCKS.held = held
        key = str(self.lock_path)
        if key not in held:
            inner = _FileLock(self.lock_path)
            inner.__enter__()
            held[key] = [1, inner]
        else:
            held[key][0] += 1
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        held = getattr(_HELD_BUNDLE_LOCKS, "held", {})
        key = str(self.lock_path)
        if key in held:
            held[key][0] -= 1
            if held[key][0] <= 0:
                _, inner = held.pop(key)
                inner.__exit__(exc_type, exc_val, exc_tb)


class CoordinatingJsonGovernanceRecordStore(JsonGovernanceRecordStore):
    """Atomic file-locked and multi-instance coordinating JSON record store."""

    def __init__(self, storage_path: str | Path, *, id_fields: Sequence[str]) -> None:
        super().__init__(storage_path, id_fields=id_fields)
        self._flock_path = self.storage_path.with_name(f".{self.storage_path.name}.flock")

    def _file_lock(self) -> _FileLock:
        return _FileLock(self._flock_path)

    @property
    def read_only(self) -> bool:
        return _read_only_filesystem(self.storage_path.parent)

    def _read_snapshot(self, read: Callable[[], _ReadResult]) -> _ReadResult:
        def refreshed() -> _ReadResult:
            with self._lock:
                self._refresh()
                return read()
        return _read_under_shared_lock(self._flock_path, refreshed)

    def _refresh(self) -> None:
        if self.storage_path.exists():
            self._load()
        else:
            self._records = {}

    def get(self, record_id: str) -> Dict[str, Any] | None:
        if self.read_only:
            return self._read_snapshot(lambda: JsonGovernanceRecordStore.get(self, record_id))
        with self._file_lock(), self._lock:
            self._refresh()
            return super().get(record_id)

    def list_all(self) -> list[Dict[str, Any]]:
        if self.read_only:
            return self._read_snapshot(lambda: JsonGovernanceRecordStore.list_all(self))
        with self._file_lock(), self._lock:
            self._refresh()
            return super().list_all()

    def put(self, record: Dict[str, Any]) -> None:
        with self._file_lock(), self._lock:
            self._refresh()
            super().put(record)

    def insert_if_absent(
        self, record: Dict[str, Any]
    ) -> tuple[bool, Dict[str, Any]]:
        with self._file_lock(), self._lock:
            self._refresh()
            return super().insert_if_absent(record)

    def compare_and_set(
        self,
        expected_record: Dict[str, Any],
        record: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any] | None]:
        with self._file_lock(), self._lock:
            self._refresh()
            return super().compare_and_set(expected_record, record)

    def delete(self, record_id: str) -> bool:
        with self._file_lock(), self._lock:
            self._refresh()
            key = str(record_id)
            if key in self._records:
                del self._records[key]
                self._save()
                return True
            return False

    def delete_if_equals(
        self,
        record_id: str,
        expected_snapshot: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any] | None]:
        clean_id = str(record_id or "").strip()
        if not clean_id:
            return False, None
        with self._file_lock(), self._lock:
            self._refresh()
            if clean_id not in self._records:
                return False, None
            current = self._records[clean_id]
            if current != expected_snapshot:
                return False, copy.deepcopy(current)
            del self._records[clean_id]
            self._save()
            return True, None


def _delete_record(store: Any, record_id: str) -> bool:
    """Safely delete a record across json and postgres stores without requiring record_store changes."""
    if store is None:
        return False
    if hasattr(store, "delete") and callable(store.delete):
        try:
            return bool(store.delete(record_id))
        except Exception:
            pass
    # JsonGovernanceRecordStore compatibility
    if hasattr(store, "_records") and hasattr(store, "_save") and hasattr(store, "_lock"):
        with store._lock:
            if hasattr(store, "_refresh_if_needed") and callable(store._refresh_if_needed):
                store._refresh_if_needed()
            if str(record_id) in store._records:
                del store._records[str(record_id)]
                store._save()
                return True
            return False
    # PostgresGovernanceRecordStore compatibility
    if hasattr(store, "_records") and hasattr(store._records, "_use_conn"):
        try:
            with store._records._use_conn(None) as conn:
                query = f"DELETE FROM {store._records.table_name} WHERE record_id = %s"
                cursor = conn.execute(query, (str(record_id),))
                return bool(getattr(cursor, "rowcount", 0) > 0)
        except Exception:
            return False
    return False


def _delete_record_if_equals(
    store: Any,
    record_id: str,
    expected_snapshot: Dict[str, Any],
) -> tuple[bool, Dict[str, Any] | None]:
    """Safely delete record_id only if its stored state exactly equals expected_snapshot."""
    if store is None:
        return False, None
    clean_id = str(record_id or "").strip()
    if not clean_id:
        return False, None
    if hasattr(store, "delete_if_equals") and callable(store.delete_if_equals):
        try:
            return store.delete_if_equals(clean_id, expected_snapshot)
        except Exception:
            pass
    if hasattr(store, "_journal"):
        journal_store = store._journal
        if hasattr(journal_store, "delete_if_equals") and callable(journal_store.delete_if_equals):
            try:
                return journal_store.delete_if_equals(clean_id, expected_snapshot)
            except Exception:
                pass
        if isinstance(journal_store, dict):
            current = journal_store.get(clean_id)
            if current != expected_snapshot and not (
                isinstance(current, dict)
                and all(expected_snapshot.get(k) == v for k, v in current.items())
            ):
                return False, copy.deepcopy(current) if current is not None else None
            journal_store.pop(clean_id, None)
            return True, None
    if hasattr(store, "_records") and hasattr(store, "_save") and hasattr(store, "_lock"):
        with store._lock:
            if hasattr(store, "_refresh_if_needed") and callable(store._refresh_if_needed):
                store._refresh_if_needed()
            if clean_id not in store._records:
                return False, None
            current = store._records[clean_id]
            if current != expected_snapshot:
                return False, copy.deepcopy(current)
            del store._records[clean_id]
            store._save()
            return True, None
    return False, None

CANONICAL_WRITE_AUTHORITY = "governance-decision-journal-svc"

_ENTRY_ID_FIELDS: Sequence[str] = ("id", "entry_id")
_IDEMPOTENCY_ID_FIELDS: Sequence[str] = ("idempotency_key",)
_AUDIT_ID_FIELDS: Sequence[str] = ("audit_id",)
_OUTBOX_ID_FIELDS: Sequence[str] = ("event_id", "id")

_PATCHABLE_FIELDS: Sequence[str] = (
    "title",
    "body",
    "tags",
    "linkedStrategyIds",
    "linkedPersonaIds",
    "visibility",
    "category",
    "contextRefs",
)
_LIST_FIELDS = {"tags", "linkedStrategyIds", "linkedPersonaIds", "contextRefs"}

_MAX_CAS_ATTEMPTS = 8
_TITLE_MAX_LENGTH = 160
_BODY_MAX_LENGTH = 20000

_IDEM_STATUS_PENDING = "pending"
_IDEM_STATUS_SUCCEEDED = "succeeded"
_IDEM_STATUS_NOT_FOUND = "not_found"
_IDEM_STATUS_FAILED = "failed"
_IDEM_WAIT_ATTEMPTS = 2000
_IDEM_WAIT_SECONDS = 0.005


class DecisionJournalValidationError(ValueError):
    """Raised when a decision journal write violates the field contract."""


class DecisionJournalConcurrencyError(RuntimeError):
    """Raised when a patch could not commit after retrying compare-and-set."""


class DecisionJournalCollisionError(ValueError):
    """Raised when an entry ID collides with an existing record owned by another principal or tenant."""


class DecisionJournalAccessDeniedError(PermissionError):
    """Raised when accessing a private decision journal entry outside of authorized tenant/actor scope."""


def _persistence_mode() -> str:
    backend = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower()
    return "governance_postgres_store" if backend == "postgres" else "governance_json_store"


def _validate_title(title: Any) -> str:
    clean = str(title if title is not None else "").strip()
    if not clean or len(clean) > _TITLE_MAX_LENGTH:
        raise DecisionJournalValidationError(
            f"title must be 1-{_TITLE_MAX_LENGTH} characters"
        )
    return clean


def _validate_body(body: Any) -> str:
    text = str(body if body is not None else "")
    if len(text) > _BODY_MAX_LENGTH:
        raise DecisionJournalValidationError(f"body must be at most {_BODY_MAX_LENGTH} characters")
    return text


class DecisionJournalStores:
    """Bundle of the durable owner stores backing decision journal writes."""

    def __init__(
        self,
        *,
        entries: GovernanceRecordStore,
        idempotency: GovernanceRecordStore,
        audit: GovernanceRecordStore,
        outbox: Optional[GovernanceRecordStore] = None,
        data_dir: Optional[str | Path] = None,
    ) -> None:
        self.entries = entries
        self.idempotency = idempotency
        self.audit = audit
        self.outbox = outbox
        if data_dir is not None:
            self.data_dir = Path(data_dir)
        elif hasattr(entries, "storage_path"):
            self.data_dir = getattr(entries, "storage_path").parent
        else:
            self.data_dir = Path("/tmp/pantheon_dj_locks")
        self._bundle_flock_path = self.data_dir / ".decision_journal_bundle.flock"
        self._tx_lock = threading.RLock()
        self._frozen_read_snapshot = False

    def bundle_lock(self) -> _BundleFileLock:
        return _BundleFileLock(self._bundle_flock_path)

    def is_bundle_locked(self) -> bool:
        """Check if any thread or process is actively holding the bundle lock."""
        if self._frozen_read_snapshot:
            # Frozen read models must never coordinate/recover owner writes.
            return True
        held = getattr(_HELD_BUNDLE_LOCKS, "held", {})
        key = str(self._bundle_flock_path.resolve())
        if held.get(key, [0])[0] > 0:
            return True
        if not self._bundle_flock_path.exists():
            return False
        try:
            fd = os.open(str(self._bundle_flock_path), os.O_RDWR, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except (BlockingIOError, OSError):
                return True
            finally:
                os.close(fd)
        except OSError:
            return False


class _FrozenJournalRecords:
    """Request-local query snapshot, never another persistence authority."""

    def __init__(self, store: Any) -> None:
        self._rows = {
            _record_id(row, store.id_fields): copy.deepcopy(row)
            for row in store.list_all()
        }

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._rows.get(str(record_id)))

    def list_all(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(list(self._rows.values()))


def _requires_read_snapshot(stores: DecisionJournalStores) -> bool:
    return isinstance(stores.entries, CoordinatingJsonGovernanceRecordStore) and stores.entries.read_only


def _with_read_snapshot(stores: DecisionJournalStores, read: Callable[[DecisionJournalStores], _ReadResult]) -> _ReadResult:
    def capture() -> DecisionJournalStores:
        snapshot = DecisionJournalStores(
            entries=_FrozenJournalRecords(stores.entries),
            idempotency=_FrozenJournalRecords(stores.idempotency),
            audit=_FrozenJournalRecords(stores.audit),
            outbox=_FrozenJournalRecords(stores.outbox) if stores.outbox is not None else None,
            data_dir=stores.data_dir,
        )
        snapshot._frozen_read_snapshot = True
        return snapshot

    # Hold the existing writer bundle lock across capture of every store.
    # A frozen snapshot then reuses the ordinary visibility/commit predicates
    # but deliberately does not run repair, even for crash-pending records.
    snapshot = _read_under_shared_lock(stores._bundle_flock_path, capture)
    return read(snapshot)


def _build_journal_record_store(
    storage_path: Path,
    *,
    table: str,
    id_fields: Sequence[str],
) -> GovernanceRecordStore:
    backend = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower()
    if backend in ("", "json"):
        return CoordinatingJsonGovernanceRecordStore(storage_path, id_fields=id_fields)
    return build_governance_record_store(storage_path, table=table, id_fields=id_fields)


def build_decision_journal_stores(data_dir: str | Path) -> DecisionJournalStores:
    """Build the durable stores backing decision journal writes.

    Uses ``GOVERNANCE_STORE_BACKEND`` (``json`` for dev, ``postgres`` for
    staging/production) -- the same posture already governing freeze orders
    and rollbacks -- so this owner never silently downgrades to a
    process-local dict when a durable backend is configured.
    """

    base = Path(data_dir)
    entries = _build_journal_record_store(
        base / "decision_journal_entries.json",
        table="governance.decision_journal_entries",
        id_fields=_ENTRY_ID_FIELDS,
    )
    idempotency = _build_journal_record_store(
        base / "decision_journal_idempotency.json",
        table="governance.decision_journal_idempotency",
        id_fields=_IDEMPOTENCY_ID_FIELDS,
    )
    audit = _build_journal_record_store(
        base / "decision_journal_audit.json",
        table="governance.decision_journal_audit",
        id_fields=_AUDIT_ID_FIELDS,
    )
    backend = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower()
    outbox = None
    if backend in ("", "json") or os.getenv("PANTHEON_DECISION_JOURNAL_OUTBOX") == "1":
        outbox = _build_journal_record_store(
            base / "decision_journal_outbox.json",
            table="governance.decision_journal_outbox",
            id_fields=_OUTBOX_ID_FIELDS,
        )
    return DecisionJournalStores(
        entries=entries,
        idempotency=idempotency,
        audit=audit,
        outbox=outbox,
        data_dir=base,
    )


def _project(record: Dict[str, Any]) -> Dict[str, Any]:
    tenant_val = str(record.get("tenant_id") or record.get("tenantId") or "")
    user_val = str(record.get("user_id") or record.get("userId") or record.get("createdBy") or "")
    created_by_val = str(record.get("createdBy") or record.get("actor_id") or user_val or "")
    res = {
        "id": str(record.get("id") or ""),
        "title": str(record.get("title") or ""),
        "body": str(record.get("body") or ""),
        "tags": list(record.get("tags") or []),
        "linkedStrategyIds": list(record.get("linkedStrategyIds") or []),
        "linkedPersonaIds": list(record.get("linkedPersonaIds") or []),
        "visibility": str(record.get("visibility") or "private"),
        "createdAt": str(record.get("createdAt") or ""),
        "updatedAt": str(record.get("updatedAt") or record.get("createdAt") or ""),
        "version": int(record.get("version") or 1),
        "createdBy": created_by_val,
        "tenantId": tenant_val,
        "tenant_id": tenant_val,
        "userId": user_val,
        "user_id": user_val,
        "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
        "persistenceMode": str(record.get("persistenceMode") or _persistence_mode()),
    }
    if "category" in record and record.get("category") is not None:
        res["category"] = str(record["category"])
    raw_refs = record.get("contextRefs") if "contextRefs" in record else record.get("context_refs")
    if raw_refs is not None:
        res["contextRefs"] = list(raw_refs)
    return res


def domain_creation_idempotency_key(
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    entry_id: str,
) -> str:
    """Format a collision-free idempotency key for domain-layer entry creation transactions."""
    clean_tenant = urllib.parse.quote(str(tenant_id or "").strip(), safe="-_.~")
    clean_actor = urllib.parse.quote(str(actor_id or "").strip(), safe="-_.~")
    clean_id = urllib.parse.quote(str(entry_id or "").strip(), safe="-_.~")
    return f"domain:create:{clean_tenant}:{clean_actor}:{clean_id}"


def create_idempotency_key(
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    idempotency_key: str,
) -> str:
    """Format an unambiguous structured scope key for BFF create requests."""
    clean_tenant = urllib.parse.quote(str(tenant_id or "").strip(), safe="-_.~")
    clean_user = urllib.parse.quote(str(user_id or "").strip(), safe="-_.~")
    clean_key = urllib.parse.quote(str(idempotency_key or "").strip(), safe="-_.~")
    return f"create:{clean_tenant}:{clean_user}:{clean_key}"


def patch_idempotency_key(
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    idempotency_key: str,
) -> str:
    """Format an unambiguous structured scope key for patch mutations."""
    clean_tenant = urllib.parse.quote(str(tenant_id or "").strip(), safe="-_.~")
    clean_actor = urllib.parse.quote(str(actor_id or "").strip(), safe="-_.~")
    clean_key = urllib.parse.quote(str(idempotency_key or "").strip(), safe="-_.~")
    if clean_tenant:
        if clean_actor:
            return f"{clean_tenant}:{clean_actor}:{clean_key}"
        return f"{clean_tenant}:{clean_key}"
    return clean_key


def _coordinate_pending_txs(stores: DecisionJournalStores, entry: Optional[Dict[str, Any]]) -> bool:
    """Coordinate pending transactions and outbox events for committed entries across crashes.

    Returns True if all pending transactions/events are coordinated and committed (or none were pending),
    False if any pending transaction's secondary write failed.
    """
    if not isinstance(entry, dict):
        return True

    clean_id = str(entry.get("id") or "").strip()
    if not clean_id:
        return True

    all_coordinated = True

    # 1. Coordinate creation outbox
    creation_outbox = entry.get("_creation_outbox")
    if creation_outbox:
        evt_id = str(creation_outbox.get("event_id") or creation_outbox.get("id") or "")
        clean_tenant = str(entry.get("tenant_id") or entry.get("tenantId") or "").strip()
        clean_actor = str(entry.get("createdBy") or entry.get("actor_id") or "").strip()
        create_idem_key = domain_creation_idempotency_key(
            tenant_id=clean_tenant,
            actor_id=clean_actor,
            entry_id=clean_id,
        )

        secondary_ok = True
        if stores.outbox is not None and creation_outbox:
            if not evt_id or stores.outbox.get(evt_id) is None:
                try:
                    stores.outbox.put(creation_outbox)
                except Exception:
                    secondary_ok = False
                    all_coordinated = False

        if secondary_ok:
            with stores.bundle_lock(), stores._tx_lock:
                fresh = stores.entries.get(clean_id)
                if isinstance(fresh, dict):
                    fresh_outbox = fresh.get("_creation_outbox")
                    if fresh_outbox is not None:
                        fresh_evt_id = str(fresh_outbox.get("event_id") or fresh_outbox.get("id") or "")
                        if not evt_id or not fresh_evt_id or evt_id == fresh_evt_id:
                            committed_fresh = dict(fresh)
                            committed_fresh.pop("_creation_outbox", None)
                            updated, _ = stores.entries.compare_and_set(fresh, committed_fresh)
                            if updated:
                                if stores.idempotency is not None:
                                    idem_rec = stores.idempotency.get(create_idem_key)
                                    if idem_rec is not None and idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                                        try:
                                            stores.idempotency.put({
                                                **idem_rec,
                                                "status": _IDEM_STATUS_SUCCEEDED,
                                            })
                                        except Exception:
                                            pass

    # 2. Coordinate patch transaction history
    with stores.bundle_lock(), stores._tx_lock:
        fresh_entry = stores.entries.get(clean_id)
        if not isinstance(fresh_entry, dict):
            return all_coordinated
        tx_history = fresh_entry.get("_tx_history")
        if not tx_history or not isinstance(tx_history, list):
            return all_coordinated

        for tx in tx_history:
            if not isinstance(tx, dict):
                continue
            audit = tx.get("audit")
            audit_id = tx.get("audit_id")
            outbox = tx.get("outbox")
            outbox_id = tx.get("outbox_id")
            idem_key = tx.get("idempotency_key")

            # Check if this tx is already succeeded
            if idem_key and stores.idempotency is not None:
                existing_idem = stores.idempotency.get(idem_key)
                if existing_idem is not None and existing_idem.get("status") == _IDEM_STATUS_SUCCEEDED:
                    continue

            # Never finalize with failed secondary writes
            secondary_ok = True
            if audit and audit_id and stores.audit is not None:
                if stores.audit.get(audit_id) is None:
                    try:
                        stores.audit.put({"audit_id": audit_id, **audit})
                    except Exception:
                        secondary_ok = False
                        all_coordinated = False

            if outbox and outbox_id and stores.outbox is not None:
                if stores.outbox.get(outbox_id) is None:
                    try:
                        stores.outbox.put(outbox)
                    except Exception:
                        secondary_ok = False
                        all_coordinated = False

            if secondary_ok and idem_key and stores.idempotency is not None:
                idem_rec = stores.idempotency.get(idem_key)
                if idem_rec is None or idem_rec.get("status") in (_IDEM_STATUS_PENDING, _IDEM_STATUS_FAILED):
                    try:
                        base_rec = idem_rec if isinstance(idem_rec, dict) else {
                            "idempotency_key": idem_key,
                            "raw_idempotency_key": tx.get("raw_idempotency_key"),
                            "tenant_id": tx.get("tenant_id"),
                            "actor_id": tx.get("actor_id"),
                            "user_id": tx.get("user_id"),
                            "request_hash": tx.get("request_hash"),
                            "entry_id": clean_id,
                        }
                        stores.idempotency.put({
                            **base_rec,
                            "status": _IDEM_STATUS_SUCCEEDED,
                            "patch_id": audit_id,
                            "audit": audit,
                            "entry": tx.get("entry") or base_rec.get("candidate_entry") or base_rec.get("entry") or _project(fresh_entry),
                        })
                    except Exception:
                        pass
            elif not secondary_ok:
                all_coordinated = False

    return all_coordinated


def _has_uncommitted_transactions(
    stores: DecisionJournalStores,
    entry: Optional[Dict[str, Any]],
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Check if an entry has any pending uncommitted creation or patch transactions."""
    if not isinstance(entry, dict):
        return False, None

    clean_id = str(entry.get("id") or "").strip()
    if not clean_id:
        return False, None

    if entry.get("_creation_outbox") is not None:
        clean_tenant = str(entry.get("tenant_id") or entry.get("tenantId") or "").strip()
        clean_actor = str(entry.get("createdBy") or entry.get("actor_id") or "").strip()
        create_idem_key = domain_creation_idempotency_key(
            tenant_id=clean_tenant,
            actor_id=clean_actor,
            entry_id=clean_id,
        )
        if stores.idempotency is not None:
            idem_rec = stores.idempotency.get(create_idem_key)
            if idem_rec is None or idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                return True, {"type": "creation", "entry_id": clean_id}

    tx_history = entry.get("_tx_history")
    if isinstance(tx_history, list) and tx_history:
        for tx in reversed(tx_history):
            if not isinstance(tx, dict):
                continue
            idem_key = tx.get("idempotency_key")
            if idem_key and stores.idempotency is not None:
                idem_rec = stores.idempotency.get(idem_key)
                if idem_rec is None or idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                    return True, tx

    return False, None


def create_entry(
    stores: DecisionJournalStores,
    *,
    entry_id: str,
    title: str,
    body: str,
    actor_id: str,
    created_at: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    linked_strategy_ids: Optional[List[str]] = None,
    linked_persona_ids: Optional[List[str]] = None,
    visibility: str = "private",
    category: Optional[str] = None,
    context_refs: Optional[List[Dict[str, Any]]] = None,
    contextRefs: Optional[List[Dict[str, Any]]] = None,
    version: Optional[int] = None,
    updated_at: Optional[str] = None,
    updatedAt: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a decision journal entry, persisted through the owner store.

    Tenant-and-user scoped:
    - Rejects private supplied-ID collisions across actors/tenants (raises
      DecisionJournalCollisionError and never returns another principal's private record).
    - If the entry already exists for the identical tenant and actor/user, returns the
      canonical persisted record idempotently.
    """

    with stores.bundle_lock(), stores._tx_lock:
        clean_id = str(entry_id or "").strip()
        if not clean_id:
            raise DecisionJournalValidationError("entry_id is required")

        clean_tenant = str(tenant_id or "").strip()
        clean_actor = str(actor_id or "").strip()
        clean_user = str(user_id or clean_actor).strip()

        actual_version = int(version) if version is not None else 1
        actual_updated_at = str(updated_at or updatedAt or created_at)
        actual_category = str(category).strip() if category is not None else None
        actual_context_refs = None
        if context_refs is not None:
            actual_context_refs = list(context_refs)
        elif contextRefs is not None:
            actual_context_refs = list(contextRefs)

        event_id = f"evt-dj-{uuid.uuid4().hex[:12]}"
        creation_data = {
            "id": clean_id,
            "title": _validate_title(title),
            "body": _validate_body(body),
            "tags": list(tags or []),
            "linkedStrategyIds": list(linked_strategy_ids or []),
            "linkedPersonaIds": list(linked_persona_ids or []),
            "visibility": str(visibility or "private"),
            "createdAt": created_at,
            "updatedAt": actual_updated_at,
            "version": actual_version,
            "createdBy": clean_actor,
            "tenantId": clean_tenant,
            "tenant_id": clean_tenant,
            "userId": clean_user,
            "user_id": clean_user,
            "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
            "persistenceMode": _persistence_mode(),
        }
        if actual_category is not None:
            creation_data["category"] = actual_category
        if actual_context_refs is not None:
            creation_data["contextRefs"] = actual_context_refs

        creation_outbox = {
            "event_id": event_id,
            "id": event_id,
            "event_type": "decision_journal.entry.created",
            "aggregate_type": "DecisionJournalEntry",
            "aggregate_id": clean_id,
            "tenant_id": clean_tenant,
            "actor_id": clean_actor,
            "user_id": clean_user,
            "timestamp": created_at,
            "data": creation_data,
        }

        create_idem_key = domain_creation_idempotency_key(
            tenant_id=clean_tenant,
            actor_id=clean_actor,
            entry_id=clean_id,
        )
        creation_outbox["idempotency_key"] = create_idem_key

        record = {
            "id": clean_id,
            "title": _validate_title(title),
            "body": _validate_body(body),
            "tags": list(tags or []),
            "linkedStrategyIds": list(linked_strategy_ids or []),
            "linkedPersonaIds": list(linked_persona_ids or []),
            "visibility": str(visibility or "private"),
            "createdAt": created_at,
            "updatedAt": actual_updated_at,
            "version": actual_version,
            "createdBy": clean_actor,
            "actor_id": clean_actor,
            "tenant_id": clean_tenant,
            "tenantId": clean_tenant,
            "user_id": clean_user,
            "userId": clean_user,
            "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
            "persistenceMode": _persistence_mode(),
            "_creation_outbox": creation_outbox,
            "_tx_history": [],
        }
        if actual_category is not None:
            record["category"] = actual_category
        if actual_context_refs is not None:
            record["contextRefs"] = actual_context_refs
        inserted, canonical = stores.entries.insert_if_absent(record)
        if not inserted:
            # Existing record found. Verify ownership: must match tenant and actor/user
            existing_tenant = str(canonical.get("tenant_id") or canonical.get("tenantId") or "").strip()
            existing_actor = str(canonical.get("createdBy") or canonical.get("actor_id") or "").strip()
            existing_user = str(canonical.get("user_id") or canonical.get("userId") or existing_actor).strip()

            # Check tenant match
            tenant_match = (existing_tenant == clean_tenant)
            # Check actor/user match
            actor_match = (existing_actor == clean_actor) or (existing_user == clean_user)

            if not tenant_match or not actor_match:
                # Supplied ID collision across different actors or tenants!
                raise DecisionJournalCollisionError(
                    f"Supplied entry ID {clean_id!r} collides with an existing record owned by another principal or tenant."
                )
            # If the entry was left with uncommitted creation outbox from a prior crash, publish it now.
            # Never publish a new/duplicate event for an already-committed entry, and never roll back
            # a pre-existing entry that this attempt did not insert.
            creation_outbox_to_publish = canonical.get("_creation_outbox")
            if stores.outbox is not None and creation_outbox_to_publish is not None:
                evt_id = str(creation_outbox_to_publish.get("event_id") or creation_outbox_to_publish.get("id") or "")
                if not evt_id or stores.outbox.get(evt_id) is None:
                    try:
                        stores.outbox.put(creation_outbox_to_publish)
                    except Exception as exc:
                        raise exc

            if "_creation_outbox" in canonical:
                committed_canonical = dict(canonical)
                committed_canonical.pop("_creation_outbox", None)
                stores.entries.put(committed_canonical)
                canonical = committed_canonical

            # Coordinate pending transactions on canonical
            _coordinate_pending_txs(stores, canonical)
            fresh_canonical = stores.entries.get(clean_id)
            if fresh_canonical is not None:
                canonical = fresh_canonical

            # Resolve committed snapshot or fail closed
            committed = _get_committed_entry_snapshot(stores, canonical, coordinate_if_unlocked=False)
            if committed is None:
                if stores.idempotency is not None:
                    stores.idempotency.put({
                        "idempotency_key": create_idem_key,
                        "tenant_id": clean_tenant,
                        "actor_id": clean_actor,
                        "user_id": clean_user,
                        "entry_id": clean_id,
                        "status": _IDEM_STATUS_FAILED,
                        "created_at": time.time(),
                    })
                raise DecisionJournalConcurrencyError(
                    f"Entry {clean_id} could not be resolved to a committed snapshot"
                )

            projected_committed = _project(committed)
            if stores.idempotency is not None:
                stores.idempotency.put({
                    "idempotency_key": create_idem_key,
                    "tenant_id": clean_tenant,
                    "actor_id": clean_actor,
                    "user_id": clean_user,
                    "entry_id": clean_id,
                    "status": _IDEM_STATUS_SUCCEEDED,
                    "entry": projected_committed,
                    "created_at": time.time(),
                })
            # Authorized idempotent recreate by same owner in same tenant: return committed snapshot
            return projected_committed

        if stores.idempotency is not None:
            stores.idempotency.put({
                "idempotency_key": create_idem_key,
                "tenant_id": clean_tenant,
                "actor_id": clean_actor,
                "user_id": clean_user,
                "entry_id": clean_id,
                "status": _IDEM_STATUS_PENDING,
                "created_at": time.time(),
                "created_pid": os.getpid(),
                "staged_outbox": creation_outbox,
            })

        # Publish outbox event
        if stores.outbox is not None:
            try:
                stores.outbox.put(creation_outbox)
            except Exception:
                _delete_record(stores.entries, clean_id)
                if stores.idempotency is not None:
                    _delete_record(stores.idempotency, create_idem_key)
                raise

        if stores.idempotency is not None:
            stores.idempotency.put({
                "idempotency_key": create_idem_key,
                "tenant_id": clean_tenant,
                "actor_id": clean_actor,
                "user_id": clean_user,
                "entry_id": clean_id,
                "status": _IDEM_STATUS_SUCCEEDED,
                "created_at": time.time(),
            })

        committed_record = dict(record)
        committed_record.pop("_creation_outbox", None)
        stores.entries.put(committed_record)
        canonical = committed_record

        return _project(canonical)


def _is_entry_accessible(
    record: Dict[str, Any],
    *,
    clean_tenant: Optional[str] = None,
    target_actors: Optional[Set[str]] = None,
    include_unscoped_legacy: bool = False,
) -> bool:
    """Fail-closed access control policy for decision journal entries.

    - Tenant-scoped records require exact matching tenant_id. Unscoped queries
      or mismatched tenants are strictly denied.
    - Legacy records missing tenant_id require governed legacy access (include_unscoped_legacy=True).
      Ordinary unscoped reads are strictly denied even for authored legacy.
    - Private visibility records require authenticated author match.
      When accessed via governed legacy access, private records still require matching author.
    """
    record_tenant = str(record.get("tenant_id") or record.get("tenantId") or "").strip()
    record_actors = {
        str(record.get("createdBy") or "").strip(),
        str(record.get("actor_id") or "").strip(),
        str(record.get("userId") or "").strip(),
        str(record.get("user_id") or "").strip(),
    } - {""}
    visibility = str(record.get("visibility") or "private").strip().lower()
    actors = target_actors or set()

    # 1. Tenant boundary
    if record_tenant:
        if clean_tenant is None or clean_tenant != record_tenant:
            return False
    else:
        # Row has no tenant
        if clean_tenant is not None and not include_unscoped_legacy:
            return False
        is_canonical = bool(record.get("canonicalWriteAuthority"))
        if not is_canonical and not include_unscoped_legacy:
            return False

    # 2. Visibility & Principal / Author boundary
    if visibility == "private":
        if record_actors:
            if not actors:
                is_canonical = bool(record.get("canonicalWriteAuthority"))
                if not (not record_tenant and is_canonical):
                    return False
            elif not (record_actors & actors):
                return False
        elif not include_unscoped_legacy:
            return False

    return True




def _get_committed_entry_snapshot(
    stores: DecisionJournalStores,
    entry: Optional[Dict[str, Any]],
    *,
    coordinate_if_unlocked: bool = True,
) -> Optional[Dict[str, Any]]:
    """Resolve the authoritative committed snapshot of an entry.

    If an uncommitted creation is in flight, returns None.
    If an uncommitted patch transaction is in flight, returns before_entry.
    Coordinates pending transactions if store bundle is unlocked and coordinate_if_unlocked is True.
    """
    if not isinstance(entry, dict):
        return None

    clean_id = str(entry.get("id") or "").strip()
    if not clean_id:
        return None

    is_locked = stores.is_bundle_locked()
    if not is_locked and coordinate_if_unlocked:
        _coordinate_pending_txs(stores, entry)
        current = stores.entries.get(clean_id)
        if not isinstance(current, dict):
            return None
        entry = current

    # 1. Check creation outbox / creation commit status
    if entry.get("_creation_outbox") is not None:
        clean_tenant = str(entry.get("tenant_id") or entry.get("tenantId") or "").strip()
        clean_actor = str(entry.get("createdBy") or entry.get("actor_id") or "").strip()
        create_idem_key = domain_creation_idempotency_key(
            tenant_id=clean_tenant,
            actor_id=clean_actor,
            entry_id=clean_id,
        )
        is_committed = False
        if stores.idempotency is not None:
            idem_rec = stores.idempotency.get(create_idem_key)
            if idem_rec is not None and idem_rec.get("status") == _IDEM_STATUS_SUCCEEDED:
                is_committed = True
        if not is_committed:
            return None

    # 2. Check patch transaction history
    tx_history = entry.get("_tx_history")
    if isinstance(tx_history, list) and tx_history:
        candidate_entry = dict(entry)
        for tx in reversed(tx_history):
            if not isinstance(tx, dict):
                continue
            idem_key = tx.get("idempotency_key")
            is_committed = False
            if idem_key and stores.idempotency is not None:
                idem_rec = stores.idempotency.get(idem_key)
                if idem_rec is not None and idem_rec.get("status") == _IDEM_STATUS_SUCCEEDED:
                    is_committed = True
            if not is_committed:
                before_entry = tx.get("before_entry")
                if before_entry is not None and isinstance(before_entry, dict):
                    candidate_entry = dict(before_entry)
                else:
                    return None
            else:
                break
        return candidate_entry

    return dict(entry)


def get_entry(
    stores: DecisionJournalStores,
    entry_id: str,
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    user_id: Optional[str] = None,
    include_unscoped_legacy: bool = False,
) -> Optional[Dict[str, Any]]:
    """Retrieve a single decision journal entry by ID with fail-closed scope enforcement."""
    if _requires_read_snapshot(stores):
        return _with_read_snapshot(stores, lambda snapshot: get_entry(
            snapshot, entry_id, tenant_id=tenant_id, actor_id=actor_id,
            user_id=user_id, include_unscoped_legacy=include_unscoped_legacy,
        ))
    clean_id = str(entry_id or "").strip()
    if not clean_id:
        return None

    raw_record = stores.entries.get(clean_id)
    if raw_record is None:
        return None

    record = _get_committed_entry_snapshot(
        stores,
        raw_record,
        coordinate_if_unlocked=not stores.is_bundle_locked(),
    )
    if record is None:
        return None

    clean_tenant = str(tenant_id).strip() if tenant_id is not None else None
    target_actors = {str(actor_id or "").strip(), str(user_id or "").strip()} - {""}

    if not _is_entry_accessible(
        record,
        clean_tenant=clean_tenant,
        target_actors=target_actors,
        include_unscoped_legacy=include_unscoped_legacy,
    ):
        return None

    return _project(record)


def list_entries(
    stores: DecisionJournalStores,
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    user_id: Optional[str] = None,
    include_unscoped_legacy: bool = False,
) -> List[Dict[str, Any]]:
    """List decision journal entries with fail-closed tenant and user isolation."""
    if _requires_read_snapshot(stores):
        return _with_read_snapshot(stores, lambda snapshot: list_entries(
            snapshot, tenant_id=tenant_id, actor_id=actor_id,
            user_id=user_id, include_unscoped_legacy=include_unscoped_legacy,
        ))
    all_records = stores.entries.list_all()
    clean_tenant = str(tenant_id).strip() if tenant_id is not None else None
    target_actors = {str(actor_id or "").strip(), str(user_id or "").strip()} - {""}

    filtered: List[Dict[str, Any]] = []
    for raw_record in all_records:
        record = _get_committed_entry_snapshot(
            stores,
            raw_record,
            coordinate_if_unlocked=not stores.is_bundle_locked(),
        )
        if record is None:
            continue
        if not _is_entry_accessible(
            record,
            clean_tenant=clean_tenant,
            target_actors=target_actors,
            include_unscoped_legacy=include_unscoped_legacy,
        ):
            continue
        filtered.append(_project(record))

    filtered.sort(key=lambda entry: (entry.get("updatedAt") or entry.get("createdAt") or ""), reverse=True)
    return filtered


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    changes = []
    for field in _PATCHABLE_FIELDS:
        if before.get(field) == after.get(field):
            continue
        changes.append({"field": field, "before": before.get(field), "after": after.get(field)})
    return {
        "changedFields": [change["field"] for change in changes],
        "changes": changes,
        "before": before,
        "after": after,
    }


def _attempt_crash_recovery(
    stores: DecisionJournalStores,
    record: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Recover an in-flight reservation across instance or process crash."""
    if stores.is_bundle_locked():
        return None

    entry_id = str(record.get("entry_id") or "").strip()
    if not entry_id:
        return None

    current_entry = stores.entries.get(entry_id)
    if current_entry is None:
        failed_record = {
            **record,
            "status": _IDEM_STATUS_FAILED,
            "reason": "uncommitted_mutation_aborted",
        }
        stores.idempotency.put(failed_record)
        return failed_record

    _coordinate_pending_txs(stores, current_entry)

    rec_tx_id = record.get("tx_id")
    rec_idem_key = record.get("idempotency_key")
    rec_tenant = str(record.get("tenant_id") or "").strip()
    rec_actor = str(record.get("actor_id") or "").strip()
    rec_user = str(record.get("user_id") or rec_actor).strip()
    rec_hash = record.get("request_hash")

    tx_history = current_entry.get("_tx_history", [])
    matching_tx = None
    if isinstance(tx_history, list):
        for tx in tx_history:
            if not isinstance(tx, dict):
                continue
            tx_id = tx.get("tx_id")
            tx_idem_key = tx.get("idempotency_key")
            id_match = False
            if rec_tx_id and tx_id and rec_tx_id == tx_id:
                id_match = True
            elif rec_idem_key and tx_idem_key and rec_idem_key == tx_idem_key:
                id_match = True

            if not id_match:
                continue

            tx_tenant = str(tx.get("tenant_id") or "").strip()
            if rec_tenant != tx_tenant:
                continue

            tx_actor = str(tx.get("actor_id") or "").strip()
            if rec_actor != tx_actor:
                continue

            tx_user = str(tx.get("user_id") or tx_actor).strip()
            if rec_user != tx_user:
                continue

            tx_hash = tx.get("request_hash")
            if rec_hash and tx_hash and rec_hash != tx_hash:
                continue

            tx_entry_id = str(tx.get("entry_id") or "").strip()
            if tx_entry_id and tx_entry_id != entry_id:
                continue

            matching_tx = tx
            break

    if matching_tx is not None:
        target_entry = matching_tx.get("entry") or current_entry
        target_actors = {rec_actor, rec_user} - {""}
        if not _is_entry_accessible(
            target_entry,
            clean_tenant=rec_tenant if rec_tenant else None,
            target_actors=target_actors,
        ):
            failed_record = {
                **record,
                "status": _IDEM_STATUS_FAILED,
                "reason": "unauthorized_replay_access_denied",
            }
            stores.idempotency.put(failed_record)
            return failed_record

        staged_audit = matching_tx.get("audit") or record.get("staged_audit")
        staged_outbox = matching_tx.get("outbox") or record.get("staged_outbox")

        try:
            if staged_audit and stores.audit is not None:
                audit_id = str(staged_audit.get("auditId") or staged_audit.get("audit_id") or "")
                if audit_id and stores.audit.get(audit_id) is None:
                    stores.audit.put({"audit_id": audit_id, **staged_audit})

            if staged_outbox and stores.outbox is not None:
                event_id = str(staged_outbox.get("event_id") or staged_outbox.get("id") or "")
                if event_id and stores.outbox.get(event_id) is None:
                    stores.outbox.put(staged_outbox)
        except Exception:
            failed_record = {
                **record,
                "status": _IDEM_STATUS_FAILED,
                "reason": "secondary_writes_failed_during_recovery",
            }
            stores.idempotency.put(failed_record)
            return failed_record

        recovered_record = {
            **record,
            "status": _IDEM_STATUS_SUCCEEDED,
            "entry": matching_tx.get("entry") or record.get("candidate_entry") or _project(current_entry),
            "audit": staged_audit,
            "patch_id": (staged_audit or {}).get("auditId"),
        }
        stores.idempotency.put(recovered_record)
        return recovered_record

    failed_record = {
        **record,
        "status": _IDEM_STATUS_FAILED,
        "reason": "uncommitted_mutation_aborted",
    }
    stores.idempotency.put(failed_record)
    return failed_record


def _await_idempotency_resolution(
    stores: DecisionJournalStores,
    idempotency_key: str,
    reservation: Dict[str, Any],
) -> Dict[str, Any]:
    """Block until a concurrently-held idempotency reservation resolves or is recovered."""
    record = reservation
    if (
        record.get("status") == _IDEM_STATUS_PENDING
        and record.get("created_pid") == os.getpid()
        and record.get("created_thread") == threading.get_ident()
    ):
        return record

    created_pid = record.get("created_pid")
    is_dead = False
    if created_pid and created_pid != os.getpid():
        try:
            os.kill(created_pid, 0)
        except ProcessLookupError:
            is_dead = True
        except PermissionError:
            pass

    max_attempts = 1 if is_dead else 50
    for _attempt in range(max_attempts):
        if not isinstance(record, dict) or record.get("status") != _IDEM_STATUS_PENDING:
            break
        time.sleep(0.005)
        record = stores.idempotency.get(idempotency_key)
    else:
        if isinstance(record, dict) and record.get("status") == _IDEM_STATUS_PENDING:
            if stores.is_bundle_locked() or not is_dead:
                return record
            recovered = _attempt_crash_recovery(stores, record)
            if recovered is not None:
                return recovered

    if (
        isinstance(record, dict)
        and record.get("status") == _IDEM_STATUS_FAILED
        and record.get("reason") == "secondary_writes_failed_during_recovery"
        and not stores.is_bundle_locked()
    ):
        recovered = _attempt_crash_recovery(stores, record)
        if recovered is not None:
            return recovered

    return record if isinstance(record, dict) else reservation


def _resolved_idempotency_result(
    record: Dict[str, Any],
    request_hash: str,
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    # Scope check on reservation replay
    if tenant_id is not None:
        rec_tenant = str(record.get("tenant_id") or "").strip()
        if rec_tenant and rec_tenant != str(tenant_id).strip():
            return {"status": "conflict", "reason": "cross_tenant_idempotency_conflict"}
    if actor_id is not None:
        rec_actor = str(record.get("actor_id") or "").strip()
        if rec_actor and rec_actor != str(actor_id).strip():
            return {"status": "conflict", "reason": "cross_actor_idempotency_conflict"}

    if record.get("request_hash") != request_hash:
        return {
            "status": "conflict",
            "existing_patch_id": record.get("patch_id"),
            "entry": record.get("entry"),
            "audit": record.get("audit"),
        }
    status = record.get("status")
    if status == _IDEM_STATUS_SUCCEEDED:
        res_entry = record.get("entry")
        if isinstance(res_entry, dict):
            clean_tenant = str(tenant_id).strip() if tenant_id is not None else None
            target_actors = {str(actor_id or "").strip(), str(user_id or "").strip()} - {""}
            if not _is_entry_accessible(res_entry, clean_tenant=clean_tenant, target_actors=target_actors):
                return {
                    "status": "failed",
                    "reason": "unauthorized_replay_access_denied",
                    "idempotency_key": record.get("idempotency_key"),
                }
        return {"status": "replayed", "entry": record.get("entry"), "audit": record.get("audit")}
    if status == _IDEM_STATUS_NOT_FOUND:
        return None
    if status == _IDEM_STATUS_PENDING:
        return {
            "status": "pending",
            "reason": "concurrent_mutation_in_progress",
            "idempotency_key": record.get("idempotency_key"),
        }
    if status == _IDEM_STATUS_FAILED:
        return {
            "status": "failed",
            "reason": record.get("reason") or "previous_mutation_failed",
            "idempotency_key": record.get("idempotency_key"),
        }
    raise DecisionJournalConcurrencyError(
        f"decision journal patch for idempotency key {record.get('idempotency_key')} "
        f"left no replayable result (status={status!r})"
    )


def patch_entry(
    stores: DecisionJournalStores,
    entry_id: str,
    *,
    patch: Dict[str, Any],
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    patched_at: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Apply a merge patch to a decision journal entry with atomic multi-store commit and recovery."""

    clean_id = str(entry_id or "").strip()
    if not clean_id:
        return None

    clean_tenant = str(tenant_id or "").strip()
    clean_actor = str(actor_id or "").strip()
    clean_user = str(user_id or clean_actor).strip()

    # Scope-bound idempotency reservation key
    scoped_idem_key = patch_idempotency_key(
        tenant_id=clean_tenant,
        actor_id=clean_actor,
        idempotency_key=idempotency_key,
    )

    reservation = {
        "idempotency_key": scoped_idem_key,
        "raw_idempotency_key": idempotency_key,
        "tenant_id": clean_tenant,
        "actor_id": clean_actor,
        "user_id": clean_user,
        "request_hash": request_hash,
        "entry_id": clean_id,
        "patch_id": None,
        "status": _IDEM_STATUS_PENDING,
        "entry": None,
        "audit": None,
        "created_pid": os.getpid(),
        "created_thread": threading.get_ident(),
        "created_at": time.time(),
    }
    reserved, existing = stores.idempotency.insert_if_absent(reservation)
    if not reserved:
        resolved = _await_idempotency_resolution(stores, scoped_idem_key, existing)
        return _resolved_idempotency_result(
            resolved,
            request_hash,
            tenant_id=clean_tenant if clean_tenant else None,
            actor_id=clean_actor if clean_actor else None,
            user_id=clean_user if clean_user else None,
        )

    with stores.bundle_lock(), stores._tx_lock:
        before: Optional[Dict[str, Any]] = None
        candidate: Optional[Dict[str, Any]] = None
        updated: bool = False
        audit_id: Optional[str] = None
        event_id: Optional[str] = None
        try:
            for _attempt in range(_MAX_CAS_ATTEMPTS):
                stored = stores.entries.get(clean_id)
                if stored is None:
                    stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                    return None

                # Coordinate pending transactions on stored
                _coordinate_pending_txs(stores, stored)
                fresh_stored = stores.entries.get(clean_id)
                if fresh_stored is not None:
                    stored = fresh_stored

                # 1. Resolve committed snapshot for authorization
                committed = _get_committed_entry_snapshot(stores, stored, coordinate_if_unlocked=False)
                if committed is None:
                    stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                    return None

                # 2. Enforce tenant isolation on mutation using committed snapshot
                rec_tenant = str(committed.get("tenant_id") or committed.get("tenantId") or "").strip()
                if rec_tenant:
                    if not clean_tenant or rec_tenant != clean_tenant:
                        stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                        return None
                else:
                    if clean_tenant:
                        stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                        return None
                    if not bool(committed.get("canonicalWriteAuthority")):
                        stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                        return None

                record_actors = {
                    str(committed.get("createdBy") or "").strip(),
                    str(committed.get("actor_id") or "").strip(),
                    str(committed.get("userId") or "").strip(),
                    str(committed.get("user_id") or "").strip(),
                } - {""}

                # 3. Enforce user private scope on mutation using committed snapshot
                visibility = str(committed.get("visibility") or "private").strip().lower()
                if visibility == "private":
                    target_actors = {clean_actor, clean_user} - {""}
                    if record_actors:
                        if not target_actors:
                            is_canonical = bool(committed.get("canonicalWriteAuthority"))
                            if not (not rec_tenant and is_canonical):
                                stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                                return None
                        elif not (record_actors & target_actors):
                            stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                            return None
                    elif not (not rec_tenant and bool(committed.get("canonicalWriteAuthority"))):
                        stores.idempotency.put({**reservation, "status": _IDEM_STATUS_NOT_FOUND})
                        return None

                # 4. Require transaction recovery/fencing:
                # A pending predecessor MUST NOT be incorporated into a successful successor
                # without its durable secondary commit!
                has_pending, pending_tx = _has_uncommitted_transactions(stores, stored)
                if has_pending:
                    stores.idempotency.put({**reservation, "status": _IDEM_STATUS_FAILED})
                    tx_desc = (pending_tx or {}).get("tx_id") or (pending_tx or {}).get("type") or "prior"
                    raise DecisionJournalConcurrencyError(
                        f"Entry {clean_id} has pending uncommitted predecessor transaction ({tx_desc}); "
                        "mutation fenced until prior secondary commit is durable."
                    )

                before = dict(stored)
                candidate = dict(before)
                for field in _PATCHABLE_FIELDS:
                    if field not in patch:
                        continue
                    value = patch[field]
                    if value is None and field in _LIST_FIELDS:
                        candidate[field] = []
                    elif value is not None:
                        candidate[field] = value
                if "title" in patch and patch["title"] is not None:
                    candidate["title"] = _validate_title(candidate["title"])
                if "body" in patch and patch["body"] is not None:
                    candidate["body"] = _validate_body(candidate["body"])
                if "context_refs" in patch and "contextRefs" not in patch:
                    candidate["contextRefs"] = list(patch["context_refs"] or [])
                candidate["updatedAt"] = patched_at
                candidate["version"] = int(before.get("version") or 0) + 1
                candidate["canonicalWriteAuthority"] = CANONICAL_WRITE_AUTHORITY
                candidate["persistenceMode"] = _persistence_mode()

                tx_id = f"tx-dj-{uuid.uuid4().hex[:16]}"
                candidate["_last_tx_id"] = tx_id

                before_projected = _project(before)
                after_projected = _project(candidate)
                diff = _diff(before_projected, after_projected)

                # Prepare audit record
                audit_id = f"aud-decision-journal-{uuid.uuid4().hex[:12]}"
                audit = {
                    "auditId": audit_id,
                    "action": "governance.decision_journal.merge_patch",
                    "target": {"type": "DecisionJournalEntry", "id": clean_id},
                    "actorId": clean_actor,
                    "actor_id": clean_actor,
                    "tenantId": clean_tenant,
                    "tenant_id": clean_tenant,
                    "userId": clean_user,
                    "user_id": clean_user,
                    "correlationId": correlation_id,
                    "idempotencyKey": idempotency_key,
                    "idempotency_key": scoped_idem_key,
                    "scoped_idempotency_key": scoped_idem_key,
                    "tx_id": tx_id,
                    "recordedAt": patched_at,
                    "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
                    "persistenceMode": _persistence_mode(),
                    "diff": diff,
                }

                # Prepare outbox event
                outbox_event: Optional[Dict[str, Any]] = None
                if stores.outbox is not None:
                    event_id = f"evt-dj-{uuid.uuid4().hex[:12]}"
                    outbox_event = {
                        "event_id": event_id,
                        "id": event_id,
                        "event_type": "decision_journal.entry.updated",
                        "aggregate_type": "DecisionJournalEntry",
                        "aggregate_id": clean_id,
                        "tenant_id": clean_tenant,
                        "actor_id": clean_actor,
                        "user_id": clean_user,
                        "timestamp": patched_at,
                        "data": after_projected,
                        "diff": diff,
                        "idempotency_key": scoped_idem_key,
                        "raw_idempotency_key": idempotency_key,
                        "tx_id": tx_id,
                    }

                clean_before = dict(before)
                clean_before.pop("_tx_history", None)
                clean_before.pop("_creation_outbox", None)

                tx_record = {
                    "tx_id": tx_id,
                    "idempotency_key": scoped_idem_key,
                    "raw_idempotency_key": idempotency_key,
                    "entry_id": clean_id,
                    "request_hash": request_hash,
                    "version": candidate["version"],
                    "audit_id": audit_id,
                    "audit": audit,
                    "outbox_id": event_id,
                    "outbox": outbox_event,
                    "entry": after_projected,
                    "before_entry": clean_before,
                    "diff": diff,
                    "patched_at": patched_at,
                    "actor_id": clean_actor,
                    "tenant_id": clean_tenant,
                    "user_id": clean_user,
                }
                existing_txs = list(before.get("_tx_history") or [])
                candidate["_tx_history"] = (existing_txs + [tx_record])[-50:]

                # Staged intent in idempotency store before CAS (status remains pending)
                staged_reservation = {
                    **reservation,
                    "tx_id": tx_id,
                    "status": _IDEM_STATUS_PENDING,
                    "entry_id": clean_id,
                    "before_version": int(before.get("version") or 0),
                    "candidate_version": int(candidate.get("version") or 0),
                    "candidate_entry": after_projected,
                    "staged_audit": audit,
                    "staged_outbox": outbox_event,
                }
                stores.idempotency.put(staged_reservation)

                # Commit entry atomically via CAS FIRST
                updated, canonical = stores.entries.compare_and_set(before, candidate)
                if updated:
                    # CAS succeeded: finalize secondary stores atomically
                    if outbox_event and stores.outbox is not None:
                        stores.outbox.put(outbox_event)
                    if stores.audit is not None:
                        stores.audit.put({"audit_id": audit_id, **audit})
                    stores.idempotency.put(
                        {
                            **staged_reservation,
                            "patch_id": audit_id,
                            "status": _IDEM_STATUS_SUCCEEDED,
                            "entry": after_projected,
                            "audit": audit,
                        }
                    )
                    return {"status": "updated", "entry": after_projected, "audit": audit}

                # CAS failed: loop to next attempt without polluting secondary stores
            else:
                stores.idempotency.put({**reservation, "status": _IDEM_STATUS_FAILED})
                raise DecisionJournalConcurrencyError(
                    f"decision journal entry {clean_id} could not be updated after "
                    f"{_MAX_CAS_ATTEMPTS} compare-and-set attempts"
                )
        except Exception:
            if event_id and stores.outbox is not None:
                try:
                    _delete_record(stores.outbox, event_id)
                except Exception:
                    pass
            if audit_id and stores.audit is not None:
                try:
                    _delete_record(stores.audit, audit_id)
                except Exception:
                    pass
            if updated and before is not None and candidate is not None:
                try:
                    current = stores.entries.get(clean_id)
                    if current == candidate:
                        stores.entries.compare_and_set(candidate, before)
                    elif current is not None:
                        reverted = dict(current)
                        for field in _PATCHABLE_FIELDS:
                            if field in patch:
                                if field in before:
                                    reverted[field] = before[field]
                                else:
                                    reverted.pop(field, None)
                        stores.entries.put(reverted)
                except Exception:
                    pass
            try:
                stores.idempotency.put({**reservation, "status": _IDEM_STATUS_FAILED})
            except Exception:
                pass
            raise


def list_audit_events(
    stores: DecisionJournalStores,
    *,
    entry_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    user_id: Optional[str] = None,
    include_unscoped_legacy: bool = False,
) -> List[Dict[str, Any]]:
    """List audit events with fail-closed tenant and actor principal isolation.

    - Tenant-scoped audit events strictly require exact matching tenant_id.
      Omitted or blank tenant scope denies tenant-scoped audit events (never broadens across tenants).
    - Audit events for private entries require matching authorized principal
      (actor_id/user_id). Listing audit events without actor denies private diffs.
    - Legacy un-tenanted audit events require governed legacy access.
    - Gates event visibility on committed transaction outcome: never exposes provisional or rolled-back events.
    """
    if _requires_read_snapshot(stores):
        return _with_read_snapshot(stores, lambda snapshot: list_audit_events(
            snapshot, entry_id=entry_id, tenant_id=tenant_id, actor_id=actor_id,
            user_id=user_id, include_unscoped_legacy=include_unscoped_legacy,
        ))
    if not stores.is_bundle_locked():
        try:
            if entry_id:
                entry = stores.entries.get(entry_id)
                if entry:
                    _coordinate_pending_txs(stores, entry)
            else:
                for entry in stores.entries.list_all():
                    _coordinate_pending_txs(stores, entry)
        except Exception:
            pass

    clean_tenant = str(tenant_id).strip() if tenant_id is not None else None
    target_actors = {str(actor_id or "").strip(), str(user_id or "").strip()} - {""}

    events = list(stores.audit.list_all())
    entry_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    filtered: List[Dict[str, Any]] = []

    for event in events:
        if entry_id:
            target_id = str((event.get("target") or {}).get("id") or "").strip()
            if target_id != str(entry_id).strip():
                continue
        else:
            target_id = str((event.get("target") or {}).get("id") or "").strip()

        if not target_id:
            continue

        if target_id not in entry_cache:
            entry_cache[target_id] = _get_committed_entry_snapshot(
                stores,
                stores.entries.get(target_id),
                coordinate_if_unlocked=not stores.is_bundle_locked(),
            )
        committed_entry = entry_cache[target_id]
        if committed_entry is None:
            continue

        # Check version: event mutation must not exceed committed entry version
        diff = event.get("diff") or {}
        diff_after = diff.get("after") or {}
        event_ver = diff_after.get("version")
        if event_ver is not None:
            try:
                if int(event_ver) > int(committed_entry.get("version") or 0):
                    continue
            except (ValueError, TypeError):
                pass

        # Check idempotency record status if available
        idem_key = event.get("idempotency_key") or event.get("scoped_idempotency_key")
        if not idem_key:
            clean_evt_tenant = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
            clean_evt_actor = str(event.get("actor_id") or event.get("actorId") or "").strip()
            raw_key = event.get("idempotencyKey")
            if clean_evt_tenant and clean_evt_actor and raw_key:
                idem_key = patch_idempotency_key(
                    tenant_id=clean_evt_tenant,
                    actor_id=clean_evt_actor,
                    idempotency_key=raw_key,
                )

        if idem_key and stores.idempotency is not None:
            idem_rec = stores.idempotency.get(idem_key)
            if idem_rec is not None and idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                continue

        # Check matching transaction in entry history if present
        audit_id = str(event.get("auditId") or event.get("audit_id") or "")
        tx_history = committed_entry.get("_tx_history")
        if isinstance(tx_history, list) and tx_history:
            tx_uncommitted = False
            for tx in tx_history:
                if isinstance(tx, dict) and tx.get("audit_id") == audit_id:
                    tx_idem = tx.get("idempotency_key")
                    if tx_idem and stores.idempotency is not None:
                        idem_rec = stores.idempotency.get(tx_idem)
                        if idem_rec is not None and idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                            tx_uncommitted = True
                            break
            if tx_uncommitted:
                continue

        event_tenant = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
        if event_tenant:
            if clean_tenant is None or clean_tenant != event_tenant:
                continue
        else:
            if not include_unscoped_legacy:
                continue

        # Visibility and principal enforcement on audit diff
        diff = event.get("diff")
        if isinstance(diff, dict) and diff:
            diff_after = diff.get("after")
            diff_before = diff.get("before")

            has_before = bool(isinstance(diff_before, dict) and diff_before)
            has_after = bool(isinstance(diff_after, dict) and diff_after)

            before_accessible = (
                _is_entry_accessible(
                    diff_before,
                    clean_tenant=clean_tenant,
                    target_actors=target_actors,
                    include_unscoped_legacy=include_unscoped_legacy,
                )
                if has_before
                else False
            )

            after_accessible = (
                _is_entry_accessible(
                    diff_after,
                    clean_tenant=clean_tenant,
                    target_actors=target_actors,
                    include_unscoped_legacy=include_unscoped_legacy,
                )
                if has_after
                else False
            )

            if not has_before and not has_after:
                vis = str(event.get("visibility") or "private").strip().lower()
                if vis == "private":
                    event_actors = {
                        str(event.get("actor_id") or event.get("actorId") or "").strip(),
                        str(event.get("user_id") or event.get("userId") or "").strip(),
                    } - {""}
                    if event_actors:
                        if not target_actors or not (event_actors & target_actors):
                            continue
                    elif not include_unscoped_legacy:
                        continue
                filtered.append(event)
                continue

            if not before_accessible and not after_accessible:
                continue

            if (not has_before or before_accessible) and (not has_after or after_accessible):
                filtered.append(event)
                continue

            event_copy = dict(event)
            diff_copy = dict(diff)
            event_copy["diff"] = diff_copy

            if has_before and not before_accessible:
                diff_copy["before"] = None
                if "changes" in diff_copy and isinstance(diff_copy["changes"], list):
                    redacted_changes = []
                    for ch in diff_copy["changes"]:
                        if isinstance(ch, dict):
                            ch_copy = dict(ch)
                            ch_copy["before"] = None
                            redacted_changes.append(ch_copy)
                        else:
                            redacted_changes.append(ch)
                    diff_copy["changes"] = redacted_changes

            if has_after and not after_accessible:
                diff_copy["after"] = None
                if "changes" in diff_copy and isinstance(diff_copy["changes"], list):
                    redacted_changes = []
                    for ch in diff_copy["changes"]:
                        if isinstance(ch, dict):
                            ch_copy = dict(ch)
                            ch_copy["after"] = None
                            redacted_changes.append(ch_copy)
                        else:
                            redacted_changes.append(ch)
                    diff_copy["changes"] = redacted_changes

            filtered.append(event_copy)
        else:
            vis = str(event.get("visibility") or "private").strip().lower()
            if vis == "private":
                event_actors = {
                    str(event.get("actor_id") or event.get("actorId") or "").strip(),
                    str(event.get("user_id") or event.get("userId") or "").strip(),
                } - {""}
                if event_actors:
                    if not target_actors or not (event_actors & target_actors):
                        continue
                elif not include_unscoped_legacy:
                    continue
            filtered.append(event)

    filtered.sort(key=lambda event: str(event.get("recordedAt") or ""), reverse=True)
    return filtered


def list_outbox_events(
    stores: DecisionJournalStores,
    *,
    entry_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List outbox events for the decision journal."""
    if _requires_read_snapshot(stores):
        return _with_read_snapshot(stores, lambda snapshot: list_outbox_events(
            snapshot, entry_id=entry_id, tenant_id=tenant_id,
        ))
    if stores.outbox is None:
        return []

    if not stores.is_bundle_locked():
        try:
            if entry_id:
                entry = stores.entries.get(entry_id)
                if entry:
                    _coordinate_pending_txs(stores, entry)
            else:
                for entry in stores.entries.list_all():
                    _coordinate_pending_txs(stores, entry)
        except Exception:
            pass

    events = list(stores.outbox.list_all())
    entry_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    filtered: List[Dict[str, Any]] = []

    for event in events:
        agg_id = str(event.get("aggregate_id") or "").strip()
        if not agg_id:
            continue
        if entry_id and agg_id != str(entry_id).strip():
            continue
        if tenant_id:
            clean_tenant = str(tenant_id).strip()
            if str(event.get("tenant_id") or event.get("tenantId") or "").strip() != clean_tenant:
                continue

        if agg_id not in entry_cache:
            entry_cache[agg_id] = _get_committed_entry_snapshot(
                stores,
                stores.entries.get(agg_id),
                coordinate_if_unlocked=not stores.is_bundle_locked(),
            )
        committed_entry = entry_cache[agg_id]
        if committed_entry is None:
            continue

        event_type = str(event.get("event_type") or "")
        if event_type != "decision_journal.entry.created":
            # For update events, check version: mutation must not exceed committed version
            event_data = event.get("data") or {}
            event_ver = event_data.get("version")
            if event_ver is not None:
                try:
                    if int(event_ver) > int(committed_entry.get("version") or 0):
                        continue
                except (ValueError, TypeError):
                    pass

            # Check idempotency record status if available
            idem_key = event.get("idempotency_key")
            if not idem_key:
                clean_evt_tenant = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
                clean_evt_actor = str(event.get("actor_id") or event.get("actorId") or "").strip()
                raw_key = event.get("raw_idempotency_key")
                if clean_evt_tenant and clean_evt_actor and raw_key:
                    idem_key = patch_idempotency_key(
                        tenant_id=clean_evt_tenant,
                        actor_id=clean_evt_actor,
                        idempotency_key=raw_key,
                    )

            if idem_key and stores.idempotency is not None:
                idem_rec = stores.idempotency.get(idem_key)
                if idem_rec is not None and idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                    continue

            # Check matching transaction in entry history if present
            outbox_id = str(event.get("event_id") or event.get("id") or "")
            tx_history = committed_entry.get("_tx_history")
            if isinstance(tx_history, list) and tx_history:
                tx_uncommitted = False
                for tx in tx_history:
                    if isinstance(tx, dict) and tx.get("outbox_id") == outbox_id:
                        tx_idem = tx.get("idempotency_key")
                        if tx_idem and stores.idempotency is not None:
                            idem_rec = stores.idempotency.get(tx_idem)
                            if idem_rec is not None and idem_rec.get("status") != _IDEM_STATUS_SUCCEEDED:
                                tx_uncommitted = True
                                break
                if tx_uncommitted:
                    continue

        filtered.append(event)

    filtered.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
    return filtered
