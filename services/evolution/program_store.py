"""Durable owner store for the Evolution Program aggregate (U8A).

Two backends, selected exactly like other services in this codebase (see
``services/registry/pg_store.py::build_postgres_registry_store``,
``services/capital/pg_store.py::build_capital_pool_store`` and
``services/governance/record_store.py::build_governance_record_store``):

- ``EVOLUTION_PROGRAM_STORE_BACKEND=json`` (default): a single flock- and
  thread-coordinated JSON file for dev/tests, mirroring
  ``services/governance/record_store.py::JsonGovernanceRecordStore``.
- ``EVOLUTION_PROGRAM_STORE_BACKEND=postgres``: two ``PostgresJsonOwnerStore``
  tables (programs + command receipts), mirroring
  ``services/registry/pg_store.py::PostgresRegistryStore.create_with_receipt``
  and ``.commit_metadata_cas`` — the program row and its idempotency receipt
  commit together in one Postgres transaction, so a crash between "program
  written" and "receipt written" cannot happen and a same-key replay always
  returns the originally committed program instead of re-running the
  mutation or fabricating a second one.

Every store method below returns ``(program_dict, replayed: bool)``. A
divergent replay (same idempotency key, different request) raises
:class:`ProgramStoreDivergentReplayError` — never a silently accepted second
version. A stale metadata-patch precondition raises
:class:`ProgramStoreConflictError` — never a partial write.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Tuple

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


class ProgramStoreConflictError(Exception):
    """A compare-and-set precondition (stale revision, or a receipt reserved
    but never committed by a crashed transaction) failed. No partial write
    was made."""


class ProgramStoreDivergentReplayError(Exception):
    """An idempotency key was reused with a different request than the one
    it originally committed."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"idempotency_key {idempotency_key!r} was already committed with a different "
            "request payload; idempotent replay requires an identical request."
        )
        self.idempotency_key = idempotency_key


