"""Governed external-source policy for news, social, and alpha DB connectors."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence

from .connectors.base import SourceConnector, SourceEvidenceError, SourceRecord, SourceType


EXTERNAL_RESEARCH_SOURCE_TYPES = {
    SourceType.NEWS.value,
    SourceType.SOCIAL.value,
    SourceType.ALPHA_DB.value,
}

_FORBIDDEN_ALLOWED_USE = {
    "broker",
    "broker_execution",
    "canary_execution",
    "direct_execution",
    "execution",
    "lean",
    "lean_direct_feed",
    "live_execution",
    "live_trading",
    "order_generation",
    "order_routing",
    "runtime",
    "runtime_execution",
    "signal_execution",
}
_FORBIDDEN_ROUTE_VALUES = {
    "broker",
    "broker-adapter",
    "broker_adapter",
    "execution",
    "lean",
    "lean-direct",
    "lean_direct",
    "live",
    "live-execution",
    "live_execution",
    "order-router",
    "order_router",
    "runtime",
    "runtime-manager",
    "runtime_manager",
    "signal-store",
    "signal_store",
}
_ROUTE_KEY_TOKENS = {
    "consumer",
    "consumers",
    "destination",
    "destinations",
    "feed",
    "feeds",
    "route",
    "routes",
    "sink",
    "sinks",
    "target",
    "targets",
    "write_to",
}


def is_external_research_source_type(source_type: SourceType | str) -> bool:
    value = source_type.value if isinstance(source_type, SourceType) else str(source_type)
    return value in EXTERNAL_RESEARCH_SOURCE_TYPES


def validate_external_source_connector(connector: SourceConnector) -> SourceConnector:
    """Validate connector-level policy for governed external research sources."""

    if not is_external_research_source_type(connector.source_type):
        return connector

    metadata = dict(connector.metadata)
    _require_entitlement(metadata, context=f"connector {connector.connector_id}")
    _assert_no_direct_execution_route(metadata, context=f"connector {connector.connector_id} metadata")
    _assert_allowed_use_safe(connector.license_policy.allowed_use, context=f"connector {connector.connector_id}")
    return connector


def validate_external_source_record(
    record: SourceRecord,
    *,
    connector: SourceConnector | None = None,
) -> SourceRecord:
    """Return an external SourceRecord with required ACL/license/PIT fields preserved."""

    if not is_external_research_source_type(record.source_type):
        return record

    if connector is not None:
        validate_external_source_connector(connector)

    source_type = record.source_type.value
    connector_metadata = dict(connector.metadata) if connector else {}
    metadata = {**dict(record.metadata)}

    if connector is not None:
        metadata.setdefault("license_scope", connector.license_scope)
    _copy_if_missing(metadata, connector_metadata, "entitlement_tags")
    _copy_if_missing(metadata, connector_metadata, "entitlement_ref")
    if connector_metadata.get("access_scope") not in (None, "", [], {}) and _strings(
        metadata.get("access_scope"),
        default=(),
    ) in {(), ("public",)}:
        metadata["access_scope"] = connector_metadata["access_scope"]
    else:
        _copy_if_missing(metadata, connector_metadata, "access_scope")

    license_scope = _require_text(metadata.get("license_scope"), "license_scope")
    entitlements = _require_entitlement(metadata, context=f"source record {record.source_id}")
    access_scope = _strings(metadata.get("access_scope"), default=("public",))
    _assert_no_direct_execution_route(metadata, context=f"source record {record.source_id} metadata")

    if source_type == SourceType.NEWS.value:
        _validate_news_metadata(metadata, record)
    elif source_type == SourceType.SOCIAL.value:
        _validate_social_metadata(metadata, record)
    elif source_type == SourceType.ALPHA_DB.value:
        _validate_alpha_metadata(metadata, record, connector=connector)

    event_time = _parse_required_time(
        _first_present(metadata, "event_time", "published_at", "as_of_time", "as_of_date"),
        "event_time",
    )
    available_time = _parse_required_time(metadata.get("available_time"), "available_time")
    if available_time < event_time:
        raise SourceEvidenceError("available_time must be >= event_time for PIT external source records")

    content_hash = str(metadata.get("content_hash") or metadata.get("body_hash") or _content_hash(record, metadata))
    metadata.update(
        {
            "license_scope": license_scope,
            "entitlement_tags": list(entitlements),
            "access_scope": list(access_scope),
            "event_time": _iso(event_time),
            "available_time": _iso(available_time),
            "content_hash": content_hash,
            "body_hash": str(metadata.get("body_hash") or content_hash),
            "external_source_policy": "news_social_alpha_pit_v1",
            "pit": {
                **(metadata.get("pit") if isinstance(metadata.get("pit"), dict) else {}),
                "event_time": _iso(event_time),
                "available_time": _iso(available_time),
                "validated": True,
            },
            "governance": {
                **(metadata.get("governance") if isinstance(metadata.get("governance"), dict) else {}),
                "canonical_sink": "SourceRecord/EvidenceBundle",
                "direct_execution_allowed": False,
                "lean_consumption": "research_only_not_direct_action",
                "broker_consumption": "not_direct_action",
            },
        }
    )

    return SourceRecord(
        source_id=record.source_id,
        connector_id=record.connector_id,
        source_type=record.source_type.value,
        title=record.title,
        content_ref=record.content_ref,
        status=record.status.value,
        metadata=metadata,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


def external_source_bundle_metadata(
    source_records: Sequence[SourceRecord],
    evidence_items: Sequence[Any],
) -> dict[str, Any]:
    """Summarize external-source governance fields for an EvidenceBundle."""

    external_sources = [
        source for source in source_records if is_external_research_source_type(source.source_type)
    ]
    if not external_sources:
        return {}

    entitlements: list[str] = []
    access_scopes: list[str] = []
    available_times: list[str] = []
    source_types: list[str] = []
    for source in external_sources:
        metadata = dict(source.metadata)
        source_types.append(source.source_type.value)
        entitlements.extend(_strings(metadata.get("entitlement_tags"), default=()))
        access_scopes.extend(_strings(metadata.get("access_scope"), default=()))
        if metadata.get("available_time"):
            available_times.append(str(metadata["available_time"]))
    for item in evidence_items:
        metadata = dict(getattr(item, "metadata", {}) or {})
        entitlements.extend(_strings(metadata.get("entitlement_tags"), default=()))
        if getattr(item, "available_time", None):
            available_times.append(str(getattr(item, "available_time")))

    return {
        "external_source_policy": "news_social_alpha_pit_v1",
        "source_types": sorted(set(source_types)),
        "entitlement_tags": sorted(set(entitlements)),
        "access_scope": sorted(set(access_scopes)),
        "available_time": _latest_time_iso(available_times),
        "pit_validated": True,
        "direct_execution_allowed": False,
    }


def _validate_news_metadata(metadata: Mapping[str, Any], record: SourceRecord) -> None:
    _require_text(metadata.get("publisher"), "publisher")
    _require_text(_first_present(metadata, "published_at", "event_time"), "published_at")
    _require_text(record.content_ref, "source_uri")


def _validate_social_metadata(metadata: Mapping[str, Any], _record: SourceRecord) -> None:
    for field_name in ("platform", "author_id_hash", "post_id", "platform_policy_ref"):
        _require_text(metadata.get(field_name), field_name)
    trust_score = float(_require_text(metadata.get("trust_score"), "trust_score"))
    if trust_score < 0.0 or trust_score > 1.0:
        raise SourceEvidenceError("trust_score must be between 0.0 and 1.0")


def _validate_alpha_metadata(
    metadata: Mapping[str, Any],
    _record: SourceRecord,
    *,
    connector: SourceConnector | None,
) -> None:
    for field_name in ("alpha_vendor_id", "signal_id", "signal_version", "field_schema", "universe"):
        _require_text(metadata.get(field_name), field_name)
    _require_text(_first_present(metadata, "as_of_time", "as_of_date", "event_time"), "as_of_time")
    allowed_use = _strings(metadata.get("allowed_use"), default=connector.license_policy.allowed_use if connector else ())
    if not allowed_use:
        raise SourceEvidenceError("alpha_db allowed_use is required")
    _assert_allowed_use_safe(allowed_use, context="alpha_db source record")


def _copy_if_missing(target: dict[str, Any], source: Mapping[str, Any], key: str) -> None:
    if key not in target and source.get(key) not in (None, "", [], {}):
        target[key] = source[key]


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SourceEvidenceError(f"{field_name} is required for governed external source records")
    return text


def _first_present(metadata: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _strings(values: Any, *, default: Iterable[str]) -> tuple[str, ...]:
    if values in (None, "", [], ()):
        raw_values = tuple(default)
    elif isinstance(values, str):
        raw_values = (values,)
    elif isinstance(values, Sequence):
        raw_values = tuple(values)
    else:
        raw_values = (values,)
    normalized: list[str] = []
    for value in raw_values:
        text = str(value).strip()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _require_entitlement(metadata: Mapping[str, Any], *, context: str) -> tuple[str, ...]:
    tags = _strings(metadata.get("entitlement_tags"), default=())
    entitlement_ref = str(metadata.get("entitlement_ref") or "").strip()
    if entitlement_ref and entitlement_ref not in tags:
        tags = (*tags, entitlement_ref)
    if not tags:
        raise SourceEvidenceError(f"{context} requires entitlement_tags or entitlement_ref")
    return tags


def _assert_allowed_use_safe(values: Iterable[Any], *, context: str) -> None:
    normalized = {_normalize_token(value) for value in values}
    denied = sorted(normalized & _FORBIDDEN_ALLOWED_USE)
    if denied:
        raise SourceEvidenceError(f"{context} has forbidden direct execution allowed_use: {denied}")


def _assert_no_direct_execution_route(value: Any, *, context: str) -> None:
    for key, item in _iter_mapping_items(value):
        key_text = str(key).strip().lower()
        if key_text in {"direct_execution_allowed", "direct_lean_feed", "direct_broker_feed"} and bool(item):
            raise SourceEvidenceError(f"{context} cannot enable direct Lean, broker, or execution feed")
        if key_text in _ROUTE_KEY_TOKENS or any(token in key_text for token in _ROUTE_KEY_TOKENS):
            route_tokens = {_normalize_token(token) for token in _flatten_values(item)}
            denied = sorted(route_tokens & _FORBIDDEN_ROUTE_VALUES)
            if denied:
                raise SourceEvidenceError(f"{context} cannot target Lean, broker, or execution routes: {denied}")


def _iter_mapping_items(value: Any) -> Iterable[tuple[Any, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key, item
            yield from _iter_mapping_items(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_mapping_items(item)


def _flatten_values(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _flatten_values(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _flatten_values(item)
    else:
        text = str(value or "").strip()
        if text:
            yield text
            for token in text.replace("/", "_").replace("-", "_").split("_"):
                if token:
                    yield token


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _parse_required_time(value: Any, field_name: str) -> datetime:
    text = _require_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceEvidenceError(f"{field_name} must be RFC3339/ISO-8601 for PIT external source records") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_time_iso(values: Iterable[Any]) -> str | None:
    parsed: list[datetime] = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            parsed.append(_parse_required_time(value, "available_time"))
        except SourceEvidenceError:
            continue
    if not parsed:
        return None
    return _iso(max(parsed))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _content_hash(record: SourceRecord, metadata: Mapping[str, Any]) -> str:
    body = _first_present(metadata, "body", "raw_content", "content", "text", "excerpt")
    basis = str(body) if body is not None else "\n".join((record.title, record.content_ref))
    return f"sha256:{sha256(basis.encode('utf-8')).hexdigest()}"
