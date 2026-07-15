from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


_STORE_VERSION = 1
_DEFAULT_STORE_PATH = "/data/bff/assistant-command-idempotency.json"
_DEFAULT_RECOVERY_SECONDS = 30
_DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_MAX_RECORDS = 10_000
_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class CommandIdempotencyError(RuntimeError):
    def __init__(self, message: str, *, reason: str, status_code: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


class CommandIdempotencyKeyRequired(CommandIdempotencyError):
    def __init__(self) -> None:
        super().__init__(
            "Idempotency-Key is required for assistant commands",
            reason="idempotency_key_required",
            status_code=400,
        )


class CommandIdempotencyHeaderConflict(CommandIdempotencyError):
    def __init__(self) -> None:
        super().__init__(
            "Idempotency-Key and X-Idempotency-Key must match when both are supplied",
            reason="idempotency_header_conflict",
            status_code=400,
        )


class CommandIdempotencyKeyInvalid(CommandIdempotencyError):
    def __init__(self) -> None:
        super().__init__(
            "Idempotency-Key must be a non-empty value no longer than 255 characters",
            reason="idempotency_key_invalid",
            status_code=400,
        )


class CommandIdempotencyPayloadConflict(CommandIdempotencyError):
    def __init__(self) -> None:
        super().__init__(
            "Idempotency-Key was already used with a different assistant command payload",
            reason="idempotency_payload_conflict",
            status_code=409,
        )


class CommandIdempotencyInProgress(CommandIdempotencyError):
    def __init__(self) -> None:
        super().__init__(
            "The assistant command for this Idempotency-Key is still in progress",
            reason="idempotency_in_progress",
            status_code=409,
        )


class CommandIdempotencyRecoveryRequired(CommandIdempotencyError):
    def __init__(self) -> None:
        super().__init__(
            "The prior assistant command outcome is uncertain and requires explicit recovery",
            reason="idempotency_recovery_required",
            status_code=409,
        )


class CommandIdempotencyStorageError(CommandIdempotencyError):
    def __init__(self, message: str = "Assistant command idempotency storage is unavailable") -> None:
        super().__init__(message, reason="idempotency_storage_unavailable", status_code=503)


def resolve_command_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
    *,
    required: Optional[bool] = None,
) -> Optional[str]:
    """Resolve the canonical header without silently accepting conflicting aliases."""

    canonical = str(idempotency_key or "").strip()
    alias = str(x_idempotency_key or "").strip()
    if canonical and alias and canonical != alias:
        raise CommandIdempotencyHeaderConflict()
    resolved = canonical or alias
    if resolved and (len(resolved) > 255 or any(ord(char) < 32 for char in resolved)):
        raise CommandIdempotencyKeyInvalid()
    if not resolved:
        if required if required is not None else _env_bool(
            "PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_REQUIRED", False
        ):
            raise CommandIdempotencyKeyRequired()
        return None
    return resolved


