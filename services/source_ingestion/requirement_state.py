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
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


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


# This read model is deliberately kept beside the Source requirement restart
# state rather than in a paper worker.  Source ingestion is the sole writer of
# normalized market records; paper workers only consume this bounded, stored
# projection through the Source API.
MARKET_SNAPSHOT_SCHEMA_VERSION = "source_ingest_latest_market_snapshot.v1"
MARKET_SNAPSHOT_BATCH_SCHEMA_VERSION = "source_ingest_latest_market_snapshot_batch.v1"
MARKET_SNAPSHOT_CHECKSUM_ALGORITHM = "sha256"
_MARKET_SNAPSHOT_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "symbol",
        "event_time",
        "observed_at",
        "closes",
        "lineage",
        "points",
    }
)
_MARKET_SNAPSHOT_ENVELOPE_FIELDS = frozenset(
    {"state", "checksum_algorithm", "checksum"}
)


class MarketSnapshotStateError(RuntimeError):
    """Raised when the persisted stored-market snapshot projection is invalid."""


def _market_snapshot_text(value: Any, *, field_name: str) -> str:
    try:
        return _require_nonempty_string(value, field_name=field_name).strip()
    except RequirementStateError as exc:
        raise MarketSnapshotStateError(str(exc)) from exc


def _market_snapshot_timestamp(value: Any, *, field_name: str) -> str:
    text = _market_snapshot_text(value, field_name=field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketSnapshotStateError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _market_snapshot_symbol(value: Any) -> str:
    return _market_snapshot_text(value, field_name="symbol").upper()


def _market_snapshot_lookup_symbol(value: Any) -> str:
    """Resolve execution aliases to one explicit stored exchange identity.

    This normalization is deliberately read-only.  Historical snapshot rows
    retain their checksummed symbol and snapshot id, while ``2330.TW`` cannot
    accidentally select an older, separately persisted ``.TW`` series when
    the official Source projection is stored as ``2330.TWSE``.
    """

    symbol = _market_snapshot_symbol(value)
    if "." not in symbol:
        return symbol
    ticker, suffix = symbol.rsplit(".", 1)
    if suffix in {"TW", "TWSE"}:
        return f"{ticker}.TWSE"
    if suffix in {"TWO", "TPEX"}:
        return f"{ticker}.TPEX"
    return symbol


def _market_snapshot_close(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketSnapshotStateError("close must be a positive finite number")
    close = float(value)
    if not math.isfinite(close) or close <= 0:
        raise MarketSnapshotStateError("close must be a positive finite number")
    return close


@dataclass(frozen=True)
class MarketSnapshotPoint:
    """One normalized close retained in the bounded Source-side projection."""

    event_time: str
    close: float
    source_id: str
    connector_id: str
    content_ref: str
    ingest_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_time",
            _market_snapshot_timestamp(self.event_time, field_name="point event_time"),
        )
        object.__setattr__(self, "close", _market_snapshot_close(self.close))
        for field_name in ("source_id", "connector_id", "content_ref", "ingest_run_id"):
            object.__setattr__(
                self,
                field_name,
                _market_snapshot_text(getattr(self, field_name), field_name=f"point {field_name}"),
            )

    @property
    def key(self) -> tuple[str, str, str, str, float]:
        return (
            self.event_time,
            self.source_id,
            self.connector_id,
            self.content_ref,
            self.close,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_time": self.event_time,
            "close": self.close,
            "source_id": self.source_id,
            "connector_id": self.connector_id,
            "content_ref": self.content_ref,
            "ingest_run_id": self.ingest_run_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "MarketSnapshotPoint":
        if not isinstance(value, Mapping) or set(value) != {
            "event_time",
            "close",
            "source_id",
            "connector_id",
            "content_ref",
            "ingest_run_id",
        }:
            raise MarketSnapshotStateError("market snapshot point schema is invalid")
        return cls(
            event_time=value["event_time"],
            close=value["close"],
            source_id=value["source_id"],
            connector_id=value["connector_id"],
            content_ref=value["content_ref"],
            ingest_run_id=value["ingest_run_id"],
        )


@dataclass(frozen=True)
class LatestMarketSnapshot:
    """Latest bounded normalized close series for one canonical market symbol."""

    symbol: str
    points: tuple[MarketSnapshotPoint, ...]
    observed_at: str
    schema_version: str = MARKET_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _market_snapshot_symbol(self.symbol))
        points = tuple(self.points)
        if not points:
            raise MarketSnapshotStateError("market snapshot requires at least one normalized point")
        if tuple(sorted(points, key=lambda point: point.key)) != points:
            raise MarketSnapshotStateError("market snapshot points must be canonically ordered")
        if len({point.key for point in points}) != len(points):
            raise MarketSnapshotStateError("market snapshot points must be unique")
        if len({point.event_time for point in points}) != len(points):
            raise MarketSnapshotStateError(
                "market snapshot points must represent distinct event times"
            )
        object.__setattr__(self, "points", points)
        object.__setattr__(
            self,
            "observed_at",
            _market_snapshot_timestamp(self.observed_at, field_name="observed_at"),
        )
        if self.schema_version != MARKET_SNAPSHOT_SCHEMA_VERSION:
            raise MarketSnapshotStateError("unsupported market snapshot schema")

    @property
    def event_time(self) -> str:
        return self.points[-1].event_time

    @property
    def closes(self) -> tuple[float, ...]:
        return tuple(point.close for point in self.points)

    @property
    def lineage(self) -> dict[str, list[str]]:
        return {
            "source_ids": sorted({point.source_id for point in self.points}),
            "connector_ids": sorted({point.connector_id for point in self.points}),
            "content_refs": sorted({point.content_ref for point in self.points}),
            "ingest_run_ids": sorted({point.ingest_run_id for point in self.points}),
        }

    @property
    def snapshot_id(self) -> str:
        canonical = {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "event_time": self.event_time,
            "closes": list(self.closes),
            "lineage": self.lineage,
            "points": [point.to_dict() for point in self.points],
        }
        return f"mss-{sha256(_canonical_json(canonical).encode('utf-8')).hexdigest()[:24]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "event_time": self.event_time,
            "observed_at": self.observed_at,
            "closes": list(self.closes),
            "lineage": self.lineage,
            "points": [point.to_dict() for point in self.points],
        }

    def to_public_dict(self, *, requested_symbol: Any | None = None) -> dict[str, Any]:
        public_symbol = self.symbol
        if requested_symbol is not None:
            public_symbol = _market_snapshot_symbol(requested_symbol)
            if _market_snapshot_lookup_symbol(public_symbol) != self.symbol:
                raise MarketSnapshotStateError(
                    f"requested symbol {public_symbol!r} does not resolve to "
                    f"stored snapshot symbol {self.symbol!r}"
                )
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "symbol": public_symbol,
            "event_time": self.event_time,
            "observed_at": self.observed_at,
            "closes": list(self.closes),
            "lineage": self.lineage,
            "source_ref": f"source-ingest://snapshots/{self.snapshot_id}",
        }

    @classmethod
    def from_dict(cls, value: Any) -> "LatestMarketSnapshot":
        if not isinstance(value, Mapping) or set(value) != _MARKET_SNAPSHOT_STATE_FIELDS:
            raise MarketSnapshotStateError("market snapshot state schema is invalid")
        if value.get("schema_version") != MARKET_SNAPSHOT_SCHEMA_VERSION:
            raise MarketSnapshotStateError("unsupported market snapshot schema")
        points_value = value.get("points")
        if not isinstance(points_value, list):
            raise MarketSnapshotStateError("market snapshot points must be a list")
        snapshot = cls(
            symbol=value.get("symbol"),
            points=tuple(MarketSnapshotPoint.from_dict(point) for point in points_value),
            observed_at=value.get("observed_at"),
        )
        if value.get("event_time") != snapshot.event_time:
            raise MarketSnapshotStateError("market snapshot event_time does not match points")
        if value.get("closes") != list(snapshot.closes):
            raise MarketSnapshotStateError("market snapshot closes do not match points")
        if value.get("lineage") != snapshot.lineage:
            raise MarketSnapshotStateError("market snapshot lineage does not match points")
        if value.get("snapshot_id") != snapshot.snapshot_id:
            raise MarketSnapshotStateError("market snapshot id does not match stored state")
        return snapshot


