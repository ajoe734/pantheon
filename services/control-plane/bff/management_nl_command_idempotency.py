"""Crash-safe command admission for ``POST /bff/management/nl/ask``.

The conversation store remains the durable source for sessions and turns.  This
store owns the shorter command-admission state machine which must be committed
*before* any conversation or provider side effect.  File locks are deliberately
held only while reading or replacing the small state document; callers must not
hold them while an assistant provider is running.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional


STORE_VERSION = "pantheon.management-nl-command-idempotency.v1"
DEFAULT_STORAGE_PATH = "/data/bff/management-nl-command-idempotency.json"
DEFAULT_RECOVERY_SECONDS = 300.0
DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_RECORDS = 10_000
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ManagementNlCommandIdempotencyError(RuntimeError):
    """Base class for fail-closed command-admission errors."""


class ManagementNlCommandPayloadConflict(ManagementNlCommandIdempotencyError):
    """The scoped client key is already bound to a different request."""


class ManagementNlCommandRecoveryRequired(ManagementNlCommandIdempotencyError):
    """A prior execution has an uncertain outcome and must not be repeated."""


class ManagementNlCommandStorageError(ManagementNlCommandIdempotencyError):
    """The durable admission store cannot be trusted."""


@dataclass(frozen=True)
class ManagementNlCommandScope:
    actor_id: str
    tenant_id: str
    route: str
    idempotency_key: str


@dataclass(frozen=True)
class ManagementNlCommandReservation:
    storage_key: str
    request_hash: str
    token: str
    idempotency_key: str


@dataclass(frozen=True)
class ManagementNlCommandAdmission:
    state: str
    reservation: Optional[ManagementNlCommandReservation] = None
    result: Optional[Dict[str, Any]] = None
    recovery_after: Optional[float] = None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ManagementNlCommandStorageError(
            "Management NL command result cannot be serialized for durable replay"
        ) from exc


def _ensure_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists() and cursor.parent != cursor:
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ManagementNlCommandIdempotencyStore:
    """Durable, actor/tenant/route/key-scoped command reservation store."""

    def __init__(
        self,
        storage_path: str = DEFAULT_STORAGE_PATH,
        *,
        recovery_seconds: float = DEFAULT_RECOVERY_SECONDS,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        clean_path = str(storage_path or "").strip()
        if not clean_path:
            raise ManagementNlCommandStorageError(
                "Management NL command idempotency storage path is required"
            )
        self.storage_path = Path(clean_path)
        self.recovery_seconds = max(float(recovery_seconds), 0.001)
        self.retention_seconds = max(float(retention_seconds), 1.0)
        self.max_records = max(int(max_records), 1)
        self.max_response_bytes = max(int(max_response_bytes), 1024)
        self._clock = clock

    @staticmethod
    def storage_key(scope: ManagementNlCommandScope) -> str:
        parts = (
            str(scope.actor_id or "").strip(),
            str(scope.tenant_id or "").strip(),
            str(scope.route or "").strip(),
            str(scope.idempotency_key or "").strip(),
        )
        if any(not part for part in parts):
            raise ManagementNlCommandStorageError(
                "Management NL command scope requires actor, tenant, route, and idempotency key"
            )
        return "management-nl-command-v1:" + _digest("\x00".join(("v1", *parts)))

    @staticmethod
    def _validate_request_hash(request_hash: str) -> str:
        clean = str(request_hash or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", clean):
            raise ManagementNlCommandStorageError(
                "Management NL command request hash must be a SHA-256 digest"
            )
        return clean

    def admit(
        self,
        scope: ManagementNlCommandScope,
        *,
        request_hash: str,
        legacy_result: Optional[Mapping[str, Any]] = None,
        legacy_terminal: bool = False,
    ) -> ManagementNlCommandAdmission:
        """Atomically reserve, wait, or replay one scoped command.

        A terminal legacy conversation idempotency record may safely reconcile a
        reservation left between the legacy commit and this store's commit.  A
        non-terminal legacy record without a reservation is uncertain and is
        never converted into a new owner.
        """

        storage_key = self.storage_key(scope)
        clean_hash = self._validate_request_hash(request_hash)
        legacy = copy.deepcopy(dict(legacy_result)) if legacy_result is not None else None
        if legacy is not None:
            self._validate_response_size(legacy)

        with self._locked_document() as document:
            records = document["records"]
            record = records.get(storage_key)
            if isinstance(record, dict) and record.get("request_hash") != clean_hash:
                raise ManagementNlCommandPayloadConflict(
                    "Management NL idempotency key is bound to a different payload"
                )

            if legacy is not None and legacy_terminal:
                if isinstance(record, dict) and record.get("status") == "complete":
                    replay = self._completed_result(
                        record,
                        idempotency_key=scope.idempotency_key,
                    )
                    if _canonical_json(replay) != _canonical_json(legacy):
                        raise ManagementNlCommandStorageError(
                            "Management NL durable replay stores disagree on the terminal result"
                        )
                    return ManagementNlCommandAdmission(state="complete", result=replay)
                protected_legacy = self._protect_idempotency_key(
                    legacy,
                    scope.idempotency_key,
                )
                self._validate_response_size(protected_legacy)
                records[storage_key] = self._complete_record(
                    request_hash=clean_hash,
                    result=protected_legacy,
                    source="legacy_conversation_store",
                )
                self._write_document(document)
                return ManagementNlCommandAdmission(state="complete", result=legacy)

            if isinstance(record, dict):
                return self._existing_admission(
                    document,
                    storage_key,
                    record,
                    clean_hash,
                    idempotency_key=scope.idempotency_key,
                )

            if legacy is not None:
                records[storage_key] = {
                    "status": "uncertain",
                    "request_hash": clean_hash,
                    "uncertain_since": self._clock(),
                    "reason": "legacy_nonterminal_without_command_reservation",
                }
                self._write_document(document)
                raise ManagementNlCommandRecoveryRequired(
                    "A non-terminal Management NL result survived without a command reservation"
                )

            now = self._clock()
            token = uuid.uuid4().hex
            records[storage_key] = {
                "status": "in_progress",
                "request_hash": clean_hash,
                "reservation_token_hash": _digest(token),
                "started_at": now,
                "recovery_after": now + self.recovery_seconds,
                "scope": {
                    "actor_hash": _digest(str(scope.actor_id).strip()),
                    "tenant_hash": _digest(str(scope.tenant_id).strip()),
                    "route": str(scope.route).strip(),
                    "idempotency_key_hash": _digest(str(scope.idempotency_key).strip()),
                },
            }
            self._write_document(document)
            return ManagementNlCommandAdmission(
                state="owner",
                reservation=ManagementNlCommandReservation(
                    storage_key=storage_key,
                    request_hash=clean_hash,
                    token=token,
                    idempotency_key=str(scope.idempotency_key).strip(),
                ),
                recovery_after=now + self.recovery_seconds,
            )

    def observe(
        self,
        scope: ManagementNlCommandScope,
        *,
        request_hash: str,
    ) -> ManagementNlCommandAdmission:
        """Observe an existing reservation without ever creating a new owner."""

        storage_key = self.storage_key(scope)
        clean_hash = self._validate_request_hash(request_hash)
        with self._locked_document() as document:
            record = document["records"].get(storage_key)
            if not isinstance(record, dict):
                raise ManagementNlCommandStorageError(
                    "Management NL command reservation disappeared while waiting"
                )
            if record.get("request_hash") != clean_hash:
                raise ManagementNlCommandPayloadConflict(
                    "Management NL idempotency key is bound to a different payload"
                )
            return self._existing_admission(
                document,
                storage_key,
                record,
                clean_hash,
                idempotency_key=scope.idempotency_key,
            )

    def complete(
        self,
        reservation: ManagementNlCommandReservation,
        result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        copied = copy.deepcopy(dict(result))
        self._validate_response_size(copied)
        protected = self._protect_idempotency_key(copied, reservation.idempotency_key)
        self._validate_response_size(protected)
        with self._locked_document() as document:
            record = document["records"].get(reservation.storage_key)
            if not isinstance(record, dict):
                raise ManagementNlCommandStorageError(
                    "Management NL command reservation is missing at completion"
                )
            if record.get("request_hash") != reservation.request_hash:
                raise ManagementNlCommandPayloadConflict(
                    "Management NL command reservation request hash changed"
                )
            if record.get("status") == "complete":
                replay = self._completed_result(
                    record,
                    idempotency_key=reservation.idempotency_key,
                )
                if _canonical_json(replay) != _canonical_json(copied):
                    raise ManagementNlCommandStorageError(
                        "Management NL command was completed with a different result"
                    )
                return replay
            if record.get("status") != "in_progress":
                raise ManagementNlCommandRecoveryRequired(
                    "Management NL command outcome is uncertain and cannot be completed implicitly"
                )
            if record.get("reservation_token_hash") != _digest(reservation.token):
                raise ManagementNlCommandStorageError(
                    "Management NL command reservation ownership changed"
                )
            document["records"][reservation.storage_key] = self._complete_record(
                request_hash=reservation.request_hash,
                result=protected,
                source="command_owner",
            )
            self._write_document(document)
        return copied

    def mark_uncertain(
        self,
        reservation: ManagementNlCommandReservation,
        *,
        reason: str,
    ) -> None:
        """Fail closed after a known owner error without releasing the key."""

        with self._locked_document() as document:
            record = document["records"].get(reservation.storage_key)
            if not isinstance(record, dict):
                raise ManagementNlCommandStorageError(
                    "Management NL command reservation is missing"
                )
            if record.get("request_hash") != reservation.request_hash:
                raise ManagementNlCommandPayloadConflict(
                    "Management NL command reservation request hash changed"
                )
            if record.get("status") == "complete":
                return
            if record.get("status") == "in_progress" and record.get(
                "reservation_token_hash"
            ) != _digest(reservation.token):
                raise ManagementNlCommandStorageError(
                    "Management NL command reservation ownership changed"
                )
            record["status"] = "uncertain"
            record["uncertain_since"] = self._clock()
            record["reason"] = str(reason or "owner_failed")[:255]
            self._write_document(document)

    def _existing_admission(
        self,
        document: Dict[str, Any],
        storage_key: str,
        record: Dict[str, Any],
        request_hash: str,
        *,
        idempotency_key: str,
    ) -> ManagementNlCommandAdmission:
        status = str(record.get("status") or "")
        if status == "complete":
            return ManagementNlCommandAdmission(
                state="complete",
                result=self._completed_result(
                    record,
                    idempotency_key=idempotency_key,
                ),
            )
        if status == "uncertain":
            raise ManagementNlCommandRecoveryRequired(
                "A prior Management NL command has an uncertain outcome"
            )
        if status != "in_progress":
            raise ManagementNlCommandStorageError(
                "Management NL command store contains an unsupported state"
            )
        now = self._clock()
        recovery_after = float(record.get("recovery_after") or 0.0)
        if now >= recovery_after:
            record["status"] = "uncertain"
            record["uncertain_since"] = now
            record["reason"] = "reservation_expired"
            document["records"][storage_key] = record
            self._write_document(document)
            raise ManagementNlCommandRecoveryRequired(
                "A prior Management NL command expired without a terminal result"
            )
        return ManagementNlCommandAdmission(
            state="wait",
            recovery_after=recovery_after,
        )

    def _complete_record(
        self,
        *,
        request_hash: str,
        result: Mapping[str, Any],
        source: str,
    ) -> Dict[str, Any]:
        return {
            "status": "complete",
            "request_hash": request_hash,
            "completed_at": self._clock(),
            "source": source,
            "result": copy.deepcopy(dict(result)),
        }

    @classmethod
    def _completed_result(
        cls,
        record: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        result = record.get("result")
        if not isinstance(result, dict):
            raise ManagementNlCommandStorageError(
                "Completed Management NL command result is missing"
            )
        restored = cls._restore_idempotency_key(result, idempotency_key)
        if not isinstance(restored, dict):
            raise ManagementNlCommandStorageError(
                "Completed Management NL command result has an invalid shape"
            )
        return restored

    @staticmethod
    def _idempotency_key_token(idempotency_key: str) -> str:
        return f"__pantheon_management_nl_idempotency_{_digest(str(idempotency_key).strip())}__"

    @classmethod
    def _protect_idempotency_key(cls, value: Any, idempotency_key: str) -> Any:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise ManagementNlCommandStorageError(
                "Management NL idempotency key is missing while protecting replay data"
            )
        token = cls._idempotency_key_token(clean_key)
        return cls._replace_string_recursive(value, clean_key, token)

    @classmethod
    def _restore_idempotency_key(cls, value: Any, idempotency_key: str) -> Any:
        clean_key = str(idempotency_key or "").strip()
        token = cls._idempotency_key_token(clean_key)
        return cls._replace_string_recursive(value, token, clean_key)

    @classmethod
    def _replace_string_recursive(cls, value: Any, source: str, target: str) -> Any:
        if isinstance(value, str):
            return value.replace(source, target)
        if isinstance(value, list):
            return [cls._replace_string_recursive(item, source, target) for item in value]
        if isinstance(value, tuple):
            return [cls._replace_string_recursive(item, source, target) for item in value]
        if isinstance(value, dict):
            return {
                cls._replace_string_recursive(key, source, target) if isinstance(key, str) else key:
                cls._replace_string_recursive(item, source, target)
                for key, item in value.items()
            }
        return copy.deepcopy(value)

    def _validate_response_size(self, result: Mapping[str, Any]) -> None:
        encoded = _canonical_json(result).encode("utf-8")
        if len(encoded) > self.max_response_bytes:
            raise ManagementNlCommandStorageError(
                "Management NL command result exceeds the durable replay size limit"
            )

    def _locked_document(self):
        return _LockedDocument(self)

    def _read_document(self) -> Dict[str, Any]:
        if not self.storage_path.exists():
            return {"version": STORE_VERSION, "records": {}}
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManagementNlCommandStorageError(
                "Management NL command idempotency storage is unreadable"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != STORE_VERSION
            or not isinstance(payload.get("records"), dict)
        ):
            raise ManagementNlCommandStorageError(
                "Management NL command idempotency storage has an unsupported format"
            )
        return payload

    def _write_document(self, document: Dict[str, Any]) -> None:
        self._prune(document)
        try:
            encoded = _canonical_json(document)
            _ensure_directory(self.storage_path.parent)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                dir=str(self.storage_path.parent),
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    descriptor = -1
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.storage_path)
                os.chmod(self.storage_path, 0o600)
                _fsync_directory(self.storage_path.parent)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError) as exc:
            raise ManagementNlCommandStorageError(
                "Management NL command idempotency storage could not be persisted"
            ) from exc

    def _prune(self, document: Dict[str, Any]) -> None:
        records = document["records"]
        now = self._clock()
        expired = [
            key
            for key, record in records.items()
            if isinstance(record, dict)
            and record.get("status") == "complete"
            and now - float(record.get("completed_at") or 0.0) > self.retention_seconds
        ]
        for key in expired:
            records.pop(key, None)
        overflow = len(records) - self.max_records
        if overflow <= 0:
            return
        completed = sorted(
            (
                float(record.get("completed_at") or 0.0),
                key,
            )
            for key, record in records.items()
            if isinstance(record, dict) and record.get("status") == "complete"
        )
        for _, key in completed[:overflow]:
            records.pop(key, None)
        if len(records) > self.max_records:
            raise ManagementNlCommandStorageError(
                "Management NL command store is full of unresolved reservations"
            )


class _LockedDocument:
    def __init__(self, store: ManagementNlCommandIdempotencyStore) -> None:
        self.store = store
        self.handle: Any = None
        self.document: Optional[Dict[str, Any]] = None

    def __enter__(self) -> Dict[str, Any]:
        try:
            _ensure_directory(self.store.storage_path.parent)
            lock_path = self.store.storage_path.with_name(
                f"{self.store.storage_path.name}.lock"
            )
            existed = lock_path.exists()
            self.handle = lock_path.open("a+", encoding="utf-8")
            os.chmod(lock_path, 0o600)
            if not existed:
                _fsync_directory(lock_path.parent)
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
            self.document = self.store._read_document()
            return self.document
        except ManagementNlCommandIdempotencyError:
            self._close()
            raise
        except OSError as exc:
            self._close()
            raise ManagementNlCommandStorageError(
                "Management NL command idempotency lock could not be acquired"
            ) from exc

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self._close()
        return False

    def _close(self) -> None:
        if self.handle is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
