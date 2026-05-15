"""Source connector and ingest-run domain models.

This module is intentionally small: it defines the governed lifecycle surface
that higher-level adapters can use without pulling raw vendor clients into
SD-03 core logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4


_SECRET_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[A-Za-z0-9._/@:+-]+$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:/@+-]+$")
_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "token",
}


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


def _normalized_sequence(values: Sequence[str] | str | None, *, default: Sequence[str] = ()) -> tuple[str, ...]:
    if values is None:
        return tuple(default)
    if isinstance(values, str):
        normalized = (values.strip(),)
    else:
        normalized = tuple(str(value).strip() for value in values)
    return tuple(value for value in normalized if value)


def _assert_no_inline_secret_mapping(value: Mapping[str, Any], *, field_name: str) -> None:
    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if normalized_key in _SENSITIVE_FIELD_NAMES and item not in (None, "", [], {}):
            raise SourceEvidenceError(f"{field_name}.{key} must use secret_ref_id instead of an inline secret")
        if isinstance(item, Mapping):
            _assert_no_inline_secret_mapping(item, field_name=f"{field_name}.{key}")


def _validate_public_ref(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    ref = str(value).strip()
    if not ref:
        return None
    if any(char.isspace() for char in ref):
        raise SourceEvidenceError(f"{field_name} must not contain whitespace")
    if "=" in ref:
        raise SourceEvidenceError(f"{field_name} must be a reference id, not an inline assignment")
    if not (_SECRET_REF_RE.match(ref) or _SAFE_TOKEN_RE.match(ref)):
        raise SourceEvidenceError(f"{field_name} must be a safe secret or policy reference")
    return ref


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
class SecretRef:
    secret_ref_id: str
    required_scopes: Sequence[str] = field(default_factory=tuple)
    rotation_policy_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "secret_ref_id", _validate_public_ref(self.secret_ref_id, "secret_ref_id"))
        object.__setattr__(self, "required_scopes", _normalized_sequence(self.required_scopes))
        object.__setattr__(
            self,
            "rotation_policy_ref",
            _validate_public_ref(self.rotation_policy_ref, "rotation_policy_ref"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "secret_ref_id": self.secret_ref_id,
            "required_scopes": list(self.required_scopes),
            "rotation_policy_ref": self.rotation_policy_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | str | None) -> "SecretRef | None":
        if isinstance(data, cls):
            return data
        if data in (None, ""):
            return None
        if isinstance(data, str):
            return cls(secret_ref_id=data)
        return cls(
            secret_ref_id=str(data["secret_ref_id"]),
            required_scopes=_normalized_sequence(data.get("required_scopes")),
            rotation_policy_ref=data.get("rotation_policy_ref"),
        )


@dataclass(frozen=True)
class AuthPolicy:
    auth_type: AuthType | str = AuthType.NONE
    secret_ref: SecretRef | Mapping[str, Any] | str | None = None
    auth_scope: Sequence[str] = field(default_factory=tuple)
    audience: str | None = None

    def __post_init__(self) -> None:
        auth_type = _coerce_enum(AuthType, self.auth_type, "auth_type")
        secret_ref = SecretRef.from_dict(self.secret_ref)
        if auth_type != AuthType.NONE and secret_ref is None:
            raise SourceEvidenceError(f"auth_type {auth_type.value} requires secret_ref_id")
        object.__setattr__(self, "auth_type", auth_type)
        object.__setattr__(self, "secret_ref", secret_ref)
        object.__setattr__(self, "auth_scope", _normalized_sequence(self.auth_scope))
        object.__setattr__(self, "audience", _validate_public_ref(self.audience, "auth_policy.audience"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "auth_type": self.auth_type.value,
            "secret_ref": self.secret_ref.to_dict() if self.secret_ref else None,
            "auth_scope": list(self.auth_scope),
            "audience": self.audience,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
        *,
        fallback_auth_type: AuthType | str = AuthType.NONE,
        fallback_secret_ref_id: str | None = None,
    ) -> "AuthPolicy":
        if not data:
            secret_ref = SecretRef(secret_ref_id=fallback_secret_ref_id) if fallback_secret_ref_id else None
            return cls(auth_type=fallback_auth_type, secret_ref=secret_ref)
        _assert_no_inline_secret_mapping(data, field_name="auth_policy")
        secret_ref_payload = data.get("secret_ref")
        if secret_ref_payload is None and data.get("secret_ref_id"):
            secret_ref_payload = {"secret_ref_id": data.get("secret_ref_id")}
        return cls(
            auth_type=data.get("auth_type", fallback_auth_type),
            secret_ref=secret_ref_payload,
            auth_scope=_normalized_sequence(data.get("auth_scope")),
            audience=data.get("audience"),
        )


@dataclass(frozen=True)
class RateLimitPolicy:
    requests_per_minute: int | None = None
    burst: int | None = None
    retry_after_seconds: int | None = None
    concurrency: int | None = None
    policy_ref: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("requests_per_minute", "burst", "retry_after_seconds", "concurrency"):
            value = getattr(self, field_name)
            if value is not None and int(value) <= 0:
                raise SourceEvidenceError(f"rate_limit_policy.{field_name} must be > 0")
            if value is not None:
                object.__setattr__(self, field_name, int(value))
        object.__setattr__(self, "policy_ref", _validate_public_ref(self.policy_ref, "rate_limit_policy.policy_ref"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "burst": self.burst,
            "retry_after_seconds": self.retry_after_seconds,
            "concurrency": self.concurrency,
            "policy_ref": self.policy_ref,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any] | None,
        *,
        fallback_policy_ref: str | None = None,
    ) -> "RateLimitPolicy":
        if not data:
            return cls(policy_ref=fallback_policy_ref)
        return cls(
            requests_per_minute=data.get("requests_per_minute"),
            burst=data.get("burst"),
            retry_after_seconds=data.get("retry_after_seconds"),
            concurrency=data.get("concurrency"),
            policy_ref=data.get("policy_ref", fallback_policy_ref),
        )


@dataclass(frozen=True)
class LicensePolicy:
    license_scope: str
    allowed_use: Sequence[str] = field(default_factory=lambda: ("research",))
    attribution_required: bool = False
    redistribution_allowed: bool = False
    policy_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "license_scope", _require(self.license_scope, "license_scope"))
        object.__setattr__(self, "allowed_use", _normalized_sequence(self.allowed_use, default=("research",)))
        object.__setattr__(self, "attribution_required", bool(self.attribution_required))
        object.__setattr__(self, "redistribution_allowed", bool(self.redistribution_allowed))
        object.__setattr__(self, "policy_ref", _validate_public_ref(self.policy_ref, "license_policy.policy_ref"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "license_scope": self.license_scope,
            "allowed_use": list(self.allowed_use),
            "attribution_required": self.attribution_required,
            "redistribution_allowed": self.redistribution_allowed,
            "policy_ref": self.policy_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, *, fallback_license_scope: str) -> "LicensePolicy":
        if not data:
            return cls(license_scope=fallback_license_scope)
        return cls(
            license_scope=str(data.get("license_scope") or fallback_license_scope),
            allowed_use=_normalized_sequence(data.get("allowed_use"), default=("research",)),
            attribution_required=bool(data.get("attribution_required", False)),
            redistribution_allowed=bool(data.get("redistribution_allowed", False)),
            policy_ref=data.get("policy_ref"),
        )


@dataclass(frozen=True)
class SourceMetadata:
    display_name: str | None = None
    homepage_url: str | None = None
    docs_url: str | None = None
    owner: str | None = None
    tags: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for field_name in ("display_name", "homepage_url", "docs_url", "owner"):
            value = getattr(self, field_name)
            object.__setattr__(self, field_name, str(value).strip() if value not in (None, "") else None)
        object.__setattr__(self, "tags", _normalized_sequence(self.tags))

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "homepage_url": self.homepage_url,
            "docs_url": self.docs_url,
            "owner": self.owner,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SourceMetadata":
        if not data:
            return cls()
        return cls(
            display_name=data.get("display_name"),
            homepage_url=data.get("homepage_url"),
            docs_url=data.get("docs_url"),
            owner=data.get("owner"),
            tags=_normalized_sequence(data.get("tags")),
        )


class SourceConnectorProvider(Protocol):
    """SDK surface for provider-owned connectors.

    Provider examples and real adapters both expose a governed connector
    contract plus the fetch configuration consumed by source-ingest. Raw vendor
    clients stay behind the provider object instead of entering SD-03 core code.
    """

    def connector(self) -> "SourceConnector":
        ...

    def fetch_config(self) -> Mapping[str, Any]:
        ...


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
    auth_policy: Mapping[str, Any] | AuthPolicy | None = None
    rate_limit_policy: Mapping[str, Any] | RateLimitPolicy | None = None
    license_policy: Mapping[str, Any] | LicensePolicy | None = None
    source_metadata: Mapping[str, Any] | SourceMetadata | None = None
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
        secret_ref_id = _validate_public_ref(self.secret_ref_id, "secret_ref_id")
        object.__setattr__(self, "secret_ref_id", secret_ref_id)
        if isinstance(self.auth_policy, AuthPolicy):
            auth_policy = self.auth_policy
        else:
            auth_policy = AuthPolicy.from_dict(
                self.auth_policy,
                fallback_auth_type=self.auth_type,
                fallback_secret_ref_id=secret_ref_id,
            )
        if auth_policy.secret_ref and secret_ref_id is None:
            secret_ref_id = auth_policy.secret_ref.secret_ref_id
            object.__setattr__(self, "secret_ref_id", secret_ref_id)
        if secret_ref_id and auth_policy.secret_ref is None:
            auth_policy = AuthPolicy(auth_type=self.auth_type, secret_ref=SecretRef(secret_ref_id=secret_ref_id))
        object.__setattr__(self, "auth_policy", auth_policy)
        object.__setattr__(self, "auth_type", auth_policy.auth_type)
        if isinstance(self.rate_limit_policy, RateLimitPolicy):
            rate_limit_policy = self.rate_limit_policy
        else:
            rate_limit_policy = RateLimitPolicy.from_dict(
                self.rate_limit_policy,
                fallback_policy_ref=self.rate_limit_policy_ref,
            )
        object.__setattr__(self, "rate_limit_policy", rate_limit_policy)
        object.__setattr__(self, "rate_limit_policy_ref", rate_limit_policy.policy_ref or self.rate_limit_policy_ref)
        if isinstance(self.license_policy, LicensePolicy):
            license_policy = self.license_policy
        else:
            license_policy = LicensePolicy.from_dict(self.license_policy, fallback_license_scope=self.license_scope)
        object.__setattr__(self, "license_policy", license_policy)
        object.__setattr__(self, "license_scope", license_policy.license_scope)
        source_metadata = (
            self.source_metadata
            if isinstance(self.source_metadata, SourceMetadata)
            else SourceMetadata.from_dict(self.source_metadata)
        )
        object.__setattr__(self, "source_metadata", source_metadata)
        metadata = dict(self.metadata)
        _assert_no_inline_secret_mapping(metadata, field_name="metadata")
        object.__setattr__(self, "metadata", metadata)

    def policy_summary(self) -> dict[str, Any]:
        return {
            "auth": self.auth_policy.to_dict(),
            "rate_limit": self.rate_limit_policy.to_dict(),
            "license": self.license_policy.to_dict(),
            "source_metadata": self.source_metadata.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "source_connector.v2",
            "connector_id": self.connector_id,
            "source_type": self.source_type.value,
            "provider": self.provider,
            "auth_type": self.auth_type.value,
            "secret_ref_id": self.secret_ref_id,
            "supported_modes": [mode.value for mode in self.supported_modes],
            "license_scope": self.license_scope,
            "status": self.status.value,
            "rate_limit_policy_ref": self.rate_limit_policy_ref,
            "auth_policy": self.auth_policy.to_dict(),
            "rate_limit_policy": self.rate_limit_policy.to_dict(),
            "license_policy": self.license_policy.to_dict(),
            "source_metadata": self.source_metadata.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceConnector":
        return cls(
            connector_id=str(data["connector_id"]),
            source_type=str(data["source_type"]),
            provider=str(data["provider"]),
            license_scope=str(data["license_scope"]),
            auth_type=str(data.get("auth_type", AuthType.NONE.value)),
            secret_ref_id=data.get("secret_ref_id"),
            supported_modes=list(data.get("supported_modes") or (ConnectorMode.BATCH.value,)),
            status=str(data.get("status", ConnectorStatus.ENABLED.value)),
            rate_limit_policy_ref=data.get("rate_limit_policy_ref"),
            auth_policy=data.get("auth_policy"),
            rate_limit_policy=data.get("rate_limit_policy"),
            license_policy=data.get("license_policy"),
            source_metadata=data.get("source_metadata"),
            metadata=dict(data.get("metadata", {})),
        )


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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceRecord":
        return cls(
            source_id=str(data["source_id"]),
            connector_id=str(data["connector_id"]),
            source_type=str(data["source_type"]),
            title=str(data["title"]),
            content_ref=str(data["content_ref"]),
            status=str(data.get("status", SourceRecordStatus.NORMALIZED.value)),
            metadata=dict(data.get("metadata", {})),
            trace_id=str(data.get("trace_id") or ""),
            created_at=data.get("created_at") or _utc_now(),
        )


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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IngestEvent":
        return cls(
            event_type=str(data["event_type"]),
            ingest_run_id=str(data["ingest_run_id"]),
            status=str(data["status"]),
            trace_id=str(data.get("trace_id") or ""),
            created_at=data.get("created_at") or _utc_now(),
            message=data.get("message"),
        )


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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IngestRun":
        return cls(
            ingest_run_id=str(data["ingest_run_id"]),
            connector_id=str(data["connector_id"]),
            source_type=str(data["source_type"]),
            trigger_type=str(data["trigger_type"]),
            trace_id=str(data["trace_id"]),
            status=str(data.get("status", IngestRunStatus.QUEUED.value)),
            started_at=data.get("started_at") or _utc_now(),
            finished_at=data.get("finished_at"),
            raw_count=int(data.get("raw_count", 0)),
            normalized_count=int(data.get("normalized_count", 0)),
            rejected_count=int(data.get("rejected_count", 0)),
            events=[IngestEvent.from_dict(event) for event in data.get("events", [])],
        )
