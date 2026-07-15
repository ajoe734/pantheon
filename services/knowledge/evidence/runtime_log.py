"""Durable, tamper-evident runtime evidence JSONL logs.

The log is owned by one service and written to a caller-provided path.  Each
record is recursively redacted before it is checksummed, linked to the prior
record, and appended with one ``O_APPEND`` write while an inter-process lock is
held.  Readers validate the complete sequence and hash chain before returning
any records.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Mapping


RUNTIME_EVIDENCE_SCHEMA_VERSION = "runtime_evidence_log.v1"
REDACTED_VALUE = "[REDACTED]"

_REQUIRED_FIELDS = {
    "schema_version",
    "sequence",
    "previous_checksum",
    "recorded_at",
    "owner_service",
    "event_type",
    "payload",
    "checksum",
}
_SECRET_TOKENS = {
    "authorization",
    "bearer",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passwords",
    "passwd",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_IDENTITY_TOKENS = {
    "actor",
    "email",
    "identity",
    "operator",
    "person",
    "persona",
    "principal",
    "username",
    "user",
}
_NOTE_TOKENS = {
    "annotation",
    "annotations",
    "comment",
    "comments",
    "description",
    "descriptions",
    "message",
    "messages",
    "note",
    "notes",
    "rationale",
    "rationales",
    "reason",
    "reasons",
    "summary",
    "summaries",
    "text",
    "texts",
}
_SECRET_KEYS = {
    "access_key",
    "api_key",
    "private_key",
    "session_key",
    "signing_key",
}
_IDENTITY_KEYS = {
    "approved_by",
    "created_by",
    "decision_by",
    "opened_by",
    "owner_user_id",
    "requested_by",
    "reviewed_by",
    "updated_by",
}
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


class RuntimeEvidenceLogError(RuntimeError):
    """Base error for runtime evidence log operations."""


class RuntimeEvidenceVerificationError(RuntimeEvidenceLogError):
    """Raised when a runtime evidence log is malformed or tampered with."""


@dataclass(frozen=True)
class RuntimeEvidenceVerification:
    """Summary returned after the entire log has been verified."""

    record_count: int
    last_sequence: int
    last_checksum: str | None
    owner_service: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _normalize_timestamp(value: str | None) -> str:
    timestamp = _utc_now() if value is None else _require_text(value, "recorded_at")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("recorded_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("recorded_at must include a timezone")
    return timestamp


def _normalized_key(key: str) -> tuple[str, set[str]]:
    snake = _CAMEL_BOUNDARY.sub("_", key).lower()
    normalized = _NON_ALNUM.sub("_", snake).strip("_")
    return normalized, {token for token in normalized.split("_") if token}


def _is_sensitive_key(key: str) -> bool:
    normalized, tokens = _normalized_key(key)
    if normalized in _IDENTITY_KEYS or normalized in _SECRET_KEYS or normalized.endswith("_by"):
        return True
    return bool(tokens & (_SECRET_TOKENS | _IDENTITY_TOKENS | _NOTE_TOKENS))


def redact_runtime_evidence(value: Any) -> Any:
    """Return a recursively redacted JSON-compatible copy of ``value``.

    Identity, secret/token/credential, and free-form note-like fields are
    replaced as a whole.  Non-sensitive values are retained so identifiers for
    datasets, policies, proofs, and jobs remain useful for correlation.
    """

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            redacted[key] = REDACTED_VALUE if _is_sensitive_key(key) else redact_runtime_evidence(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_runtime_evidence(item) for item in value]
    return value


def _canonical_record_bytes(record: Mapping[str, Any]) -> bytes:
    unsigned = dict(record)
    unsigned.pop("checksum", None)
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_runtime_evidence_checksum(record: Mapping[str, Any]) -> str:
    """Compute the SHA-256 checksum for a record, excluding ``checksum``."""

    return sha256(_canonical_record_bytes(record)).hexdigest()


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _locked(path: Path, *, exclusive: bool) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    process_lock = _process_lock(lock_path)
    with process_lock:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _verification_error(line_number: int, message: str) -> RuntimeEvidenceVerificationError:
    return RuntimeEvidenceVerificationError(f"runtime evidence verification failed at line {line_number}: {message}")


def _validate_record_shape(record: Mapping[str, Any], *, line_number: int) -> None:
    missing = sorted(_REQUIRED_FIELDS - set(record))
    if missing:
        raise _verification_error(line_number, f"missing fields: {', '.join(missing)}")
    if record.get("schema_version") != RUNTIME_EVIDENCE_SCHEMA_VERSION:
        raise _verification_error(line_number, "schema_version mismatch")
    if not isinstance(record.get("sequence"), int) or isinstance(record.get("sequence"), bool):
        raise _verification_error(line_number, "sequence must be an integer")
    if not isinstance(record.get("owner_service"), str) or not str(record["owner_service"]).strip():
        raise _verification_error(line_number, "owner_service must be a non-empty string")
    if not isinstance(record.get("event_type"), str) or not str(record["event_type"]).strip():
        raise _verification_error(line_number, "event_type must be a non-empty string")
    if not isinstance(record.get("recorded_at"), str) or not str(record["recorded_at"]).strip():
        raise _verification_error(line_number, "recorded_at must be a non-empty string")
    try:
        _normalize_timestamp(str(record["recorded_at"]))
    except ValueError as exc:
        raise _verification_error(line_number, str(exc)) from exc
    if not isinstance(record.get("payload"), Mapping):
        raise _verification_error(line_number, "payload must be an object")
    if record.get("previous_checksum") is not None and not isinstance(record.get("previous_checksum"), str):
        raise _verification_error(line_number, "previous_checksum must be a string or null")
    if not isinstance(record.get("checksum"), str) or not str(record["checksum"]).strip():
        raise _verification_error(line_number, "checksum must be a non-empty string")


def _read_verified_unlocked(path: Path, *, expected_owner: str | None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    content = path.read_bytes()
    if not content:
        return []
    if not content.endswith(b"\n"):
        raise _verification_error(1 + content.count(b"\n"), "truncated JSONL record")

    records: list[dict[str, Any]] = []
    prior_checksum: str | None = None
    owner = expected_owner
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if not raw_line.strip():
            raise _verification_error(line_number, "empty JSONL record")
        try:
            parsed = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _verification_error(line_number, f"malformed JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise _verification_error(line_number, "record must be an object")
        _validate_record_shape(parsed, line_number=line_number)

        expected_sequence = line_number
        if parsed["sequence"] != expected_sequence:
            raise _verification_error(
                line_number,
                f"sequence mismatch: expected {expected_sequence}, got {parsed['sequence']!r}",
            )
        if parsed["previous_checksum"] != prior_checksum:
            raise _verification_error(line_number, "previous_checksum chain mismatch")
        record_owner = str(parsed["owner_service"])
        if owner is None:
            owner = record_owner
        elif record_owner != owner:
            raise _verification_error(
                line_number,
                f"owner_service mismatch: expected {owner!r}, got {record_owner!r}",
            )

        try:
            expected_checksum = compute_runtime_evidence_checksum(parsed)
        except (TypeError, ValueError) as exc:
            raise _verification_error(line_number, f"record is not canonical JSON: {exc}") from exc
        if parsed["checksum"] != expected_checksum:
            raise _verification_error(line_number, "checksum mismatch")
        prior_checksum = expected_checksum
        records.append(parsed)
    return records


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_single_line(path: Path, line: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        original_size = os.fstat(descriptor).st_size
        written = os.write(descriptor, line)
        if written != len(line):
            os.ftruncate(descriptor, original_size)
            os.fsync(descriptor)
            raise OSError(errno.EIO, f"short runtime evidence append: wrote {written} of {len(line)} bytes")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


class RuntimeEvidenceLog:
    """Append and verify one service-owned runtime evidence log."""

    def __init__(self, path: str | Path, *, owner_service: str) -> None:
        self.path = Path(path)
        self.owner_service = _require_text(owner_service, "owner_service")

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        """Redact, chain, durably append, and return one evidence record."""

        normalized_event_type = _require_text(event_type, "event_type")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        timestamp = _normalize_timestamp(recorded_at)
        with _locked(self.path, exclusive=True):
            existing = _read_verified_unlocked(self.path, expected_owner=self.owner_service)
            previous_checksum = existing[-1]["checksum"] if existing else None
            record: dict[str, Any] = {
                "schema_version": RUNTIME_EVIDENCE_SCHEMA_VERSION,
                "sequence": len(existing) + 1,
                "previous_checksum": previous_checksum,
                "recorded_at": timestamp,
                "owner_service": self.owner_service,
                "event_type": normalized_event_type,
                "payload": redact_runtime_evidence(payload),
            }
            record["checksum"] = compute_runtime_evidence_checksum(record)
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            _append_single_line(self.path, encoded)
            return record

    def read_verified(self) -> list[dict[str, Any]]:
        """Verify the complete log and return its records."""

        with _locked(self.path, exclusive=False):
            return _read_verified_unlocked(self.path, expected_owner=self.owner_service)

    def verify(self) -> RuntimeEvidenceVerification:
        """Verify the complete log and return its durable head summary."""

        records = self.read_verified()
        return RuntimeEvidenceVerification(
            record_count=len(records),
            last_sequence=int(records[-1]["sequence"]) if records else 0,
            last_checksum=str(records[-1]["checksum"]) if records else None,
            owner_service=self.owner_service,
        )


def append_runtime_evidence(
    path: str | Path,
    *,
    owner_service: str,
    event_type: str,
    payload: Mapping[str, Any],
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for one durable runtime evidence append."""

    return RuntimeEvidenceLog(path, owner_service=owner_service).append(
        event_type,
        payload,
        recorded_at=recorded_at,
    )


def read_runtime_evidence(path: str | Path, *, owner_service: str) -> list[dict[str, Any]]:
    """Convenience wrapper that returns only fully verified records."""

    return RuntimeEvidenceLog(path, owner_service=owner_service).read_verified()


def verify_runtime_evidence(path: str | Path, *, owner_service: str) -> RuntimeEvidenceVerification:
    """Convenience wrapper that verifies and summarizes a runtime evidence log."""

    return RuntimeEvidenceLog(path, owner_service=owner_service).verify()


__all__ = [
    "REDACTED_VALUE",
    "RUNTIME_EVIDENCE_SCHEMA_VERSION",
    "RuntimeEvidenceLog",
    "RuntimeEvidenceLogError",
    "RuntimeEvidenceVerification",
    "RuntimeEvidenceVerificationError",
    "append_runtime_evidence",
    "compute_runtime_evidence_checksum",
    "read_runtime_evidence",
    "redact_runtime_evidence",
    "verify_runtime_evidence",
]