class CommandIdempotencyStore:
    """Crash-aware, file-backed idempotency storage for assistant commands.

    Only digests of actor, route, client key, and request payload are persisted.
    Completed response envelopes are retained so an exact retry can return the
    original response. The store file and lock are always owner-readable only.

    An operation that exits without ``complete`` is marked uncertain. It is
    never retried implicitly. After the bounded recovery delay an operator can
    explicitly release that exact actor/route/key/payload tuple with
    ``recover_uncertain`` and a unique recovery identifier.
    """

    def __init__(
        self,
        storage_path: Optional[str] = None,
        *,
        recovery_seconds: Optional[int] = None,
        retention_seconds: Optional[int] = None,
        max_records: Optional[int] = None,
        max_response_bytes: Optional[int] = None,
    ) -> None:
        self.storage_path = Path(
            storage_path
            or os.getenv("PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_STORE_PATH")
            or _DEFAULT_STORE_PATH
        )
        self.recovery_seconds = _bounded_env_int(
            "PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS",
            recovery_seconds,
            _DEFAULT_RECOVERY_SECONDS,
            minimum=0,
        )
        self.retention_seconds = _bounded_env_int(
            "PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_RETENTION_SECONDS",
            retention_seconds,
            _DEFAULT_RETENTION_SECONDS,
            minimum=60,
        )
        self.max_records = _bounded_env_int(
            "PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_MAX_RECORDS",
            max_records,
            _DEFAULT_MAX_RECORDS,
            minimum=1,
        )
        self.max_response_bytes = _bounded_env_int(
            "PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_MAX_RESPONSE_BYTES",
            max_response_bytes,
            _DEFAULT_MAX_RESPONSE_BYTES,
            minimum=1024,
        )

    def transaction(
        self,
        *,
        actor_id: str,
        route: str,
        idempotency_key: str,
        request_payload: Any,
    ) -> "CommandIdempotencyTransaction":
        return CommandIdempotencyTransaction(
            store=self,
            storage_key=_storage_key(actor_id, route, idempotency_key),
            request_hash=_request_hash(request_payload),
        )

    def recover_uncertain(
        self,
        *,
        actor_id: str,
        route: str,
        idempotency_key: str,
        request_payload: Any,
        recovery_id: str,
    ) -> None:
        """Explicitly release a stale uncertain reservation for one exact request.

        This method is deliberately not an HTTP bypass. An operational recovery
        surface must authenticate its caller and supply a unique audit/recovery
        identifier before invoking it.
        """

        clean_recovery_id = str(recovery_id or "").strip()
        if not clean_recovery_id or len(clean_recovery_id) > 255:
            raise CommandIdempotencyKeyInvalid()
        storage_key = _storage_key(actor_id, route, idempotency_key)
        request_hash = _request_hash(request_payload)
        lock_handle = self._lock()
        try:
            document = self._read_store()
            record = document["records"].get(storage_key)
            if not isinstance(record, dict) or record.get("request_hash") != request_hash:
                raise CommandIdempotencyPayloadConflict()
            now = time.time()
            status = str(record.get("status") or "")
            if status == "complete":
                return
            if status == "in_progress" and now < float(record.get("recovery_after") or 0):
                raise CommandIdempotencyInProgress()
            if status not in {"in_progress", "uncertain"}:
                raise CommandIdempotencyRecoveryRequired()
            document["records"][storage_key] = {
                "status": "released",
                "request_hash": request_hash,
                "recovered_at": now,
                "recovery_id_hash": _digest(clean_recovery_id),
            }
            self._write_store(document)
        finally:
            self._unlock(lock_handle)

    def _lock(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.storage_path.with_name(f"{self.storage_path.name}.lock")
            handle = open(lock_path, "a+", encoding="utf-8")
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            return handle
        except OSError as exc:
            raise CommandIdempotencyStorageError() from exc

    @staticmethod
    def _unlock(handle: Any) -> None:
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _read_store(self) -> Dict[str, Any]:
        if not self.storage_path.exists():
            return {"version": _STORE_VERSION, "records": {}}
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandIdempotencyStorageError(
                "Assistant command idempotency storage is unreadable; refusing an uncertain retry"
            ) from exc
        if (
            not isinstance(document, dict)
            or document.get("version") != _STORE_VERSION
            or not isinstance(document.get("records"), dict)
        ):
            raise CommandIdempotencyStorageError(
                "Assistant command idempotency storage has an unsupported format"
            )
        return document

    def _write_store(self, document: Dict[str, Any]) -> None:
        self._prune(document)
        try:
            serialized = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                dir=str(self.storage_path.parent),
            )
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    fd = -1
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.storage_path)
                os.chmod(self.storage_path, 0o600)
                directory_fd = os.open(self.storage_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if fd >= 0:
                    os.close(fd)
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except (OSError, TypeError, ValueError) as exc:
            raise CommandIdempotencyStorageError() from exc

    def _prune(self, document: Dict[str, Any]) -> None:
        records = document.get("records", {})
        now = time.time()
        expired = [
            key
            for key, record in records.items()
            if isinstance(record, dict)
            and record.get("status") == "complete"
            and now - float(record.get("completed_at") or 0) > self.retention_seconds
        ]
        for key in expired:
            records.pop(key, None)
        overflow = len(records) - self.max_records
        if overflow <= 0:
            return
        completed = sorted(
            (
                (float(record.get("completed_at") or 0), key)
                for key, record in records.items()
                if isinstance(record, dict) and record.get("status") == "complete"
            ),
        )
        for _, key in completed[:overflow]:
            records.pop(key, None)
        if len(records) > self.max_records:
            raise CommandIdempotencyStorageError(
                "Assistant command idempotency storage is full of unresolved records"
            )


class CommandIdempotencyTransaction:
    def __init__(self, *, store: CommandIdempotencyStore, storage_key: str, request_hash: str) -> None:
        self.store = store
        self.storage_key = storage_key
        self.request_hash = request_hash
        self.replayed = False
        self.response: Optional[Dict[str, Any]] = None
        self._lock_handle: Any = None
        self._document: Optional[Dict[str, Any]] = None
        self._completed = False

    def __enter__(self) -> "CommandIdempotencyTransaction":
        self._lock_handle = self.store._lock()
        try:
            self._document = self.store._read_store()
            records = self._document["records"]
            record = records.get(self.storage_key)
            now = time.time()
            if isinstance(record, dict):
                if record.get("request_hash") != self.request_hash:
                    raise CommandIdempotencyPayloadConflict()
                status = str(record.get("status") or "")
                if status == "complete":
                    response = record.get("response")
                    if not isinstance(response, dict):
                        raise CommandIdempotencyStorageError(
                            "Completed assistant command response is missing from idempotency storage"
                        )
                    self.replayed = True
                    self.response = copy.deepcopy(response)
                    return self
                if status == "in_progress":
                    if now < float(record.get("recovery_after") or 0):
                        raise CommandIdempotencyInProgress()
                    record["status"] = "uncertain"
                    record["uncertain_since"] = now
                    self.store._write_store(self._document)
                    raise CommandIdempotencyRecoveryRequired()
                if status == "uncertain":
                    raise CommandIdempotencyRecoveryRequired()
                if status != "released":
                    raise CommandIdempotencyStorageError(
                        "Assistant command idempotency record has an unsupported state"
                    )
            previous_recovery = copy.deepcopy(record) if isinstance(record, dict) else None
            new_record: Dict[str, Any] = {
                "status": "in_progress",
                "request_hash": self.request_hash,
                "started_at": now,
                "recovery_after": now + self.store.recovery_seconds,
            }
            if previous_recovery is not None:
                new_record["recovery"] = {
                    "recovered_at": previous_recovery.get("recovered_at"),
                    "recovery_id_hash": previous_recovery.get("recovery_id_hash"),
                }
            records[self.storage_key] = new_record
            self.store._write_store(self._document)
            return self
        except Exception:
            self.store._unlock(self._lock_handle)
            self._lock_handle = None
            raise

    def complete(self, response: Dict[str, Any]) -> None:
        if self.replayed:
            return
        if self._document is None or self._lock_handle is None:
            raise CommandIdempotencyStorageError("Assistant command idempotency transaction is not active")
        try:
            copied = copy.deepcopy(response)
            encoded = json.dumps(copied, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise CommandIdempotencyStorageError(
                "Assistant command response cannot be persisted for exact replay"
            ) from exc
        if len(encoded.encode("utf-8")) > self.store.max_response_bytes:
            raise CommandIdempotencyStorageError(
                "Assistant command response exceeds the idempotency replay storage limit"
            )
        self._document["records"][self.storage_key] = {
            "status": "complete",
            "request_hash": self.request_hash,
            "completed_at": time.time(),
            "response": copied,
        }
        self.store._write_store(self._document)
        self._completed = True

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            if not self.replayed and not self._completed and self._document is not None:
                record = self._document["records"].get(self.storage_key)
                if isinstance(record, dict) and record.get("status") == "in_progress":
                    record["status"] = "uncertain"
                    record["uncertain_since"] = time.time()
                    self.store._write_store(self._document)
        finally:
            self.store._unlock(self._lock_handle)
            self._lock_handle = None
        return False


def _storage_key(actor_id: str, route: str, idempotency_key: str) -> str:
    return _digest(f"v1\x00{actor_id}\x00{route}\x00{idempotency_key}")


def _request_hash(payload: Any) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CommandIdempotencyStorageError(
            "Assistant command payload cannot be canonicalized for idempotency"
        ) from exc
    return _digest(canonical)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _env_bool(name: str, fallback: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_env_int(
    name: str,
    supplied: Optional[int],
    fallback: int,
    *,
    minimum: int,
) -> int:
    raw: Any = supplied if supplied is not None else os.getenv(name)
    if raw in (None, ""):
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, value)