def _request_digest(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def receipt_key(namespace: str, tenant_id: str, actor_id: str, idempotency_key: str) -> str:
    """Scope an idempotent command receipt by tenant + actor + namespace + key.

    Mirrors ``services/registry/pg_store.py::PostgresRegistryStore.receipt_key``:
    each component is length-prefixed before joining so no delimiter
    collision between fields is possible, and the whole framed string is
    hashed to a fixed size.
    """
    parts = [str(tenant_id or ""), str(actor_id or ""), namespace, str(idempotency_key or "")]
    framed = "|".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


ProgramFactory = Callable[[], Dict[str, Any]]
Mutator = Callable[[Dict[str, Any]], Dict[str, Any]]


class ProgramStore(Protocol):
    def create_with_receipt(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        idempotency_key: Optional[str],
        request_fingerprint: Any,
        program_factory: ProgramFactory,
    ) -> Tuple[Dict[str, Any], bool]:
        ...

    def get(self, program_id: str) -> Optional[Dict[str, Any]]:
        ...

    def list_all(self) -> List[Dict[str, Any]]:
        ...

    def patch_metadata_cas(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        program_id: str,
        idempotency_key: Optional[str],
        request_fingerprint: Any,
        base_snapshot: Dict[str, Any],
        mutate: Mutator,
    ) -> Tuple[Dict[str, Any], bool]:
        ...


# --------------------------------------------------------------------------- #
# Postgres backend
# --------------------------------------------------------------------------- #

class PostgresProgramStore:
    """Durable Postgres-backed program owner store. See module docstring."""

    def __init__(
        self,
        *,
        dsn: str,
        programs_table: str = "evolution.programs",
        receipts_table: str = "evolution.program_command_receipts",
        bootstrap: bool = True,
    ) -> None:
        self._programs = PostgresJsonOwnerStore(
            dsn=dsn, table=programs_table, owner_service="evolution-svc", bootstrap=bootstrap,
        )
        self._receipts = PostgresJsonOwnerStore(
            dsn=dsn, table=receipts_table, owner_service="evolution-svc", bootstrap=bootstrap,
        )

    def create_with_receipt(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        idempotency_key: Optional[str],
        request_fingerprint: Any,
        program_factory: ProgramFactory,
    ) -> Tuple[Dict[str, Any], bool]:
        request_digest = _request_digest({"request": request_fingerprint})
        scoped_key = (
            receipt_key("create_program", tenant_id, actor_id, idempotency_key)
            if idempotency_key
            else None
        )
        with self._programs.transaction() as conn:
            reservation: Optional[Dict[str, Any]] = None
            if scoped_key:
                # Lock ordering: programs table before receipts table (same
                # global order as PostgresRegistryStore.create_with_receipt)
                # to avoid a deadlock against the patch path.
                self._programs.lock_table(conn=conn)
                reservation = {
                    "idempotency_key": idempotency_key,
                    "receipt_key": scoped_key,
                    "request_digest": request_digest,
                    "committed_program": None,
                    "committed_at": None,
                }
                reserved, receipt_payload = self._receipts.insert_if_absent(
                    scoped_key, reservation, conn=conn,
                )
                if not reserved:
                    if receipt_payload.get("request_digest") != request_digest:
                        raise ProgramStoreDivergentReplayError(idempotency_key)
                    committed_program = receipt_payload.get("committed_program")
                    if committed_program is None:
                        raise ProgramStoreConflictError(
                            "idempotency receipt reserved but not yet committed"
                        )
                    return committed_program, True

            program = program_factory()
            created, canonical = self._programs.insert_if_absent(
                program["program_id"], program, conn=conn,
            )
            committed_program = program if created else canonical

            if scoped_key:
                finalized = dict(reservation)
                finalized["committed_program"] = committed_program
                finalized["committed_at"] = committed_program.get("created_at")
                filled, _ = self._receipts.compare_and_set(
                    scoped_key, reservation, finalized, conn=conn,
                )
                if not filled:
                    raise ProgramStoreConflictError("idempotency receipt commit race")
        return committed_program, False

    def get(self, program_id: str) -> Optional[Dict[str, Any]]:
        return self._programs.get(program_id)

    def list_all(self) -> List[Dict[str, Any]]:
        return self._programs.list_all()

    def patch_metadata_cas(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        program_id: str,
        idempotency_key: Optional[str],
        request_fingerprint: Any,
        base_snapshot: Dict[str, Any],
        mutate: Mutator,
    ) -> Tuple[Dict[str, Any], bool]:
        request_digest = _request_digest({"request": request_fingerprint})
        scoped_key = (
            receipt_key("patch_program_name", tenant_id, actor_id, idempotency_key)
            if idempotency_key
            else None
        )
        with self._programs.transaction() as conn:
            reservation: Optional[Dict[str, Any]] = None
            if scoped_key:
                self._programs.lock_table(conn=conn)
                reservation = {
                    "idempotency_key": idempotency_key,
                    "receipt_key": scoped_key,
                    "program_id": program_id,
                    "request_digest": request_digest,
                    "committed_program": None,
                    "committed_at": None,
                }
                reserved, receipt_payload = self._receipts.insert_if_absent(
                    scoped_key, reservation, conn=conn,
                )
                if not reserved:
                    if receipt_payload.get("request_digest") != request_digest:
                        raise ProgramStoreDivergentReplayError(idempotency_key)
                    committed_program = receipt_payload.get("committed_program")
                    if committed_program is None:
                        raise ProgramStoreConflictError(
                            "idempotency receipt reserved but not yet committed"
                        )
                    return committed_program, True

            new_payload = mutate(base_snapshot)
            ok, canonical = self._programs.compare_and_set(
                program_id, base_snapshot, new_payload, conn=conn,
            )
            if not ok:
                raise ProgramStoreConflictError(program_id)

            if scoped_key:
                finalized = dict(reservation)
                finalized["committed_program"] = canonical
                finalized["committed_at"] = canonical.get("updated_at")
                filled, _ = self._receipts.compare_and_set(
                    scoped_key, reservation, finalized, conn=conn,
                )
                if not filled:
                    raise ProgramStoreConflictError(program_id)
        return canonical, False


# --------------------------------------------------------------------------- #
# JSON dev backend
# --------------------------------------------------------------------------- #

_HELD_FLOCKS = threading.local()


class _FileLock:
    """Re-entrant cross-process and thread-safe POSIX file lock.

    Mirrors ``services/governance/record_store.py::_FileLock``.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path.resolve()

    def __enter__(self) -> "_FileLock":
        held = getattr(_HELD_FLOCKS, "held", None)
        if held is None:
            held = {}
            _HELD_FLOCKS.held = held
        key = str(self.lock_path)
        if key not in held:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o666)
            fcntl.flock(fd, fcntl.LOCK_EX)
            held[key] = [1, fd]
        else:
            held[key][0] += 1
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        held = getattr(_HELD_FLOCKS, "held", {})
        key = str(self.lock_path)
        if key in held:
            held[key][0] -= 1
            if held[key][0] <= 0:
                _, fd = held.pop(key)
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass


class JsonProgramStore:
    """Atomic file-locked JSON owner store for dev/test posture.

    Programs and their idempotency receipts live in one JSON file (two
    top-level maps) so the same coordinating lock covers both — giving the
    same "aggregate + receipt commit together" atomicity as the Postgres
    backend's shared transaction, without a real database. Mirrors
    ``services/governance/record_store.py::JsonGovernanceRecordStore``.
    """

    def __init__(self, storage_path: Any) -> None:
        self.storage_path = Path(storage_path)
        self._flock_path = self.storage_path.with_name(f".{self.storage_path.name}.flock")
        self._lock = threading.RLock()
        self._programs: Dict[str, Dict[str, Any]] = {}
        self._receipts: Dict[str, Dict[str, Any]] = {}
        if self.storage_path.exists():
            self._load()

    @contextmanager
    def _coordinate(self) -> Iterator[None]:
        if self._lock._is_owned():  # type: ignore[attr-defined]
            self._refresh()
            yield
        else:
            with _FileLock(self._flock_path), self._lock:
                self._refresh()
                yield

    def _refresh(self) -> None:
        if self.storage_path.exists():
            self._load()
        else:
            self._programs = {}
            self._receipts = {}

    def _load(self) -> None:
        text = self.storage_path.read_text(encoding="utf-8").strip()
        if not text:
            self._programs, self._receipts = {}, {}
            return
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError(f"{self.storage_path} must contain a JSON object")
        self._programs = dict(payload.get("programs") or {})
        self._receipts = dict(payload.get("receipts") or {})

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"programs": self._programs, "receipts": self._receipts},
            indent=2,
            sort_keys=True,
        ) + "\n"
        temporary_path = self.storage_path.with_name(
            f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp"
        )
        fd = os.open(str(temporary_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.storage_path)
        finally:
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def create_with_receipt(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        idempotency_key: Optional[str],
        request_fingerprint: Any,
        program_factory: ProgramFactory,
    ) -> Tuple[Dict[str, Any], bool]:
        request_digest = _request_digest({"request": request_fingerprint})
        scoped_key = (
            receipt_key("create_program", tenant_id, actor_id, idempotency_key)
            if idempotency_key
            else None
        )
        with self._coordinate():
            if scoped_key:
                existing = self._receipts.get(scoped_key)
                if existing is not None:
                    if existing.get("request_digest") != request_digest:
                        raise ProgramStoreDivergentReplayError(idempotency_key)
                    committed_program = existing.get("committed_program")
                    if committed_program is None:
                        raise ProgramStoreConflictError(
                            "idempotency receipt reserved but not yet committed"
                        )
                    return json.loads(json.dumps(committed_program)), True

            program = program_factory()
            self._programs[program["program_id"]] = json.loads(json.dumps(program))
            if scoped_key:
                self._receipts[scoped_key] = {
                    "idempotency_key": idempotency_key,
                    "receipt_key": scoped_key,
                    "request_digest": request_digest,
                    "committed_program": json.loads(json.dumps(program)),
                    "committed_at": program.get("created_at"),
                }
            self._save()
            return json.loads(json.dumps(program)), False

    def get(self, program_id: str) -> Optional[Dict[str, Any]]:
        with self._coordinate():
            record = self._programs.get(str(program_id))
            return json.loads(json.dumps(record)) if record is not None else None

    def list_all(self) -> List[Dict[str, Any]]:
        with self._coordinate():
            return [json.loads(json.dumps(v)) for v in self._programs.values()]

    def patch_metadata_cas(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        program_id: str,
        idempotency_key: Optional[str],
        request_fingerprint: Any,
        base_snapshot: Dict[str, Any],
        mutate: Mutator,
    ) -> Tuple[Dict[str, Any], bool]:
        request_digest = _request_digest({"request": request_fingerprint})
        scoped_key = (
            receipt_key("patch_program_name", tenant_id, actor_id, idempotency_key)
            if idempotency_key
            else None
        )
        with self._coordinate():
            if scoped_key:
                existing = self._receipts.get(scoped_key)
                if existing is not None:
                    if existing.get("request_digest") != request_digest:
                        raise ProgramStoreDivergentReplayError(idempotency_key)
                    committed_program = existing.get("committed_program")
                    if committed_program is None:
                        raise ProgramStoreConflictError(
                            "idempotency receipt reserved but not yet committed"
                        )
                    return json.loads(json.dumps(committed_program)), True

            current = self._programs.get(program_id)
            if current != base_snapshot:
                raise ProgramStoreConflictError(program_id)

            new_payload = mutate(base_snapshot)
            self._programs[program_id] = json.loads(json.dumps(new_payload))
            if scoped_key:
                self._receipts[scoped_key] = {
                    "idempotency_key": idempotency_key,
                    "receipt_key": scoped_key,
                    "program_id": program_id,
                    "request_digest": request_digest,
                    "committed_program": json.loads(json.dumps(new_payload)),
                    "committed_at": new_payload.get("updated_at"),
                }
            self._save()
            return json.loads(json.dumps(new_payload)), False


# --------------------------------------------------------------------------- #
# Backend selection (env-var driven, mirroring
# services/governance/record_store.py::build_governance_record_store and
# services/capital/pg_store.py::build_capital_pool_store)
# --------------------------------------------------------------------------- #

def _program_store_backend() -> str:
    return os.getenv("EVOLUTION_PROGRAM_STORE_BACKEND", "json").strip().lower() or "json"


def _program_store_bootstrap() -> bool:
    return os.getenv("EVOLUTION_PROGRAM_STORE_BOOTSTRAP", "1").strip().lower() not in (
        "0", "false", "no",
    )


def build_program_store(json_storage_path: Any) -> ProgramStore:
    """Build the Evolution Program owner store from environment config.

    ``EVOLUTION_PROGRAM_STORE_BACKEND=json`` (default) uses a local
    flock-coordinated JSON file at ``json_storage_path`` — dev/test posture.
    ``EVOLUTION_PROGRAM_STORE_BACKEND=postgres`` requires
    ``EVOLUTION_PROGRAM_STORE_DSN`` or ``DATABASE_URL`` and raises an
    explicit configuration error rather than silently falling back to JSON.
    """
    backend = _program_store_backend()
    if backend == "json":
        return JsonProgramStore(json_storage_path)
    if backend != "postgres":
        raise ValueError("EVOLUTION_PROGRAM_STORE_BACKEND must be json or postgres")
    dsn = os.getenv("EVOLUTION_PROGRAM_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "EVOLUTION_PROGRAM_STORE_DSN or DATABASE_URL is required for the "
            "Postgres evolution program store"
        )
    return PostgresProgramStore(
        dsn=dsn,
        programs_table=os.getenv("EVOLUTION_PROGRAM_TABLE", "evolution.programs"),
        receipts_table=os.getenv("EVOLUTION_PROGRAM_RECEIPTS_TABLE", "evolution.program_command_receipts"),
        bootstrap=_program_store_bootstrap(),
    )
