"""Source connector and ingest-run domain models.

This module is intentionally small: it defines the governed lifecycle surface
that higher-level adapters can use without pulling raw vendor clients into
SD-03 core logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SourceEvidenceError(f"{field_name} is required")
    return normalized


def _coerce_enum(enum_type: type[Enum], value: Enum | str, field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise SourceEvidenceError(f"{field_name} must be one of: {allowed}") from exc


class SourceEvidenceError(ValueError):
    """Raised when SD-03 source/evidence invariants are violated."""


class SourceType(str, Enum):
    PAPER = "paper"
    REPO = "repo"
    INTERNAL_NOTE = "internal_note"
    FILING = "filing"
    NEWS = "news"
    SOCIAL = "social"
    ALPHA_DB = "alpha_db"
    MACRO = "macro"
    MARKET = "market"
    TELEMETRY = "telemetry"


class AuthType(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH = "oauth"
    SECRET_REF = "secret_ref"
    BROKER_REF = "broker_ref"


class ConnectorMode(str, Enum):
    BATCH = "batch"
    STREAMING = "streaming"
    WEBHOOK = "webhook"


class ConnectorStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"


class IngestRunStatus(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"
    NORMALIZING = "normalizing"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class SourceRecordStatus(str, Enum):
    RAW = "raw"
    NORMALIZED = "normalized"
    INDEXED = "indexed"
    REJECTED = "rejected"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class SourceConnector:
    connector_id: str
    source_type: SourceType | str
    provider: str
    license_scope: str
    auth_type: AuthType | str = AuthType.NONE
    secret_ref_id: str | None = None
    supported_modes: Sequence[ConnectorMode | str] = field(default_factory=lambda: (ConnectorMode.BATCH,))
    status: ConnectorStatus | str = ConnectorStatus.ENABLED
    rate_limit_policy_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "connector_id", _require(self.connector_id, "connector_id"))
        object.__setattr__(self, "provider", _require(self.provider, "provider"))
        object.__setattr__(self, "license_scope", _require(self.license_scope, "license_scope"))
        object.__setattr__(self, "source_type", _coerce_enum(SourceType, self.source_type, "source_type"))
        object.__setattr__(self, "auth_type", _coerce_enum(AuthType, self.auth_type, "auth_type"))
        object.__setattr__(self, "status", _coerce_enum(ConnectorStatus, self.status, "status"))
        modes = tuple(_coerce_enum(ConnectorMode, mode, "supported_modes") for mode in self.supported_modes)
        if not modes:
            raise SourceEvidenceError("supported_modes must include at least one mode")
        object.__setattr__(self, "supported_modes", modes)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "source_type": self.source_type.value,
            "provider": self.provider,
            "auth_type": self.auth_type.value,
            "secret_ref_id": self.secret_ref_id,
            "supported_modes": [mode.value for mode in self.supported_modes],
            "license_scope": self.license_scope,
            "status": self.status.value,
            "rate_limit_policy_ref": self.rate_limit_policy_ref,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    connector_id: str
    source_type: SourceType | str
    title: str
    content_ref: str
    status: SourceRecordStatus | str = SourceRecordStatus.NORMALIZED
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    created_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _require(self.source_id, "source_id"))
        object.__setattr__(self, "connector_id", _require(self.connector_id, "connector_id"))
        object.__setattr__(self, "title", _require(self.title, "title"))
        object.__setattr__(self, "content_ref", _require(self.content_ref, "content_ref"))
        object.__setattr__(self, "source_type", _coerce_enum(SourceType, self.source_type, "source_type"))
        object.__setattr__(self, "status", _coerce_enum(SourceRecordStatus, self.status, "status"))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "trace_id", self.trace_id or str(self.metadata.get("trace_id") or ""))

    @property
    def is_rejected(self) -> bool:
        return self.status == SourceRecordStatus.REJECTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "connector_id": self.connector_id,
            "source_type": self.source_type.value,
            "title": self.title,
            "content_ref": self.content_ref,
            "status": self.status.value,
            "metadata": dict(self.metadata),
            "trace_id": self.trace_id,
            "created_at": _iso(self.created_at),
        }


@dataclass(frozen=True)
class IngestEvent:
    event_type: str
    ingest_run_id: str
    status: IngestRunStatus | str
    trace_id: str
    created_at: datetime | str = field(default_factory=_utc_now)
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _require(self.event_type, "event_type"))
        object.__setattr__(self, "ingest_run_id", _require(self.ingest_run_id, "ingest_run_id"))
        object.__setattr__(self, "status", _coerce_enum(IngestRunStatus, self.status, "status"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "ingest_run_id": self.ingest_run_id,
            "status": self.status.value,
            "trace_id": self.trace_id,
            "created_at": _iso(self.created_at),
            "message": self.message,
        }


@dataclass
class IngestRun:
    ingest_run_id: str
    connector_id: str
    source_type: SourceType | str
    trigger_type: str
    trace_id: str
    status: IngestRunStatus | str = IngestRunStatus.QUEUED
    started_at: datetime | str = field(default_factory=_utc_now)
    finished_at: datetime | str | None = None
    raw_count: int = 0
    normalized_count: int = 0
    rejected_count: int = 0
    events: list[IngestEvent] = field(default_factory=list)

    _ALLOWED_TRANSITIONS = {
        IngestRunStatus.QUEUED: {IngestRunStatus.FETCHING, IngestRunStatus.REJECTED, IngestRunStatus.FAILED},
        IngestRunStatus.FETCHING: {IngestRunStatus.NORMALIZING, IngestRunStatus.FAILED},
        IngestRunStatus.NORMALIZING: {IngestRunStatus.INDEXING, IngestRunStatus.REJECTED, IngestRunStatus.FAILED},
        IngestRunStatus.INDEXING: {IngestRunStatus.COMPLETED, IngestRunStatus.FAILED},
        IngestRunStatus.COMPLETED: set(),
        IngestRunStatus.FAILED: set(),
        IngestRunStatus.REJECTED: set(),
    }

    def __post_init__(self) -> None:
        self.ingest_run_id = _require(self.ingest_run_id, "ingest_run_id")
        self.connector_id = _require(self.connector_id, "connector_id")
        self.trigger_type = _require(self.trigger_type, "trigger_type")
        self.trace_id = _require(self.trace_id, "trace_id")
        self.source_type = _coerce_enum(SourceType, self.source_type, "source_type")
        self.status = _coerce_enum(IngestRunStatus, self.status, "status")
        if not self.events:
            self.events.append(
                IngestEvent(
                    event_type="IngestRunQueued",
                    ingest_run_id=self.ingest_run_id,
                    status=self.status,
                    trace_id=self.trace_id,
                )
            )

    @classmethod
    def new(
        cls,
        *,
        connector_id: str,
        source_type: SourceType | str,
        trigger_type: str,
        trace_id: str,
    ) -> "IngestRun":
        return cls(
            ingest_run_id=f"ingest-{uuid4().hex[:12]}",
            connector_id=connector_id,
            source_type=source_type,
            trigger_type=trigger_type,
            trace_id=trace_id,
        )

    def transition(self, next_status: IngestRunStatus | str, *, message: str | None = None) -> IngestEvent:
        next_value = _coerce_enum(IngestRunStatus, next_status, "next_status")
        allowed = self._ALLOWED_TRANSITIONS[self.status]
        if next_value not in allowed:
            raise SourceEvidenceError(f"Cannot transition ingest run from {self.status.value} to {next_value.value}")
        self.status = next_value
        if next_value in {IngestRunStatus.COMPLETED, IngestRunStatus.FAILED, IngestRunStatus.REJECTED}:
            self.finished_at = _utc_now()
        event = IngestEvent(
            event_type={
                IngestRunStatus.FETCHING: "IngestRunStarted",
                IngestRunStatus.NORMALIZING: "SourceNormalizingStarted",
                IngestRunStatus.INDEXING: "EvidenceIndexingStarted",
                IngestRunStatus.COMPLETED: "IngestRunCompleted",
                IngestRunStatus.FAILED: "IngestRunFailed",
                IngestRunStatus.REJECTED: "IngestRunRejected",
            }[next_value],
            ingest_run_id=self.ingest_run_id,
            status=next_value,
            trace_id=self.trace_id,
            message=message,
        )
        self.events.append(event)
        return event

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingest_run_id": self.ingest_run_id,
            "connector_id": self.connector_id,
            "source_type": self.source_type.value,
            "trigger_type": self.trigger_type,
            "status": self.status.value,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "raw_count": self.raw_count,
            "normalized_count": self.normalized_count,
            "rejected_count": self.rejected_count,
            "trace_id": self.trace_id,
            "events": [event.to_dict() for event in self.events],
        }