def _market_snapshots_from_lines(
    lines: Iterable[str],
    *,
    path: Path,
) -> dict[str, LatestMarketSnapshot]:
    snapshots: dict[str, LatestMarketSnapshot] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line, object_pairs_hook=_reject_duplicate_json_keys)
        except (json.JSONDecodeError, _DuplicateJsonKey) as exc:
            raise MarketSnapshotStateError(
                f"market snapshot log is malformed at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(envelope, Mapping) or set(envelope) != _MARKET_SNAPSHOT_ENVELOPE_FIELDS:
            raise MarketSnapshotStateError(
                f"market snapshot envelope is invalid at {path}:{line_number}"
            )
        if envelope.get("checksum_algorithm") != MARKET_SNAPSHOT_CHECKSUM_ALGORITHM:
            raise MarketSnapshotStateError("market snapshot checksum algorithm must be sha256")
        state = envelope.get("state")
        checksum = envelope.get("checksum")
        if not isinstance(state, Mapping) or not isinstance(checksum, str):
            raise MarketSnapshotStateError("market snapshot envelope state or checksum is invalid")
        if _SHA256_RE.fullmatch(checksum) is None or not hmac.compare_digest(checksum, _checksum(state)):
            raise MarketSnapshotStateError("market snapshot checksum mismatch")
        try:
            snapshot = LatestMarketSnapshot.from_dict(state)
        except MarketSnapshotStateError as exc:
            raise MarketSnapshotStateError(
                f"market snapshot state is invalid at {path}:{line_number}: {exc}"
            ) from exc
        snapshots[snapshot.symbol] = snapshot
    return snapshots


class LatestMarketSnapshotStore:
    """Append-only, checksummed Source projection for latest normalized closes.

    The store accepts only already-normalized SourceRecord data.  Its read API
    opens no connector, does no provider I/O, and has no scheduler dependency.
    """

    def __init__(self, path: str | Path, *, max_closes: int = 60) -> None:
        if type(max_closes) is not int or max_closes < 2:
            raise ValueError("max_closes must be an integer >= 2")
        self.path = Path(path)
        self.max_closes = max_closes
        self._lock = threading.RLock()
        self._latest_by_symbol: dict[str, LatestMarketSnapshot] = {}
        self.reload()

    def reload(self) -> dict[str, LatestMarketSnapshot]:
        with self._lock:
            if not self.path.exists():
                self._latest_by_symbol = {}
                return {}
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                    try:
                        snapshots = _market_snapshots_from_lines(handle, path=self.path)
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except MarketSnapshotStateError:
                self._latest_by_symbol = {}
                raise
            except (OSError, UnicodeError) as exc:
                self._latest_by_symbol = {}
                raise MarketSnapshotStateError(
                    f"market snapshot log is unreadable: {self.path}"
                ) from exc
            self._latest_by_symbol = snapshots
            return dict(snapshots)

    def get(self, symbol: str) -> LatestMarketSnapshot | None:
        with self._lock:
            return self._latest_by_symbol.get(_market_snapshot_lookup_symbol(symbol))

    @staticmethod
    def _point_from_record(record: Any, *, ingest_run_id: str) -> tuple[str, MarketSnapshotPoint] | None:
        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        normalized = metadata.get("normalized_row")
        row = normalized if isinstance(normalized, Mapping) else metadata
        symbol = row.get("symbol_canonical") or row.get("symbol") or metadata.get("symbol_canonical") or metadata.get("symbol")
        close = row.get("close") if row.get("close") is not None else metadata.get("close")
        event_time = (
            row.get("event_time")
            or row.get("trade_date")
            or row.get("as_of_date")
            or metadata.get("event_time")
            or metadata.get("available_time")
            or getattr(record, "created_at", None)
        )
        if symbol in (None, "") or close is None or event_time in (None, ""):
            return None
        try:
            normalized_symbol = _market_snapshot_symbol(symbol)
            point = MarketSnapshotPoint(
                event_time=event_time,
                close=close,
                source_id=getattr(record, "source_id", None),
                connector_id=getattr(record, "connector_id", None),
                content_ref=getattr(record, "content_ref", None),
                ingest_run_id=ingest_run_id,
            )
        except MarketSnapshotStateError:
            return None
        return normalized_symbol, point

    def append_normalized_records(
        self,
        records: Sequence[Any],
        *,
        ingest_run_id: str,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        """Project normalized stored closes into one bounded snapshot per symbol."""

        run_id = _market_snapshot_text(ingest_run_id, field_name="ingest_run_id")
        observed = _market_snapshot_timestamp(
            observed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            field_name="observed_at",
        )
        grouped: dict[str, list[MarketSnapshotPoint]] = {}
        accepted_record_count = 0
        for record in records:
            if bool(getattr(record, "is_rejected", False)):
                continue
            candidate = self._point_from_record(record, ingest_run_id=run_id)
            if candidate is None:
                continue
            symbol, point = candidate
            grouped.setdefault(symbol, []).append(point)
            accepted_record_count += 1

        if not grouped:
            return {
                "schema_version": MARKET_SNAPSHOT_BATCH_SCHEMA_VERSION,
                "ingest_run_id": run_id,
                "accepted_record_count": 0,
                "updated_snapshot_count": 0,
                "snapshots": [],
            }

        updated: list[LatestMarketSnapshot] = []
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                file_preexisted = self.path.exists()
                with self.path.open("a+", encoding="utf-8") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        handle.seek(0)
                        current = _market_snapshots_from_lines(handle, path=self.path)
                        for symbol in sorted(grouped):
                            prior = current.get(symbol)
                            points_by_event_time = {
                                point.event_time: point
                                for point in (prior.points if prior is not None else ())
                            }
                            for point in grouped[symbol]:
                                # A newer normalized record for an already-seen
                                # trading day replaces that day's lineage. This
                                # keeps closes chronologically distinct while
                                # retaining the exact official record selected
                                # by the latest successful ingest run.
                                points_by_event_time[point.event_time] = point
                            points = tuple(
                                sorted(
                                    points_by_event_time.values(),
                                    key=lambda point: point.key,
                                )[-self.max_closes :]
                            )
                            candidate = LatestMarketSnapshot(
                                symbol=symbol,
                                points=points,
                                observed_at=observed,
                            )
                            if prior is not None and prior.points == candidate.points:
                                continue
                            state = candidate.to_dict()
                            envelope = {
                                "state": state,
                                "checksum_algorithm": MARKET_SNAPSHOT_CHECKSUM_ALGORITHM,
                                "checksum": _checksum(state),
                            }
                            handle.seek(0, os.SEEK_END)
                            handle.write(_canonical_json(envelope) + "\n")
                            current[symbol] = candidate
                            updated.append(candidate)
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
                self._latest_by_symbol = current
            except MarketSnapshotStateError:
                self._latest_by_symbol = {}
                raise
            except (OSError, UnicodeError) as exc:
                raise MarketSnapshotStateError(
                    f"market snapshot append failed: {self.path}"
                ) from exc

        return {
            "schema_version": MARKET_SNAPSHOT_BATCH_SCHEMA_VERSION,
            "ingest_run_id": run_id,
            "accepted_record_count": accepted_record_count,
            "updated_snapshot_count": len(updated),
            "snapshots": [snapshot.to_public_dict() for snapshot in updated],
        }


__all__ = [
    "CHECKSUM_ALGORITHM",
    "SCHEMA_VERSION",
    "RequirementSnapshot",
    "RequirementSnapshotStore",
    "RequirementStateError",
    "RequirementStateStore",
    "MARKET_SNAPSHOT_SCHEMA_VERSION",
    "MARKET_SNAPSHOT_BATCH_SCHEMA_VERSION",
    "MARKET_SNAPSHOT_CHECKSUM_ALGORITHM",
    "MarketSnapshotStateError",
    "MarketSnapshotPoint",
    "LatestMarketSnapshot",
    "LatestMarketSnapshotStore",
]
