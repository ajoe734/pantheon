"""Append-only restart truth for authoritative persona source requirements.

The store deliberately persists only a digest of the desired persona state and
the resulting requirement-to-connector bindings.  Raw persona documents,
credentials, and connector configuration payloads do not belong in this log.

Every non-blank JSONL record is checksummed independently.  Loading is strict:
one malformed historical record makes the complete log unusable instead of
silently accepting a newer suffix as authoritative truth.
"""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import re
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "source_ingest_requirement_snapshot.v1"
CHECKSUM_ALGORITHM = "sha256"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "sequence",
        "desired_state_sha256",
        "bindings",
        "binding_count",
        "persona_count",
        "authority",
        "authoritative",
    }
)
_ENVELOPE_FIELDS = frozenset({"state", "checksum_algorithm", "checksum"})


class RequirementStateError(RuntimeError):
    """Raised when authoritative requirement restart truth is invalid."""


class _DuplicateJsonKey(ValueError):
    pass


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(state: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(state).encode("utf-8")).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _require_nonempty_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequirementStateError(f"{field_name} must be a non-empty string")
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise RequirementStateError(f"{field_name} contains a control character")
    return value


def _normalize_digest(value: Any) -> str:
    if not isinstance(value, str):
        raise RequirementStateError("desired_state_sha256 must be a 64-character SHA-256 hex digest")
    normalized = value.lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise RequirementStateError("desired_state_sha256 must be a 64-character SHA-256 hex digest")
    return normalized


def _normalize_bindings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RequirementStateError("bindings must map requirement ids to connector ids")
    normalized: dict[str, str] = {}
    for requirement_id, connector_id in value.items():
        requirement = _require_nonempty_string(requirement_id, field_name="binding requirement id")
        connector = _require_nonempty_string(connector_id, field_name="binding connector id")
        normalized[requirement] = connector
    return dict(sorted(normalized.items()))


def _require_nonnegative_int(value: Any, *, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise RequirementStateError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class RequirementSnapshot:
    """Sanitized authoritative desired-state restart snapshot."""

    sequence: int
    desired_state_sha256: str
    bindings: Mapping[str, str]
    persona_count: int
    authority: str
    authoritative: bool = True
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Prevent a caller from mutating the in-memory authority record after
        # its checksum has been written.
        object.__setattr__(self, "bindings", MappingProxyType(dict(self.bindings)))

    @property
    def binding_count(self) -> int:
        return len(self.bindings)

    @property
    def sequence_no(self) -> int:
        """Compatibility spelling used by other controller state models."""

        return self.sequence

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "desired_state_sha256": self.desired_state_sha256,
            "bindings": dict(sorted(self.bindings.items())),
            "binding_count": self.binding_count,
            "persona_count": self.persona_count,
            "authority": self.authority,
            "authoritative": self.authoritative,
        }

    def same_content_as(self, other: "RequirementSnapshot") -> bool:
        return (
            self.desired_state_sha256 == other.desired_state_sha256
            and dict(self.bindings) == dict(other.bindings)
            and self.persona_count == other.persona_count
            and self.authority == other.authority
            and self.authoritative is other.authoritative
        )


def _snapshot_from_state(state: Any) -> RequirementSnapshot:
    if not isinstance(state, Mapping):
        raise RequirementStateError("state must be an object")
    if set(state) != _STATE_FIELDS:
        missing = sorted(_STATE_FIELDS - set(state))
        unexpected = sorted(set(state) - _STATE_FIELDS)
        raise RequirementStateError(
            f"state schema fields do not match (missing={missing}, unexpected={unexpected})"
        )
    if state.get("schema_version") != SCHEMA_VERSION:
        raise RequirementStateError("unsupported requirement snapshot schema")

    sequence = state.get("sequence")
    if type(sequence) is not int or sequence < 1:
        raise RequirementStateError("sequence must be a positive integer")
    digest = _normalize_digest(state.get("desired_state_sha256"))
    # On disk the digest must already be canonical; accepting uppercase here
    # would make the checksummed representation differ from append output.
    if state.get("desired_state_sha256") != digest:
        raise RequirementStateError("desired_state_sha256 must use lowercase hex")
    bindings = _normalize_bindings(state.get("bindings"))
    if dict(state.get("bindings")) != bindings:
        raise RequirementStateError("bindings must be stored in canonical requirement-id order")
    binding_count = _require_nonnegative_int(state.get("binding_count"), field_name="binding_count")
    if binding_count != len(bindings):
        raise RequirementStateError("binding_count does not match bindings")
    persona_count = _require_nonnegative_int(state.get("persona_count"), field_name="persona_count")
    authority = _require_nonempty_string(state.get("authority"), field_name="authority")
    authoritative = state.get("authoritative")
    if type(authoritative) is not bool:
        raise RequirementStateError("authoritative must be a boolean")

    return RequirementSnapshot(
        sequence=sequence,
        desired_state_sha256=digest,
        bindings=bindings,
        persona_count=persona_count,
        authority=authority,
        authoritative=authoritative,
    )


def _snapshots_from_lines(lines: Iterable[str], *, path: Path) -> list[RequirementSnapshot]:
    snapshots: list[RequirementSnapshot] = []
    previous_sequence = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line, object_pairs_hook=_reject_duplicate_json_keys)
        except (json.JSONDecodeError, _DuplicateJsonKey) as exc:
            raise RequirementStateError(
                f"requirement snapshot log is malformed at {path}:{line_number}: {exc}"
            ) from exc
        try:
            if not isinstance(envelope, Mapping) or set(envelope) != _ENVELOPE_FIELDS:
                raise RequirementStateError("snapshot envelope schema is invalid")
            if envelope.get("checksum_algorithm") != CHECKSUM_ALGORITHM:
                raise RequirementStateError("snapshot checksum algorithm must be sha256")
            state = envelope.get("state")
            if not isinstance(state, Mapping):
                raise RequirementStateError("snapshot state must be an object")
            expected_checksum = envelope.get("checksum")
            if not isinstance(expected_checksum, str) or _SHA256_RE.fullmatch(expected_checksum) is None:
                raise RequirementStateError("snapshot checksum must be a lowercase SHA-256 digest")
            actual_checksum = _checksum(state)
            if not hmac.compare_digest(expected_checksum, actual_checksum):
                raise RequirementStateError("snapshot checksum mismatch")
            snapshot = _snapshot_from_state(state)
            if snapshot.sequence <= previous_sequence:
                raise RequirementStateError(
                    f"snapshot sequence regression: {snapshot.sequence} follows {previous_sequence}"
                )
        except RequirementStateError as exc:
            raise RequirementStateError(
                f"requirement snapshot log is invalid at {path}:{line_number}: {exc}"
            ) from exc
        snapshots.append(snapshot)
        previous_sequence = snapshot.sequence
    return snapshots


class RequirementSnapshotStore:
    """Append-only checksummed JSONL store for desired-state authority."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._latest: RequirementSnapshot | None = None
        self.reload()

    @property
    def latest(self) -> RequirementSnapshot | None:
        with self._lock:
            return self._latest

    def reload(self) -> RequirementSnapshot | None:
        """Validate the complete historical log and return its latest state."""

        with self._lock:
            if not self.path.exists():
                self._latest = None
                return None
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                    try:
                        snapshots = _snapshots_from_lines(handle, path=self.path)
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except RequirementStateError:
                self._latest = None
                raise
            except (OSError, UnicodeError) as exc:
                self._latest = None
                raise RequirementStateError(f"requirement snapshot log is unreadable: {self.path}") from exc
            self._latest = snapshots[-1] if snapshots else None
            return self._latest

    def append(
        self,
        desired_state_sha256: str,
        bindings: Mapping[str, str],
        persona_count: int,
        authority: str,
        authoritative: bool = True,
    ) -> RequirementSnapshot:
        """Append a new sanitized snapshot, or return the exact current one.

        The file is locked across validation and append so separate store
        instances cannot allocate the same sequence.  A fully authoritative
        empty ``bindings`` mapping is valid and records removal of all prior
        requirements.
        """

        digest = _normalize_digest(desired_state_sha256)
        normalized_bindings = _normalize_bindings(bindings)
        normalized_persona_count = _require_nonnegative_int(persona_count, field_name="persona_count")
        normalized_authority = _require_nonempty_string(authority, field_name="authority")
        if type(authoritative) is not bool:
            raise RequirementStateError("authoritative must be a boolean")

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                file_preexisted = self.path.exists()
                with self.path.open("a+", encoding="utf-8") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        handle.seek(0)
                        snapshots = _snapshots_from_lines(handle, path=self.path)
                        latest = snapshots[-1] if snapshots else None
                        candidate = RequirementSnapshot(
                            sequence=(latest.sequence + 1) if latest is not None else 1,
                            desired_state_sha256=digest,
                            bindings=normalized_bindings,
                            persona_count=normalized_persona_count,
                            authority=normalized_authority,
                            authoritative=authoritative,
                        )
                        if latest is not None and latest.same_content_as(candidate):
                            self._latest = latest
                            return latest

                        state = candidate.to_dict()
                        envelope = {
                            "state": state,
                            "checksum_algorithm": CHECKSUM_ALGORITHM,
                            "checksum": _checksum(state),
                        }
                        handle.seek(0, os.SEEK_END)
                        handle.write(_canonical_json(envelope) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

                if not file_preexisted:
                    directory_fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            except RequirementStateError:
                self._latest = None
                raise
            except (OSError, UnicodeError) as exc:
                self._latest = None
                raise RequirementStateError(f"requirement snapshot append failed: {self.path}") from exc

            self._latest = candidate
            return candidate


# A descriptive alias for callers that name the persistence surface after the
# module rather than the snapshot object.
RequirementStateStore = RequirementSnapshotStore


__all__ = [
    "CHECKSUM_ALGORITHM",
    "SCHEMA_VERSION",
    "RequirementSnapshot",
    "RequirementSnapshotStore",
    "RequirementStateError",
    "RequirementStateStore",
]
